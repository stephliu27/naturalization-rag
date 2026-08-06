# Asylum & Naturalization Barrier Navigator

A retrieval-augmented generation (RAG) tool that answers questions about U.S. naturalization and asylum eligibility with citations to primary sources — so every answer can be traced back to the governing policy text.

**Status:** In active development. See [Roadmap](#roadmap) for what's shipped and what's next.

---

## The Problem

U.S. naturalization policy is public, authoritative, and effectively unreadable.

The USCIS Policy Manual runs thousands of pages of dense legal English, cross-referenced against federal case law that most applicants have no way to find. For an applicant deciding whether they qualify for a fee waiver or an English-testing exemption, the information exists — it's just buried in a format built for adjudicators and attorneys, not for the people the rules apply to. That barrier falls hardest on applicants whose first language isn't English.

The failure mode isn't that answers don't exist. It's that finding them requires already knowing where to look.

## The Approach

A question-answering tool over a corpus of USCIS Policy Manual sections and federal opinions, with one non-negotiable design constraint:

> **Every answer must cite the retrieved source passages it came from.**

This is the core product decision, and it's a deliberate tradeoff. A general-purpose LLM gives more fluent, confident-sounding answers about immigration law. It also paraphrases policy it can't point to, which in a legal context is worse than useless — a plausible wrong answer about eligibility carries real cost for the person acting on it.

So the system is built to constrain generation to retrieved context and surface the citations alongside the answer. Users get plain language *and* a path back to the authority. Fluency is subordinate to traceability.

### Scope

v1 covers the three decision points where applicants most often get stuck:

- **Eligibility** — who qualifies, and under what conditions
- **Fee waivers** — income thresholds and documentation requirements
- **Testing exemptions** — English and civics test waivers (age, disability, residency)

Broader asylum coverage is deliberately deferred. Narrow and correct beats broad and unreliable, especially in a domain where a wrong answer has consequences.

---

## Architecture

```
USCIS Policy Manual ──┐
                      ├──► ingestion ──► cleaned corpus ──► chunk + embed ──► ChromaDB
CourtListener API ────┘                  (+ metadata)                            │
                                                                                 ▼
                          answer + citations ◄── Claude ◄── retrieved context ◄── query
```

**Ingestion** scrapes Policy Manual sections and pulls federal opinions, normalizing both into plain text with a metadata sidecar (source, date, URL) so provenance survives every downstream step.

**Indexing** chunks the corpus into overlapping ~300–500 token segments and embeds them into a ChromaDB vector index, tagged by source document and section.

**Retrieval and generation** embeds the user's question, retrieves top-k relevant chunks, and constructs a prompt that instructs Claude to answer *only* from the provided context and cite the chunk IDs it used.

---

## Roadmap

| Stage | Status |
|---|---|
| USCIS Policy Manual scraper (`scripts/scrape_uscis.py`) | Shipped |
| Case-law ingestion via CourtListener API | In progress |
| Cleaning pass + metadata sidecar | In progress |
| Chunking and embedding layer (ChromaDB) | Planned |
| RAG query loop with forced citation | Planned |
| Barrier-type tagging (financial, linguistic, procedural, timeline) | Planned |
| Streamlit interface | Planned |

### Barrier tagging

A planned layer classifies each retrieved passage by the *kind* of barrier it creates — financial, linguistic, procedural, or timeline-related. The intent is to make the shape of the obstacle legible, not just the rule: a user who learns they're blocked by a documentation requirement rather than an income threshold knows something actionable about what to do next.

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
```

`.env` is gitignored and should never be committed.

---

## Disclaimer

This tool provides **legal information, not legal advice.** It surfaces and cites public policy text; it does not interpret anyone's individual circumstances. Immigration decisions should involve a qualified attorney or an accredited representative.

## Motivation

This project grew out of research on naturalization-rate disparities across demographic groups and coursework in asylum law — work that kept pointing at the same conclusion: the hardest problems in immigration law are often not doctrinal. They're about who can find, read, and act on the right information.
