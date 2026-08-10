"""Clean the scraped USCIS chapters in data/raw/uscis into data/processed/uscis.

Local and instant, unlike the scraper: re-run it as often as you like. That is the whole
reason the contestable calls live here and not in scrape_uscis.py — the scraper captures,
processing decides.

Emits the corpus-wide text format: one paragraph per line, footnote definitions as
"[^ N] ..." on the line after the paragraph citing them, "[N]" markers left in the body
so the matching digit joins the two. process_caselaw.py emits the same shape from two
completely different markups, so build_index.py never learns there were two sources.
"""

import glob
import json
import os
import re
import unicodedata

INPUT_DIR = "data/raw/uscis"
OUTPUT_DIR = "data/processed/uscis"

# Committed, unlike data/raw: Streamlit Cloud clones the repo on a cold start and cannot
# run a 30s scrape plus 26 CourtListener requests against a 125/day cap.

# Raw files come in pairs — chapter.txt next to chapter_metadata.json. This suffix is how
# we tell one from the other while globbing, so it has to match save_chapter() upstream.
METADATA_SUFFIX = "_metadata.json"

# Prefix scrape_uscis.py puts on every line of an alert-message banner. Consumed here:
# leaving it would put the literal string "[ALERT] " into the index.
ALERT_PREFIX = "[ALERT] "

# Every alert we have read, and the call we made on it. Keyed by (stem, opening words)
# rather than by chapter: a chapter can gain a second banner, and that one must reach the
# report instead of inheriting a verdict passed on different text.
#
# Recording the keeps as well as the drops is what makes the report mean "something new
# appeared." A drop-list alone would flag the five Rhode Island lines every single run, and
# a report that cries wolf five times is one you stop reading — which costs you the alert
# that actually matters. Unreviewed alerts are still KEPT, not dropped: silence is the
# failure mode we care about, and the Rhode Island block is proof an alert can be the most
# important text in its chapter.
_VOL_12_D_2 = ("volume_12_part_d_general_naturalization_requirements_chapter_2_"
               "lawful_permanent_resident_admission_for_naturalization")
_VOL_1_E_8 = "volume_1_part_e_adjudications_chapter_8_discretionary_analysis"

ALERTS_REVIEWED = {
    # Volume 7 cross-reference about TPS and adjustment of status. Site chrome pointing at
    # a volume we do not scrape, in a chapter about LPR admission. Drop.
    (_VOL_12_D_2, "ALERT: USCIS has updated Volume 7"): "drop",

    # A District of Rhode Island order vacating the policy memoranda behind this very
    # chapter. Not chrome — it is the current state of the law the chapter describes. Keep.
    (_VOL_1_E_8, "On June 5, 2026, the U.S. District Court"): "keep",
    (_VOL_1_E_8, "USCIS strongly disagrees with the Court"): "keep",
    (_VOL_1_E_8, "The Policy Memoranda and the Policy Alert"): "keep",
    (_VOL_1_E_8, "With entry of final judgment this order"): "keep",
    (_VOL_1_E_8, "USCIS will issue updated instructions"): "keep",
}


# The chapter's own footnote block, which USCIS titles singular when there is one note.
# Verified the exact shape on all 79: heading appears once, immediately above the block, and
# the block runs to EOF. Chosen over "the first [^ N] line" because this is the document
# declaring its own boundary rather than us inferring one from a line that looks like a marker.
FOOTNOTE_HEADING = re.compile(r"^#+\s+Footnotes?\s*$")

# A definition line in that block. Any other non-empty line in the block is a continuation of
# the note above it — 3 chapters wrap quoted statute across lines this way (the CSA
# "marihuana" definition, INA 321 twice).
FOOTNOTE_DEFINITION = re.compile(r"^\[\^\s*(\d+)\]\s*(.*)$")

# An inline reference in the body. Digits-only is what makes this safe: checked every
# bracketed group in all 79 bodies and no non-footnote one is all digits ([INA 312(b)],
# [12 USCIS-PM D], [Reserved], [Family Name]). The leading \s* is what stops removal from
# leaving "interview.  The" behind.
FOOTNOTE_MARKER = re.compile(r"\s*\[\s*(\d+)\s*\]")

# Citation shapes, stripped so what remains is the note speaking in its own words. Built by
# reading the corpus, not from memory — the same trap that put two invented phrases in the
# linguistic CourtListener query. Order matters: the cross-reference pattern has to run
# before the bare-bracket one, or it loses the anchor it needs.
CITATION_PATTERNS = [re.compile(pattern, re.I) for pattern in [
    # Internal cross-references always terminate in a [N USCIS-PM ...] bracket, so anchoring
    # on the bracket handles arbitrary internal commas and periods ("Children of U.S.
    # Citizens") that a comma-delimited pattern chokes on.
    r"\bsee\s+[^\[]{0,250}?\[\d+\s+USCIS-PM[^\]]*\]",
    r"\[\d+\s+USCIS-PM[^\]]*\]",
    r"\b\d+\s+USCIS-PM\s+[A-Z][\w.()]*",
    # Agency and court decisions.
    r"\bMatter\s+of\s+[^\[]{0,90}?\d+\s+I&N\s+Dec\.[^)]*\)",
    r"\b(?:In\s+re|Petition\s+of|Application\s+of)\s+[^\[]{0,60}?\d+\s+[A-Z][\w.’' ]{1,22}\s*\d+[^)]*\)",
    r"\b[A-Z][\w'’.\-]*(?:\s+[A-Z][\w'’.\-]*)*\s+v\.\s+[^\[]{0,90}?\d+\s+[A-Z][\w.’' ]{1,22}\s*\d+[^)]*\)",
    r"\b\d+\s+F\.?\s?Supp\.?\s?\d?d?\s+\d+",
    r"\b\d+\s+I&N\s+Dec\.\s+[\d,\s]+",
    # Statutes, regulations, session laws.
    r"\bINA\s+\d+[\w().\-]*",
    r"\b\d+\s+CFR\s+(?:Part\s+)?\d+[\w().\-]*",
    r"\b\d+\s+U\.?\s?S\.?\s?C\.?\s*§*\s*\d+[\w().\-]*",
    r"\bPub\.\s*L\.\s*(?:No\.\s*)?[\d\-]+",
    r"\b\d+\s+Stat\.\s+[\d,\s]+",
    r"\b\d+\s+FR\s+[\d,\-\s]+",
    r"\bH\.R\.\s*REP\.[^.]{0,40}",
    r"\b(?:\d+\s+)?U\.S\.C\.C\.A\.N\.\s*\d+",
    r"\bch\.\s*\d+",
    r"\bTitle\s+\d+\s+of\s+the\s+Code\s+of\s+Federal\s+Regulations",
    r"\bDHS\s+Delegation\s+[\d.]+",
    # Named acts: "the Nationality Act of 1940", "National Defense Authorization Act".
    r"(?:\b[A-Z][\w’'\-]*\s+){1,6}Act(?:\s+of\s+\d{4})?",
    # Manual structure cited without a bracket.
    r"\b(?:Volume|Part|Chapter|Section|Subsection|Appendix)\s+[\w\d]+(?:,\s*[^,.\[]+)*",
    # Forms, and the phrasing that exists only to point somewhere else.
    r"(?:\b[A-Z][\w’'\-]*\s+){1,7}\(Form\s+[A-Z]{1,2}-\d+\w*\)",
    r"\(Form\s+[A-Z]{1,2}-\d+\w*\)",
    r"\bForm\s+[A-Z]{1,2}-\d+\w*",
    r"\bInstructions\s+for\s+Form\b",
    r"\bFee Schedule\b",
    r"\bfor\s+(?:more\s+|further\s+|additional\s+)?(?:information|guidance|discussion|details)\b",
    r"\bwebpage\b",
    r"\bAlso\s+known\s+as\b",
    r"\b(?:quoting|citing)\b",
    r"\(PDF\)",
    r"\((?:[A-Z][a-z]+ \d{1,2}, )?\d{4}\)",
    r"\bas amended\b",
    r"\bavailable at\b",
    # Citation signals last: earlier patterns consume the "See" that introduces them.
    r"\b(?:See also|See, e\.g\.,|See, for example,|But see|See generally|See|Id\.|Cf\.|Compare|accord)\b",
]]

# Discounted after stripping. "and" sitting between two statute cites is not the note
# speaking, so counting content words is what "its own words" has to mean.
FUNCTION_WORDS = frozenset(
    "a an the of in to at on and or also see former for more information instance example "
    "s no id cf generally compare but this that these those is are was were be been it its "
    "as by with from which who whom such under provided known additional about".split()
)

# Content words a note needs before it counts as saying something of its own. Deliberately
# low: over-keeping costs one short noisy chunk, over-dropping loses policy text and cannot
# be noticed later. Hand-audited 36 notes from the drop side across three rounds, zero of
# them substantive; the keep side does admit pointer-only notes near the floor, which is the
# direction we chose. Tune in Week 4 against the eval set, not by guessing here.
SUBSTANTIVE_WORD_FLOOR = 3


def content_words(text):
    """The note's own words: citation shapes stripped, function words discounted."""
    for pattern in CITATION_PATTERNS:
        text = pattern.sub(" ", text)
    # Punctuation to spaces so "(b)(3)(iii)" leftovers cannot read as words.
    words = re.sub(r"[^\w\s]", " ", text).lower().split()
    return [w for w in words if w not in FUNCTION_WORDS and len(w) > 1]


def is_substantive(note_text):
    """True when a footnote carries reasoning rather than just pointing at authority.

    Measures residual prose, not length: "See INA 319(a). See Pub. L. 106-386 (PDF)
    (October 28, 2000). See Part H, Chapter 6..." is long and says nothing of its own.
    """
    return len(content_words(note_text)) >= SUBSTANTIVE_WORD_FLOOR


def split_footnote_block(lines):
    """(body_lines, definition_lines), splitting at the chapter's footnote heading.

    A chapter with no heading is not an error — it would be one with no footnotes at all,
    and place_footnotes reports any marker left pointing at nothing.
    """
    for index, line in enumerate(lines):
        if FOOTNOTE_HEADING.match(line):
            return lines[:index], lines[index + 1:]
    return lines, []


def parse_footnotes(definition_lines):
    """(number -> text, duplicates) from the footnote block.

    Continuation lines fold into the note above with a space, keeping one note on one line so
    the output format survives; the enumerators inside quoted statute ("(A)", "(i)") stay
    visible, so the structure is still legible.

    First definition of a number wins and any later one goes to duplicates rather than
    overwriting it — Vol 1 Part E Ch 6 numbers footnote 151 as a second 15, and silently
    clobbering the real 15 would trade a reported anomaly for an unreported one.
    """
    # Fold continuations first, resolve duplicates second. Doing both in one pass means
    # tracking which of two stores the current note lives in, and that is where the bugs live.
    records = []
    for line in definition_lines:
        match = FOOTNOTE_DEFINITION.match(line)
        if match:
            records.append([int(match.group(1)), match.group(2)])
        elif line.strip() and records:
            records[-1][1] += " " + line.strip()

    notes = {}
    duplicates = []
    for number, text in records:
        if number in notes:
            duplicates.append((number, text))
        else:
            notes[number] = text

    return notes, duplicates


def place_footnotes(body_lines, notes):
    """Move kept footnotes next to the paragraph that cites them; drop the rest with their markers.

    One pass, building a new list rather than inserting into the one being walked: a body line
    can carry up to 6 markers, and mid-iteration insertion would shift every index after it.

    Each kept note lands on the line directly after its paragraph, which at one paragraph per
    line puts note and referent in the same chunk for any sane chunk size — the matching digit
    in "[3]" and "[^ 3]" is then the whole join, with no cross-chunk lookup to build.
    """
    output = []
    orphans = set()
    kept = dropped = 0

    for line in body_lines:
        # finditer preserves document order, which is the order the notes must be emitted in.
        numbers = [int(match.group(1)) for match in FOOTNOTE_MARKER.finditer(line)]
        placed = []
        strip_markers = set()

        for number in numbers:
            text = notes.get(number)
            # A marker survives iff its footnote survives. Nothing to point at, or a note we
            # decided against, means the pointer is noise — take it out with the note.
            if text is None:
                orphans.add(number)
                strip_markers.add(number)
            elif is_substantive(text):
                placed.append((number, text))
                kept += 1
            else:
                dropped += 1
                strip_markers.add(number)

        if strip_markers:
            line = FOOTNOTE_MARKER.sub(
                lambda m: "" if int(m.group(1)) in strip_markers else m.group(0), line)

        output.append(line)
        output.extend("[^ {}] {}".format(number, text) for number, text in placed)

    return output, orphans, kept, dropped


def normalize_unicode(text):
    """Drop invisible formatting characters; leave visible punctuation alone.

    Unicode category Cf is "format" — zero-width space, zero-width joiner, soft hyphen,
    byte-order mark, the bidi marks. All invisible, and 140 U+200B survived the scraper's
    whitespace collapsing precisely because `\\s` does not match them. Left in, one can
    split a word for the embedder while looking identical to a human reading the file.

    Categories, not a literal character set: the set fixes the 140 we measured, the
    category fixes the class they belong to. Curly quotes, apostrophes and en-dashes are
    category Pi/Pf/Pd and stay — they are visible, harmless to tokenizers, and rewriting
    them would silently alter quoted statutory text.

    Leaves the spacing it disturbs to tidy_spacing: removing a character that sat between
    two spaces is what creates the debris, and one function should do one thing.
    """
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def tidy_spacing(lines):
    """Close the gaps left by removing invisible characters and footnote markers.

    Both removals are subtractive in the middle of a line, so both leave holes: dropping a
    zero-width space that sat between two real spaces welds them ("- ​ INA 342" -> "-  INA
    342"), and one at a line end leaves the space before it dangling. Marker removal does
    the same at line start. Cheap to fix here, and invisible in a diff if it is not.

    Horizontal whitespace only — \\s would eat the newlines carrying one paragraph per line.
    """
    return [re.sub(r"[ \t]{2,}", " ", line).strip() for line in lines]


def resolve_alerts(stem, lines):
    """Apply the reviewed verdicts to this chapter's alert lines.

    Returns (kept_lines, unreviewed) where kept_lines has the marker consumed and dropped
    alerts removed, and unreviewed is every alert line we have no verdict for — for the
    report, not for silent deletion.
    """
    kept = []
    unreviewed = []

    for line in lines:
        if not line.startswith(ALERT_PREFIX):
            kept.append(line)
            continue

        text = line[len(ALERT_PREFIX):]
        # Longest matching opening wins, so a specific review beats a general one if both
        # are ever present. max() over an empty sequence raises, hence the default.
        matches = [(len(opening), verdict)
                   for (s, opening), verdict in ALERTS_REVIEWED.items()
                   if s == stem and text.startswith(opening)]
        verdict = max(matches, default=(0, None))[1]

        if verdict == "drop":
            continue
        if verdict is None:
            unreviewed.append(text)
        kept.append(text)

    return kept, unreviewed


def process_chapter(stem, raw_text):
    """Raw chapter text -> cleaned text, plus whatever the run should report.

    Returns (text, report) where report is a dict of findings for print_summary. A dict
    rather than a tuple of counters so adding a finding later does not break call sites —
    the same reason parse_chapter hands back its unhandled tags instead of printing them.
    """
    # Before anything reads the text: alert prefixes are matched against normalized text,
    # and a stray zero-width space inside a marker would defeat a footnote regex. Neither
    # bites today (measured: zero Cf characters inside a bracket group) but the ordering
    # costs nothing and the failure it prevents would be silent.
    lines = normalize_unicode(raw_text).split("\n")

    lines, unreviewed_alerts = resolve_alerts(stem, lines)

    body_lines, definition_lines = split_footnote_block(lines)
    notes, duplicates = parse_footnotes(definition_lines)
    body_lines, orphans, kept, dropped = place_footnotes(body_lines, notes)

    # A duplicate number cannot be placed — its marker already belongs to the first note —
    # but the text is real content, so park it at the end rather than lose it silently. Same
    # instinct as keeping unreviewed alerts: the failure mode we care about is disappearance.
    for number, text in duplicates:
        if is_substantive(text):
            body_lines.append("[^ {}] {}".format(number, text))
            kept += 1
        else:
            dropped += 1

    body_lines = tidy_spacing(body_lines)

    return "\n".join(body_lines), {
        "unreviewed_alerts": unreviewed_alerts,
        "orphan_markers": sorted(orphans),
        "duplicate_definitions": [number for number, _ in duplicates],
        "footnotes_kept": kept,
        "footnotes_dropped": dropped,
    }


def find_chapters(input_dir):
    """Every raw chapter as (stem, txt_path, metadata_path). Sorted so runs are comparable.

    Pairs are matched by filename, so a .txt whose sidecar never got written shows up here
    with a path that does not exist — read_chapter reports that rather than guessing.
    """
    chapters = []
    for txt_path in sorted(glob.glob(os.path.join(input_dir, "*.txt"))):
        stem = os.path.basename(txt_path)[:-len(".txt")]
        chapters.append((stem, txt_path, os.path.join(input_dir, stem + METADATA_SUFFIX)))
    return chapters


def read_chapter(txt_path, metadata_path):
    """Raw chapter text plus its scraper sidecar. Raises FileNotFoundError on a broken pair."""
    with open(txt_path) as f:
        raw_text = f.read()
    with open(metadata_path) as f:
        scrape_metadata = json.load(f)
    return raw_text, scrape_metadata


def build_sidecar(stem, scrape_metadata, txt_path):
    """The corpus-wide sidecar schema, filled in from the USCIS side.

    Flat and nullable rather than nested or per-source, so build_index.py reads one shape
    and never branches on where a document came from. Fields case law fills and USCIS
    cannot are explicitly None, not absent — a missing key is a KeyError downstream, and
    "this source has no court" is a fact worth stating.
    """
    return {
        "source_id": stem,          # the raw filename stem: already unique, already stable
        "source_type": "uscis",
        "title": " / ".join([scrape_metadata["volume_title"],
                             scrape_metadata["part_title"],
                             scrape_metadata["chapter_title"]]),
        "citation": None,           # TODO decide: the "12 USCIS-PM B.3" form is real and the
                                    # corpus cites itself that way. Week 4 enforces citations.
        "court_id": None,           # not a court
        "date": None,               # effective_date was cut; scraped_date answers reproducibility
        "barrier": None,            # USCIS has no labels — that is Week 5's actual work
        "url": scrape_metadata["chapter_url"],
        "retrieved": scrape_metadata["scraped_date"],
        "extracted_from": txt_path,  # which raw file this text came from
    }


def write_chapter(output_dir, stem, processed_text, sidecar):
    """Mirror the raw layout: chapter.txt beside chapter_metadata.json."""
    stem_path = os.path.join(output_dir, stem)
    with open(stem_path + ".txt", "w") as f:
        f.write(processed_text)
    with open(stem_path + METADATA_SUFFIX, "w") as f:
        json.dump(sidecar, f, indent=2)


def print_summary(written, failures, total, unreviewed_alerts, anomalies, kept, dropped):
    """What happened, and loudly when the run was partial.

    Same shape as scrape_uscis.py's summary for the same reason: a count of successes with
    no denominator hides the chapters that never made it.

    Reports what changed or looks wrong, not what worked. Per-chapter counts of routine
    work are left out deliberately — 79 lines of noise is a report you stop reading, and
    that costs you the two findings below.
    """
    print(f"\nProcessed {written} of {total} chapter(s), {len(failures)} failed.")

    # One aggregate line, not a per-chapter breakdown. 79 rows of routine counts is a report
    # you stop reading, and that costs you the findings below. A number that drifts between
    # runs is the useful signal.
    total_notes = kept + dropped
    if total_notes:
        print(f"Footnotes: {kept} kept, {dropped} dropped as citation-only "
              f"({kept / total_notes:.0%} kept).")

    # Numbering that does not add up. Both known cases are one USCIS typo in Vol 1 Part E
    # Ch 6, seen from each end: footnote 151 is published as a second 15.
    if anomalies:
        print(f"\nFootnote numbering anomalies in {len(anomalies)} chapter(s):")
        for stem in sorted(anomalies):
            orphans, duplicates = anomalies[stem]
            detail = []
            if orphans:
                detail.append(f"marker(s) with no definition: {orphans}")
            if duplicates:
                detail.append(f"number(s) defined twice: {duplicates}")
            print(f"  {stem}")
            for line in detail:
                print(f"      {line}")

    # Louder than it looks: these were KEPT, so they are in the processed text and heading
    # for the index. A new USCIS banner arriving as ordinary prose is the failure the
    # keep-and-report rule exists to catch.
    if unreviewed_alerts:
        print(f"\n{sum(len(v) for v in unreviewed_alerts.values())} unreviewed alert line(s) "
              f"in {len(unreviewed_alerts)} chapter(s) — kept, pending a keep/drop call:")
        for stem in sorted(unreviewed_alerts):
            print(f"  {stem}")
            for text in unreviewed_alerts[stem]:
                print(f"      {text[:110]}")

    if not failures:
        return

    print("\nFailures:")
    for failure in failures:
        print(f"  - {failure['stem']}")
        print(f"      {failure['error']}")


def main():
    chapters = find_chapters(INPUT_DIR)
    if not chapters:
        print(f"No chapters in {INPUT_DIR}/. Run scrape_uscis.py first.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    failures = []
    unreviewed_alerts = {}  # stem -> the alert lines we have no verdict for
    anomalies = {}          # stem -> (orphan markers, numbers defined twice)
    written = kept = dropped = 0

    print(f"Found {len(chapters)} chapter(s) in {INPUT_DIR}/.")

    for stem, txt_path, metadata_path in chapters:
        # Skip and continue, like the scraper: one malformed chapter should not cost the
        # other 78. Nothing here touches the network, so a failure is a real bug in our
        # cleaning or a raw file that was never written — both worth seeing all of.
        try:
            raw_text, scrape_metadata = read_chapter(txt_path, metadata_path)
            processed_text, report = process_chapter(stem, raw_text)
        except (OSError, ValueError, KeyError) as error:
            failures.append({"stem": stem, "error": f"{error.__class__.__name__}: {error}"})
            continue

        write_chapter(OUTPUT_DIR, stem, processed_text,
                      build_sidecar(stem, scrape_metadata, txt_path))
        written += 1

        if report["unreviewed_alerts"]:
            unreviewed_alerts[stem] = report["unreviewed_alerts"]
        if report["orphan_markers"] or report["duplicate_definitions"]:
            anomalies[stem] = (report["orphan_markers"], report["duplicate_definitions"])
        kept += report["footnotes_kept"]
        dropped += report["footnotes_dropped"]

    print_summary(written, failures, len(chapters), unreviewed_alerts,
                  anomalies, kept, dropped)


if __name__ == "__main__":
    main()
