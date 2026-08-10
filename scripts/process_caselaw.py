"""Extract the fetched CourtListener opinions in data/raw/caselaw into data/processed/caselaw.

Local and instant, like process_uscis.py: re-run it freely, and keep the contestable calls
here rather than in fetch_caselaw.py, which spends API quota it cannot get back.

Two extractors, one output format. CourtListener hands the same endpoint back in two
unrelated markups, and which one you get is an accident of who digitized the reporter:

  - `xml_harvard` populated => Harvard CAP. Real <p> elements, explicit footnote labels.
    16 of 26.
  - `xml_harvard` empty     => PDF text in `plain_text`, hard-wrapped with page furniture
    and no markup at all. 10 of 26, so every structure the CAP half reads off an attribute
    has to be inferred from layout here.

Neither payload carries a case name, court, or date, so all of that joins from the
committed manifest. Output is the corpus-wide format process_uscis.py already emits — one
paragraph per line, "[N]" in the body, "[^ N] ..." on the line after the paragraph citing
it — so build_index.py never learns there were three markups behind it.
"""

import datetime
import glob
import json
import os
import re

from bs4 import BeautifulSoup

from scraping import (
    clean_text,
    normalize_unicode,
    place_footnotes,
    tidy_spacing,
)

INPUT_DIR = "data/raw/caselaw"
OUTPUT_DIR = "data/processed/caselaw"

# Committed, unlike the payloads: this is what makes the corpus reproducible after
# data/raw is wiped, and it is the only place a case name or court exists.
MANIFEST = "data/caselaw_opinion_ids.json"

# Matches fetch_caselaw.py's opinion_path(), and excludes the search_*.json files that
# share the directory.
OPINION_GLOB = "opinion_*.json"

METADATA_SUFFIX = "_metadata.json"

COURTLISTENER_ROOT = "https://www.courtlistener.com"

# Administrative bodies, not Article III courts. The BIA has no naturalization
# jurisdiction and an OLC opinion is executive advice, so neither belongs in a corpus
# about what courts have held. None of the 26 are either today — this is a guard against
# a future selection, not a filter doing work now.
EXCLUDED_COURTS = frozenset(["bia", "olc"])

# Case law keeps every footnote, so the predicate is a constant. Named rather than a bare
# lambda because the *reason* is the interesting part: measured at 8-9% of body text with
# 48 of 61 over 200 characters, and courts park alternative holdings down there — Akpovi
# n.3 is a 7th/9th Circuit split that appears nowhere else in the corpus. USCIS drops 56%
# of its notes as bare citations; the shared format did not imply a shared policy.
def keep_every_footnote(_note_text):
    return True


# Page markers, as (tag, class) pairs. Three markups for one thing, split across
# 8 / 4 / 3 documents, all rendering as "*493" in the middle of a sentence. Dropped per the
# corpus format. The <a> variant is the reason footnotes are collected before body marks
# are read: an unqualified <a> lookup would confuse the two.
PAGE_MARKUPS = [
    ("page-number", None),
    ("span", "star-pagination"),
    ("a", "page-label"),
]

# Footnote definitions, also two markups for one thing. <footnote label="N"> sits at the
# end of the document in 14 files; Shweika and Kariuki instead wrap
# <div class="footnote" label="N"> inside a <div class="footnotes">. Both carry the number
# in `label`, which is why one reader handles both — and why matching only <footnote>
# loses 14 of the 75 notes without saying so.
FOOTNOTE_MARKUPS = [
    ("footnote", None),
    ("div", "footnote"),
]
FOOTNOTES_WRAPPER = ("div", "footnotes")

# Inline references to those definitions, one markup per footnote markup.
FOOTNOTE_MARK_MARKUPS = [
    ("footnotemark", None),
    ("a", "footnote"),
]

# Private-use characters, so a mark carried through place_footnotes cannot be confused with
# a bracket group the opinion wrote itself. Two exist: De Dandrade cites "[28] U.S.C. § 1361"
# and Aljabri quotes "[0]n the filing of the petition" (an editorial "[O]n" that OCR read as a
# zero). Both would scan as markers with no definition and be deleted, corrupting a citation
# and a quotation — silently, which is the part that matters. The markup already knows where
# the real marks are, so the text never has to be guessed at. Rewritten to "[N]" afterwards.
MARK_OPEN = "\ue000"
MARK_CLOSE = "\ue001"
CAP_MARKER = re.compile(r"\s*\ue000(\d+)\ue001")

# Leading punctuation left behind once the mark is pulled off the front of a note: CAP
# stores the number and the period separately, so ". The named defendants are ..." is what
# remains. On 43 of 75 — the other 32 start straight into the sentence, hence "if present".
NOTE_LEAD = re.compile(r"^[.\s]+")

# CAP keeps the real spaces in its text nodes, so joining with anything would insert a space
# the reporter never wrote. Six words are split by an inline tag or a page break falling
# mid-word — "Castr<em>acani</em>", "complaint be<a>*459</a>cause" — and " " would make every
# one of them two words. The opposite of the USCIS default, and the reason it is a parameter.
JOIN = ""

# ---------------------------------------------------------------------------
# The PDF half. Nothing below here is markup — it is a flattened page image, so every
# structure the CAP side reads off an attribute has to be inferred from layout instead.
# ---------------------------------------------------------------------------

# Form feed. Present in all 10, which is what makes the page the unit of work.
PAGE_BREAK = "\x0c"

# A printed line number in the left margin, and the share of lines that has to carry one
# before a document counts as pleading paper. Moya measures 39%, everything else 0-2%.
#
# Horizontal whitespace only, never \s: the form feed is whitespace too, so \s{0,3} happily
# consumed the page break sitting in front of a margin number and deleted it along with the
# digit. That collapsed Moya's 62 pages into a handful, and a footnote block then ran to the
# end of a 26,000-character "page" — swallowing the conclusion of the opinion into note 3.
PLEADING_LINE_NUMBER = re.compile(r"(?m)^[ \t]{0,3}\d{1,2}(?:[ \t]{2,}|[ \t]*$)")
PLEADING_THRESHOLD = 0.30

# Page numbers, printed bare ("6") by most and dashed ("-4-") by the Eighth Circuit, at the
# bottom by most and at the top by Miriyeva.
PAGE_NUMBER_LINE = re.compile(r"^\s*-?\s*\d{1,3}\s*-?\s*$")

# The rule some courts print above a footnote block. Furniture, but a useful confirmation
# that the block below it is what we think it is.
HORIZONTAL_RULE = re.compile(r"^\s*[_\-–—]{5,}\s*$")

# A footnote's opening line: its number, then either the text or a line break before it.
# The indent runs to 12 spaces once Moya's margin numbers are gone, hence the generous
# allowance — the ascending-number check in split_page_footnotes is what keeps it honest.
NOTE_START = re.compile(r"^\s{0,16}(\d{1,2})(?:\s*$|\s+(\S.*))")

# Used to normalize a line before asking whether it repeats: the page number is the part
# that varies, so without this every running head looks unique.
DIGITS = re.compile(r"\d+")

# How much repetition makes a line furniture rather than prose. Both floors matter: the
# share catches long documents, the minimum stops a 3-page opinion from calling its own
# first sentence a running head.
RUNNING_HEAD_MIN_PAGES = 3
RUNNING_HEAD_SHARE = 0.35

# Top-level elements we know how to emit, and what each becomes. Blockquotes take "> ",
# which is reserved corpus-wide for exactly this: 44 real block quotes of statute and
# precedent that would otherwise be indistinguishable from the court's own prose.
LINE_PREFIXES = {"p": "", "author": "", "blockquote": "> "}


def find_elements(soup, tag, class_name):
    """Every (tag, class) match. class_name None means "the tag, however it is classed"."""
    if class_name is None:
        return soup.find_all(tag)
    return soup.find_all(tag, class_=class_name)


def strip_page_markers(root):
    """Remove all three page-number markups. Returns how many came out.

    They sit mid-sentence, so leaving them puts "*493" inside the text an embedder sees.
    """
    removed = 0
    for tag, class_name in PAGE_MARKUPS:
        for element in find_elements(root, tag, class_name):
            element.decompose()
            removed += 1
    return removed


def collect_footnotes(root):
    """Pull the footnote definitions out of the tree. Returns (number -> text, unlabeled).

    Removing them is half the job: they sit at the end of the document, so left in place
    they would be emitted twice — once as their own paragraphs and once hoisted next to the
    paragraph citing them.

    Done before body marks are read, because the <div class="footnote"> variant contains a
    backlink <a class="footnote"> that is indistinguishable from an inline mark until its
    container is gone.
    """
    notes = {}
    unlabeled = 0

    for tag, class_name in FOOTNOTE_MARKUPS:
        for element in find_elements(root, tag, class_name):
            label = (element.get("label") or "").strip()
            if not label.isdigit():
                # Verified none today; reported rather than guessed at from position,
                # because a note placed on the wrong paragraph reads as if the court said it.
                unlabeled += 1
                element.decompose()
                continue

            # The backlink, which would otherwise put a stray digit at the front of the note.
            for anchor in element.find_all("a"):
                anchor.decompose()

            # One note per line: a note containing several <p> (Shweika 6) or a <blockquote>
            # (Hassan, 3 of them) folds into one, which costs those three their "> ".
            text = NOTE_LEAD.sub("", clean_text(element, JOIN))
            if text:
                notes[int(label)] = text
            element.decompose()

    # Now empty, and it is a container rather than content.
    for element in find_elements(root, *FOOTNOTES_WRAPPER):
        element.decompose()

    return notes, unlabeled


def mark_footnote_references(root):
    """Replace each inline mark with a sentinel carrying its number. Returns the count.

    The number lives in the element's text, not an attribute, and CAP sometimes italicizes
    it (<footnotemark><em>1</em></footnotemark>), so it is read from the rendered text.
    """
    marked = 0
    for tag, class_name in FOOTNOTE_MARK_MARKUPS:
        for element in find_elements(root, tag, class_name):
            digits = re.sub(r"\D", "", element.get_text())
            if not digits:
                continue
            element.replace_with(MARK_OPEN + digits + MARK_CLOSE)
            marked += 1
    return marked


def extract_cap(xml):
    """Harvard CAP XML -> (body_lines, notes, report).

    The top level of <opinion> is flat — no containers to recurse into, unlike the USCIS
    chapters — so the walk is a single pass over its children in document order. Anything
    with no branch here is counted and reported rather than dropped quietly, the same rule
    parse_chapter follows: the tags you did not think of are exactly the ones worth seeing.
    """
    # Before anything reads the text, for the same reason as the USCIS side: an invisible
    # character inside a number would defeat the digit reads below.
    soup = BeautifulSoup(normalize_unicode(xml), "xml")

    root = soup.find("opinion")
    if root is None:
        raise ValueError("no <opinion> element — payload is not Harvard CAP XML")

    pages_removed = strip_page_markers(root)
    notes, unlabeled = collect_footnotes(root)
    marks = mark_footnote_references(root)

    lines = []
    unhandled = {}

    for child in root.children:
        # NavigableStrings: the whitespace between elements. Verified there is no
        # non-whitespace text at this level in any of the 16.
        if child.name is None:
            continue

        if child.name not in LINE_PREFIXES:
            unhandled[child.name] = unhandled.get(child.name, 0) + 1
            continue

        text = clean_text(child, JOIN)
        if text:
            lines.append(LINE_PREFIXES[child.name] + text)

    return lines, notes, {
        "pages_removed": pages_removed,
        "unlabeled_footnotes": unlabeled,
        "marks_found": marks,
        "unhandled_tags": unhandled,
    }


def is_pleading_paper(text):
    """True for a document with printed line numbers down the left margin.

    Moya is the only one, but detected rather than named: the number is what makes every
    other rule here misfire, since a bare "2" in the margin is indistinguishable from a
    footnote marker. Measured at 39% of non-empty lines against 0-2% everywhere else, so
    the threshold is nowhere near anything.
    """
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return False
    numbered = sum(1 for line in lines if PLEADING_LINE_NUMBER.match(line))
    return numbered / len(lines) > PLEADING_THRESHOLD


def head_key(line):
    """A line reduced to its shape, for asking whether it repeats across pages.

    Both normalizations are needed and each was found by one leaking without the other.
    Digits go because the page number is the part that changes. Internal whitespace goes
    because these heads are column-aligned, so the padding shifts when the number gains a
    digit: "Page: 3 of 18" and "Page: 13 of 18" are the same head printed one space apart,
    and comparing them literally leaves the whole run below the repetition floor.
    """
    return DIGITS.sub("#", re.sub(r"\s+", " ", line.strip()))


def running_heads(pages):
    """Boilerplate that repeats at the top or bottom of most pages, as normalized patterns.

    Learned per document instead of enumerated, because every court prints something
    different: "No. 22-3053 Ebu v. USCIS, et al. Page 3", "YITH V. NIELSEN 27", the
    "USCA11 Case: 21-11055 Date Filed: 08/05/2022" ECF stamp. Hardcoding ten formats would
    be ten things to get wrong, and the eleventh court would still slip through.

    Digits are normalized away first — the page number is the part that changes, so the
    literal line is unique per page and only the shape repeats.
    """
    seen = {}
    for page in pages:
        lines = [line.strip() for line in page.split("\n") if line.strip()]
        if not lines:
            continue
        # Only the outermost line at each end: a repeated line in the middle of a page is
        # prose the opinion happens to say twice, not furniture.
        for position, line in (("head", lines[0]), ("foot", lines[-1])):
            key = (position, head_key(line))
            seen[key] = seen.get(key, 0) + 1

    floor = max(RUNNING_HEAD_MIN_PAGES, len(pages) * RUNNING_HEAD_SHARE)
    return {key for key, count in seen.items() if count >= floor}


def strip_furniture(lines, heads):
    """Drop the page's own chrome, keeping everything that could be text.

    Position is load-bearing, not decoration. A lone "2" is a page number at the very top or
    bottom of a page and a footnote marker anywhere else, and they are the same three
    characters — so the page-number test only ever runs against the outermost non-empty line
    at each end. Applying it line-by-line instead deleted 52 of the 61 footnotes, silently,
    because every one of them opens with exactly the string it was looking for.
    """
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty:
        return []

    drop = set()
    for position, index in (("head", non_empty[0]), ("foot", non_empty[-1])):
        stripped = lines[index].strip()
        if (PAGE_NUMBER_LINE.match(stripped)
                or (position, head_key(stripped)) in heads):
            drop.add(index)

    # A rule of underscores is never prose, so this one is safe anywhere on the page.
    return [line for index, line in enumerate(lines)
            if index not in drop and not HORIZONTAL_RULE.match(line.strip())]


def split_page_footnotes(lines, expected):
    """(body_lines, [(number, text)], next_expected) for one page.

    The footnote block runs from its first note to the bottom of the page, so finding where
    it starts is the whole problem. There is no reliable inline marker to work back from —
    a superscript digit is indistinguishable from "§ 1429" or "697 F.3d 666" once the PDF is
    flattened — so the block is found by its own numbering instead: footnotes count upward
    across the whole opinion, and a line whose leading digit is the next number we are
    waiting for is the start of that note.

    That one condition is what makes the rule safe. Donnelly's notes sit inline as
    "1 Congress transferred authority ..." on the same shape of line as the body's
    "8 U.S.C. § 1429" — but 8 is not the number we are expecting, so only the real one
    matches. Failure is one-directional too: a note we do not recognise stays in the body
    text rather than disappearing, which is the direction to fail in.
    """
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty:
        return [], [], expected

    start = None
    for index in non_empty:
        match = NOTE_START.match(lines[index])
        # Never the first line on the page: that is a continuing paragraph, or a page number
        # printed at the top, which Miriyeva does on 19 of its 21 pages.
        if match and index != non_empty[0] and int(match.group(1)) == expected:
            start = index
            break

    if start is None:
        return lines, [], expected

    body = lines[:start]
    notes = []
    current = None

    for line in lines[start:]:
        match = NOTE_START.match(line)
        if match and int(match.group(1)) == expected:
            if current:
                notes.append(current)
            current = [expected, match.group(2) or ""]
            expected += 1
        elif current is not None and line.strip():
            current[1] += " " + line.strip()

    if current:
        notes.append(current)

    return body, [(number, text) for number, text in notes], expected


def join_wrapped(lines):
    """Hard-wrapped lines back into one run of prose.

    Paragraph breaks are not reconstructed — that was cut deliberately, and the page is the
    unit instead. Sentences do survive, which is the part that matters for retrieval.

    A line ending in a hyphen joins with no space: these PDFs wrap at hyphens that are
    already in the word ("beneficiary-\\npays model"), so keeping the hyphen and closing the
    gap is right far more often than dropping it would be.
    """
    text = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if text.endswith("-"):
            text += stripped
        elif text:
            text += " " + stripped
        else:
            text = stripped
    return re.sub(r"\s{2,}", " ", text)


def extract_pdf(plain_text):
    """PDF-derived plain text -> (lines, report).

    One line per page of prose, each followed by that page's footnotes. Page-level parking
    is deliberate: the median page is ~1,800 characters and a 300-500 token chunk is
    1,200-2,000, so a page already is roughly a chunk, and a note lands within about one
    chunk of whatever it annotates. Recovering the exact sentence was rejected as costing
    more than the placement is worth.
    """
    text = normalize_unicode(plain_text)

    # Before anything else, or every later rule reads margin numbers as content.
    pleading = is_pleading_paper(text)
    if pleading:
        text = PLEADING_LINE_NUMBER.sub("", text)

    pages = text.split(PAGE_BREAK)
    heads = running_heads(pages)

    lines = []
    notes_found = 0
    expected = 1

    for page in pages:
        kept = strip_furniture(page.split("\n"), heads)
        if not kept:
            continue

        body, notes, expected = split_page_footnotes(kept, expected)

        prose = join_wrapped(body)
        if prose:
            lines.append(prose)
        for number, note_text in notes:
            lines.append("[^ {}] {}".format(number, join_wrapped([note_text])))
        notes_found += len(notes)

    return lines, {
        "pages": len(pages),
        "pleading_paper": pleading,
        "running_heads": sorted(pattern for _, pattern in heads),
        "footnotes_kept": notes_found,
        "unhandled_tags": {},
    }


def process_opinion(payload):
    """One raw payload -> (text, report). Raises ValueError on a payload we cannot read.

    Which extractor runs is decided by the payload, not the manifest: `xml_harvard` is
    populated iff the opinion came through Harvard CAP.
    """
    xml = (payload.get("xml_harvard") or "").strip()
    if not xml:
        plain = (payload.get("plain_text") or "").strip()
        if not plain:
            raise ValueError("neither xml_harvard nor plain_text — nothing to extract")

        lines, report = extract_pdf(plain)
        report.update({
            "orphan_marks": [],
            "unplaced_footnotes": [],
            "footnotes_dropped": 0,
            "lines": len(lines),
        })
        return "\n".join(tidy_spacing(lines)), report

    body_lines, notes, report = extract_cap(xml)

    body_lines, orphans, kept, dropped = place_footnotes(
        body_lines, notes, keep_every_footnote, marker=CAP_MARKER)

    # A definition with no mark anywhere in the body cannot be placed, but it is real text
    # a judge wrote, so park it at the end rather than lose it. Haroun's note 2 is the one
    # known case: 1 of 75, and it is a substantive alternative holding.
    placed = set()
    for line in body_lines:
        placed.update(int(m.group(1)) for m in CAP_MARKER.finditer(line))
    unplaced = sorted(set(notes) - placed)
    for number in unplaced:
        body_lines.append("[^ {}] {}".format(number, notes[number]))
        kept += 1

    # Sentinels have served their purpose; the corpus format is "[N]".
    body_lines = [CAP_MARKER.sub(r" [\1]", line) for line in body_lines]
    body_lines = tidy_spacing(body_lines)

    report.update({
        "orphan_marks": sorted(orphans),
        "unplaced_footnotes": unplaced,
        "footnotes_kept": kept,
        "footnotes_dropped": dropped,
        "lines": len(body_lines),
    })
    return "\n".join(body_lines), report


def load_manifest(path):
    """opinion_id -> selection record. The payloads have no case name, court, or date."""
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found — it is the only source of case metadata.")

    with open(path) as f:
        document = json.load(f)

    records = document.get("selected") or []
    if not records:
        raise SystemExit(f"{path} has an empty 'selected' list.")

    return {record["opinion_id"]: record for record in records}


def find_opinions(input_dir):
    """Every fetched opinion as (source_id, path). Sorted so runs are comparable."""
    opinions = []
    for path in sorted(glob.glob(os.path.join(input_dir, OPINION_GLOB))):
        opinions.append((os.path.basename(path)[:-len(".json")], path))
    return opinions


def build_sidecar(source_id, record, payload, path):
    """The corpus-wide sidecar schema, filled in from the case law side.

    Same flat, nullable shape process_uscis.py emits, so build_index.py reads one schema and
    never branches on source. The fields USCIS leaves None are the ones this half exists to
    fill: court, date, citation, and the hand-verified barrier labels.
    """
    return {
        "source_id": source_id,      # the raw filename stem: opinion id first, already unique
        "source_type": "caselaw",
        "title": record["case_name"],
        "citation": record.get("citation"),   # 21 of 26; null where no reporter cite exists
        "court_id": record["court_id"],       # from the manifest, not the payload: De Dandrade's
                                              # upstream court_id is wrong and is corrected there
        "date": record["date_filed"],
        # Joined rather than kept as a list: Chroma metadata values have to be scalars, and a
        # comma split is cheaper than discovering that at index time. Several cases hold on
        # more than one barrier, so the plural has to survive somehow.
        "barrier": ",".join(record["barrier"]),
        "url": COURTLISTENER_ROOT + payload["absolute_url"],
        # The payload records CourtListener's own dates, not ours, and fetch_caselaw.py saves
        # it untouched by design — so the file's mtime is when we actually retrieved it.
        "retrieved": datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat(),
        "extracted_from": path,
    }


def write_opinion(output_dir, source_id, processed_text, sidecar):
    """Mirror the raw layout: opinion.txt beside opinion_metadata.json."""
    stem_path = os.path.join(output_dir, source_id)
    with open(stem_path + ".txt", "w") as f:
        f.write(processed_text)
    with open(stem_path + METADATA_SUFFIX, "w") as f:
        json.dump(sidecar, f, indent=2)


def print_summary(written, total, layout, excluded, failures, anomalies, kept, unhandled):
    """What happened, and loudly when the run was partial.

    Same shape as process_uscis.py's, for the same reason: a count of successes with no
    denominator hides the documents that never made it. Routine per-document counts are
    left out — the findings below are what a run is read for.
    """
    print(f"\nProcessed {written} of {total} opinion(s), {len(failures)} failed.")
    if kept:
        print(f"Footnotes: {kept} kept (case law keeps all).")

    # What the PDF half inferred, because it inferred rather than read it. A running head
    # this pass fails to learn ends up welded into the prose, and a document wrongly called
    # pleading paper loses a digit off the front of every line — both are quiet, and this is
    # where they would show.
    if layout:
        print(f"\n{len(layout)} PDF-text opinion(s), furniture learned per document:")
        for source_id in sorted(layout):
            pleading, heads = layout[source_id]
            note = " [pleading paper: margin line numbers stripped]" if pleading else ""
            print(f"  {source_id}{note}")
            for head in heads:
                print(f"      dropped: {head[:100]}")

    if excluded:
        print(f"\n{len(excluded)} opinion(s) skipped as non-Article III "
              f"({', '.join(sorted(EXCLUDED_COURTS))}):")
        for source_id, court in excluded:
            print(f"  {source_id} [{court}]")

    # Tags with no branch in extract_cap. Nothing is lost silently, but the text they hold
    # is: this is the report that found 23,028 uncaptured characters on the USCIS side.
    if unhandled:
        print("\nUnhandled top-level tags:")
        for tag in sorted(unhandled):
            print(f"  {unhandled[tag]:4}  <{tag}>")

    # Numbering that does not line up. An orphan mark points at a definition that is not
    # there; an unplaced note is a definition nothing points at.
    if anomalies:
        print(f"\nFootnote anomalies in {len(anomalies)} opinion(s):")
        for source_id in sorted(anomalies):
            orphans, unplaced = anomalies[source_id]
            print(f"  {source_id}")
            if orphans:
                print(f"      mark(s) with no definition: {orphans}")
            if unplaced:
                print(f"      definition(s) with no mark, parked at end: {unplaced}")

    if not failures:
        return

    print("\nFailures:")
    for failure in failures:
        print(f"  - {failure['source_id']}")
        print(f"      {failure['error']}")


def main():
    opinions = find_opinions(INPUT_DIR)
    if not opinions:
        print(f"No opinions in {INPUT_DIR}/. Run fetch_caselaw.py first.")
        return

    manifest = load_manifest(MANIFEST)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    failures = []
    layout = {}      # source_id -> (pleading paper?, running heads dropped) for the PDF half
    excluded = []    # non-Article III, dropped on purpose
    anomalies = {}   # source_id -> (orphan marks, unplaced definitions)
    unhandled = {}   # tag -> count, across the whole run
    written = kept = 0

    print(f"Found {len(opinions)} opinion(s) in {INPUT_DIR}/.")

    for source_id, path in opinions:
        # Skip and continue, like the scrapers: one malformed payload should not cost the
        # other 25. Nothing here touches the network, so a failure is a real bug in the
        # extractor or a payload shaped in a third way — both worth seeing all of.
        try:
            with open(path) as f:
                payload = json.load(f)

            record = manifest.get(payload["id"])
            if record is None:
                raise ValueError(f"opinion {payload['id']} is on disk but not in {MANIFEST}")

            if record["court_id"] in EXCLUDED_COURTS:
                excluded.append((source_id, record["court_id"]))
                continue

            processed_text, report = process_opinion(payload)
        except (OSError, ValueError, KeyError) as error:
            failures.append({"source_id": source_id,
                             "error": f"{error.__class__.__name__}: {error}"})
            continue

        write_opinion(OUTPUT_DIR, source_id, processed_text,
                      build_sidecar(source_id, record, payload, path))
        written += 1

        if "running_heads" in report:
            layout[source_id] = (report["pleading_paper"], report["running_heads"])
        if report["orphan_marks"] or report["unplaced_footnotes"]:
            anomalies[source_id] = (report["orphan_marks"], report["unplaced_footnotes"])
        for tag, count in report["unhandled_tags"].items():
            unhandled[tag] = unhandled.get(tag, 0) + count
        kept += report["footnotes_kept"]

    print_summary(written, len(opinions), layout, excluded, failures,
                  anomalies, kept, unhandled)


if __name__ == "__main__":
    main()
