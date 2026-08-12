"""Ask the corpus a question; get back the passages that answer it, with citations.

No LLM anywhere. This is retrieval on its own, deliberately: the question "did the right
source come back" is answerable without a model, without an API key and without spending
anything, which makes it the only quality signal available for free. Generation goes on top
of this later and does not change it.

The shape of a search:
    question -> 384 numbers (same model that embedded the corpus, or the comparison is
                meaningless) -> Chroma returns the nearest chunks -> each hit is widened to
                its neighbors and printed under a citation.

Run:  venv/bin/python scripts/query.py "can I get a fee waiver for naturalization?"
      venv/bin/python scripts/query.py            # no question -> prompt in a loop,
                                                  # so the model loads once, not per question
"""

import argparse
import os
import sys
import tarfile
import textwrap

import chromadb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citations import format_citation  # noqa: E402  (after the path fix, by necessity)
from encoder import MODEL_NAME, load_encoder  # noqa: E402

INDEX_DIR = "data/chroma"
COLLECTION = "naturalization"

# The index ships as an archive and is unpacked on first use, rather than being committed as a
# directory. The reason is that **Chroma writes to its sqlite file when you only read from it**:
# opening the collection for a query bumps SQLite's change counter and rewrites a page or two,
# measured at 18 bytes, so a tracked directory reports itself modified after every single query
# and each of those is another 29 MB blob in history. The archive is 17 MB rather than 35, never
# changes unless the index is genuinely rebuilt, and unpacks in about a tenth of a second — so
# `data/chroma/` goes back to being gitignored, regenerable data, the way the rest of the repo
# treats it. `build_index.py` writes the archive as its last step, so the two cannot diverge.
INDEX_ARCHIVE = "data/chroma.tar.gz"

# Measured on generated answers, not chosen. `eval_generation.py` scored k=5 against k=8 across
# both thinking levels, twice each: k=8 puts 4 more expected paragraphs in front of the model
# and costs ~1,300 prompt tokens a question. The dilution this was held at 5 to avoid did not
# appear — the two questions that scored lower cited a different chunk of the *same* chapter,
# and their answers were as good or better. Recall is flat from 8 through 12, so 8 is where the
# free retrieval metric stops improving and the paid one stops disagreeing with it.
TOP_K = 8

# A hit is printed with its immediate neighbors from the same document. Retrieval works
# best on small passages — a 220-token chunk about fee waivers matches a fee-waiver question
# far better than a 20,000-token chapter does — but a small passage is a bad thing to reason
# from. So: match small, read wide. Deterministic IDs make this a lookup, not a second search.
NEIGHBORS = 1

WRAP = 94


def ensure_index():
    """Unpack the shipped index if it is not already on disk. Whether one is now present.

    Neither exits nor raises, deliberately, because the two callers want different failures:
    the command line prints the build command and stops, while the app renders the same fact as
    page content. `sys.exit` here would take that choice away from both — and a `SystemExit`
    inside Streamlit's cached loader surfaces as a stack trace rather than as a message.
    """
    if os.path.isdir(INDEX_DIR):
        return True
    if not os.path.isfile(INDEX_ARCHIVE):
        return False

    print("Unpacking {}...".format(INDEX_ARCHIVE))
    with tarfile.open(INDEX_ARCHIVE) as archive:
        # Extracted against the working directory because the member paths are already
        # `data/chroma/...`, the same relative-path assumption INDEX_DIR itself makes.
        #
        # `extractall` is unsafe on an *untrusted* archive — a member named `../../x` writes
        # outside the target — and this one is written by `build_index.py` in this repo, never
        # downloaded. Python 3.9 has no `filter="data"` argument to enforce that; it arrives in
        # 3.12, and is worth adding whenever this venv moves.
        archive.extractall(".")
    return os.path.isdir(INDEX_DIR)


def load_collection():
    """The Chroma collection, or a message explaining how to make one."""
    if not ensure_index():
        sys.exit("No index at {}/ and no archive at {}. Run: venv/bin/python "
                 "scripts/build_index.py".format(INDEX_DIR, INDEX_ARCHIVE))
    client = chromadb.PersistentClient(path=INDEX_DIR)
    try:
        return client.get_collection(COLLECTION)
    except Exception:
        sys.exit("No collection '{}' in {}/. Re-run build_index.py.".format(COLLECTION, INDEX_DIR))


def build_where(collection, source_type, barrier):
    """A Chroma `where` filter, or None.

    `barrier` is comma-joined on the chunk ("delay,procedural") because Chroma metadata is
    scalar, and Chroma has no substring operator — so asking for "procedural" means finding
    every combination that contains it and matching on those. One scan of 3,210 metadata
    rows, which is milliseconds, and it stays exact instead of approximating with a prefix.
    """
    clauses = []
    if source_type:
        clauses.append({"source_type": source_type})
    if barrier:
        combinations = {m["barrier"] for m in collection.get(include=["metadatas"])["metadatas"]}
        matching = sorted(c for c in combinations if barrier in c.split(","))
        if not matching:
            sys.exit("No chunks tagged barrier={}. Tagged values: {}".format(
                barrier, ", ".join(sorted(v for v in combinations if v))))
        clauses.append({"barrier": {"$in": matching}})

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def search(collection, model, question, k=TOP_K, where=None):
    """Top-k chunks for one question, as dicts with id/text/metadata/score."""
    embedding = model.encode([question], normalize_embeddings=True).tolist()
    response = collection.query(query_embeddings=embedding, n_results=k, where=where,
                                include=["documents", "metadatas", "distances"])

    hits = []
    for id_, text, metadata, distance in zip(response["ids"][0], response["documents"][0],
                                             response["metadatas"][0], response["distances"][0]):
        # The collection is cosine space over normalized vectors, so distance is 1 - cosine.
        # Reported as similarity because that is the number worth having intuitions about:
        # a relevant passage scores ~0.6, an unrelated one ~0.05.
        hits.append({"id": id_, "text": text, "metadata": metadata,
                     "score": 1 - distance})
    return hits


def neighbor_ids(metadata, width=NEIGHBORS):
    """IDs of the chunks either side of this one, clipped to the document.

    `{source_id}_{chunk_index}` is deterministic, which is the entire trick — the window is
    an ID lookup rather than a second similarity search, so it costs nothing and cannot
    return something from a different document.
    """
    index, total = metadata["chunk_index"], metadata["chunk_total"]
    span = range(max(0, index - width), min(total, index + width + 1))
    return ["{}_{}".format(metadata["source_id"], i) for i in span if i != index]


def fetch_window(collection, hits):
    """Every hit's neighbors, fetched in one call, as {id: text}."""
    wanted = {id_ for hit in hits for id_ in neighbor_ids(hit["metadata"])}
    if not wanted:
        return {}
    found = collection.get(ids=sorted(wanted), include=["documents"])
    return dict(zip(found["ids"], found["documents"]))


# Adjacent chunks share up to OVERLAP_TOKENS of carried text, so printing a window verbatim
# stutters — the reader sees the same two sentences end one chunk and open the next. Anything
# shorter than this is a coincidence (both chunks happening to end and start on "the Rule"),
# not the carry, which is 40 tokens and so never near this floor.
MIN_OVERLAP_CHARS = 40

# The carry is at most TARGET_TOKENS // 2 = 110 tokens, so ~600 characters; the slack is
# headroom, and the cap is what keeps this a bounded scan rather than a quadratic one.
MAX_OVERLAP_CHARS = 800


def strip_overlap(previous, text):
    """`text` with its leading duplicate of `previous`'s tail removed.

    A substring search rather than `previous.endswith(...)`, which was the first attempt and
    silently did nothing: `overlap_tail` never carries a chunk's *final* unit, so the carry
    ends one unit short of the end and the repeat is a substring of the tail, not a suffix.
    Exact matching is still sound — overlap is whole units joined the same way at both ends,
    so the repeated text is byte-identical rather than merely similar.
    """
    tail = previous[-(MAX_OVERLAP_CHARS * 2):]
    for length in range(min(len(text), MAX_OVERLAP_CHARS), MIN_OVERLAP_CHARS, -1):
        if text[:length] in tail:
            return text[length:].lstrip()
    return text


def print_hit(rank, hit, window, hit_ids):
    """One result: citation, position, then the neighbor window with the match marked."""
    metadata = hit["metadata"]
    print("\n{}. {:.3f}  {}".format(rank, hit["score"], format_citation(metadata)))

    position = "chunk {}/{}".format(metadata["chunk_index"] + 1, metadata["chunk_total"])
    facts = [position] + [metadata[f] for f in ("section", "barrier") if metadata[f]]
    print("   {}".format("  ·  ".join(facts)))
    print("   {}".format(metadata["url"]))

    previous = ""
    for id_ in sorted(neighbor_ids(metadata) + [hit["id"]],
                      key=lambda i: int(i.rsplit("_", 1)[1])):
        if id_ in hit_ids and id_ != hit["id"]:
            # Already printed on its own account. Naming the rank keeps the window honest
            # about being contiguous without printing the same passage twice — which the
            # smoke tests hit immediately, since four consecutive chunks can all rank.
            print("      … (result {})".format(hit_ids[id_]))
            previous = ""
            continue

        body = hit["text"] if id_ == hit["id"] else window.get(id_, "")
        body = strip_overlap(previous, body)
        previous = body or previous

        # The marker runs down every line of the matched chunk, not just its first: the
        # window is contiguous prose, so without a left rule there is nothing to show where
        # the neighbor stops and the passage that actually matched begins.
        marker = "▸" if id_ == hit["id"] else " "
        for line in body.split("\n"):
            for wrapped in textwrap.wrap(line, WRAP) or [""]:
                print("   {} {}".format(marker, wrapped))


def answer(collection, model, question, k, where):
    hits = search(collection, model, question, k, where)
    if not hits:
        print("Nothing matched.")
        return

    hit_ids = {hit["id"]: rank for rank, hit in enumerate(hits, 1)}
    window = fetch_window(collection, hits)
    for rank, hit in enumerate(hits, 1):
        print_hit(rank, hit, window, hit_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("question", nargs="*", help="the question; omit for a prompt loop")
    parser.add_argument("-k", type=int, default=TOP_K, help="how many chunks (default %d)" % TOP_K)
    parser.add_argument("--type", choices=("uscis", "caselaw"), help="restrict to one half")
    parser.add_argument("--barrier", help="restrict to one barrier tag, e.g. financial")
    args = parser.parse_args()

    collection = load_collection()
    where = build_where(collection, args.type, args.barrier)

    model = load_encoder()

    if args.question:
        answer(collection, model, " ".join(args.question), args.k, where)
        return

    # Interactive: the model load is ~2s and the query itself is ~20ms, so asking several
    # questions in one process is the difference between exploring the corpus and waiting.
    print("{} chunks indexed. Ask a question, or ctrl-D to quit.".format(collection.count()))
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question:
            answer(collection, model, question, args.k, where)


if __name__ == "__main__":
    main()
