"""USCIS-side cleaning: the footnote keep/drop rule, the block boundary, and alert verdicts.

The footnote cases below are the ones from the hand audit. They are here because the rule is
a judgment call — the tests are what stop a later tweak to CITATION_PATTERNS from quietly
reversing it.
"""

from process_uscis import (ALERTS_REVIEWED, ALERT_PREFIX, content_words, is_substantive,
                           parse_footnotes, resolve_alerts, split_footnote_block)


def one_reviewed(verdict):
    """A (stem, opening) key carrying the given verdict, read out of the table itself.

    Derived rather than hardcoded so that editing the table breaks the table, not the test.
    """
    return next(key for key, value in ALERTS_REVIEWED.items() if value == verdict)


# --- content_words / is_substantive ------------------------------------------------------

def test_bare_citation_has_no_words_of_its_own():
    # The whole basis of the rule: strip the citation shapes and judge what is left.
    assert content_words("See 8 CFR 318.1.") == []
    assert is_substantive("See 8 CFR 318.1.") is False


def test_a_long_note_can_still_say_nothing():
    # Why the rule measures residual prose rather than length. This one is 95 characters and
    # is pure pointer; a length threshold calls it substantive.
    note = ("See INA 319(a). See Pub. L. 106-386 (PDF) (October 28, 2000). "
            "See Part H, Chapter 6, Spousal Abuse.")
    assert content_words(note) == []
    assert is_substantive(note) is False


def test_reasoning_survives_the_strip():
    note = "Marriage must have existed at the time of birth."
    assert is_substantive(note) is True
    assert "marriage" in content_words(note)


def test_internal_cross_reference_is_a_citation():
    assert is_substantive("See 12 USCIS-PM D.2.") is False


def test_function_words_do_not_count_as_content():
    # "and" sitting between two statute cites is not the note speaking.
    assert content_words("See INA 319(a) and 8 CFR 318.1.") == []


# --- split_footnote_block ---------------------------------------------------------------

def test_split_at_the_plural_heading():
    lines = ["Body paragraph.", "## Footnotes", "[^ 1] A note."]
    body, definitions = split_footnote_block(lines)
    assert body == ["Body paragraph."]
    assert definitions == ["[^ 1] A note."]


def test_split_at_the_singular_heading():
    # USCIS writes "Footnote" when the chapter has exactly one. Matching only the plural
    # looks like 7 chapters failing to parse.
    body, definitions = split_footnote_block(["Body.", "## Footnote", "[^ 1] A note."])
    assert body == ["Body."]
    assert definitions == ["[^ 1] A note."]


def test_no_heading_means_no_footnotes_not_an_error():
    lines = ["Body paragraph.", "Another paragraph."]
    assert split_footnote_block(lines) == (lines, [])


# --- parse_footnotes --------------------------------------------------------------------

def test_continuation_lines_fold_into_the_note_above():
    # Three chapters wrap quoted statute across lines. One note has to stay on one line or
    # the one-paragraph-per-line output format breaks.
    notes, duplicates = parse_footnotes(
        ["[^ 1] The term means", "any of the following:", "[^ 2] Second note."])
    assert notes == {1: "The term means any of the following:", 2: "Second note."}
    assert duplicates == []


def test_first_definition_of_a_number_wins():
    # Vol 1 Part E Ch 6 numbers footnote 151 as a second 15. Clobbering the real 15 would
    # trade a reported anomaly for an unreported one.
    notes, duplicates = parse_footnotes(["[^ 15] The real note.", "[^ 15] The mislabeled one."])
    assert notes == {15: "The real note."}
    assert duplicates == [(15, "The mislabeled one.")]


def test_spacing_inside_the_marker_is_tolerated():
    notes, _ = parse_footnotes(["[^15] No space.", "[^  3] Two spaces."])
    assert notes == {15: "No space.", 3: "Two spaces."}


# --- resolve_alerts ---------------------------------------------------------------------

def test_a_drop_verdict_removes_the_alert():
    stem, opening = one_reviewed("drop")
    kept, unreviewed = resolve_alerts(stem, ["Body.", ALERT_PREFIX + opening + " to Volume 7."])
    assert kept == ["Body."]
    assert unreviewed == []


def test_a_keep_verdict_keeps_the_text_and_consumes_the_marker():
    stem, opening = one_reviewed("keep")
    kept, unreviewed = resolve_alerts(stem, [ALERT_PREFIX + opening + " ordered relief."])
    assert kept == [opening + " ordered relief."]
    assert unreviewed == []


def test_an_unreviewed_alert_is_kept_and_reported():
    # Kept, not dropped: silence is the failure mode we care about, and the Rhode Island
    # block is proof an alert can be the most important text in its chapter.
    kept, unreviewed = resolve_alerts(
        "some_chapter_we_have_not_seen", [ALERT_PREFIX + "A banner nobody has read yet."])
    assert kept == ["A banner nobody has read yet."]
    assert unreviewed == ["A banner nobody has read yet."]


def test_a_verdict_does_not_leak_across_chapters():
    # Verdicts are keyed by (stem, opening) because a chapter can gain a second banner, and
    # that one must reach the report rather than inherit a verdict passed on different text.
    _, opening = one_reviewed("drop")
    kept, unreviewed = resolve_alerts("a_different_chapter", [ALERT_PREFIX + opening])
    assert kept == [opening]
    assert unreviewed == [opening]
