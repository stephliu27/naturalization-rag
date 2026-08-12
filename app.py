"""The demo: a question box over exactly the retrieval and generation the CLI already runs.

This file is a *view*. Every number it shows is read from `data/eval/`, and every answer it
shows comes from `answer_question()` in `scripts/generate.py` at the defaults that were
measured — so the app cannot drift from the thing that was scored. Nothing here re-implements
retrieval, chunking, citation checking or scoring.

Three decisions worth knowing about, because none of them is visible in the output:

  - **The encoder and the collection load once per container, not once per interaction.**
    Streamlit re-runs this whole script on every click, so an uncached `load_encoder()` would
    pay the model load on every keystroke-adjacent rerun. `@st.cache_resource` is the hook for
    exactly this — objects too expensive to rebuild and not safe to copy.
  - **The per-session cap gates generation only.** Retrieval is free, local and deterministic,
    so a visitor who exhausts the cap keeps getting cited passages rather than an error page.
  - **Text is escaped before it is rendered.** Streamlit's markdown treats `$…$` as LaTeX, and
    this corpus is full of dollar figures.

Run:  venv/bin/streamlit run app.py
"""

import glob
import json
import os
import re
import sys

import chromadb
import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
# `query.py` resolves `data/chroma` against the working directory, and a Streamlit process can
# be started from anywhere. One chdir keeps the app and the CLI reading the same index.
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from encoder import load_encoder  # noqa: E402  (after the path fix, by necessity)
from generate import (  # noqa: E402
    GEMINI_MODEL, THINKING_LEVEL, answer_question, build_sources)
from query import COLLECTION, INDEX_ARCHIVE, INDEX_DIR, TOP_K, ensure_index, search  # noqa: E402

# One visitor must not be able to drain the day. The free tier is ~500 requests a day for
# Flash-Lite, so ten is generous for a portfolio demo and still leaves room for fifty visitors.
# Reaching it degrades to retrieval, which costs nothing and needs no key.
SESSION_CAP = 10

RESULTS_PATH = "data/eval/results.json"
GENERATION_DIR = "data/eval/generation"

PRIVACY = ("Your question is sent to a third-party model (Google Gemini) to write the answer. "
           "**Do not enter personal information** — names, case numbers, A-numbers, or facts "
           "about your own immigration situation.")

CURRENCY = ("Sources are dated, and some state rules that were later changed or struck down. "
            "The model is instructed to give the date whenever a source supports a figure only "
            "as of some past time, and to decline rather than answer from memory.")

DISCLAIMER = ("Legal information, not legal advice. This surfaces and cites public policy text; "
              "it does not interpret anyone's circumstances.")

EXAMPLE = "can I get a fee waiver for naturalization?"

# The placeholder faded further than Streamlit's default, so it reads as a sample rather than
# as text somebody left in the box. Two deliberate choices in three lines:
#
#   - `input::placeholder` is standard web-platform CSS, not one of Streamlit's generated class
#     names, so a version upgrade cannot break it — and if it somehow did, the placeholder
#     falls back to the default grey rather than disappearing.
#   - `color: inherit` with an opacity, rather than a grey hex, because a fixed grey that reads
#     as faint on the light theme is nearly invisible on the dark one. Inheriting the theme's
#     own text color and fading it works on both. `opacity: 1` is not the default in Firefox,
#     which is why it is set explicitly before being overridden.
PLACEHOLDER_CSS = """<style>
input::placeholder { color: inherit; opacity: 0.4; }
</style>"""


# --- the expensive objects, loaded once per container ------------------------------------


@st.cache_resource
def load_index():
    """The Chroma collection. `load_collection()` is not reused because it calls `sys.exit`.

    That is right for a CLI and wrong here: `SystemExit` inside a cached function surfaces as
    a stack trace, so the failure is reported as page content instead. `ensure_index()` is the
    half worth sharing — it unpacks the shipped archive, which is what makes the first query on
    a fresh host work at all — and it neither exits nor raises, precisely so both callers can
    fail in their own way.
    """
    if not ensure_index():
        return None
    try:
        return chromadb.PersistentClient(path=INDEX_DIR).get_collection(COLLECTION)
    except Exception:
        return None


@st.cache_resource
def load_model():
    """MiniLM under onnxruntime. ~80 MB downloaded on first use, then cached on the host."""
    return load_encoder()


@st.cache_data
def corpus_numbers(_collection):
    """Documents and chunks in the index. One metadata scan, which is milliseconds.

    The leading underscore tells Streamlit not to hash the argument — a Chroma collection is
    not hashable, and the cache key does not need it since there is only ever one index.
    """
    metadatas = _collection.get(include=["metadatas"])["metadatas"]
    return {
        "chunks": len(metadatas),
        "documents": len({m["source_id"] for m in metadatas}),
        "uscis": len({m["source_id"] for m in metadatas if m["source_type"] == "uscis"}),
        "caselaw": len({m["source_id"] for m in metadatas if m["source_type"] == "caselaw"}),
    }


# --- the eval numbers, read from the committed results rather than typed in ----------------


@st.cache_data
def retrieval_numbers(path=RESULTS_PATH):
    """Headline retrieval scores, re-aggregated from the per-question results.

    Aggregated here rather than pasted in: a number typed into a UI drifts from the file it
    came from the first time the eval is re-run, and these are four sums.
    """
    with open(path) as f:
        results = json.load(f)

    # JSON object keys are strings, so the integer cutoffs `eval_retrieval.py` wrote come back
    # as "8". Indexing this with `TOP_K` itself raises KeyError.
    k = str(TOP_K)
    total = len(results)
    return {
        "questions": total,
        "recall": sum(r["recall"][k] for r in results) / total,
        "found_any": sum(1 for r in results if r["hit"]),
        "found_all": sum(1 for r in results if r["recall"][k] == 1),
        "anchors": sum(r["anchors"] for r in results),
        "anchors_in_window": sum(r["anchors_in_window"] for r in results),
    }


@st.cache_data
def generation_numbers(directory=GENERATION_DIR):
    """Two things from the recorded grid: fabrication over every answer in it, and the one
    cell whose configuration matches what this app ships.

    The cell is selected by `config`, not by filename, so changing `TOP_K` or
    `THINKING_LEVEL` moves the reported numbers instead of quietly reporting the old ones —
    and reports nothing at all if no cell was ever scored at the new defaults, which is the
    honest outcome. The first matching run is enough: two runs of one configuration came back
    byte-identical on 64 of 64 answers, so there is no spread to average over.
    """
    answers = citations = fabricated = malformed = 0
    shipping = None

    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path) as f:
            grid = json.load(f)

        # Every answer in the grid, probes included — the fabrication count is the claim that
        # covers the whole measurement, so it must not quietly exclude a question.
        for question in grid["questions"]:
            answers += 1 if question.get("answer") else 0
            citations += question.get("citations") or 0
            fabricated += len(question.get("unknown") or [])
            malformed += len(question.get("malformed") or [])

        config = grid["config"]
        if shipping is None and (config["k"], config["thinking"]) == (TOP_K, THINKING_LEVEL):
            shipping = {"config": config, "totals": grid["totals"]}

    return {"answers": answers, "citations": citations, "fabricated": fabricated,
            "malformed": malformed, "shipping": shipping}


# --- provider key -------------------------------------------------------------------------


def read_dotenv(path=".env"):
    """`GEMINI_API_KEY` out of `.env`, which the CLI expects you to source by hand.

    An app has no shell to source it in, and forgetting would silently drop the demo to
    retrieval-only. Six lines rather than a `python-dotenv` pin; `.env` is gitignored, so this
    path only ever fires locally.
    """
    if not os.path.isfile(path):
        return False
    with open(path) as f:
        for line in f:
            if line.strip().startswith("GEMINI_API_KEY="):
                value = line.strip().split("=", 1)[1].strip().strip("\"'")
                if value:
                    os.environ["GEMINI_API_KEY"] = value
                    return True
    return False


def key_available():
    """Whether generation can run, having put the key where `generate.py` looks for it.

    Three homes for one value: the environment for the CLI, `st.secrets` on Streamlit Cloud,
    `.env` locally. Copying into `os.environ` means `generate.py` stays unaware of Streamlit.
    Checked *before* generating, because `api_key()` exits the process rather than raising and
    `generate()` only degrades on `FetchError`/`ParseError`.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return True
    try:
        # Accessing st.secrets with no secrets file at all raises rather than returning empty.
        key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        key = None
    if key:
        os.environ["GEMINI_API_KEY"] = key
        return True
    return read_dotenv()


# --- rendering ----------------------------------------------------------------------------


def plain(text):
    """Corpus text made safe for `st.markdown`, which is where two silent failures live.

    A single `$` is harmless; two in one block make Streamlit render everything between them
    as LaTeX, and a fee-waiver answer quoting `$1,170` and `$640` loses both figures and the
    sentence holding them. The processed corpus also keeps `##` section markers, so a passage
    beginning mid-section would render its first line as a page heading.
    """
    escaped = text.replace("$", r"\$")
    return re.sub(r"^(#+)", r"\\\1", escaped, flags=re.MULTILINE)


# Only a bracket whose whole content is an S-label matches, which is what keeps this off
# `detail[ed]` and `"[s]ole procedure"` — the legal convention for altering a letter inside a
# quotation, which the model writes because the corpus does.
LABEL_IN_TEXT = re.compile(r"\[(S\d+)\]")


def link_labels(text, sources):
    """Every `[S1]` in the answer turned into a link to the source it names.

    The label stays as the visible marker rather than being replaced by the citation, and the
    reason is that **a citation cannot identify a passage**: reporter page numbers were dropped
    on purpose, so `citation` is document-level, and five of the eight sources on a fee-waiver
    question all render as *Northwest Immigrant Rights Project v. USCIS (D.D.C. 2020)*.
    Swapping the label for that string would make five distinct passages indistinguishable and
    print the same name twice in a sentence citing two of them. The label is the only
    chunk-level handle the answer has, and it is what the citation check is arithmetic over.

    So the citation goes in the link title, where a browser shows it on hover, and the href is
    the primary source — the actual USCIS chapter or CourtListener opinion. The passage list
    below prints every citation in full beside its label, so the mapping is legible without
    hovering, which matters in a screenshot.
    """
    by_label = {source["label"]: source for source in sources}

    def replace(match):
        source = by_label.get(match.group(1))
        # An unknown label is a fabricated citation, already reported as an error below; it
        # stays plain text rather than linking somewhere. `url` is "" rather than None on any
        # chunk that lacks one, because Chroma rejects None.
        if source is None or not source["url"]:
            return match.group(0)
        # The label is bracketed *inside* the link text — `[[S1]](url)` rather than
        # `[S1](url)` — because the plain form spends the brackets on the link syntax and
        # renders as a bare `S1`, which reads as a stray token mid-sentence and no longer
        # matches either the CLI output or the labels on the passages below.
        #
        # Angle brackets around the href so a URL containing a parenthesis cannot close the
        # link early, and the citation's own quotes swapped out so they cannot close the title.
        return "[[{}]](<{}> \"{}\")".format(match.group(1), source["url"],
                                            source["citation"].replace('"', "'"))

    # A function replacement rather than a template, because a citation or URL containing a
    # backslash would otherwise be read as a group reference by `re.sub`.
    return LABEL_IN_TEXT.sub(replace, text)


def render_answer(result, sources):
    st.markdown(link_labels(plain(result["answer"]), sources))

    # The mechanical checks, surfaced rather than logged. A fabricated label is the failure
    # this project claims not to have, so it gets the loudest treatment available.
    if result["unknown"]:
        st.error("Fabricated citation: {} — the answer cited a source that was never "
                 "supplied.".format(", ".join(result["unknown"])))
    if result["malformed"]:
        st.warning("Cited something that is not one of the retrieved sources: {}".format(
            ", ".join(repr(m) for m in result["malformed"])))
    if result["truncated"]:
        st.warning("The answer hit the output ceiling, so its last citation may be cut.")

    usage = result["usage"]
    st.caption("{} of {} passages cited · {} citations · {} tokens in, {} out{}".format(
        len(result["cited"]), len(result["sources"]), result["citations"],
        usage.get("promptTokenCount", "?"), usage.get("candidatesTokenCount", "?"),
        ", {} thinking".format(usage["thoughtsTokenCount"])
        if usage.get("thoughtsTokenCount") else ""))


def render_sources(sources, cited=()):
    """The passages, each under its citation. Cited ones open by default.

    This is the part that stays useful when generation is unavailable, so it is not a
    footnote to the answer — it is the same passages the CLI prints, widened to their
    neighbors, with the overlap between adjacent chunks already stripped upstream.
    """
    for source in sources:
        was_cited = source["label"] in cited
        header = "{}  {}  ·  score {:.3f}{}".format(
            source["label"], source["citation"], source["score"],
            "  ·  cited" if was_cited else "")
        # Bold as well as open, because `expanded` is only visible while nobody has touched
        # the passages: one click collapses a cited source and there is then nothing left
        # distinguishing it from the four the answer ignored. An expander label takes markdown.
        if was_cited:
            header = "**{}**".format(header)
        with st.expander(header, expanded=was_cited):
            facts = [f for f in (source["section"], source["source_type"]) if f]
            if facts:
                st.caption(" · ".join(facts))
            st.markdown(plain(source["text"]))
            if source["url"]:
                st.caption(source["url"])


def render_sidebar(corpus, retrieval, generation):
    """What was measured, straight off disk. The reason to trust the answer above."""
    st.sidebar.header("How it is measured")

    st.sidebar.markdown(
        "**Corpus** — {documents} documents, {chunks:,} chunks "
        "({uscis} Policy Manual chapters, {caselaw} court opinions)".format(**corpus))

    st.sidebar.markdown(
        "**Retrieval** — {questions} hand-written questions, each naming the documents a "
        "correct answer needs and verbatim sentences from them.".format(**retrieval))
    st.sidebar.markdown(
        "- recall@{k}: **{recall:.0%}**\n"
        "- found at least one expected document: **{found_any}/{questions}**\n"
        "- found every expected document: **{found_all}/{questions}**\n"
        "- expected paragraphs returned: **{anchors_in_window}/{anchors}**".format(
            k=TOP_K, **retrieval))

    st.sidebar.markdown("**Generation** — {answers} answers over a 4-cell grid of retrieval "
                        "depth and reasoning effort.".format(**generation))
    lines = ["- fabricated citations: **{fabricated}** of {citations} citations".format(
        **generation)]
    if generation["shipping"]:
        totals = generation["shipping"]["totals"]
        lines += [
            "- expected paragraphs inside a *cited* source: **{}/{}**".format(
                totals["anchors_in_cited"], totals["anchors_in_context"]),
            "- expected documents cited, of those retrieved: **{}/{}**".format(
                totals["expected_cited"], totals["expected_retrieved"]),
        ]
    else:
        # Silence here would read as "not measured" when the truth is "measured at other
        # settings," which is a different and more useful thing to say.
        lines.append("- no cell in the grid was scored at k={} / thinking {}".format(
            TOP_K, THINKING_LEVEL))
    st.sidebar.markdown("\n".join(lines))

    st.sidebar.caption(
        "Defaults are the measured winner of that grid, not inherited: `{}`, k={}, thinking "
        "{}. Retrieval runs locally and needs no key.".format(
            GEMINI_MODEL, TOP_K, THINKING_LEVEL))

    st.sidebar.divider()
    st.sidebar.caption(DISCLAIMER)


# --- the page -----------------------------------------------------------------------------


def retrieve_only(collection, encoder, question):
    """Retrieval without generation — the no-key and past-the-cap path.

    Calls the same two functions `answer_question` does, so the passages are identical to the
    ones a generated answer would have been built from.
    """
    return build_sources(collection, search(collection, encoder, question, k=TOP_K))


def main():
    st.set_page_config(page_title="Naturalization Barrier Navigator", page_icon="⚖️")
    # The only HTML this app injects, and it is a literal constant — no user input reaches it,
    # so `unsafe_allow_html` is carrying a static stylesheet rather than opening a hole.
    st.markdown(PLACEHOLDER_CSS, unsafe_allow_html=True)

    collection = load_index()
    if collection is None:
        st.title("Naturalization Barrier Navigator")
        st.error("No search index at `{}/` and no archive at `{}`. Build one with "
                 "`python scripts/build_index.py` — `data/processed/` is committed, so "
                 "nothing needs re-scraping.".format(INDEX_DIR, INDEX_ARCHIVE))
        st.stop()

    corpus = corpus_numbers(collection)
    render_sidebar(corpus, retrieval_numbers(), generation_numbers())

    st.title("Naturalization Barrier Navigator")
    st.markdown(
        "Ask about the United States naturalization process. The answer is written only from "
        "the {documents} documents below it — {uscis} USCIS Policy Manual chapters and "
        "{caselaw} federal court opinions — and every claim carries a citation to the passage "
        "that supports it.".format(**corpus))
    st.info(PRIVACY, icon="🔒")

    st.session_state.setdefault("requests", 0)
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("sources", None)

    can_generate = key_available()
    remaining = SESSION_CAP - st.session_state["requests"]

    # A form so Enter submits and so a rerun mid-typing cannot fire a request.
    #
    # The example is a `placeholder`, not a `value`: grey hint text that disappears on the
    # first keystroke, prefixed `e.g.` so it reads as a sample. Prefilled, it would read as
    # leftover state from whoever used the app last — or worse, as a canned question the demo
    # only works for.
    with st.form("ask"):
        question = st.text_input("Your question", placeholder="e.g. {}".format(EXAMPLE))
        submitted = st.form_submit_button("Ask", type="primary")

    # Submitting an empty box would otherwise do nothing at all, silently, which reads as a
    # broken button rather than as a missing question.
    if submitted and not question.strip():
        st.caption("Type a question first — for example, *{}*".format(EXAMPLE))

    if submitted and question.strip():
        if can_generate and remaining > 0:
            # Counted before the call, not after: a request that fails has still been spent,
            # and the cap exists to protect the quota rather than to count successes.
            st.session_state["requests"] += 1
            with st.spinner("Retrieving passages and writing an answer…"):
                result, sources = answer_question(collection, load_model(), question.strip())
        else:
            with st.spinner("Retrieving passages…"):
                result, sources = None, retrieve_only(collection, load_model(),
                                                      question.strip())
        st.session_state.update(result=result, sources=sources)

    # Rendered from session state rather than from the branch above, because opening a passage
    # re-runs this script — and an answer built in the previous run would otherwise vanish.
    sources = st.session_state["sources"]
    result = st.session_state["result"]
    if sources is None:
        return

    st.divider()

    if result is None:
        # Retrieval-only, and the two reasons for it are different problems: one is the host's
        # configuration, the other is the visitor's own budget.
        if not can_generate:
            st.warning("No `GEMINI_API_KEY` configured, so no answer was generated. "
                       "The retrieved passages are below.")
        else:
            st.warning("Session limit of {} generated answers reached, so one visitor cannot "
                       "spend the day's quota. Retrieval still works — the passages are "
                       "below.".format(SESSION_CAP))
    elif result["answer"] is None:
        # A provider failure is a normal Tuesday on a free tier. `degraded_category` is the
        # machine-readable half; the prose half goes in the caption so the cause is visible.
        if result["degraded_category"] == "rate_limited":
            st.warning("Generation quota reached — showing the retrieved sources directly.")
        else:
            st.warning("No answer was generated, so the retrieved sources are shown "
                       "directly.")
        st.caption(result["degraded"])
    else:
        render_answer(result, sources)
        st.caption(CURRENCY)

    st.subheader("Retrieved passages")
    st.caption("Each passage is widened to the chunks either side of the match, so nothing is "
               "read as a fragment.")
    render_sources(sources, cited=result["cited"] if result and result["answer"] else ())

    if can_generate:
        st.caption("{} of {} generated answers used this session.".format(
            st.session_state["requests"], SESSION_CAP))


# Guarded, unlike most Streamlit examples, so that `import app` in a test does not run the whole
# page. Streamlit executes a script with `__name__ == "__main__"` — verified rather than assumed
# — so this costs nothing at run time and makes `plain()` and `link_labels()` testable.
if __name__ == "__main__":
    main()
