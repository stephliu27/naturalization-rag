"""Turn retrieved passages into a cited answer. One function, one HTTP POST.

Retrieval is already measured and does the work; this is the last mile. The design goal is
not a good-sounding answer, it is a *checkable* one: every claim carries a source label,
labels map to deterministic chunk IDs, and a label naming something that was never in the
context window is a hallucination this script catches mechanically rather than by reading.

No SDK. The current `google-genai` requires Python >= 3.10 and this venv is 3.9.6, so the
choice was a stale SDK or the wire format; the wire format has no ceiling on which request
fields it can send, and `requests` was already pinned. Retry and error categories come from
`scraping.py`, so a 429 here behaves the way a 429 in the case law fetcher does.

Run:  venv/bin/python scripts/generate.py "can I get a fee waiver for naturalization?"
      venv/bin/python scripts/generate.py "..." --dry-run   # assemble the prompt, call nothing
      set -a; . ./.env; set +a                              # GEMINI_API_KEY
"""

import argparse
import json
import os
import re
import sys
import textwrap
import time

import requests
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citations import format_citation  # noqa: E402  (after the path fix, by necessity)
from query import (  # noqa: E402
    MODEL_NAME, TOP_K, build_where, fetch_window, load_collection, neighbor_ids, search,
    strip_overlap)
from scraping import (  # noqa: E402
    FetchError, ParseError, backoff_seconds, category_for_status, retry_after_seconds)

GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Free-tier Flash. The id is a path segment, not a library feature, so switching model is a
# `--model` flag and nothing else. Google no longer publishes free-tier RPD in the docs — it
# routes you to AI Studio — so treat the daily budget as unknown and check it there.
#
# A pinned version, not the `gemini-flash-latest` alias: an alias would change the model
# under a recorded eval score without changing a line of code, which is the same reason
# temperature is 0. Note that ListModels is not an availability check — it still lists
# gemini-2.5-flash, which now 404s for new keys with "no longer available to new users".
GEMINI_MODEL = "gemini-3.6-flash"

OLLAMA_ROOT = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"

# Temperature 0 because these answers get scored. Note it does *not* buy determinism on a
# thinking model: the same question twice gave 979 thought tokens both times but a clean
# refusal once and a rules-checklist the other. Treat a single answer as one sample.
TEMPERATURE = 0

# How much reasoning to allow: minimal / low / medium / high. Low, because the task is
# grounded extraction from five passages, not deliberation — and because thinking spends the
# *output* budget below. Measured: default thinking took 979 tokens on the fee-waiver probe,
# "low" took 58.
#
# This field is documented only for the newer Interactions API, and works here anyway —
# verified by sending an invalid value and getting "Invalid value at
# generation_config.thinking_config.thinking_level" rather than "Cannot find field". Reaching
# it is the concrete payoff of talking to the API directly instead of through an SDK.
THINKING_LEVEL = "low"

# Shared by reasoning *and* the answer, which is the trap: at 1024 a 979-token thinking pass
# left 45 tokens for the reply and the answer came back truncated mid-sentence with
# finishReason MAX_TOKENS. Sized for the thinking pass, not for the answer.
#
# Generous on purpose. Unused output budget costs nothing on this tier, while a truncated
# answer wastes a whole request — and requests are the scarce resource here, not tokens. A
# tight ceiling spends the scarce thing to conserve the abundant one. Answers measure 40-113
# tokens, so the brevity instruction is what actually bounds length; this only bounds the
# pathological case. Model ceiling is 65,536 if `--thinking high` ever needs more.
MAX_OUTPUT_TOKENS = 8192

# Generation is slower than any scrape in this repo; the read timeout is for a cold model.
TIMEOUT = (5, 90)

MAX_ATTEMPTS = 3
BACKOFF_BASE = 5
MAX_BACKOFF = 30
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Past this we stop waiting and degrade to showing passages. A 429 whose Retry-After is
# minutes away is the daily quota, not a burst, and no amount of sleeping fixes it today.
MAX_RETRY_AFTER = 60

# The refusal is a fixed string so "did it decline" is a boolean rather than a judgment call.
# This is what makes the unanswerable probe scorable — "what is the filing fee for Form
# N-400" has no answer in this corpus, because the Policy Manual carries no dollar amounts.
INSUFFICIENT = "Not covered by the retrieved sources."

SYSTEM_INSTRUCTION = """You answer questions about United States naturalization using only \
the numbered sources given to you.

Rules:
- Use only the sources. Do not add statutes, deadlines, dollar amounts, form numbers or \
procedures from your own knowledge, even when you are confident they are correct.
- After every sentence that makes a factual claim, cite the source that supports it as a \
bracketed label: [S2]. Where two sources support it, cite both: [S2][S4].
- If the sources do not answer the question, reply with exactly this sentence: \
"{insufficient}" followed by one sentence naming what is missing. Do not answer from \
memory instead.
- If two sources disagree, say so and cite both. Do not choose between them.
- The sources are dated and some describe rules that were later changed or struck down. When \
a source supports a figure or rule only as of some past date, give that date and say it may \
not be current. Never present a dated figure as though it were the current one.
- Do not give legal advice and do not tell the reader what to do in their own case.
- Be brief: one short paragraph, or a few bullets.""".format(insufficient=INSUFFICIENT)

# A citation is any bracketed group; the labels are the S-numbers inside it. Two regexes
# rather than one, because the group that contains *no* label is the interesting failure —
# `[8 CFR 316.5]` is the model citing a statute as though it were a provided source.
BRACKETED = re.compile(r"\[([^\]]*)\]")
LABEL = re.compile(r"S(\d+)")

WRAP = 94


def api_key():
    """Fail at the guess, not on a confusing 400 from the API."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit(
            "GEMINI_API_KEY is not set.\n"
            "  set -a; . ./.env; set +a\n"
            "  key from https://aistudio.google.com/apikey (free tier, no card)"
        )
    return key


def strip_carry(text, earlier):
    """`text` with any earlier block's carried tail removed from its front.

    Every block from the same document, most recent first, not just the previous one: the
    ranking interleaves documents, so the block sharing a chunk boundary with this one is
    often two or three labels back. Checking only the last one silently half-works.
    """
    for previous in reversed(earlier):
        stripped = strip_overlap(previous, text)
        if stripped != text:
            return stripped
    return text


def window_text(hit, window, seen):
    """A hit widened to its neighbors, as one block of prose. Mutates `seen`.

    The same text `query.py` prints, for the same reason: a 220-token chunk is what matches
    well and a bad thing to reason from. But two hits from one document can have *windows*
    that overlap without being neighbors of each other — rank 1 and rank 4 both from NWIRP,
    two chunks apart, share the chunk between them. Printed that way the model gets the same
    paragraph under two labels and no way to choose between them, so a chunk is emitted once
    and the higher-ranked block keeps it. The hit's own chunk is never skipped; it is the
    thing being cited. Text carried across *blocks* is `strip_carry`'s job, not this one's.
    """
    ids = sorted(neighbor_ids(hit["metadata"]) + [hit["id"]],
                 key=lambda i: int(i.rsplit("_", 1)[1]))

    parts, previous = [], ""
    for id_ in ids:
        if id_ in seen and id_ != hit["id"]:
            previous = ""  # not contiguous with what follows, so nothing to de-duplicate
            continue
        seen.add(id_)
        body = strip_overlap(previous, hit["text"] if id_ == hit["id"] else window.get(id_, ""))
        previous = body or previous
        if body:
            parts.append(body)
    return "\n".join(parts)


def build_sources(collection, hits):
    """Retrieved chunks as labeled source blocks: [{label, citation, text, id, ...}].

    The label is what the model cites and the chunk id is what it resolves to. The model
    never sees the id — it is 80 characters of filename and buys nothing at generation time,
    while the mapping lives here and makes a citation check a dictionary lookup.
    """
    # Seeded with every hit, so no block's neighbor expansion can swallow a chunk that is
    # about to arrive under its own label. Hits are walked in rank order, so where two blocks
    # want the same neighbor the better match gets it.
    seen = {hit["id"] for hit in hits}
    window = fetch_window(collection, hits)

    # Blocks already emitted, per document — the carry only exists between chunks of the same
    # document, and a 40-character coincidence between two unrelated opinions is not carry.
    emitted = {}

    sources = []
    for rank, hit in enumerate(hits, 1):
        metadata = hit["metadata"]
        source_id = metadata["source_id"]
        text = strip_carry(window_text(hit, window, seen), emitted.get(source_id, []))
        emitted.setdefault(source_id, []).append(text)
        sources.append({
            "label": "S{}".format(rank),
            "citation": format_citation(metadata),
            "section": metadata.get("section", ""),
            "text": text,
            "id": hit["id"],
            "source_id": source_id,
            "source_type": metadata["source_type"],
            "url": metadata.get("url", ""),
            "score": round(hit["score"], 3),
        })
    return sources


def build_prompt(question, sources):
    """The user turn: sources first, question last.

    Question last on purpose — an instruction placed after 3.5K tokens of statute is the one
    the model has just read, and the failure mode here is drifting into a general answer
    about naturalization rather than the question asked.
    """
    blocks = []
    for source in sources:
        header = "[{}] {}".format(source["label"], source["citation"])
        if source["section"]:
            header += " — {}".format(source["section"])
        blocks.append("{}\n{}".format(header, source["text"]))

    return "SOURCES\n\n{}\n\nQUESTION\n\n{}".format("\n\n---\n\n".join(blocks), question)


def call_gemini(prompt, model, key, thinking=THINKING_LEVEL):
    """One POST to generateContent, with retries. Returns the raw payload."""
    url = "{}/models/{}:generateContent".format(GEMINI_ROOT, model)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {"temperature": TEMPERATURE,
                             "maxOutputTokens": MAX_OUTPUT_TOKENS,
                             "thinkingConfig": {"thinkingLevel": thinking}},
    }
    return post_with_retries(url, headers, body)


def call_ollama(prompt, model, key=None, thinking=None):
    """The no-key fallback, so someone who clones this can run it without signing up.

    Takes `key` and `thinking` it ignores, purely so both providers have one call shape.
    """
    body = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_INSTRUCTION,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": MAX_OUTPUT_TOKENS},
    }
    return post_with_retries("{}/api/generate".format(OLLAMA_ROOT),
                             {"Content-Type": "application/json"}, body)


def post_with_retries(url, headers, body):
    """POST JSON, retrying transient statuses. Raises FetchError with a category.

    Same shape as `fetch_json` in the case law fetcher, and deliberately so: the categories
    are what the caller degrades on, and `rate_limited` needs to mean the same thing in both.
    """
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = None
        try:
            response = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            category = category_for_status(status)
            # The API puts a real explanation in the body — "API key not valid", "quota
            # exceeded for model X". Surfacing "HTTP 400" instead throws that away.
            detail = error_detail(error.response)

            if status not in RETRYABLE_STATUS:
                raise FetchError(category, "HTTP {}: {}".format(status, detail))

            wait = retry_after_seconds(error.response)
            if wait is not None and wait > MAX_RETRY_AFTER:
                raise FetchError(category, "HTTP {}, Retry-After {}s exceeds the {}s we will "
                                          "wait: {}".format(status, wait, MAX_RETRY_AFTER,
                                                            detail))
            last_error = FetchError(category, "HTTP {} after {} attempt(s): {}".format(
                status, attempt, detail))

        except requests.exceptions.Timeout:
            last_error = FetchError("timeout", "timed out after {} attempt(s)".format(attempt))

        except requests.exceptions.ConnectionError as error:
            last_error = FetchError("connection_error", "{} after {} attempt(s)".format(
                error.__class__.__name__, attempt))

        except ValueError as error:
            raise ParseError("bad_json", "response was not JSON: {}".format(error))

        if attempt < MAX_ATTEMPTS:
            time.sleep(wait if wait is not None
                       else backoff_seconds(attempt, BACKOFF_BASE, MAX_BACKOFF))

    raise last_error


def error_detail(response):
    """The API's own error message, or the status text if the body is not shaped that way."""
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return (response.text or "").strip()[:200] or response.reason


def read_gemini(payload):
    """(text, finish_reason, usage) from a generateContent response.

    Every one of these can be absent. An answer blocked by a safety filter comes back with
    no candidate at all, which is an IndexError rather than an error message unless it is
    handled here — and immigration questions are exactly the kind that trip those filters.
    """
    usage = payload.get("usageMetadata", {})

    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise ParseError("no_candidate",
                         "no candidate returned{}".format(
                             " (blockReason {})".format(blocked) if blocked else ""))

    candidate = candidates[0]
    finish = candidate.get("finishReason", "")
    parts = (candidate.get("content") or {}).get("parts") or []
    # Thought-summary parts are marked `thought: true` and are not the answer. None come back
    # at present, but they do once `includeThoughts` is set, and joining them in would put the
    # model's reasoning into the answer and past the citation check.
    text = "".join(part.get("text", "") for part in parts
                   if not part.get("thought")).strip()

    if not text:
        # MAX_TOKENS with no text means the whole budget went to reasoning tokens; SAFETY
        # means the answer was cut. Either way, an empty string is not an answer.
        raise ParseError("empty_answer", "candidate had no text (finishReason {})".format(
            finish or "unset"))
    return text, finish, usage


def read_ollama(payload):
    text = (payload.get("response") or "").strip()
    if not text:
        raise ParseError("empty_answer", "no response text")
    usage = {"promptTokenCount": payload.get("prompt_eval_count"),
             "candidatesTokenCount": payload.get("eval_count")}
    return text, "STOP" if payload.get("done") else "", usage


PROVIDERS = {
    "gemini": {"call": call_gemini, "read": read_gemini, "model": GEMINI_MODEL,
               "key": api_key},
    "ollama": {"call": call_ollama, "read": read_ollama, "model": OLLAMA_MODEL,
               "key": lambda: None},
}


def check_citations(answer, sources):
    """Which labels the answer cited, and which of them do not exist.

    The grounding claim this project can actually make. Labels map to deterministic chunk
    IDs, so a citation to [S7] when five sources were provided is a fabrication caught by
    arithmetic — no judgment, no second model, no reading. `malformed` catches the other
    shape: a bracketed group with no label in it at all, which is the model citing a statute
    as if it were one of our sources.
    """
    known = {source["label"] for source in sources}

    cited, unknown, malformed = [], [], []
    for group in BRACKETED.findall(answer):
        labels = LABEL.findall(group)
        if not labels:
            malformed.append(group.strip())
            continue
        for number in labels:
            label = "S{}".format(number)
            (cited if label in known else unknown).append(label)

    return {
        "cited": sorted(set(cited), key=lambda l: int(l[1:])),
        "unknown": sorted(set(unknown), key=lambda l: int(l[1:])),
        "uncited": sorted(known - set(cited), key=lambda l: int(l[1:])),
        "malformed": sorted(set(malformed)),
        "citations": len(cited),
    }


def generate(question, sources, provider="gemini", model=None, thinking=THINKING_LEVEL):
    """Retrieved chunks in, a cited answer out. Never raises; degrades instead.

    A provider failure returns `answer=None` with a `degraded` reason so the caller can show
    the passages it already has. That is the whole reason this returns a dict rather than a
    string: quota exhaustion is a normal Tuesday on a free tier, and a demo that hard-fails
    on it is worse than one that shows five citable passages and says why.
    """
    spec = PROVIDERS[provider]
    model = model or spec["model"]
    # `degraded` is prose for a human; `degraded_category` is the same fact for a caller. An
    # eval loop has to tell "wait and retry this question" from "abort, every question will
    # fail" from "record it, that is a finding" — and it should not learn that by matching
    # English out of a message someone may reword.
    result = {"question": question, "provider": provider, "model": model, "answer": None,
              "degraded": None, "degraded_category": None,
              "refused_verbatim": False, "truncated": False, "usage": {},
              "sources": [{k: source[k] for k in ("label", "citation", "id", "source_id",
                                                  "source_type", "score")}
                          for source in sources]}

    if not sources:
        result["degraded"] = "nothing retrieved"
        result["degraded_category"] = "no_sources"
        return result

    try:
        payload = spec["call"](build_prompt(question, sources), model, spec["key"](), thinking)
        answer, finish, usage = spec["read"](payload)
    except (FetchError, ParseError) as error:
        result["degraded"] = "{}: {}".format(error.category, error)
        result["degraded_category"] = error.category
        return result

    result["answer"] = answer
    result["usage"] = usage
    # Named for what it measures, which is instruction-following and not judgment. A model can
    # reach the right conclusion and word it its own way: Flash-Lite answered "The provided
    # sources do not state the current filing fee" — a refusal by any reading, and False here.
    # Do not treat False as "it answered"; it means either that or "it declined in other words."
    # The looser notion is not mechanically decidable, so it gets hand-counted rather than
    # approximated by a keyword list that would make a fuzzy number look exact.
    result["refused_verbatim"] = answer.startswith(INSUFFICIENT)
    # A truncated answer's last citation may be missing rather than absent, so the citation
    # check is only trustworthy when the model finished.
    result["truncated"] = finish == "MAX_TOKENS"
    result.update(check_citations(answer, sources))
    return result


def answer_question(collection, encoder, question, k=TOP_K, where=None, provider="gemini",
                    model=None, thinking=THINKING_LEVEL):
    """Retrieve, then generate. The two halves stay separate so each can be scored alone."""
    hits = search(collection, encoder, question, k=k, where=where)
    sources = build_sources(collection, hits)
    return generate(question, sources, provider=provider, model=model,
                    thinking=thinking), sources


def print_result(result, sources, show_sources=False):
    print()
    if result["answer"] is None:
        # The graceful path: no answer, but the passages are still the useful part.
        # No trailing period: the API's own messages already end in one.
        print("No generated answer — {}".format(result["degraded"]))
        print("Showing the retrieved sources directly.\n")
        show_sources = True
    else:
        for line in result["answer"].split("\n"):
            for wrapped in textwrap.wrap(line, WRAP) or [""]:
                print(wrapped)

        print("\n{}".format("-" * WRAP))
        for source in sources:
            marker = "*" if source["label"] in result.get("cited", []) else " "
            print("{} [{}] {:.3f}  {}".format(marker, source["label"], source["score"],
                                              source["citation"]))

        flags = []
        if result["unknown"]:
            flags.append("FABRICATED citations: {}".format(", ".join(result["unknown"])))
        if result["malformed"]:
            flags.append("non-source citations: {}".format(
                ", ".join(repr(m) for m in result["malformed"])))
        if result["truncated"]:
            flags.append("answer hit maxOutputTokens, so citations may be cut")
        if result["refused_verbatim"]:
            flags.append("used the required refusal sentence")
        for flag in flags:
            print("  ! {}".format(flag))

        usage = result["usage"]
        if usage.get("promptTokenCount"):
            # Thinking is reported because it spends MAX_OUTPUT_TOKENS alongside the answer;
            # without it here, a truncated reply looks like a verbose model rather than a
            # budget that reasoning already consumed.
            thoughts = usage.get("thoughtsTokenCount")
            print("  {} tokens in, {} out{}  ·  {} citations over {} of {} sources".format(
                usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
                ", {} thinking".format(thoughts) if thoughts else "",
                result["citations"], len(result["cited"]), len(sources)))

    if show_sources:
        for source in sources:
            print("\n[{}] {:.3f}  {}".format(source["label"], source["score"],
                                             source["citation"]))
            print("     {}".format(source["url"]))
            for line in source["text"].split("\n"):
                for wrapped in textwrap.wrap(line, WRAP) or [""]:
                    print("     {}".format(wrapped))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("question", nargs="+", help="the question to answer")
    parser.add_argument("-k", type=int, default=TOP_K,
                        help="how many chunks to retrieve (default %d)" % TOP_K)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="gemini")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--thinking", choices=("minimal", "low", "medium", "high"),
                        default=THINKING_LEVEL,
                        help="reasoning effort, shares the output budget (default %s)"
                             % THINKING_LEVEL)
    parser.add_argument("--type", choices=("uscis", "caselaw"), help="restrict to one half")
    parser.add_argument("--barrier", help="restrict to one barrier tag, e.g. financial")
    parser.add_argument("--dry-run", action="store_true",
                        help="assemble and print the prompt; call no API")
    parser.add_argument("--show-sources", action="store_true",
                        help="print the passages under the answer")
    parser.add_argument("--json", metavar="PATH", help="write the result as JSON")
    args = parser.parse_args()

    question = " ".join(args.question)
    collection = load_collection()
    where = build_where(collection, args.type, args.barrier)
    encoder = SentenceTransformer(MODEL_NAME)

    if args.dry_run:
        hits = search(collection, encoder, question, k=args.k, where=where)
        sources = build_sources(collection, hits)
        prompt = build_prompt(question, sources)
        print(SYSTEM_INSTRUCTION)
        print("\n{}\n".format("=" * WRAP))
        print(prompt)
        # chars/4 is the usual English rule of thumb and not Gemini's tokenizer; the real
        # count comes back in usageMetadata the first time this actually runs.
        print("\n{}\n{} sources, {} chars of prompt, roughly {} tokens (approximate)".format(
            "=" * WRAP, len(sources), len(prompt), len(prompt) // 4))
        return

    result, sources = answer_question(collection, encoder, question, k=args.k, where=where,
                                      provider=args.provider, model=args.model,
                                      thinking=args.thinking)
    print_result(result, sources, show_sources=args.show_sources)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)
        print("\nWrote {}".format(args.json))

    # A fabricated citation is a correctness failure, so it deserves an exit code — that is
    # what lets a future eval or CI job notice it without parsing this output.
    if result.get("unknown"):
        sys.exit(1)


if __name__ == "__main__":
    main()
