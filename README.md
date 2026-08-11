# Naturalization Barrier Navigator

A retrieval-augmented generation (RAG) tool that answers questions about U.S. naturalization eligibility and cites the policy text behind every answer.

**Status:** In active development. See [Roadmap](#roadmap).

---

## The Problem

The USCIS Policy Manual is public and authoritative and close to unusable if you aren't an adjudicator or an attorney. It runs thousands of pages of dense legal English, cross-referenced against federal case law that most applicants have no way to find. Someone trying to work out whether they qualify for a fee waiver or an English-testing exemption is looking for something that exists, written for somebody else. Finding it requires already knowing where to look, and that falls hardest on applicants whose first language isn't English.

## The Approach

Question answering over USCIS Policy Manual chapters and federal court opinions, with one hard constraint:

> Every answer cites the source passages it came from.

A general-purpose LLM answers immigration questions more fluently, but it will also paraphrase policy it can't point to, and a confident wrong answer about eligibility costs the person who acts on it. Generation is restricted to retrieved text, with the citations shown alongside.

Where the corpus doesn't cover a question, the tool says so instead of answering from the nearest available text.

### Scope

v1 covers three decision points:

- **Eligibility** — who qualifies, and under what conditions
- **Fee waivers** — income thresholds and documentation requirements
- **Testing exemptions** — English and civics waivers (age, disability, residency)

Asylum is out of scope for v1 and may be added later.

---

## Architecture

```
USCIS Policy Manual ──┐
                      ├──► ingestion ──► cleaned corpus ──► chunk + embed ──► ChromaDB
CourtListener API ────┘                  (+ metadata)                            │
                                                                                 ▼
                       answer + citations ◄── Gemini ◄── retrieved context ◄── query
```

**Ingestion** scrapes Policy Manual chapters and pulls federal opinions into plain text, each with a metadata sidecar on the same ten-field schema for both halves — id, type, title, citation, court, date, barrier, URL, retrieval date, and the file it was extracted from.

**Processing** is where the corpus is actually made. The three sources arrive in three unrelated markups — USCIS HTML, Harvard CAP XML, and PDF text with no markup at all — and are collapsed into one line-oriented format so the indexer never branches on where a document came from. Substantive footnotes are kept and moved beneath the paragraph that cites them; citation-only ones are dropped.

**Indexing** chunks the corpus, embeds it locally with `all-MiniLM-L6-v2`, and stores the vectors in ChromaDB with the full metadata sidecar on every chunk. Chunk sizing is set by the model rather than by convention: MiniLM reads at most 256 tokens and silently ignores the rest, so anything longer would be stored and displayed intact while half of it stayed unmatchable. Chunks therefore target 220 tokens against a hard 256 ceiling, with 40 tokens of overlap, and a chunk closes early at a section heading so it covers one topic rather than two. **105 documents become 3,210 chunks** — median 196 tokens, none over the ceiling, about four and a half minutes to embed on a laptop CPU.

**Retrieval** embeds the question with the same model, takes the top-k chunks, and widens each one to its immediate neighbors before showing it — small passages match a question better than whole chapters do, but a small passage is a bad thing to reason from. The neighbor window is a lookup rather than a second search: chunk IDs are `{source_id}_{chunk_index}`, so the surrounding text is fetched by ID and cannot come from another document.

**Citations** are derived, not stored. A Policy Manual URL already encodes its own citation, so `/volume-12-part-a-chapter-1` becomes `12 USCIS-PM A.1` by regex; opinions combine a court map and the date into `Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)`, falling back to the court and year alone for the five opinions too recent to have a reporter citation. Where a chunk contains a footnote, the citation names it — `12 USCIS-PM B.4, n.8`.

**Evaluation** scores retrieval against a hand-written question set before any model is involved. Fifteen questions, each keyed to the documents that should come back and to verbatim sentences those documents contain, so a run reports both whether the right chapter arrived and whether the right paragraph did. Keys are checked against the corpus before they are scored — an answer key naming a document that does not exist scores zero and is indistinguishable from a retrieval failure.

**Generation** is the next layer and is not built yet: the retrieved passages become the only context a model is allowed to answer from, with the citations carried through to the answer. Retrieval is deliberately usable and measurable without it — whether the right source came back is a question no model needs to answer.

### Corpus

**105 documents, 2,042,021 characters.** 79 Policy Manual chapters — all of Volume 12 (Citizenship and Naturalization) plus Volume 1 Parts B and E — and 26 federal opinions.

The case law is a deliberate selection rather than a scrape. Five searches, one per barrier type, with the top results read by hand; `data/caselaw_opinion_ids.json` records every opinion's ID alongside the reason it was included, and `fetch_caselaw.py` refuses to fetch a record without one. Committing IDs rather than text keeps the corpus reproducible — CourtListener's ranking shifts over time, so re-running the searches would not.

The full selection method, the five queries, the rejected cases and the reasoning behind each call are written up in [docs/corpus-selection.md](docs/corpus-selection.md).

---

## Roadmap

| Stage | Status |
|---|---|
| USCIS Policy Manual scraper (`scripts/scrape_uscis.py`) | Shipped |
| Volume-agnostic scraping; Vol. 1 added alongside Vol. 12 | Shipped |
| Case-law ingestion via CourtListener API (`scripts/fetch_caselaw.py`) | Shipped |
| Text extraction and cleaning into `data/processed/` | Shipped |
| Chunking and embedding layer (`scripts/build_index.py`, ChromaDB) | Shipped |
| Retrieval with citations (`scripts/query.py`, `scripts/citations.py`) | Shipped |
| Retrieval evaluation against a hand-built question set (`scripts/eval_retrieval.py`) | Shipped |
| Answer generation constrained to retrieved passages | Next |
| Barrier-type tagging (financial, linguistic, procedural, timeline) | Planned |
| Streamlit interface | Planned |

### Barrier tagging

A planned layer tags each passage by the kind of barrier it describes: financial, linguistic, procedural, or timeline. Knowing you're blocked by a documentation requirement rather than an income threshold changes what you do next.

---

## Stack

Python · BeautifulSoup · lxml · CourtListener REST API · sentence-transformers · ChromaDB · Gemini · Streamlit

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
COURTLISTENER_API_TOKEN=your_token_here
```

A CourtListener token is free at https://www.courtlistener.com/profile/api-token/. Generation runs on Gemini's free tier; a local Ollama model is supported as a no-key fallback, so the repo is runnable without signing up for anything.

Scraping and indexing need no key at all — embeddings are computed locally.

`.env` is gitignored and should never be committed.

### Building the index

`data/processed/` is committed, so the index builds without re-scraping anything:

```bash
venv/bin/python scripts/build_index.py            # ~4.5 min, writes data/chroma/
venv/bin/python scripts/build_index.py --dry-run  # chunk and report only, ~13 sec
```

The model downloads itself on first use (~90 MB). `--dry-run` skips the embedding pass and prints the chunk-size distribution, the per-source split, and the documents contributing the most chunks — it exists so chunk sizing can be tuned in seconds rather than minutes. Almost all of its runtime is startup — importing sentence-transformers is about 5 seconds and building the tokenizer another 2.5 — while the chunking itself is about 2.3.

### Querying

```bash
venv/bin/python scripts/query.py "can I get a fee waiver for naturalization?"
venv/bin/python scripts/query.py --type uscis --barrier financial -k 10
venv/bin/python scripts/query.py            # no question: prompt in a loop
```

Retrieval only — no model is called to write an answer, and none is needed to tell whether the right source came back. Each hit prints its citation (`12 USCIS-PM B.4, n.8`, `Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)`), its position in the document, its section heading, and the passage itself widened to the chunks either side so nothing is read as a fragment. `--type` and `--barrier` restrict the search; omitting the question opens a prompt loop, which loads the model once instead of once per question.

Several of the top five often come from the same document, because chunk counts per document run from 1 to 248 and the ranking is over chunks rather than documents. That reads like a defect and mostly is not: the document taking the most slots is usually the one that answers the question, and its adjacent chunks are how the neighbor window ends up covering a continuous stretch of the relevant section. No cap is applied — see below for what capping was measured to cost.

---

## Evaluation

```bash
venv/bin/python scripts/eval_retrieval.py             # score the question set
venv/bin/python scripts/eval_retrieval.py --validate  # check the question set only, ~1 sec
venv/bin/python scripts/eval_retrieval.py -k 10 --json results.json
```

`data/eval/questions.json` holds fifteen questions written in an applicant's voice. Each names the documents a correct answer needs, verbatim sentences from those documents, and the documents that are acceptable to return without being required. Keys are text and document IDs, never chunk IDs — chunk sizing is still tunable and every chunk ID would shift beneath a stored key, leaving an eval that keeps reporting while measuring nothing.

Two things get scored, because they fail separately. **Recall** asks whether the right document ranked in the top k. **Anchors** ask whether the specific paragraph came back, checked as exact text, and are scored twice: once against the matched chunks and once against the neighbor window a reader actually sees.

At k=5 over 15 questions and 35 anchors:

| | |
|---|---|
| recall@1 / @3 / @5 / @10 | 43% / 50% / 57% / 70% |
| questions finding every expected document | 7 / 15 |
| anchors in a matched chunk | 10 / 35 |
| anchors once widened to the neighbor window | 14 / 35 |

Three results worth stating plainly, because they set what is worth building next:

**Case law is the weak half.** Policy Manual chapters are found 8 times in 12; court opinions 2 times in 10. It is not that opinions are returned rarely — they take 59% of the returned slots against a 47% share of the index. The wrong opinions come back. The 26 opinions all concern naturalization procedure and cite the same statutes, so they sit close together and close to any procedural question.

**Most of that is a cutoff, not a ranking failure.** At k=10, case law goes from 2 of 10 to 6 of 10 while the Policy Manual half does not move at all — the right opinion is usually just under the line. The default stays at k=5 because a larger k also feeds a generator more passages that are, by construction, worse matches, and whether that helps or hurts an answer is not something a retrieval metric can decide.

**Capping how many slots one document may hold was measured and rejected.** Limiting any document to a single slot raises recall from 57% to 67% and drops expected passages from 14 of 35 to 11 — more chapters, fewer paragraphs, which is the wrong trade for a tool whose output is passages someone reads. Limits of two or three change recall by three points and passages by two, which is inside the noise of a fifteen-question set.

---

## Disclaimer

This tool provides **legal information, not legal advice.** It surfaces and cites public policy text; it does not interpret anyone's individual circumstances. Immigration decisions should involve a qualified attorney or an accredited representative.

## Motivation

This project grew out of research on naturalization-rate disparities across demographic groups and coursework in asylum law. Both kept surfacing the same pattern: what stops people is often access to the law rather than the law itself.
