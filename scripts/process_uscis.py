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
    """
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


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

    TODO footnotes, next commit. In order:
      1. Split body from block at the "## Footnote" / "## Footnotes" heading.
      2. Parse the block to number -> text, folding continuation lines into the note above.
      3. Classify each note substantive vs bare cite (residual prose, biased toward keeping).
      4. Kept note -> onto the line after the paragraph carrying its marker, marker left in
         place; a body line can carry up to 6 markers.
      5. Dropped note -> its marker goes too, without leaving "interview.  The".
      6. Report markers with no definition and numbers defined twice (Vol 1 Part E Ch 6 has
         both — USCIS mis-numbered footnote 151 as a second 15).
    """
    # Before anything reads the text: alert prefixes are matched against normalized text,
    # and a stray zero-width space inside a marker would defeat a footnote regex. Neither
    # bites today (measured: zero Cf characters inside a bracket group) but the ordering
    # costs nothing and the failure it prevents would be silent.
    lines = normalize_unicode(raw_text).split("\n")

    lines, unreviewed_alerts = resolve_alerts(stem, lines)

    return "\n".join(lines), {"unreviewed_alerts": unreviewed_alerts}


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


def print_summary(written, failures, total, unreviewed_alerts):
    """What happened, and loudly when the run was partial.

    Same shape as scrape_uscis.py's summary for the same reason: a count of successes with
    no denominator hides the chapters that never made it.

    Reports what changed or looks wrong, not what worked. Per-chapter counts of routine
    work are left out deliberately — 79 lines of noise is a report you stop reading, and
    that costs you the two findings below.
    """
    print(f"\nProcessed {written} of {total} chapter(s), {len(failures)} failed.")

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
    written = 0

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

    print_summary(written, failures, len(chapters), unreviewed_alerts)


if __name__ == "__main__":
    main()
