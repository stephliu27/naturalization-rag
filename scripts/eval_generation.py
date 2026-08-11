"""Score generated answers on the same question set retrieval is scored on.

The retrieval eval asks whether the right passage arrived. This asks what the model did with
it, on two checks that cost nothing beyond the requests themselves:

    mechanical      fabricated labels, non-source brackets, uncited sources, refusal wording
    anchor@cited    do the sources the answer *cited* contain the expected paragraph

The second is the stronger one, and the reason the question set was written with verbatim
anchors. "The anchor was somewhere in the context" is a claim about retrieval and is already
measured; "the anchor is in a source the answer leaned on" is a claim about grounding.

**It is a lower bound on correctness, not a proxy for it.** An answer can be sound and score
zero: on `fee-waiver` the case law recites the same policy, so the model cites that instead
and the expected chapter's anchors appear nowhere in what it cited. A number this cheap earns
a floor, not a verdict — where the score and an answer you have read disagree, the answer wins.

Neither check says the answer is *right*. Nothing free does — that is the head-to-head's job,
by hand. What these catch is the failure mode that matters most here: a fluent answer citing
sources that do not support it.

One configuration per run, written to its own JSON, compared afterward. Never two variables at
once: `--compare` says so out loud when the two files differ in more than one field. Run every
configuration twice — the gap between two runs of the *same* config is the noise floor, and a
cross-config difference smaller than it is not a result. Temperature 0 is not determinism on a
thinking model.

Run:  venv/bin/python scripts/eval_generation.py --dry-run          # free; the ceiling at this k
      venv/bin/python scripts/eval_generation.py --thinking low --run 1
      venv/bin/python scripts/eval_generation.py --thinking low --run 2   # the noise floor
      venv/bin/python scripts/eval_generation.py --compare a.json b.json
      set -a; . ./.env; set +a                                      # GEMINI_API_KEY
"""

import argparse
import json
import os
import sys
import textwrap
import time

from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_retrieval import load_questions  # noqa: E402  (after the path fix, by necessity)
from generate import THINKING_LEVEL, answer_question, build_sources, build_prompt  # noqa: E402
from query import MODEL_NAME, TOP_K, load_collection, search  # noqa: E402

PROBES_PATH = "data/eval/probes.json"
RESULTS_DIR = "data/eval/generation"

# Not generate.py's default, on purpose. Free-tier limits are per model and gemini-3.6-flash
# gets 20 requests a day — two visitors at the planned session cap, so it cannot serve the
# deployed demo whatever it scores. Flash-Lite gets 500. Score the model you ship.
EVAL_MODEL = "gemini-3.1-flash-lite"

# Read from AI Studio, not from documentation, which no longer publishes them.
REQUESTS_PER_MINUTE = {"gemini-3.1-flash-lite": 15, "gemini-3.6-flash": 5}
UNKNOWN_MODEL_RPM = 5  # the tightest limit seen on this tier; guess low, not high
PACE_SLACK = 1.0       # the limit is enforced server-side and our clock is not theirs

# A 429 that survives generate.py's own three attempts is a burst limit we outran, so the loop
# waits out a full minute and retries the *same question*. Scoring a rate-limit hole as "no
# answer" would put an invisible gap in one arm of a comparison, which is worse than the wait.
RATE_LIMIT_COOLDOWN = 65
MAX_RATE_LIMIT_RETRIES = 2

# Same circuit breaker the scrapers use. Daily exhaustion should stop the run, not burn
# fourteen more questions discovering the same thing fourteen more times.
CONSECUTIVE_FAILURE_LIMIT = 3

# Categories where every remaining question would fail the same way: a bad key, a model id
# that does not exist, a malformed request. Nothing is gained by working through the set.
FATAL_CATEGORIES = ("forbidden", "not_found", "client_error", "bad_json")

# Named subsets, derived for free from the retrieval eval rather than chosen by feel. `movers`
# is every question that gains an expected document at k=8 — the only ones a k comparison can
# possibly move upward. `controls` is where slots 6-8 add nothing expected, which is where
# dilution would show up instead. A subset run needs both halves or it measures only upside.
SUBSETS = {
    "movers": ["false-testimony", "removal-proceedings", "court-decides-or-remands",
               "notice-to-appear-warrant"],
    "controls": ["delay-after-interview", "appeal-denial", "spouse-three-years"],
}

WRAP = 94


def load_probes(path=PROBES_PATH):
    """Questions with no expected documents, scored mechanically only. Absent file is fine."""
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return json.load(f)["probes"]


def select(questions, probes, names, include_probes):
    """The questions to run, in order, each tagged with whether it carries an answer key.

    A probe is a question the retrieval eval structurally cannot score, so it rides in the
    same run and is reported apart from the totals rather than folded into them.
    """
    chosen = []
    for question in questions:
        entry = dict(question)
        entry["probe"] = False
        entry.setdefault("acceptable", [])
        chosen.append(entry)

    if include_probes:
        for probe in probes:
            entry = dict(probe)
            entry["probe"] = True
            entry["expected"] = []
            entry["acceptable"] = []
            chosen.append(entry)

    if not names:
        return chosen

    # A token is either a subset name or a question id, so `--questions movers,fee-waiver`
    # works. Unknown ids are an error rather than an empty run — a typo that silently scores
    # nothing is the same trap the retrieval eval's key validation exists to prevent.
    wanted = []
    for name in names:
        wanted.extend(SUBSETS.get(name, [name]))
    known = {entry["id"] for entry in chosen}
    missing = [name for name in wanted if name not in known]
    if missing:
        sys.exit("No question with id: {}\nKnown: {}".format(
            ", ".join(missing), ", ".join(sorted(known))))
    return [entry for entry in chosen if entry["id"] in wanted]


def score_answer(result, sources, question):
    """Anchor coverage of the cited sources, plus which documents the answer leaned on.

    Anchor containment identifies a document on its own: the retrieval eval's `--validate`
    proves every anchor appears in exactly one document corpus-wide, so there is no need to
    check the anchor landed in the *right* source as well as in a cited one.
    """
    by_label = {source["label"]: source for source in sources}
    # Fabricated labels are already counted; they cannot contribute text, so they drop here.
    cited = [label for label in result.get("cited", []) if label in by_label]

    anchors = [a for entry in question["expected"] for a in entry["anchors"]]
    context = "\n".join(source["text"] for source in sources)
    cited_text = "\n".join(by_label[label]["text"] for label in cited)

    expected_ids = [entry["source_id"] for entry in question["expected"]]
    allowed = set(expected_ids) | set(question.get("acceptable", []))
    retrieved = {source["source_id"] for source in sources}
    cited_ids = {by_label[label]["source_id"] for label in cited}

    return {
        "anchors": len(anchors),
        # The ceiling and the score. in_context is what retrieval handed over at this k, so
        # in_cited can never beat it — reporting both is what separates "the model ignored the
        # passage" from "the passage was never there," which are opposite fixes.
        "anchors_in_context": sum(1 for a in anchors if a in context),
        "anchors_in_cited": sum(1 for a in anchors if a in cited_text),
        "missing_from_cited": [a for a in anchors if a not in cited_text],
        "expected_retrieved": [i for i in expected_ids if i in retrieved],
        "expected_cited": [i for i in expected_ids if i in cited_ids],
        # Undecidable on a probe, which has no answer key at all, so it stays empty rather
        # than counting every citation as off-target.
        "off_target_cites": sorted(
            (label for label in cited if by_label[label]["source_id"] not in allowed),
            key=lambda l: int(l[1:])) if allowed else [],
    }


class Pacer:
    """Holds request *starts* at least `interval` apart, sleeping only what is owed.

    RPM bounds the interval between starts, and generation takes seconds — so a request that
    took 6s against a 4s interval owes nothing. A flat sleep after each one would add a minute
    to a 15-question run to respect a limit that was never close.
    """

    def __init__(self, interval):
        self.interval = interval
        self.last = None

    def wait(self):
        if self.last is not None:
            owed = self.interval - (time.time() - self.last)
            if owed > 0:
                time.sleep(owed)
        self.last = time.time()


def ask(collection, encoder, question, args):
    """One request. Returns (result, sources, seconds); never raises.

    A bug in this repo is caught and categorized too — not to hide it, it is printed and
    counts toward the circuit breaker, but a crash at question twelve should not throw away
    eleven answers that already cost quota.
    """
    start = time.time()
    try:
        result, sources = answer_question(collection, encoder, question["question"], k=args.k,
                                          provider=args.provider, model=args.model,
                                          thinking=args.thinking)
    except Exception as error:  # noqa: BLE001  (deliberate: quota already spent is worth more)
        print("   ! {}: {}".format(error.__class__.__name__, error))
        result = {"answer": None, "degraded": "{}: {}".format(error.__class__.__name__, error),
                  "degraded_category": "harness_error", "refused_verbatim": False,
                  "truncated": False, "usage": {}}
        sources = []
    return result, sources, time.time() - start


def run(collection, encoder, questions, args):
    """Every question once, paced and retried. Returns records for however many completed."""
    pacer = Pacer(args.pace)
    records, consecutive = [], 0

    for position, question in enumerate(questions, 1):
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            pacer.wait()
            result, sources, seconds = ask(collection, encoder, question, args)

            if result["degraded_category"] != "rate_limited" or attempt == MAX_RATE_LIMIT_RETRIES:
                break
            print("   rate limited on {}, waiting {}s and retrying the same question".format(
                question["id"], RATE_LIMIT_COOLDOWN))
            time.sleep(RATE_LIMIT_COOLDOWN)

        record = {"id": question["id"], "question": question["question"],
                  "probe": question["probe"], "seconds": round(seconds, 2)}
        for field in ("answer", "degraded", "degraded_category", "refused_verbatim",
                      "truncated", "usage"):
            record[field] = result.get(field)
        for field in ("cited", "unknown", "uncited", "malformed", "citations"):
            record[field] = result.get(field, [] if field != "citations" else 0)
        record["sources"] = [{k: source[k] for k in ("label", "citation", "source_id", "score")}
                             for source in sources]
        record.update(score_answer(result, sources, question))
        records.append(record)

        print_line(position, len(questions), record)

        if record["answer"] is None:
            consecutive += 1
            if record["degraded_category"] in FATAL_CATEGORIES:
                print("\nStopping: {} fails the same way for every question.".format(
                    record["degraded_category"]))
                break
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                print("\nStopping after {} consecutive failures.".format(consecutive))
                break
        else:
            consecutive = 0

    return records


def status(record):
    """One word for the row. `flag` is reserved for defects a reader must not scroll past."""
    if record["answer"] is None:
        return "FAIL"
    if record["unknown"] or record["malformed"] or record["truncated"]:
        return "flag"
    if record["refused_verbatim"]:
        return "decl"
    return "ok"


def print_line(position, total, record):
    """One question's row, printed as it completes — a paced run is minutes long."""
    if record["answer"] is None:
        print("{:>3}/{}  {:<5} {:<26} {}".format(position, total, "FAIL", record["id"],
                                                 record["degraded"]))
        return

    coverage = ("anchors {}/{} cited, {}/{} in context".format(
        record["anchors_in_cited"], record["anchors"],
        record["anchors_in_context"], record["anchors"]) if record["anchors"]
        else "probe, no answer key")
    thoughts = (record["usage"] or {}).get("thoughtsTokenCount") or 0
    print("{:>3}/{}  {:<5} {:<26} {:<38} {:>4} thought  {:>5.1f}s".format(
        position, total, status(record), record["id"], coverage, thoughts, record["seconds"]))

    for flag in flags_for(record):
        print("        ! {}".format(flag))


def flags_for(record):
    """Everything about this answer a reader should not have to go find."""
    flags = []
    if record["unknown"]:
        flags.append("FABRICATED citations: {}".format(", ".join(record["unknown"])))
    if record["malformed"]:
        flags.append("non-source citations: {}".format(
            ", ".join(repr(m) for m in record["malformed"])))
    if record["truncated"]:
        flags.append("hit maxOutputTokens, so the citation check is not trustworthy here")
    if record["refused_verbatim"]:
        flags.append("used the required refusal sentence")
    if record["off_target_cites"]:
        flags.append("cited {} from a document that is neither expected nor acceptable".format(
            ", ".join(record["off_target_cites"])))
    # The interesting asymmetry: retrieval delivered the document and the answer ignored it.
    # A prompt or reranking problem, and invisible in any retrieval metric.
    ignored = [i for i in record["expected_retrieved"] if i not in record["expected_cited"]]
    if ignored:
        flags.append("retrieved but never cited: {}".format(", ".join(ignored)))
    return flags


def summarize(records):
    """The aggregate. Probes are excluded from anything with an answer key behind it."""
    scored = [r for r in records if not r["probe"]]
    answered = [r for r in scored if r["answer"] is not None]
    usage = [r["usage"] or {} for r in answered]

    def total(field):
        return sum(len(r[field]) for r in answered)

    def tokens(field):
        return sum(u.get(field) or 0 for u in usage)

    anchors = sum(r["anchors"] for r in scored)
    sources = sum(len(r["sources"]) for r in answered)
    return {
        "questions": len(scored),
        "answered": len(answered),
        "degraded": {category: sum(1 for r in scored if r["degraded_category"] == category)
                     for category in sorted({r["degraded_category"] for r in scored
                                             if r["degraded_category"]})},
        "citations": sum(r["citations"] for r in answered),
        "fabricated": total("unknown"),
        "malformed": total("malformed"),
        "off_target_cites": total("off_target_cites"),
        "sources_cited": sum(len(r["cited"]) for r in answered),
        "sources_shown": sources,
        "anchors": anchors,
        "anchors_in_cited": sum(r["anchors_in_cited"] for r in scored),
        "anchors_in_context": sum(r["anchors_in_context"] for r in scored),
        "refused_verbatim": sum(1 for r in answered if r["refused_verbatim"]),
        "truncated": sum(1 for r in answered if r["truncated"]),
        # Conditional on retrieval, which is the only fair way to ask it: an answer cannot
        # cite a document that never arrived, and that failure is already scored elsewhere.
        "expected_retrieved": sum(len(r["expected_retrieved"]) for r in scored),
        "expected_cited": sum(len(r["expected_cited"]) for r in scored),
        "prompt_tokens": tokens("promptTokenCount"),
        "answer_tokens": tokens("candidatesTokenCount"),
        "thought_tokens": tokens("thoughtsTokenCount"),
        "seconds": round(sum(r["seconds"] for r in answered), 1),
    }


def print_report(records, totals, config):
    print("\n{}\n".format("-" * WRAP))
    print("{:<16}{}, k={}, thinking {}".format(
        "config", config["model"], config["k"], config["thinking"]))

    answered, questions = totals["answered"], totals["questions"]
    degraded = ", ".join("{} {}".format(count, name)
                         for name, count in totals["degraded"].items())
    print("{:<16}{}/{} generated{}".format(
        "answers", answered, questions, " ({})".format(degraded) if degraded else ""))
    if not answered:
        return

    print("{:<16}{} across {} answers, {} fabricated, {} non-source brackets".format(
        "citations", totals["citations"], answered, totals["fabricated"], totals["malformed"]))
    print("{:<16}{}/{} retrieved sources were cited at all".format(
        "coverage", totals["sources_cited"], totals["sources_shown"]))

    # The headline. Both columns always, for the same reason the diversity simulation prints
    # two: the gap between them is the whole diagnosis. The caveat is printed rather than
    # left in the docstring because this is the number that will get quoted out of context.
    print("{:<16}{}/{} in the sources the answer cited, {}/{} anywhere in the context".format(
        "anchors", totals["anchors_in_cited"], totals["anchors"],
        totals["anchors_in_context"], totals["anchors"]))
    print("{:<16}a lower bound — an answer can be sound and cite a different document that "
          "recites the same rule".format(""))
    print("{:<16}{}/{} expected documents that retrieval returned were cited".format(
        "documents", totals["expected_cited"], totals["expected_retrieved"]))
    # Denominator is *distinct sources cited*, not label mentions: off_target_cites counts
    # sources and `citations` counts every `[S2]` typed, so pairing them read as a far lower
    # rate than the truth — 11 of 71 rather than 11 of 42.
    print("{:<16}{} of {} cited sources are neither expected nor acceptable".format(
        "off-target", totals["off_target_cites"], totals["sources_cited"]))
    print("{:<16}{} used the verbatim refusal{}".format(
        "refusals", totals["refused_verbatim"],
        "; hand-count the ones that declined in other words" if answered else ""))
    if totals["truncated"]:
        print("{:<16}{} answers hit maxOutputTokens — raise MAX_OUTPUT_TOKENS before "
              "trusting this run".format("truncated", totals["truncated"]))

    print("{:<16}{:,} in / {:,} out / {:,} thinking, {:.0f} / {:.0f} / {:.0f} per answer".format(
        "tokens", totals["prompt_tokens"], totals["answer_tokens"], totals["thought_tokens"],
        totals["prompt_tokens"] / answered, totals["answer_tokens"] / answered,
        totals["thought_tokens"] / answered))
    print("{:<16}{:.1f}s per request, {:.0f}s of request time in total".format(
        "latency", totals["seconds"] / answered, totals["seconds"]))

    probes = [r for r in records if r["probe"]]
    if probes:
        print("\n{:<16}scored mechanically only; read these against `look_for` in "
              "probes.json".format("probes"))
        for record in probes:
            print("\n  {} — {}".format(record["id"], record["question"]))
            body = record["answer"] or "no answer ({})".format(record["degraded"])
            for line in body.split("\n"):
                for wrapped in textwrap.wrap(line, WRAP - 4) or [""]:
                    print("    {}".format(wrapped))

    print("\nOne answer is one sample. Temperature 0 is not determinism on a thinking model, "
          "so a one- or\ntwo-question difference between runs may be noise.")


def dry_run(collection, encoder, questions, args):
    """Retrieval and prompt assembly only, no API. Free, and it bounds what a run can score.

    `anchors in context` here is the ceiling on `anchors in cited sources` — running a
    configuration whose ceiling is unchanged spends quota to measure nothing. This is the
    cheap way to find that out, and it is how k=10 was ruled out before any key existed.
    """
    print("\nDry run — k={}, no requests\n".format(args.k))
    reachable = shown = prompt_chars = 0

    for question in questions:
        hits = search(collection, encoder, question["question"], k=args.k)
        sources = build_sources(collection, hits)
        prompt_chars += len(build_prompt(question["question"], sources))
        anchors = [a for entry in question["expected"] for a in entry["anchors"]]
        context = "\n".join(source["text"] for source in sources)
        found = sum(1 for a in anchors if a in context)
        reachable += found
        shown += len(anchors)
        print("     {:<26} anchors {}/{} reachable{}".format(
            question["id"], found, len(anchors),
            "   (probe)" if question["probe"] else ""))

    print("\n{:<16}{}/{} anchors are in the context at k={} — the ceiling on what an answer "
          "can cite".format("ceiling", reachable, shown, args.k))
    print("{:<16}{:,} chars of prompt over {} questions, roughly {:,} tokens each "
          "(approximate)".format("prompt", prompt_chars, len(questions),
                                 prompt_chars // len(questions) // 4))


def compare(paths):
    """Two runs side by side, per question and in total.

    Prints which config fields differ before any number, because a comparison across two
    changed variables is not a comparison and the totals will not say so on their own.
    """
    runs = []
    for path in paths:
        with open(path) as f:
            runs.append(json.load(f))
    left, right = runs

    differ = [field for field in sorted(set(left["config"]) | set(right["config"]))
              if left["config"].get(field) != right["config"].get(field)]
    print("\nCompare — {} vs {}".format(*[os.path.basename(p) for p in paths]))
    for field in differ:
        print("  {:<12} {} -> {}".format(field, left["config"].get(field),
                                         right["config"].get(field)))
    # Two runs of one config is the one comparison where nothing differing is the point: the
    # only variable is the sample, so whatever gap appears here is noise by construction, and
    # every other comparison has to clear it before it counts as a result.
    if differ == ["run"]:
        print("  same config, different sample — this gap is the noise floor")
    elif len(differ) != 1:
        print("  ! {} config fields differ; this measures more than one variable at once"
              .format(len(differ)) if differ else "  ! identical configs, including the run "
                                                  "number — is one of these a stale file?")

    by_id = {record["id"]: record for record in right["questions"]}
    print("\n{:<26} {:>13} {:>13} {:>11} {:>13}".format(
        "", "anchors cited", "off-target", "citations", "thought"))
    for record in left["questions"]:
        other = by_id.get(record["id"])
        if not other:
            continue
        print("{:<26} {:>6} -> {:<4} {:>6} -> {:<4} {:>4} -> {:<4} {:>6} -> {:<5}".format(
            record["id"],
            "{}/{}".format(record["anchors_in_cited"], record["anchors"]),
            "{}/{}".format(other["anchors_in_cited"], other["anchors"]),
            len(record["off_target_cites"]), len(other["off_target_cites"]),
            record["citations"], other["citations"],
            (record["usage"] or {}).get("thoughtsTokenCount") or 0,
            (other["usage"] or {}).get("thoughtsTokenCount") or 0))

    print()
    for field in ("anchors_in_cited", "anchors_in_context", "expected_cited", "fabricated",
                  "off_target_cites", "refused_verbatim", "thought_tokens"):
        print("{:<20} {:>8} -> {:<8}".format(field, left["totals"].get(field),
                                             right["totals"].get(field)))
    print("\nOne answer is one sample; a difference of one or two questions may be noise.")


def default_path(args):
    """Config in the filename, so two runs cannot quietly overwrite each other."""
    return os.path.join(RESULTS_DIR, "{}_k{}_{}_r{}.json".format(
        args.model, args.k, args.thinking, args.run))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("-k", type=int, default=TOP_K,
                        help="how many chunks to retrieve (default %d)" % TOP_K)
    parser.add_argument("--model", default=EVAL_MODEL,
                        help="the model to score (default %s)" % EVAL_MODEL)
    parser.add_argument("--thinking", choices=("minimal", "low", "medium", "high"),
                        default=THINKING_LEVEL,
                        help="reasoning effort (default %s)" % THINKING_LEVEL)
    parser.add_argument("--provider", default="gemini", choices=("gemini", "ollama"))
    parser.add_argument("--run", type=int, default=1,
                        help="repeat number for this config; two runs of one config give the "
                             "noise floor every other comparison is read against")
    parser.add_argument("--questions", help="comma-separated ids or subset names: {}".format(
        ", ".join(sorted(SUBSETS))))
    parser.add_argument("--no-probes", action="store_true",
                        help="skip the unscored probes in %s" % PROBES_PATH)
    parser.add_argument("--pace", type=float,
                        help="seconds between request starts (default: from the model's RPM)")
    parser.add_argument("--dry-run", action="store_true",
                        help="retrieve and assemble prompts, call no API")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="print two finished runs side by side and exit")
    parser.add_argument("--json", metavar="PATH", help="where to write results")
    args = parser.parse_args()

    if args.compare:
        compare(args.compare)
        return

    if args.pace is None:
        rpm = REQUESTS_PER_MINUTE.get(args.model, UNKNOWN_MODEL_RPM)
        args.pace = 0 if args.provider == "ollama" else 60.0 / rpm + PACE_SLACK

    questions = select(load_questions(), load_probes(),
                       args.questions.split(",") if args.questions else None,
                       not args.no_probes)

    collection = load_collection()
    encoder = SentenceTransformer(MODEL_NAME)

    if args.dry_run:
        dry_run(collection, encoder, questions, args)
        return

    config = {"model": args.model, "k": args.k, "thinking": args.thinking,
              "provider": args.provider, "questions": len(questions), "run": args.run}
    print("\n{} questions, {}, k={}, thinking {}, run {}, {:.0f}s between requests\n".format(
        len(questions), args.model, args.k, args.thinking, args.run, args.pace))

    records = run(collection, encoder, questions, args)
    totals = summarize(records)
    print_report(records, totals, config)

    # Written even when the breaker tripped: the answers that completed cost quota and are
    # still worth comparing, and a partial run is obvious from `answered` in the totals.
    path = args.json or default_path(args)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w") as f:
        json.dump({"config": config, "totals": totals, "questions": records}, f, indent=2)
    print("\nWrote {}".format(path))

    if totals["fabricated"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
