"""Chunk data/processed into passages, embed them locally, store them in Chroma.

The first script that reads the corpus rather than producing it, and the payoff for the
shared sidecar schema: one loop over 105 documents with no branch on where they came from.

Local and free end to end — all-MiniLM-L6-v2 runs on CPU, Chroma writes to a directory.
Re-running is minutes and costs nothing, which is why this rebuilds the collection from
scratch instead of trying to update it in place.

Run:  venv/bin/python scripts/build_index.py [--dry-run]
      --dry-run chunks and reports without embedding anything or writing the index. That is
      how you tune the sizes below: the chunking is the part worth iterating on, and paying
      4.5 minutes of embedding to see a chunk-size histogram is a waste. It still loads the
      model, because the tokenizer that counts chunk sizes comes with it — ~12s wall clock
      against ~2.5s of actual chunking.
"""

import glob
import json
import logging
import os
import re
import sys
import time
import chromadb
from sentence_transformers import SentenceTransformer

INPUT_DIRS = ["data/processed/uscis", "data/processed/caselaw"]
INDEX_DIR = "data/chroma"
COLLECTION = "naturalization"

METADATA_SUFFIX = "_metadata.json"

MODEL_NAME = "all-MiniLM-L6-v2"

# The number that set every other number in this file. all-MiniLM-L6-v2 has
# max_seq_length 256 and silently truncates past it: the text stays whole in Chroma and
# comes back whole from a query, but the vector only ever represented the first 256
# tokens. So a chunk longer than this is not a bigger chunk, it is a chunk with an
# invisible tail — nothing after the cut can ever be matched on.
#
# This is why the sizes below are not the usual 300-500: that advice assumes a 512-token
# encoder. Read off the model rather than taken from the advice.
MODEL_TOKEN_LIMIT = 256

# Aim under the ceiling rather than at it, so packing has room to keep a paragraph whole
# instead of guillotining it at 256. Chunks land at or below MODEL_TOKEN_LIMIT either way
# (see chunk_units for why that is guaranteed, not hoped for).
TARGET_TOKENS = 220

# Carried from the end of one chunk into the start of the next, so a sentence split across
# a boundary is still wholly present somewhere. Small on purpose: the neighbour window
# (chunk_index +/- 1, see query.py) is the real answer to fragmentation, and overlap is
# just insurance against a boundary landing mid-thought.
OVERLAP_TOKENS = 40

# A heading only forces a chunk break once there is a real chunk to break. Measured over
# the 655 USCIS sections: median 224 tokens (about one chunk, which is the happy case),
# but 10% are under 30 and 27% under 110 — a hard break at every heading would mint 63
# chunks that are essentially just a heading with nothing under it. Below this floor the
# pending chunk absorbs the heading and keeps going.
MIN_FLUSH_TOKENS = 80

# Section markers process_uscis.py emits. Case law carries none, deliberately — heading
# detection there is noisy in both directions, so it waits until the eval set can score it
# rather than be guessed at. `section` is populated for the USCIS half, empty for case law.
HEADING = re.compile(r"^(#{2,4})\s+(.*)$")

# A footnote definition, hoisted onto its own line by the processing scripts. Searched
# anywhere in the chunk rather than at line starts: the shape does not occur in prose, and
# a long footnote can be sentence-split, which moves the marker off a line start.
FOOTNOTE_DEFINITION = re.compile(r"\[\^\s*(\d+)\]")

# Sentence boundary, used only to break up lines that exceed the model's ceiling. Closing
# quotes and brackets are pulled along so a split never orphans them.
#
# Known imprecision: legal abbreviations are periods followed by whitespace, so this splits
# "404 F. Supp. 3d 393" into three. It costs nothing in the text — join_units puts the space
# back, so a chunk is byte-identical to its source — and only means a chunk boundary can land
# mid-citation. The fix is an abbreviation blacklist, which is a list that never ends; not
# worth it unless retrieval measurably suffers.
SENTENCE_BREAK = re.compile(r'(?<=[.!?;:])["”’\')\]]*\s+')

# Neither is Article III and neither should be indexed as case law. A guard, not a working
# filter — none of the 26 selected opinions are from either, and this exists so that stays
# true if the manifest grows.
EXCLUDED_COURTS = ("bia", "olc")

# Chroma rejects None outright (TypeError: cannot convert Python object to MetadataValue),
# and the sidecar schema is deliberately nullable — `citation` is null for all 79 USCIS
# chapters and 5 of 26 opinions, `court_id`/`date`/`barrier` for all of USCIS. So every
# value is coerced on the way in. Empty string, not the string "None", so a `where` filter
# reads as "absent" rather than matching literal text.
#
# Everything stays scalar. Chroma 1.5.9 does accept lists, but the fields that would want
# one (`barrier`, `footnote_numbers`) are comma-joined anyway: `where` filtering over list
# members is not something to depend on, and a comma-joined string is one `split(",")` away
# from a list at read time.
SIDECAR_FIELDS = ("source_id", "source_type", "title", "citation", "court_id",
                  "date", "barrier", "url", "retrieved", "extracted_from")

# Chroma's per-call cap is in the low thousands; the whole corpus is 3,210 chunks, so this
# is really just a guard against a future corpus tripping a limit mid-run.
ADD_BATCH = 1000


def find_documents(input_dirs):
    """Every processed document as (stem, txt_path, metadata_path). Sorted for comparable runs.

    Same pairing-by-filename as the processing scripts: a .txt whose sidecar is missing
    turns up here with a path that does not exist, and read_document says so.
    """
    documents = []
    for input_dir in input_dirs:
        for txt_path in sorted(glob.glob(os.path.join(input_dir, "*.txt"))):
            stem = os.path.basename(txt_path)[:-len(".txt")]
            documents.append((stem, txt_path,
                              os.path.join(input_dir, stem + METADATA_SUFFIX)))
    return documents


def read_document(txt_path, metadata_path):
    """A document's lines plus its sidecar. Blank lines dropped — the format has none anyway."""
    with open(txt_path) as f:
        lines = [line.rstrip("\n") for line in f]
    with open(metadata_path) as f:
        sidecar = json.load(f)
    return [line for line in lines if line.strip()], sidecar


def split_oversized(text, count_tokens):
    """Break one line into pieces that each fit under the model's ceiling.

    Only ever called on a line that does not fit, which is 386 of 6,425 lines corpus-wide
    and 369 of those are case law: the PDF half is one line per *page*, so its lines run to
    757 tokens where the USCIS half's median line is 30. Packing short lines was never the
    hard part on that half; splitting long ones is.

    Sentences are enough. Measured over every over-long line in the corpus: 8,978 sentences,
    median 11 tokens, longest 115, and zero still over the ceiling. The word-level fallback
    below therefore never runs today — it is here because "no sentence is too long" is a
    fact about this corpus, not about the splitter, and the failure it prevents is silent.
    """
    pieces = []
    for sentence in SENTENCE_BREAK.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if count_tokens(sentence) <= MODEL_TOKEN_LIMIT:
            pieces.append(sentence)
            continue
        # Fallback: a single sentence over the ceiling. Chop on whitespace, accepting that
        # the seam is arbitrary — an arbitrary seam beats an invisible tail.
        words, run = sentence.split(), []
        for word in words:
            run.append(word)
            if count_tokens(" ".join(run)) > MODEL_TOKEN_LIMIT:
                pieces.append(" ".join(run[:-1]))
                run = [word]
        if run:
            pieces.append(" ".join(run))
    return pieces


def to_units(lines, count_tokens):
    """Lines -> the atoms the packer moves around, each tagged with its section path.

    A unit is (text, tokens, kind, section, breaks_before). `kind` is "line" for a whole
    source line and "fragment" for a piece of a split one, which is what lets a chunk
    rejoin fragments with a space while keeping real paragraphs on separate lines.
    `breaks_before` marks a unit that a heading introduced, so the packer knows a section
    boundary is available there.

    Heading lines stay in the text rather than being lifted into metadata only. They are
    the most compressed statement of what a section is about ("## D. Continuous Residence"),
    they cost ~8 tokens, and a chunk that carries its own heading reads correctly when the
    UI prints it underneath an answer.
    """
    units = []
    path = {}           # heading level -> text, so a deeper heading replaces only its own level
    pending_break = False

    for line in lines:
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            path = {lvl: text for lvl, text in path.items() if lvl < level}
            path[level] = heading.group(2).strip()
            pending_break = True

        section = " > ".join(path[lvl] for lvl in sorted(path))
        tokens = count_tokens(line)
        if tokens <= MODEL_TOKEN_LIMIT:
            units.append((line, tokens, "line", section, pending_break))
            pending_break = False
        else:
            for i, piece in enumerate(split_oversized(line, count_tokens)):
                units.append((piece, count_tokens(piece),
                              "line" if i == 0 else "fragment", section,
                              pending_break if i == 0 else False))
                pending_break = False
    return units


def join_units(units):
    """Units back into text: fragments rejoin their sentence with a space, lines with a newline.

    Keeps the corpus format (one paragraph per line) intact through chunking, so a chunk
    printed to a user looks like the document it came from rather than like reflowed soup.
    """
    out = ""
    for text, _, kind, _, _ in units:
        if not out:
            out = text
        elif kind == "fragment":
            out += " " + text
        else:
            out += "\n" + text
    return out


def overlap_tail(units, incoming_tokens):
    """The trailing units to carry into the next chunk, or [] for no overlap.

    Three ways this declines to overlap, all guarding against a carry that would either
    re-emit most of a chunk or push the next one past the ceiling:
      - never carry every unit (a single-unit chunk gets no overlap, or it repeats forever)
      - never carry more than half the target, which is what a 250-token trailing unit
        would do if asked politely
      - never carry so much that the carry plus the unit about to be appended breaks the
        ceiling. This one is the whole reason the parameter exists: the first version
        seeded the carry and *then* appended, which put 117 chunks over 256 (max 352) on
        the first dry run. Overlap is insurance, so it yields to the ceiling every time.
    """
    tail, tokens = [], 0
    for unit in reversed(units[:-1] if len(units) > 1 else []):
        tail.insert(0, unit)
        tokens += unit[1]
        if tokens >= OVERLAP_TOKENS:
            break
    if not tail or tokens > TARGET_TOKENS // 2:
        return []
    if tokens + incoming_tokens > MODEL_TOKEN_LIMIT:
        return []
    return tail


def chunk_units(units):
    """Pack units into chunks of at most MODEL_TOKEN_LIMIT tokens.

    Two flush rules:
      - adding this unit would push past TARGET_TOKENS, so close the chunk first
      - this unit is the first line under a new heading and the pending chunk is already
        substantial (MIN_FLUSH_TOKENS), so close it on the section boundary instead of
        letting a chunk straddle two sections

    Why the size ceiling actually holds, rather than approximately holding: a chunk only
    grows while pending + unit <= TARGET, so it can only exceed TARGET when the excess
    arrived in one step — either a lone unit (to_units caps those at MODEL_TOKEN_LIMIT) or
    an overlap carry immediately followed by one (overlap_tail refuses a carry that would
    do that). Hence every chunk is <= MODEL_TOKEN_LIMIT, with no truncation anywhere. The
    ceiling check in print_summary verifies it every run rather than trusting this paragraph.

    Overlap is not carried across a heading break. Bleeding the previous section's tail
    into a new section would make the `section` field a lie, and section-level parent
    retrieval needs it honest.
    """
    chunks, pending, pending_tokens = [], [], 0

    for unit in units:
        _, tokens, _, _, breaks_before = unit

        section_break = breaks_before and pending_tokens >= MIN_FLUSH_TOKENS
        size_break = pending and pending_tokens + tokens > TARGET_TOKENS

        if section_break or size_break:
            chunks.append(pending)
            # A section boundary is a real boundary; a size boundary is an artifact of the
            # window, which is exactly the case overlap exists to soften.
            pending = [] if section_break else overlap_tail(pending, tokens)
            pending_tokens = sum(u[1] for u in pending)

        pending.append(unit)
        pending_tokens += tokens

    if pending:
        chunks.append(pending)
    return chunks


def chunk_metadata(units, sidecar, index, total):
    """One chunk's Chroma metadata: the whole sidecar, flattened and scalarised, plus position.

    Everything here is stored whether or not v1 reads it, because adding a field later
    means re-embedding while a field already present is a metadata write. `section` is the
    clearest case — nothing queries it today, and the section-level upgrade to parent
    retrieval is dead in the water without it.
    """
    metadata = {field: (sidecar.get(field) or "") for field in SIDECAR_FIELDS}

    # Which document and where in it. Without source_id you cannot tell that five results
    # are five fragments of one opinion; without the index pair you cannot fetch neighbours
    # or say "3 of 12".
    metadata["chunk_index"] = index
    metadata["chunk_total"] = total

    # Footnotes get no special handling in the chunker — they are packed as ordinary lines,
    # so a short note rides along with the paragraph citing it and a 2,182-char one becomes
    # its own chunk, both by doing nothing. This field is the whole reason that works: it
    # records which notes happened to land here, so a hit can be cited as `n.3`.
    numbers = FOOTNOTE_DEFINITION.findall(join_units(units))
    metadata["footnote_numbers"] = ",".join(numbers)

    # The heading in effect where the chunk starts. Empty for all case law.
    metadata["section"] = units[0][3]
    return metadata


def build_chunks(documents, count_tokens):
    """Every document chunked, as (id, text, metadata). Also returns what was skipped."""
    records, skipped = [], []

    for stem, txt_path, metadata_path in documents:
        lines, sidecar = read_document(txt_path, metadata_path)

        if sidecar.get("court_id") in EXCLUDED_COURTS:
            skipped.append((stem, f"court_id={sidecar['court_id']}"))
            continue
        if not lines:
            skipped.append((stem, "no content"))
            continue

        chunks = chunk_units(to_units(lines, count_tokens))
        total = len(chunks)
        for index, units in enumerate(chunks):
            # Deterministic, never a UUID. Stable IDs make every later change — new barrier
            # labels, the USCIS citation field, a freshness flag — an update in place rather
            # than a rebuild, and they make a run diffable against the previous one.
            records.append((f"{sidecar['source_id']}_{index}",
                            join_units(units),
                            chunk_metadata(units, sidecar, index, total)))
    return records, skipped


def print_summary(records, skipped, documents, count_tokens, elapsed):
    """Chunk shape and the two distributions that decide whether retrieval will behave."""
    print(f"\n{len(records)} chunks from {len(documents) - len(skipped)} documents "
          f"in {elapsed:.1f}s.")

    sizes = sorted(count_tokens(text) for _, text, _ in records)
    over = sum(1 for s in sizes if s > MODEL_TOKEN_LIMIT)
    print(f"Tokens per chunk: median {sizes[len(sizes) // 2]}, "
          f"p10 {sizes[len(sizes) // 10]}, p90 {sizes[int(0.9 * len(sizes))]}, max {sizes[-1]}.")

    # Must be zero. Anything here is text sitting in the index that no query can reach.
    if over:
        print(f"  !! {over} chunk(s) over the {MODEL_TOKEN_LIMIT}-token ceiling — "
              f"their tails will not be embedded.")

    by_type = {}
    per_document = {}
    for _, _, metadata in records:
        by_type[metadata["source_type"]] = by_type.get(metadata["source_type"], 0) + 1
        per_document[metadata["source_id"]] = metadata["chunk_total"]
    for source_type in sorted(by_type):
        print(f"  {source_type}: {by_type[source_type]} chunks "
              f"({by_type[source_type] / len(records):.0%})")

    # The imbalance to watch. Top-k retrieves chunks, not documents, so a long opinion gets
    # proportionally more chances before relevance is considered. Printed every run so the
    # number is in front of you before the eval set blames retrieval.
    ranked = sorted(per_document.items(), key=lambda kv: -kv[1])
    share = sum(count for _, count in ranked[:3]) / len(records)
    print(f"  longest 3 documents are {share:.0%} of all chunks:")
    for source_id, count in ranked[:3]:
        print(f"      {count:4d}  {source_id[:78]}")

    with_notes = sum(1 for _, _, m in records if m["footnote_numbers"])
    with_section = sum(1 for _, _, m in records if m["section"])
    print(f"  {with_notes} chunks carry footnotes, {with_section} carry a section heading.")

    if skipped:
        print(f"\nSkipped {len(skipped)} document(s):")
        for stem, reason in skipped:
            print(f"  - {stem} ({reason})")


def write_index(records):
    """Embed and store. Rebuilds the collection rather than updating it.

    Deliberate: chunk sizes change while tuning, and changing them changes which IDs exist.
    Upserting into a stale collection leaves orphans from the previous parameters sitting in
    the index, silently, where they will be retrieved and believed. Re-embedding the whole
    corpus is a few minutes on CPU and costs nothing, so the safe option is also the cheap one.
    """
    print(f"\nLoading {MODEL_NAME} (downloads ~90 MB on first use)...")
    model = SentenceTransformer(MODEL_NAME)

    texts = [text for _, text, _ in records]
    print(f"Embedding {len(texts)} chunks on CPU...")
    started = time.time()
    # Normalised here so cosine distance is a dot product, matching the collection's space.
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True,
                              normalize_embeddings=True, convert_to_numpy=True)
    print(f"Embedded in {time.time() - started:.1f}s "
          f"({embeddings.shape[1]} dimensions).")

    os.makedirs(INDEX_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=INDEX_DIR)
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    # Cosine, not the L2 default: MiniLM is trained for cosine similarity, and on normalised
    # vectors the two rank identically — but only the cosine distance is readable as a score.
    collection = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    for start in range(0, len(records), ADD_BATCH):
        batch = records[start:start + ADD_BATCH]
        collection.add(
            ids=[record[0] for record in batch],
            documents=[record[1] for record in batch],
            metadatas=[record[2] for record in batch],
            embeddings=embeddings[start:start + ADD_BATCH].tolist(),
        )

    print(f"Wrote {collection.count()} chunks to {INDEX_DIR}/ "
          f"(collection '{COLLECTION}').")


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    documents = find_documents(INPUT_DIRS)
    if not documents:
        print(f"No documents in {' or '.join(INPUT_DIRS)}. "
              f"Run process_uscis.py and process_caselaw.py first.")
        return

    # The tokenizer, not a word count or a chars/4 estimate. The ceiling this whole file is
    # built around is measured in the model's own WordPiece tokens, so counting them any
    # other way reintroduces exactly the truncation the ceiling exists to prevent.
    tokenizer = SentenceTransformer(MODEL_NAME).tokenizer
    cache = {}

    # Counting an over-long line makes the tokenizer warn that running it through the model
    # "will result in indexing errors". Correct in general, wrong here: we count precisely
    # so those lines get split before anything reaches the model. Silenced so it does not
    # read as a failure in the summary — this script's own ceiling check is the real alarm.
    logging.getLogger("transformers.tokenization_utils_base").setLevel(logging.ERROR)

    def count_tokens(text):
        # Same lines get counted repeatedly through packing and reporting; the corpus is
        # small enough that memoising is simpler than restructuring to count once.
        if text not in cache:
            cache[text] = len(tokenizer.tokenize(text))
        return cache[text]

    started = time.time()
    records, skipped = build_chunks(documents, count_tokens)
    print_summary(records, skipped, documents, count_tokens, time.time() - started)

    if dry_run:
        print("\n--dry-run: nothing embedded, nothing written.")
        return
    write_index(records)


if __name__ == "__main__":
    main()
