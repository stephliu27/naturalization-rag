"""Shared scrape and processing infrastructure. Pure functions only, no network and no
filesystem.

Fetching stays per-source: USCIS is anonymous HTML at full speed, CourtListener is
token-authed JSON where a 429 is routine.

The text helpers below live here rather than in one of the processors because both
processors need them: USCIS and case law arrive in unrelated markup and converge on the
same output format, so the functions that shape that format are the shared half.
"""

import re
import unicodedata


class ScrapeError(Exception):
    """One failure type for the whole scrape, so the main loop catches one thing.

    The category, not just the message, is what the breaker and the summary act on.
    """

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class FetchError(ScrapeError):
    """Could not get the resource: network problem, timeout, or an error status."""


class ParseError(ScrapeError):
    """Got a real response, but it was not shaped the way we expect."""


def make_safe_filename(text):
    """Lowercase, underscores, alphanumerics only."""
    text = text.lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def category_for_status(status):
    """Translate an HTTP status code into one of our failure categories."""
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "client_error"


def retry_after_seconds(response):
    """Seconds the server asked us to wait (429, 503), or None if it did not say."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        # Clamped: time.sleep rejects a negative, so a malformed header would crash the run.
        return max(0, int(value))
    except ValueError:
        # Retry-After may also be an HTTP date; fall back to our own backoff in that case.
        return None


def backoff_seconds(attempt, base, maximum):
    """How long to wait before attempt N when the server gave us no guidance.

    base and maximum are parameters, not constants — each scraper tunes its own.
    """
    return min(base * (2 ** (attempt - 1)), maximum)


# An inline footnote reference in body text. Digits-only is what makes this safe on the
# USCIS side: checked every bracketed group in all 79 bodies and no non-footnote one is all
# digits ([INA 312(b)], [12 USCIS-PM D], [Reserved]). Case law never emits a bracket except
# through this module, so it is safe there by construction. The leading \s* is what stops
# removal from leaving "interview.  The" behind.
FOOTNOTE_MARKER = re.compile(r"\s*\[\s*(\d+)\s*\]")


def clean_text(element, separator=" "):
    """Visible text with word boundaries kept, then punctuation pulled back onto its word.
    Without the " " separator inline links weld to their neighbours: "an initialForm N-648as".

    The separator is a parameter because the two markups disagree about whose job the space
    is. USCIS HTML drops it at tag boundaries, so " " has to put it back — 1293 welds on the
    first scrape. Harvard CAP keeps it in the text nodes, so " " instead *inserts* one that
    was never there and splits words the reporter broke across a page or an italic run:
    "Castracani" -> "Castr acani", "because" -> "be cause". Faithful rendering there means
    adding nothing, and the two callers pass what their source needs.

    strip follows the separator rather than being its own argument, because the two are one
    decision. get_text strips each string *before* joining, so "" plus stripping deletes the
    very whitespace it was supposed to trust — CAP pretty-prints its markup, and the pair
    turned 331 sentence boundaries into "naturalization.See8 U.S.C.". Put a separator in and
    you must strip; trust the source and you must not.
    """
    text = element.get_text(separator, strip=bool(separator))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:)\]])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text).strip()


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


def place_footnotes(body_lines, notes, keep, marker=FOOTNOTE_MARKER):
    """Move kept footnotes next to the paragraph that cites them; drop the rest with their markers.

    One pass, building a new list rather than inserting into the one being walked: a body line
    can carry up to 6 markers, and mid-iteration insertion would shift every index after it.

    Each kept note lands on the line directly after its paragraph, which at one paragraph per
    line puts note and referent in the same chunk for any sane chunk size — the matching digit
    in "[3]" and "[^ 3]" is then the whole join, with no cross-chunk lookup to build.

    `keep` is a predicate on the note text, not a constant, because the two halves of the
    corpus decided differently: USCIS drops the 47% that are bare citations, case law keeps
    everything because judicial footnotes carry alternative holdings. Shared format, per-half
    policy — passing the predicate in is what keeps that a caller's decision.

    `marker` is a parameter for a narrower reason: USCIS ships "[N]" in its own raw text, so
    that is the only marker it can have, but case law knows its marks from the markup and
    passes a sentinel instead — two opinions write bracketed digits of their own that this
    function would otherwise delete. See CAP_MARKER in process_caselaw.py.
    """
    output = []
    orphans = set()
    kept = dropped = 0

    for line in body_lines:
        # finditer preserves document order, which is the order the notes must be emitted in.
        numbers = [int(match.group(1)) for match in marker.finditer(line)]
        placed = []
        strip_markers = set()

        for number in numbers:
            text = notes.get(number)
            # A marker survives iff its footnote survives. Nothing to point at, or a note we
            # decided against, means the pointer is noise — take it out with the note.
            if text is None:
                orphans.add(number)
                strip_markers.add(number)
            elif keep(text):
                placed.append((number, text))
                kept += 1
            else:
                dropped += 1
                strip_markers.add(number)

        if strip_markers:
            line = marker.sub(
                lambda m: "" if int(m.group(1)) in strip_markers else m.group(0), line)

        output.append(line)
        output.extend("[^ {}] {}".format(number, text) for number, text in placed)

    return output, orphans, kept, dropped
