"""Citation formatting — the strings every answer, result and UI label is judged on.

Pure formatting, so all of it is testable. Worth testing because a silent break here is
invisible in the pipeline and wrong in every single output: the tool's whole claim is that a
citation points at the passage it came from.
"""

from citations import (CASE_NAMES, case_citation, case_name, court_label, footnote_suffix,
                       format_citation, uscis_citation)


# --- uscis_citation ----------------------------------------------------------------------

def test_url_encodes_its_own_citation():
    # The sidecar `citation` field is null for all 79 chapters. This is why that was a missing
    # formatter rather than missing data — the URL already carries volume, part and chapter.
    url = "https://www.uscis.gov/policy-manual/volume-12-part-a-chapter-1"
    assert uscis_citation(url) == "12 USCIS-PM A.1"


def test_part_letter_is_uppercased_and_two_digit_chapters_survive():
    url = "https://www.uscis.gov/policy-manual/volume-1-part-e-chapter-10"
    assert uscis_citation(url) == "1 USCIS-PM E.10"


def test_a_non_chapter_url_yields_nothing():
    # The table of contents is a policy-manual URL and not a chapter, so the pattern has to
    # decline rather than half-match.
    assert uscis_citation("https://www.uscis.gov/policy-manual/table-of-contents") is None


def test_missing_url_is_not_a_crash():
    assert uscis_citation(None) is None


# --- court_label -------------------------------------------------------------------------

def test_court_id_maps_to_a_reporter_abbreviation():
    assert court_label("ca8") == "8th Cir."
    assert court_label("nysd") == "S.D.N.Y."
    assert court_label("cadc") == "D.C. Cir."


def test_an_unmapped_court_prints_raw_rather_than_vanishing():
    # The map covers only the 18 courts in the manifest. A corpus that grows should announce
    # the gap the first time someone looks at a result, not silently drop the court.
    assert court_label("ilsd") == "ilsd"


# --- case_name ---------------------------------------------------------------------------

def test_multi_word_surnames_are_why_the_map_is_hand_written():
    # "Keep the last word of the plaintiff" would give Diaz, Dandrade and Reis — inventing a
    # party name for 3 of 26, silently. Multi-word surnames carry no marker distinguishing
    # them from given names, so the rule cannot be repaired, only extended forever.
    assert case_name({"source_id": "opinion_7798407_elvis_leonel_morfa_diaz_v_acting_"
                                   "secretary_department_of_homeland_security"}).startswith("Morfa Diaz")
    assert case_name({"source_id": "opinion_7252751_de_dandrade_v_us_dept_of_homeland_sec"}
                     ).startswith("De Dandrade")
    assert case_name({"source_id": "opinion_7239370_dos_reis_v_mccleary"}) == "Dos Reis v. McCleary"


def test_given_names_are_dropped_from_the_docket_caption():
    # CourtListener stores docket captions, not citation names: "Mazen Shweika v. ...".
    name = case_name({"source_id": "opinion_1035207_mazen_shweika_v_dept_of_homeland_security"})
    assert name == "Shweika v. Department of Homeland Security"
    assert "Mazen" not in name


def test_an_unmapped_opinion_falls_back_to_its_caption():
    # Long, but never a case name that does not exist.
    assert case_name({"source_id": "opinion_999", "title": "Some v. Caption"}) == "Some v. Caption"


def test_nothing_at_all_still_returns_a_string():
    assert case_name({}) == "Unknown case"


def test_every_mapped_name_names_two_parties():
    # A cheap guard over all 26 hand-written entries: a normalization that lost the "v." lost
    # a party, which is the one way this map can be wrong without looking wrong.
    for source_id, name in CASE_NAMES.items():
        assert " v. " in name, source_id


# --- case_citation -----------------------------------------------------------------------

def test_full_citation_with_a_reporter():
    metadata = {"source_id": "opinion_1035207_mazen_shweika_v_dept_of_homeland_security",
                "citation": "723 F.3d 710", "court_id": "ca6", "date": "2013-07-25"}
    assert case_citation(metadata) == ("Shweika v. Department of Homeland Security, "
                                       "723 F.3d 710 (6th Cir. 2013)")


def test_the_five_opinions_with_no_reporter_still_cite():
    # Too recent or unpublished, and they never will have one. The court-and-year
    # parenthetical carries the identification on its own.
    metadata = {"source_id": "opinion_10846400_joseph_ebu_v_uscis",
                "citation": None, "court_id": "ca6", "date": "2025-04-16"}
    assert case_citation(metadata) == "Ebu v. USCIS (6th Cir. 2025)"


def test_no_empty_parenthetical_when_court_and_date_are_missing():
    # An empty "()" is worse than none.
    assert case_citation({"source_id": "opinion_6349504_donnelly_v_carrp"}) == "Donnelly v. CARRP"


# --- footnote_suffix ---------------------------------------------------------------------

def test_one_footnote_is_n_and_several_are_nn():
    # Legal citation distinguishes these, and getting it wrong is the kind of tell that costs
    # a reader's trust in everything else on the line.
    assert footnote_suffix("3") == ", n.3"
    assert footnote_suffix("3,4") == ", nn.3, 4"


def test_no_footnotes_adds_nothing():
    # Chroma metadata cannot hold None, so the empty case arrives as "" rather than absent.
    assert footnote_suffix("") == ""
    assert footnote_suffix(None) == ""


# --- format_citation ---------------------------------------------------------------------

def test_uscis_chunk_with_a_footnote():
    metadata = {"source_type": "uscis", "footnote_numbers": "8",
                "url": "https://www.uscis.gov/policy-manual/volume-12-part-b-chapter-4"}
    assert format_citation(metadata) == "12 USCIS-PM B.4, n.8"
    assert format_citation(metadata, with_footnotes=False) == "12 USCIS-PM B.4"


def test_caselaw_chunk_routes_to_the_case_shape():
    metadata = {"source_type": "caselaw", "footnote_numbers": "3",
                "source_id": "opinion_7798290_hafils_akpovi_v_david_douglas",
                "citation": "43 F.4th 832", "court_id": "ca8", "date": "2022-08-05"}
    assert format_citation(metadata) == "Akpovi v. Douglas, 43 F.4th 832 (8th Cir. 2022), n.3"


def test_a_broken_uscis_url_falls_back_to_the_chapter_title():
    metadata = {"source_type": "uscis", "url": "not-a-url", "title": "Chapter 4 - Fee Waivers"}
    assert format_citation(metadata) == "Chapter 4 - Fee Waivers"


def test_empty_metadata_never_raises_and_never_returns_empty():
    # A result with a broken citation still has to be printable — the alternative is hiding
    # the result, which is worse than printing a weak label.
    assert format_citation({}) == "Unknown case"
