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
                          answer + citations ◄── Claude ◄── retrieved context ◄── query
```

**Ingestion** scrapes Policy Manual chapters and pulls federal opinions into plain text, each with a metadata sidecar (volume, part, chapter, source, date, URL).

**Indexing** chunks along the manual's own section headings, targeting 300–500 tokens with slight overlap, then embeds the chunks into a ChromaDB index.

**Retrieval and generation** embeds the question, retrieves the top-k chunks, and prompts Claude to answer only from those chunks and cite them.

---

## Roadmap

| Stage | Status |
|---|---|
| USCIS Policy Manual scraper (`scripts/scrape_uscis.py`) | Shipped |
| Volume-agnostic scraping; Vol. 1 added alongside Vol. 12 | In progress |
| Case-law ingestion via CourtListener API | In progress |
| Cleaning pass + metadata sidecar | In progress |
| Chunking and embedding layer (ChromaDB) | Planned |
| RAG query loop with forced citation | Planned |
| Barrier-type tagging (financial, linguistic, procedural, timeline) | Planned |
| Streamlit interface | Planned |

### Barrier tagging

A planned layer tags each passage by the kind of barrier it describes: financial, linguistic, procedural, or timeline. Knowing you're blocked by a documentation requirement rather than an income threshold changes what you do next.

---

## Stack

Python · BeautifulSoup · CourtListener REST API · ChromaDB · Anthropic API · Streamlit

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
COURTLISTENER_API_TOKEN=your_token_here
```

A CourtListener token is free at https://www.courtlistener.com/profile/api-token/.

`.env` is gitignored and should never be committed.

---

## Disclaimer

This tool provides **legal information, not legal advice.** It surfaces and cites public policy text; it does not interpret anyone's individual circumstances. Immigration decisions should involve a qualified attorney or an accredited representative.

## Motivation

This project grew out of research on naturalization-rate disparities across demographic groups and coursework in asylum law. Both kept surfacing the same pattern: what stops people is often access to the law rather than the law itself.
