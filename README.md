# Naturalization Barrier Navigator

[![tests](https://github.com/stephliu27/naturalization-rag/actions/workflows/tests.yml/badge.svg)](https://github.com/stephliu27/naturalization-rag/actions/workflows/tests.yml)

A retrieval-augmented generation (RAG) tool that answers questions about U.S. naturalization eligibility and cites the policy text behind every answer.

**[Try it → naturalization-rag.vercel.app](https://naturalization-rag.vercel.app)**

Ask a question, read the answer, and open the passages it was written from. Every claim carries a link to the Policy Manual chapter or court opinion behind it, and [a second page](https://naturalization-rag.vercel.app/evaluation) reports what the retrieval and generation actually score.

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

**Current dollar amounts are out of scope, because the source puts them elsewhere.** The Policy Manual states who qualifies for a waiver and what documents establish it, but routes the amounts themselves to a separate fee schedule (Form G-1055) and to regulation at 8 CFR 106, so fees can change without policy being rewritten. Court opinions in the corpus do quote fee tables, but as of the date they were decided — one recites a fee schedule the same opinion went on to block from taking effect.

So a fee question returns dated figures with the date attached and a caveat that they may not be current, which is accurate but weaker than it should be: the chapters that name the fee schedule as the authority rank just outside the default depth, so the answer does not yet point there. It is the clearest case for a deeper default that the evaluation has produced.

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

**Indexing** chunks the corpus, embeds it locally with `all-MiniLM-L6-v2`, and stores the vectors in ChromaDB with the full metadata sidecar on every chunk. Chunk sizing is set by the model rather than by convention: MiniLM reads at most 256 tokens and silently ignores the rest, so anything longer would be stored and displayed intact while half of it stayed unmatchable. Chunks therefore target 220 tokens against a hard 256 ceiling, with 40 tokens of overlap, and a chunk closes early at a section heading so it covers one topic rather than two. **105 documents become 3,210 chunks** — median 196 tokens, none over the ceiling, a few minutes to embed on a laptop CPU. The model runs on `onnxruntime` rather than PyTorch: the weights are identical, so every number below is unchanged, but the install drops from 863 MB to 310 MB — which is mostly cold-start time on a host that has to rebuild its environment when it wakes.

**Retrieval** embeds the question with the same model, takes the top-k chunks, and widens each one to its immediate neighbors before showing it — small passages match a question better than whole chapters do, but a small passage is a bad thing to reason from. The neighbor window is a lookup rather than a second search: chunk IDs are `{source_id}_{chunk_index}`, so the surrounding text is fetched by ID and cannot come from another document.

**Citations** are derived, not stored. A Policy Manual URL already encodes its own citation, so `/volume-12-part-a-chapter-1` becomes `12 USCIS-PM A.1` by regex; opinions combine a court map and the date into `Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)`, falling back to the court and year alone for the five opinions too recent to have a reporter citation. Where a chunk contains a footnote, the citation names it — `12 USCIS-PM B.4, n.8`.

**Evaluation** scores retrieval against a hand-written question set before any model is involved. Fifteen questions, each keyed to the documents that should come back and to verbatim sentences those documents contain, so a run reports both whether the right chapter arrived and whether the right paragraph did. Keys are checked against the corpus before they are scored — an answer key naming a document that does not exist scores zero and is indistinguishable from a retrieval failure.

**Generation** hands the retrieved passages to a model as the only context it may answer from, labeled `[S1]`, `[S2]` and so on, and then checks the citations by arithmetic. The label-to-chunk map never leaves the caller, so a citation naming a source that was never supplied is caught by counting rather than by reading, and a bracketed group holding no label at all — a statute cited as though it were one of the sources — is caught the same way. Refusal is a fixed sentence rather than a phrasing the model chooses, which makes "did it decline" a boolean. Retrieval stays usable and measurable without any of this: whether the right source came back is a question no model needs to answer.

Generation is a single HTTP request rather than a provider SDK. The current SDK requires a newer Python than the pinned environment, and talking to the API directly also reaches request fields the SDK does not expose — the reasoning-effort control used here is documented for a different endpoint and works anyway. Retries, backoff and error categories are shared with the scrapers, so a rate-limit response means the same thing in both.

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
| Answer generation constrained to retrieved passages, with mechanical citation checking (`scripts/generate.py`) | Shipped |
| Evaluation of generated answers, including retrieval depth (`scripts/eval_generation.py`) | Shipped |
| Deployed interface over the same functions the CLI calls (`app.py`) | Shipped |
| Unit tests over the pure functions, run in CI (`tests/`, GitHub Actions) | Shipped |
| Custom web interface over the retrieval service as a containerized API (`web/`, `api/`) | Shipped |
| Scored comparison against a no-retrieval baseline on the same question set | Planned |
| Barrier-type tagging extended to the Policy Manual half | Planned |

### Barrier tagging

Each passage carries a tag for the kind of barrier it describes: `procedural`, `character`, `delay`, `linguistic`, or `financial`. Knowing you're blocked by a documentation requirement rather than an income threshold changes what you do next, and `--barrier` restricts a search to one kind.

The tags cover the case-law half — 1,511 chunks — where they come from the hand-verified holding recorded for each opinion in `data/caselaw_opinion_ids.json`. The 1,699 Policy Manual chunks are untagged.

That asymmetry is why `--barrier` is a command-line filter and not a control in the interface. Restricting to one barrier type currently removes the entire Policy Manual half, so asking about fee waivers under `financial` returns litigation over the fee rule and drops the chapter that states the eligibility grounds. A filter that silently makes answers worse is worse than no filter. Extending the tags is a term-frequency pass over a small vocabulary, checked against the hand labels — the cheap method measured before an expensive one — and the control belongs in the interface only once both halves are covered.

---

## Stack

Python · BeautifulSoup · lxml · CourtListener REST API · onnxruntime · ChromaDB · Gemini · FastAPI · Streamlit

TypeScript · Next.js · Tailwind CSS · Docker · GitHub Actions · Azure Container Apps · Vercel

## Setup

**Python 3.9 through 3.12.** Every version in `requirements.txt` is pinned, and the newest of those pins with a prebuilt wheel tops out at 3.12 — on 3.13 or later, `pip` falls back to compiling NumPy and ONNX Runtime from source, which is slow and fails outright on a memory-limited host. The pins are exact rather than ranges because ONNX Runtime is what turns text into vectors: the committed index was embedded by a specific version of it, and a query embedded by a different one is being compared against vectors it did not produce. The deployed app runs 3.12 for the same reason.

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

The pipeline and the command line need nothing else. The HTTP service and the site add their own, kept in separate files so a deploy of one does not install the other's dependencies:

```bash
pip install -r api/requirements.txt   # FastAPI, on top of the pins above
cd web && npm install                 # Next.js and Tailwind
```

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
COURTLISTENER_API_TOKEN=your_token_here
```

A CourtListener token is free at https://www.courtlistener.com/profile/api-token/, and a Gemini key is free at https://aistudio.google.com/apikey with no card. Generation runs on Gemini's free tier, whose daily request limit is visible only in AI Studio rather than in the published documentation; a local Ollama model is supported as a no-key fallback, so the repo is runnable without signing up for anything.

Scraping and indexing need no key at all — embeddings are computed locally.

`.env` is gitignored and should never be committed.

### Tests

```bash
pip install -r requirements-dev.txt
venv/bin/python -m pytest tests/ -q
```

62 tests over the pure functions — the text helpers, the retry math, the footnote keep/drop rule, the alert verdicts, table-of-contents label parsing, and every citation shape the tool prints. No network, no filesystem, no model, so the suite runs in well under a second.

They exist to pin down decisions rather than to raise a coverage number, so the cases are the ones from the real audits: `See 8 CFR 318.1.` scores zero content words while `Marriage must have existed at the time of birth.` clears the floor; a 95-character note that is nothing but pointers is still not substantive, which is why the rule measures residual prose instead of length. The two `clean_text` separator tests are the two opposite failures it has to straddle — `" "` unwelds USCIS inline links, and `""` avoids splitting a word Harvard CAP broke across an italic run. The citation tests hold the shapes a reader checks the tool against, including the three multi-word surnames — Morfa Diaz, De Dandrade, Dos Reis — that are the reason the case-name map is hand-written rather than derived by rule.

GitHub Actions runs them on every push, on Python 3.9 and 3.12, installing the full pinned runtime rather than only what the tests import — a pin that fails to resolve is a claim this README makes, and it should break the build rather than a stranger's clone.

### Building the index

**Usually you don't have to.** The built index ships as `data/chroma.tar.gz` (17 MB) and unpacks itself into `data/chroma/` the first time anything queries it, which takes about a tenth of a second. Querying a fresh clone works immediately.

It ships as an archive rather than as a committed directory because Chroma writes to its SQLite file when you only read from it — opening the collection for a query bumps the change counter and rewrites a page or two — so a tracked directory reports itself modified after every query, and each of those would be another 29 MB blob in the history. The archive is half the size, changes only when the index is genuinely rebuilt, and lets `data/chroma/` stay gitignored like the regenerable data it is.

To rebuild it from scratch — `data/processed/` is committed, so this needs no re-scraping:

```bash
venv/bin/python scripts/build_index.py            # writes data/chroma/, then repacks the archive
venv/bin/python scripts/build_index.py --dry-run  # chunk and report only, ~2 sec
```

Repacking is the last step of the build rather than a separate command, because the archive is the only copy anyone else sees: a rebuild that skipped it would leave every clone and the deployed app on the previous index while everything here looked correct.

The model downloads itself on first use (~80 MB). `--dry-run` skips the embedding pass and prints the chunk-size distribution, the per-source split, and the documents contributing the most chunks — it exists so chunk sizing can be tuned in seconds rather than minutes.

### Querying

```bash
venv/bin/python scripts/query.py "can I get a fee waiver for naturalization?"
venv/bin/python scripts/query.py --type uscis --barrier financial -k 10
venv/bin/python scripts/query.py            # no question: prompt in a loop
```

Retrieval only — no model is called to write an answer, and none is needed to tell whether the right source came back. Each hit prints its citation (`12 USCIS-PM B.4, n.8`, `Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)`), its position in the document, its section heading, and the passage itself widened to the chunks either side so nothing is read as a fragment. `--type` and `--barrier` restrict the search; omitting the question opens a prompt loop, which loads the model once instead of once per question.

Several of the top eight often come from the same document — three or more on nine of the fifteen evaluation questions — because chunk counts per document run from 1 to 248 and the ranking is over chunks rather than documents. That reads like a defect and mostly is not: the document taking the most slots is usually the one that answers the question, and its adjacent chunks are how the neighbor window ends up covering a continuous stretch of the relevant section. No cap is applied — see below for what capping was measured to cost.

### Generating an answer

```bash
venv/bin/python scripts/generate.py "can I get a fee waiver for naturalization?"
venv/bin/python scripts/generate.py "..." --dry-run           # assemble the prompt, call nothing
venv/bin/python scripts/generate.py "..." -k 5 --thinking high     # override the defaults
venv/bin/python scripts/generate.py "..." --provider ollama    # local model, no key
```

The retrieved passages are labeled `[S1]`, `[S2]` and so on, and the model is instructed to cite one after every factual claim, to flag disagreement between sources rather than choose a side, to date any figure a source supports only as of some past time, and to refuse with a fixed sentence when the passages do not answer the question. Output marks which sources were actually cited, and warns when a citation names a source that was never supplied.

`--dry-run` prints the assembled prompt without spending a request, which is how prompt changes get inspected for free. `--thinking` sets reasoning effort; it shares one budget with the answer, so a large reasoning pass under a small ceiling truncates the reply rather than the reasoning.

A failure never raises. A rate-limited or unavailable model returns the retrieved passages under an explanation instead, because on a free tier quota exhaustion is ordinary and a demo that hard-fails on it is worse than one that shows eight citable passages and says why.

Passage text repeated across two labels is removed before the prompt is built — two hits from one document can widen onto the same neighbor, and adjacent chunks share their overlap besides. Left in, the same paragraph arrives under two labels and there is no basis for citing one over the other.

### The interface

Two front ends over one backend, both calling the same `answer_question()` the command line calls, at the same measured defaults. Neither re-implements retrieval, chunking or citation checking.

**[naturalization-rag.vercel.app](https://naturalization-rag.vercel.app)** is the current one: a static Next.js page on Vercel talking to a FastAPI service in a container. **[naturalization-rag.streamlit.app](https://naturalization-rag.streamlit.app)** is the original, still running and unchanged, because links to it are already in circulation.

```bash
venv/bin/uvicorn api.main:app --reload --port 8000   # the API
cd web && npm run dev                                # the site
venv/bin/streamlit run app.py                        # or the Streamlit version
```

A question box, the cited answer, and the passages it was written from underneath it. Each passage is expandable, and the ones the answer actually cited are highlighted and open, so which passages carry the answer stays readable no matter what a reader collapses.

Each `[S1]` in the answer links to the passage it names, with its citation on hover. The label rather than the citation stays in the prose deliberately: citations are document-level, so five of the eight passages on a fee-waiver question carry the same one, and only the label says which passage a claim rests on.

**Retrieval and generation are separate requests, because they cost different things.** `POST /search` is local, keyless and deterministic and returns in about a third of a second; `POST /answer` is a round trip to a hosted model and takes around ten. The page fires both at once and paints twice, so a reader has the passages in front of them while the answer is still being written rather than watching a spinner for the whole duration. `/answer` re-runs its own retrieval instead of accepting passages from the client, which costs that third of a second and means the client cannot forge the sources an answer claims to rest on.

Every number the site states is read out of `data/eval/` when the site is built, and the retrieval depth and reasoning effort it names are parsed from the Python that defines them, so the page cannot report a score for a configuration the service is not running. Each of those reads throws rather than defaulting: a build that cannot see the results fails instead of rendering a page of zeroes that looks measured.

Generation is capped per client and per day, so one visitor cannot spend the day's free-tier requests. Past the cap, and on any provider failure, both front ends keep retrieving and show the cited passages without an answer. The key lives only on the container, never in the repository, the image, or anything the browser receives.

**Questions are sent to a third-party model to write the answer, so the interface says not to enter personal information** — no names, case numbers or facts about your own situation. Retrieval alone never leaves the machine it runs on.

### Deployment

```
browser ──► Vercel (static Next.js)
                │  fetch
                ▼
         Azure Container Apps ──► Gemini
         FastAPI + onnxruntime + ChromaDB
```

The container image is built by GitHub Actions and published to the GitHub Container Registry, not built in the cloud provider that runs it. `az acr build` is the obvious way to build without a local Docker daemon and it is unavailable on a trial-credit subscription, which is where this started. Building in CI turned out better anyway: the registry is free for a public repository, no machine needs a Docker daemon, and the image that ships is the one CI produced rather than whatever was on somebody's laptop.

The container holds one Chroma index and one encoder behind a lock. ChromaDB writes a few bytes to its SQLite file on every read, so concurrent queries are concurrent writers to a single connection; the lock covers retrieval and is released before the model call, so simultaneous visitors queue only on the fast half. Replicas are pinned to one for the same class of reason: the rate-limit counters live in process memory, and a second replica would hand out a second copy of the day's budget.

A model call is bounded well under the ingress timeout. The retry policy that makes the command line robust — three attempts, a ninety-second read timeout, honored `Retry-After` between them — can outlast the proxy in front of the container, at which point the work continues against a connection that has already closed. Past the deadline the request degrades to its passages instead. **A retry policy is only correct relative to the deadline of whatever is calling it.**

`api/README.md` has the deploy commands and the configuration variables.

---

## Evaluation

```bash
venv/bin/python scripts/eval_retrieval.py             # score the question set
venv/bin/python scripts/eval_retrieval.py --validate  # check the question set only, ~1 sec
venv/bin/python scripts/eval_retrieval.py -k 10 --json results.json
```

`data/eval/questions.json` holds fifteen questions written in an applicant's voice. Each names the documents a correct answer needs, verbatim sentences from those documents, and the documents that are acceptable to return without being required. Keys are text and document IDs, never chunk IDs — chunk sizing is still tunable and every chunk ID would shift beneath a stored key, leaving an eval that keeps reporting while measuring nothing.

Two things get scored, because they fail separately. **Recall** asks whether the right document ranked in the top k. **Anchors** ask whether the specific paragraph came back, checked as exact text, and are scored twice: once against the matched chunks and once against the neighbor window a reader actually sees.

At k=8 over 15 questions and 35 anchors:

| | |
|---|---|
| recall@1 / @3 / @5 / @8 / @10 | 43% / 50% / 57% / 70% / 70% |
| questions finding every expected document | 8 / 15 |
| questions finding at least one | 13 / 15 |
| anchors in a matched chunk | 14 / 35 |
| anchors once widened to the neighbor window | 18 / 35 |

Three results worth stating plainly, because they set what is worth building next:

**Case law is the weak half.** Policy Manual chapters are found 8 times in 12; court opinions 2 times in 10. It is not that opinions are returned rarely — they take 59% of the returned slots against a 47% share of the index. The wrong opinions come back. The 26 opinions all concern naturalization procedure and cite the same statutes, so they sit close together and close to any procedural question.

**Most of that is a cutoff, not a ranking failure.** At k=10, case law goes from 2 of 10 to 6 of 10 while the Policy Manual half does not move at all — the right opinion is usually just under the line. The Policy Manual misses are far misses instead, at ranks 17, 38 and 66, which no cutoff reaches; they are a vocabulary gap between how an applicant asks and how policy is written.

**Depth past 8 buys nothing, and 8 was then checked against 5 on generated answers.** Recall at 8 and at 10 is the same 70% over the same questions, so the two extra slots add only passages that dilute — which ruled out k=10 before a single model call. What a retrieval metric could not decide was whether the extra passages help or hurt an *answer*, since they are by construction worse matches. Scoring both depths on generated output settled it: k=8 puts four more expected paragraphs in front of the model, and the two questions that scored lower turned out to have cited a different chunk of the same chapter, with answers as good or better. The default is 8.

**Capping how many slots one document may hold was measured and rejected.** Limiting any document to a single slot raises recall from 70% to 77% and drops expected passages from 18 of 35 to 12 — more chapters, fewer paragraphs, which is the wrong trade for a tool whose output is passages someone reads. Limits of two or three move recall by three points and passages by one, inside the noise of a fifteen-question set.


### Scoring the answers

```bash
venv/bin/python scripts/eval_generation.py --dry-run   # free; the anchor ceiling at this k
venv/bin/python scripts/eval_generation.py --thinking low --run 1
venv/bin/python scripts/eval_generation.py --compare a.json b.json
```

Retrieval evaluation asks whether the right passage arrived. This asks what the model did with it, on two checks that cost nothing beyond the requests themselves. **Mechanical:** fabricated labels, brackets that cite something never supplied, sources left uncited, whether the fixed refusal sentence was used. **Anchor coverage:** whether the sources the answer *cited* contain the expected paragraph — a stronger claim than the anchor merely being somewhere in the context, and it reuses the question set already written for retrieval.

Neither check says the answer is *right*, and the second is a floor rather than a verdict: it is sensitive to which chunk of a document got the citation, so an answer can be sound and score zero. Where the score and an answer someone has read disagree, the answer wins.

Four configurations on Gemini Flash-Lite — retrieval depth 5 and 8, reasoning effort low and high — over 15 questions plus one probe, each run twice, 128 requests:

| | |
|---|---|
| fabricated citations, all 128 answers | **0** |
| anchors inside a cited source (k=8, low) | 16 of 18 available, 35 total |
| expected documents cited, of those retrieved | 12 of 14 |
| answers produced | 15 of 15, no degradation |

**Two runs of the same configuration were byte-identical on 64 of 64 answer pairs**, so the measurement's own noise floor is zero and any difference between configurations is a real one. That is worth half the request budget: without it, a two-anchor gap between settings cannot be told apart from the weather.

**Reasoning effort was set by measurement, not inherited.** The default had been chosen for an output-budget constraint that no longer existed. Scored both ways, the higher setting is worse: on a question the corpus can only answer with a dated figure, it states a dollar amount from a rule a court blocked from taking effect, where the lower setting declines and says what is missing. It also costs about 60% more reasoning tokens for that.

**Retrieval depth was settled the same way**, and the result corrected the assumption behind it. Going deeper was expected to dilute; it did not. The extra passages are cited on 13 of 15 questions, and where a score dropped, the model had cited a different chunk of the same chapter while giving an equal or better answer. One question shows the subtler effect: three added passages, none of them cited, changed which of the five unchanged passages the model leaned on.

---

## Disclaimer

This tool provides **legal information, not legal advice.** It surfaces and cites public policy text; it does not interpret anyone's individual circumstances. Immigration decisions should involve a qualified attorney or an accredited representative.

## Motivation

This project grew out of research on naturalization-rate disparities across demographic groups and coursework in asylum law. Both kept surfacing the same pattern: what stops people is often access to the law rather than the law itself.
