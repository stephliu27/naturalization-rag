"""Shared scrape and processing helpers. Pure functions, no network and no filesystem.

Where a case came out of a real audit, the docstring says so — the point of encoding those is
that the test documents why the function is shaped this way, not just that it still runs.
"""

from bs4 import BeautifulSoup

from scraping import (backoff_seconds, category_for_status, clean_text, make_safe_filename,
                      normalize_unicode, place_footnotes, retry_after_seconds, tidy_spacing)


def paragraph(html):
    """A <p> element, since clean_text takes a node rather than a string."""
    return BeautifulSoup(html, "lxml").p


class FakeResponse:
    """Only .headers is read, so a dict is the whole interface."""

    def __init__(self, headers):
        self.headers = headers


# --- make_safe_filename ------------------------------------------------------------------

def test_make_safe_filename_lowercases_and_underscores():
    assert make_safe_filename("Volume 12 Part D") == "volume_12_part_d"


def test_make_safe_filename_drops_punctuation_and_collapses_runs():
    # Chapter titles carry commas, colons and parentheses; a run of them must not become a
    # run of underscores, or two chapters differing only in punctuation collide on disk.
    assert make_safe_filename("Spouses (Form N-400): Filing") == "spouses_form_n400_filing"


def test_make_safe_filename_strips_edges():
    assert make_safe_filename("  - Purpose and Background - ") == "purpose_and_background"


# --- category_for_status -----------------------------------------------------------------

def test_category_for_status_maps_each_category():
    assert category_for_status(403) == "forbidden"
    assert category_for_status(404) == "not_found"
    assert category_for_status(429) == "rate_limited"
    assert category_for_status(503) == "server_error"
    assert category_for_status(400) == "client_error"


def test_category_for_status_treats_all_5xx_as_server_error():
    # The circuit breaker acts on the category, so an unfamiliar 5xx has to land in the
    # retryable bucket rather than fall through to client_error.
    assert category_for_status(599) == "server_error"


# --- retry_after_seconds -----------------------------------------------------------------

def test_retry_after_seconds_reads_an_integer_header():
    assert retry_after_seconds(FakeResponse({"Retry-After": "30"})) == 30


def test_retry_after_seconds_none_when_absent():
    assert retry_after_seconds(FakeResponse({})) is None


def test_retry_after_seconds_none_on_http_date():
    # Retry-After is also allowed to be a date. Returning None hands the caller back to its
    # own backoff instead of crashing on int().
    assert retry_after_seconds(FakeResponse({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None


def test_retry_after_seconds_clamps_negative():
    # time.sleep rejects a negative, so a malformed header would take the run down.
    assert retry_after_seconds(FakeResponse({"Retry-After": "-5"})) == 0


# --- backoff_seconds ---------------------------------------------------------------------

def test_backoff_seconds_doubles_then_clamps():
    assert [backoff_seconds(n, 5, 60) for n in (1, 2, 3, 4, 5)] == [5, 10, 20, 40, 60]


def test_backoff_seconds_takes_its_bounds_from_the_caller():
    # base and maximum are arguments because each scraper tunes its own: CourtListener's
    # 429 is routine where USCIS's is a surprise.
    assert backoff_seconds(3, 1, 100) == 4


# --- clean_text --------------------------------------------------------------------------

def test_clean_text_separator_unwelds_inline_links():
    # The 1,293-occurrence bug: get_text(strip=True) concatenates descendants with nothing
    # between them, so any paragraph with an inline link came out as "an initialForm N-648as"
    # — tokens no embedding model has ever seen.
    element = paragraph('<p>an initial <a href="#">Form N-648</a> as</p>')
    assert clean_text(element, " ") == "an initial Form N-648 as"


def test_clean_text_empty_separator_does_not_split_words():
    # The mirror-image failure on the case law side. Harvard CAP keeps the spaces in its text
    # nodes, so passing " " *inserts* one the source never had and splits a word the reporter
    # broke across an italic run: "Castracani" -> "Castr acani".
    element = paragraph("<p>Castr<em>acani</em> was denied</p>")
    assert clean_text(element, "") == "Castracani was denied"


def test_clean_text_pulls_punctuation_back():
    element = paragraph("<p>the applicant <em>must</em> , however , file</p>")
    assert clean_text(element, " ") == "the applicant must, however, file"


# --- normalize_unicode -------------------------------------------------------------------

def test_normalize_unicode_drops_invisible_formatting():
    # 140 zero-width spaces survived the scraper's whitespace collapsing because \s does not
    # match them. Left in, one splits a word for the embedder while looking identical here.
    assert normalize_unicode("INA​ 342") == "INA 342"


def test_normalize_unicode_keeps_visible_punctuation():
    # Curly quotes and en-dashes are visible, harmless to tokenizers, and rewriting them
    # would silently alter quoted statutory text.
    text = "the “best” — INA 319–320"
    assert normalize_unicode(text) == text


# --- tidy_spacing ------------------------------------------------------------------------

def test_tidy_spacing_closes_holes_and_strips():
    # Removing a character that sat between two spaces is what creates the debris.
    assert tidy_spacing(["-   INA 342  ", " a  b"]) == ["- INA 342", "a b"]


def test_tidy_spacing_leaves_single_spaces_alone():
    assert tidy_spacing(["one two three"]) == ["one two three"]


# --- place_footnotes ---------------------------------------------------------------------

def keep_substantive(text):
    """Stand-in for the USCIS policy: a note that only points at authority is noise."""
    return "See" not in text


def test_place_footnotes_puts_a_kept_note_on_the_next_line():
    # One paragraph per line means "the next line" is what lands note and referent in the
    # same chunk, which is the whole reason the join is just a matching digit.
    body, orphans, kept, dropped = place_footnotes(
        ["Text with a note.[3]"], {3: "Substantive reasoning here."}, keep_substantive)
    assert body == ["Text with a note.[3]", "[^ 3] Substantive reasoning here."]
    assert (kept, dropped, orphans) == (1, 0, set())


def test_place_footnotes_drops_a_bare_citation_with_its_marker():
    # A marker survives iff its footnote survives, so nothing is left dangling.
    body, _, kept, dropped = place_footnotes(
        ["Text with a note.[7]"], {7: "See 8 CFR 318.1."}, keep_substantive)
    assert body == ["Text with a note."]
    assert (kept, dropped) == (0, 1)


def test_place_footnotes_reports_an_orphan_marker_and_strips_it():
    # A marker pointing at nothing is a pointer to noise. Reported, not silently kept.
    body, orphans, _, _ = place_footnotes(
        ["Text with a note.[9]"], {}, keep_substantive)
    assert body == ["Text with a note."]
    assert orphans == {9}


def test_place_footnotes_keeps_document_order_on_a_multi_marker_line():
    # A body line carries up to 6 markers, and finditer order is the order they must emit in.
    body, _, _, _ = place_footnotes(
        ["First.[2] Second.[1]"], {1: "Note one text.", 2: "Note two text."}, keep_substantive)
    assert body == ["First.[2] Second.[1]", "[^ 2] Note two text.", "[^ 1] Note one text."]
