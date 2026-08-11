"""Score retrieval against the hand-written question set. No model call beyond the encoder.

The only quality signal in this project that is free: whether the document that answers a
question came back in the top k is decidable without a language model, without an API key
and without a judgment call. So it gets measured first, and generation is tuned afterward
against a number that already exists.

Two metrics, because they fail differently:
    recall@k   did the expected *document* rank in the top k
    anchor@k   did the expected *paragraph* come back, checked as verbatim text

Recall is the honest headline. Anchor is the one that catches a chunker that returns the
right chapter and the wrong page — which the neighbor window can then rescue, so anchors are
scored twice, once against the matched chunks and once against the printed window.

Nothing here refers to a chunk id. Chunk sizing is still tunable, and every id shifts the
moment it changes; an eval keyed to ids would keep passing while measuring the wrong thing.

Run:  venv/bin/python scripts/eval_retrieval.py
      venv/bin/python scripts/eval_retrieval.py --validate   # check the question set only
      venv/bin/python scripts/eval_retrieval.py -k 10 --json results.json
"""

import argparse
import glob
import json
import os
import sys

from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from query import (  # noqa: E402  (after the path fix, by necessity)
    MODEL_NAME, fetch_window, load_collection, neighbor_ids, search)

QUESTIONS_PATH = "data/eval/questions.json"
PROCESSED_DIRS = ["data/processed/uscis", "data/processed/caselaw"]

# Reported as a curve rather than a single number. One query at the largest cutoff answers
# all four — the ranked list is a prefix of itself — so the curve is free, and it says
# something a single number cannot: recall@1 is what a generator with a tight context sees,
# recall@10 is the ceiling retrieval could reach if reranking were added.
CUTOFFS = (1, 3, 5, 10)

# The k the headline numbers use, and the k query.py prints at.
HEADLINE_K = 5

# How deep the ranked list is fetched. Larger than the largest cutoff so the diversity
# simulation below has something to promote — a cap can only pull up a document that ranked
# somewhere, and with a pool of 10 the simulation would be measuring the pool as much as the
# cap. One query either way: Chroma returning 20 neighbors instead of 10 is not a cost.
POOL = 20

# Per-document caps to simulate. Not a feature — nothing in query.py caps anything. This is
# the arithmetic that decides whether a post-retrieval diversity filter is worth writing,
# done before writing it, because the crowding is known and its cost is not.
CAPS = (1, 2, 3)


def load_questions(path=QUESTIONS_PATH):
    with open(path) as f:
        return json.load(f)["questions"]


def document_text(source_id):
    """The processed document, or None if no file matches — validation reports the miss."""
    for directory in PROCESSED_DIRS:
        path = os.path.join(directory, source_id + ".txt")
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()
    return None


def indexed_source_ids(collection):
    """Every source_id in the index. One scan of 3,210 metadata rows, which is milliseconds."""
    return {m["source_id"] for m in collection.get(include=["metadatas"])["metadatas"]}


def chunk_texts(collection, source_id):
    """Every chunk of one document, by metadata filter rather than by id arithmetic."""
    got = collection.get(where={"source_id": source_id}, include=["documents"])
    return got["documents"]


def validate(collection, questions):
    """Check the question set against the corpus and the index. Returns a list of problems.

    An eval keyed to a source_id that does not exist would score 0 and
    read as a retrieval failure forever, so the keys are machine-checked before they are
    trusted. Four separate things can be wrong, and each has its own message:

      - a source_id that is in no document (a typo, or a document that left the corpus)
      - an anchor that is not in the document it is filed under (copied from memory)
      - an anchor that is also in some *other* document (so it cannot identify a passage)
      - an anchor that no single chunk wholly contains (chunk-dependent, so a warning:
        the anchor straddles a boundary today and anchor@k cannot ever find it)
    """
    problems = []
    known = indexed_source_ids(collection)
    all_documents = {}
    for directory in PROCESSED_DIRS:
        for path in sorted(glob.glob(os.path.join(directory, "*.txt"))):
            all_documents[os.path.basename(path)[:-len(".txt")]] = open(path).read()

    seen_ids = set()
    for question in questions:
        qid = question["id"]
        if qid in seen_ids:
            problems.append("{}: duplicate question id".format(qid))
        seen_ids.add(qid)
        if not question["expected"]:
            problems.append("{}: no expected sources".format(qid))

        expected_ids = {e["source_id"] for e in question["expected"]}
        for source_id in expected_ids & set(question.get("acceptable", [])):
            problems.append("{}: {} is both expected and acceptable".format(qid, source_id))

        for source_id in sorted(expected_ids | set(question.get("acceptable", []))):
            if source_id not in known:
                problems.append("{}: source_id not in the index — {}".format(qid, source_id))

        for entry in question["expected"]:
            source_id = entry["source_id"]
            text = document_text(source_id)
            if text is None:
                problems.append("{}: no processed document for {}".format(qid, source_id))
                continue
            chunks = chunk_texts(collection, source_id)
            for anchor in entry["anchors"]:
                if anchor not in text:
                    problems.append("{}: anchor not in {} — {!r}".format(
                        qid, source_id, anchor[:60]))
                    continue
                elsewhere = sorted(other for other, body in all_documents.items()
                                   if other != source_id and anchor in body)
                if elsewhere:
                    problems.append("{}: anchor also in {} — {!r}".format(
                        qid, ", ".join(elsewhere[:2]), anchor[:60]))
                if not any(anchor in chunk for chunk in chunks):
                    problems.append("{}: anchor straddles a chunk boundary, unreachable by "
                                    "anchor@k — {!r}".format(qid, anchor[:60]))
    return problems


def capped_indices(ranked, cap, k):
    """Positions of the top k after no document may occupy more than `cap` slots.

    Exactly what a post-retrieval diversity filter would do, and deliberately nothing more:
    it walks the existing ranking, skips a document that has had its share, and stops at k.
    Relevance order is untouched, so this cannot invent a result — it can only promote one
    that the crowding pushed below the cutoff.

    Positions rather than source_ids so the caller can index back into either the document
    list or the hits themselves. That is not a convenience: scoring a cap on recall alone is
    what made cap=1 look like a win when it was quietly discarding the chunk that held the
    answer, and the fix needs the chunk text, not just the document name.
    """
    kept, counts = [], {}
    for position, source_id in enumerate(ranked):
        if counts.get(source_id, 0) >= cap:
            continue
        counts[source_id] = counts.get(source_id, 0) + 1
        kept.append(position)
        if len(kept) == k:
            break
    return kept


def recall_of(ranked, expected_ids):
    """Fraction of the expected documents present in a ranked list."""
    return sum(1 for source_id in expected_ids if source_id in ranked) / len(expected_ids)


def anchors_found(collection, hits, anchors):
    """(anchors inside a matched chunk, anchors inside the printed neighbor window).

    The window is scored too because it is what a reader and a generator actually see — an
    anchor one chunk away from a match is not a retrieval failure. Called once per cap as
    well as for the uncapped top k, which costs one small `get` each and is what lets the
    diversity simulation answer "at what price" rather than only "how much".
    """
    matched = "\n".join(hit["text"] for hit in hits)
    window = fetch_window(collection, hits)
    widened = matched + "\n" + "\n".join(
        window.get(id_, "") for hit in hits for id_ in neighbor_ids(hit["metadata"]))
    return ([a for a in anchors if a in matched], [a for a in anchors if a in widened])


def score_question(collection, model, question, headline_k, max_k):
    """One question's numbers. Queries once at max_k; every cutoff is a prefix of that list."""
    hits = search(collection, model, question["question"], k=max_k)
    ranked = [hit["metadata"]["source_id"] for hit in hits]

    expected_ids = [entry["source_id"] for entry in question["expected"]]
    allowed = set(expected_ids) | set(question.get("acceptable", []))

    # The fixed curve, plus the headline k if it is not already one of them. Without the
    # union, `-k 15` computed a curve at 1/3/5/10 and then looked up recall[15], which
    # raised KeyError from inside the results loop — every k outside CUTOFFS crashed, and
    # nothing exercised one until a comparison run wanted k=15.
    recall = {cutoff: recall_of(ranked[:cutoff], expected_ids)
              for cutoff in sorted(set(CUTOFFS) | {headline_k})}

    # Rank of the first expected document, 1-based. None if it never appears — reported
    # rather than folded into a mean, since a made-up rank for a miss would flatter the
    # reciprocal-rank average.
    first = next((i for i, source_id in enumerate(ranked, 1) if source_id in expected_ids),
                 None)

    # Anchors, at the headline k. Scored against the matched chunk text and again against
    # the neighbor window, because the window is what a reader and a generator actually see.
    top = hits[:headline_k]
    anchors = [a for entry in question["expected"] for a in entry["anchors"]]
    in_chunk, in_window = anchors_found(collection, top, anchors)

    # What a diversity cap would have done to this question — scored on *both* metrics.
    # Recall alone said cap=1 was the best setting; it is the setting that throws away a
    # document's second chunk, which is frequently the one holding the paragraph. A cap that
    # gains a chapter and loses the page is not an improvement, and a simulation that cannot
    # express that is worse than none, because it recommends with confidence.
    capped_scores = {}
    for cap in CAPS:
        positions = capped_indices(ranked, cap, headline_k)
        cap_chunk, cap_window = anchors_found(collection, [hits[i] for i in positions],
                                              anchors)
        capped_scores[cap] = {
            "recall": recall_of([ranked[i] for i in positions], expected_ids),
            "anchors_in_chunk": len(cap_chunk),
            "anchors_in_window": len(cap_window),
        }

    # Off-target and crowding, both at the headline k. Off-target counts results that are
    # neither expected nor merely acceptable, so `acceptable` is what keeps this number from
    # punishing a perfectly good neighboring chapter. Crowding is the largest number of
    # results from one document — the known failure mode, measured instead of guessed at.
    top_ids = ranked[:headline_k]
    off_target = [source_id for source_id in top_ids if source_id not in allowed]
    crowding = max((top_ids.count(source_id) for source_id in set(top_ids)), default=0)
    crowded_by = max(set(top_ids), key=top_ids.count) if top_ids else ""

    # Whether the crowding is the problem or just what a focused question looks like. One
    # chapter taking all 5 slots on a question that chapter answers is a correct result;
    # the same shape from a document that answers nothing is the failure. Counting them
    # together is how a diversity filter gets tuned against a number that is half noise.
    crowded_off_target = bool(crowded_by) and crowded_by not in allowed

    return {
        "id": question["id"],
        "question": question["question"],
        "recall": recall,
        "capped": capped_scores,
        "hit": recall[headline_k] > 0,
        "first_rank": first,
        "top_score": round(hits[0]["score"], 3) if hits else 0.0,
        "anchors": len(anchors),
        "anchors_in_chunk": len(in_chunk),
        "anchors_in_window": len(in_window),
        "missing_anchors": [a for a in anchors if a not in in_window],
        "expected": expected_ids,
        "found": [source_id for source_id in expected_ids if source_id in top_ids],
        "off_target": off_target,
        "crowding": crowding,
        "crowded_by": crowded_by,
        "crowded_off_target": crowded_off_target,
        "ranked": top_ids,
    }


def print_report(results, headline_k):
    """Per question, then the aggregate. The aggregate is the line that goes in the README."""
    print("\nRetrieval eval — {} questions, headline k={}\n".format(len(results), headline_k))

    for result in results:
        status = "ok " if result["recall"][headline_k] == 1 else ("part" if result["hit"]
                                                                 else "MISS")
        print("{:<4} {:<26} recall {:.0%}  anchors {}/{} (window {}/{})  rank {}".format(
            status, result["id"],
            result["recall"][headline_k],
            result["anchors_in_chunk"], result["anchors"],
            result["anchors_in_window"], result["anchors"],
            result["first_rank"] if result["first_rank"] else "-"))

        for source_id in result["expected"]:
            if source_id not in result["found"]:
                print("       missed  {}".format(source_id))
        if result["crowding"] >= 3:
            print("       crowded {} of {} from {}{}".format(
                result["crowding"], headline_k, result["crowded_by"],
                " (not a source for this question)" if result["crowded_off_target"] else ""))
        if result["off_target"]:
            print("       off     {}".format(", ".join(sorted(set(result["off_target"])))))

    total = len(results)
    cutoffs = sorted(set(CUTOFFS) | {headline_k})
    print("\n{:<12}{}".format("recall@k", "  ".join(
        "@{}: {:.0%}".format(cutoff, sum(r["recall"][cutoff] for r in results) / total)
        for cutoff in cutoffs)))

    full = sum(1 for r in results if r["recall"][headline_k] == 1)
    any_hit = sum(1 for r in results if r["hit"])
    print("{:<12}{}/{} questions found every expected source, {}/{} found at least one"
          .format("documents", full, total, any_hit, total))

    anchors = sum(r["anchors"] for r in results)
    print("{:<12}{}/{} in a matched chunk, {}/{} once widened to the neighbor window".format(
        "anchors", sum(r["anchors_in_chunk"] for r in results), anchors,
        sum(r["anchors_in_window"] for r in results), anchors))

    ranks = [r["first_rank"] for r in results if r["first_rank"]]
    if ranks:
        print("{:<12}mean reciprocal rank {:.2f} over the {} questions that hit".format(
            "rank", sum(1 / rank for rank in ranks) / len(ranks), len(ranks)))

    crowded = [r for r in results if r["crowding"] >= 3]
    harmful = [r for r in crowded if r["crowded_off_target"]]
    print("{:<12}{}/{} questions have 3+ of {} results from one document, {} of those from a "
          "document that is not a source for the question".format(
              "crowding", len(crowded), total, headline_k, len(harmful)))

    # The block that decides whether the diversity filter gets written, and the reason it
    # reports two columns rather than one. Scored on recall alone, cap=1 reads as the best
    # setting available; scored on anchors as well, it is the worst, because the chunk it
    # discards is often the one holding the paragraph. Both columns or neither.
    baseline_recall = sum(r["recall"][headline_k] for r in results) / total
    baseline_chunk = sum(r["anchors_in_chunk"] for r in results)
    baseline_window = sum(r["anchors_in_window"] for r in results)
    print("\n{:<12}what a per-document cap would do to the top {}:".format(
        "diversity", headline_k))
    print("            {:<10} {:>8} {:>10} {:>10}".format("", "recall", "anchors", "window"))
    print("            {:<10} {:>8.0%} {:>7}/{:<2} {:>7}/{:<2}".format(
        "uncapped", baseline_recall, baseline_chunk, anchors, baseline_window, anchors))
    for cap in sorted(CAPS, reverse=True):
        cap_recall = sum(r["capped"][cap]["recall"] for r in results) / total
        cap_chunk = sum(r["capped"][cap]["anchors_in_chunk"] for r in results)
        cap_window = sum(r["capped"][cap]["anchors_in_window"] for r in results)
        # The trap gets a sentence; everything else gets its deltas and no adjective. A cap
        # that gains documents while losing paragraphs is the one failure a reader of this
        # table can talk themselves past, and it is the one that actually happened.
        if cap_recall > baseline_recall and cap_window < baseline_window:
            verdict = "  <- more chapters, fewer paragraphs"
        else:
            deltas = []
            if cap_recall != baseline_recall:
                deltas.append("{:+.0f}pp recall".format((cap_recall - baseline_recall) * 100))
            if cap_window != baseline_window:
                deltas.append("{:+d} window".format(cap_window - baseline_window))
            verdict = "  ({})".format(", ".join(deltas)) if deltas else ""
        print("            N={:<8} {:>8.0%} {:>7}/{:<2} {:>7}/{:<2}{}".format(
            cap, cap_recall, cap_chunk, anchors, cap_window, anchors, verdict))

    off = sum(len(r["off_target"]) for r in results)
    print("{:<12}{} of {} results are neither expected nor acceptable".format(
        "off-target", off, total * headline_k))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("-k", type=int, default=HEADLINE_K,
                        help="headline cutoff (default %d)" % HEADLINE_K)
    parser.add_argument("--validate", action="store_true",
                        help="check the question set against the corpus and exit")
    parser.add_argument("--json", metavar="PATH", help="also write per-question results")
    args = parser.parse_args()

    questions = load_questions()
    collection = load_collection()

    # Validation needs the index but not the encoder, so it runs in about a second — cheap
    # enough to be the thing you run after editing the question set, every time.
    problems = validate(collection, questions)
    if problems:
        print("Question set has {} problem(s):".format(len(problems)))
        for problem in problems:
            print("  - {}".format(problem))
        if args.validate:
            sys.exit(1)
        sys.exit("\nFix these before scoring; a bad key scores 0 and looks like a miss.")
    print("Question set validates: {} questions, {} expected documents, {} anchors, all "
          "present in exactly one document and reachable in one chunk.".format(
              len(questions),
              sum(len(q["expected"]) for q in questions),
              sum(len(e["anchors"]) for q in questions for e in q["expected"])))
    if args.validate:
        return

    model = SentenceTransformer(MODEL_NAME)
    max_k = max(POOL, max(CUTOFFS), args.k)
    results = [score_question(collection, model, question, args.k, max_k)
               for question in questions]
    print_report(results, args.k)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print("\nWrote {}".format(args.json))


if __name__ == "__main__":
    main()
