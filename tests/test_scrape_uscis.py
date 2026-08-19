"""Table-of-contents label parsing, which is how the scraper decides what to fetch."""

from scrape_uscis import toc_label


def test_reduces_a_volume_heading_to_its_label():
    assert toc_label("Volume 12 - Citizenship and Naturalization") == "Volume 12"


def test_reduces_a_part_heading_to_its_label():
    assert toc_label("Part B - Submission of Benefit Requests") == "Part B"


def test_volume_1_does_not_match_volume_12():
    # The reason this function exists rather than a substring check: "Volume 1" in "Volume 12"
    # is true, so naive matching pulls in Volumes 10, 11 and 12 when only Volume 1 was asked
    # for. Comparing exact labels is what makes the target list mean what it says.
    assert toc_label("Volume 1 - General Policies and Procedures") == "Volume 1"
    assert toc_label("Volume 12 - Citizenship and Naturalization") != "Volume 1"


def test_collapses_non_breaking_spaces():
    # The manual is full of them, and an unnormalized label never compares equal.
    assert toc_label("Volume  12 - Citizenship") == "Volume 12"


def test_returns_none_for_anything_else():
    # A TOC heading that is neither a volume nor a part is not a failure — the caller skips it.
    assert toc_label("Chapter 3 - Continuous Residence") is None
    assert toc_label("Appendices") is None
