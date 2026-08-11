"""Turn a chunk's metadata into a citation a lawyer would recognize.

Pure formatting over fields already on every chunk — no I/O, no index, no model. Lives in
its own module because the query script, the answer generator and the UI all need the same
string, and a citation that renders differently in three places is a citation nobody trusts.

Two shapes, chosen by `source_type`:
    uscis     12 USCIS-PM A.1
    caselaw   Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)

Both accept a trailing `, n.3` naming a footnote the chunk happens to contain.
"""

import re

# USCIS chapter URLs are the citation, encoded. https://.../volume-12-part-a-chapter-1 is
# `12 USCIS-PM A.1` — the sidecar `citation` field is null for all 79 chapters, and this is
# why that is a missing formatter rather than missing data. Verified 79/79 against the URLs.
USCIS_URL = re.compile(r"/policy-manual/volume-(\d+)-part-([a-z])-chapter-(\d+)")

# CourtListener's court ids to the reporter abbreviations a citation actually uses. Only the
# 18 courts in the manifest; an unmapped id prints raw rather than silently vanishing, so a
# corpus that grows announces the gap the first time you look at a result.
COURTS = {
    "ca2": "2d Cir.",
    "ca3": "3d Cir.",
    "ca5": "5th Cir.",
    "ca6": "6th Cir.",
    "ca7": "7th Cir.",
    "ca8": "8th Cir.",
    "ca9": "9th Cir.",
    "ca11": "11th Cir.",
    "cadc": "D.C. Cir.",
    "dcd": "D.D.C.",
    "mad": "D. Mass.",
    "mdd": "D. Md.",
    "nyed": "E.D.N.Y.",
    "nysd": "S.D.N.Y.",
    "nywd": "W.D.N.Y.",
    "oknd": "N.D. Okla.",
    "txwd": "W.D. Tex.",
    "vaed": "E.D. Va.",
}

# CourtListener case names are docket captions, not citation names: they carry parties' given
# names ("Mazen Shweika v. ..."), and they are inconsistent about agencies — the same corpus
# has "Dep't of Homeland Security", "U.S. Dept of Homeland Security" and the full "United
# States Citizenship and Immigration Services". So the caption is normalized here.
#
# Two rules, and the second one is a deliberate departure from citation convention:
#   - Drop parties' given names. Bluebook, and it is what makes a case name a case name.
#   - Spell institutions out, keeping only agency acronyms everyone already reads (USCIS,
#     INS). Bluebook would contract these to "Sec'y, U.S. Dep't of Homeland Sec.", which is
#     correct and less legible to the applicants this tool is for. Legibility wins; the
#     court-and-year parenthetical is doing the work of identifying the authority anyway.
#
# The map is hand-written rather than a rule because no rule survives the data: "keep the
# last word of the plaintiff" gives Diaz for Morfa Diaz, Dandrade for De Dandrade and Reis
# for Dos Reis — it invents a party name for 3 of 26, silently. Multi-word surnames carry no
# marker distinguishing them from given names, so the rule cannot be repaired, only extended
# with a list of surname particles that never ends. 26 checked lines are smaller than that
# list and cannot be subtly wrong. Each entry keeps its caption as a trailing comment so the
# normalization is verifiable without opening the manifest, and anything unmapped falls back
# to the caption verbatim: long, but never a case name that does not exist.
CASE_NAMES = {
    "opinion_1035207_mazen_shweika_v_dept_of_homeland_security":
        "Shweika v. Department of Homeland Security",  # Mazen Shweika v. Dep't of Homeland Security
    "opinion_10846400_joseph_ebu_v_uscis":
        "Ebu v. USCIS",  # Joseph Ebu v. USCIS
    "opinion_1980523_gizzo_v_immigration_naturalization_service":
        "Gizzo v. INS",  # Gizzo v. Immigration & Naturalization Service
    "opinion_2391590_rico_v_immigration_naturalization_service":
        "Rico v. INS",  # Rico v. Immigration & Naturalization Service
    "opinion_4243031_seanlim_yith_v_kirstjen_nielsen":
        "Yith v. Nielsen",  # Seanlim Yith v. Kirstjen Nielsen
    "opinion_4416725_emad_haroun_v_us_dept_of_homeland_security":
        "Haroun v. U.S. Department of Homeland Security",  # Emad Haroun v. U.S. Dept of Homeland Security
    "opinion_4565506_moya_v_united_states_department_of_homeland_security":
        "Moya v. U.S. Department of Homeland Security",  # Moya v. United States Department of Homeland Security
    "opinion_4574859_northwest_immigrant_rights_project_v_united_states_citizenship_and_immigration_services":
        "Northwest Immigrant Rights Project v. USCIS",  # Northwest Immigrant Rights Project v. United States Citizenship and Immigration Services
    "opinion_4766320_gunay_miriyeva_v_uscis":
        "Miriyeva v. USCIS",  # Gunay Miriyeva v. USCIS
    "opinion_625607_gonzalez_v_secretary_of_department_of_homeland_security":
        "Gonzalez v. Secretary of Department of Homeland Security",  # Gonzalez v. Secretary of Department of HomeLand Security
    "opinion_6349504_donnelly_v_carrp":
        "Donnelly v. CARRP",  # Donnelly v. CARRP
    "opinion_7223114_rivera_v_us_citizenship_immigration_services":
        "Rivera v. USCIS",  # Rivera v. U.S. Citizenship & Immigration Services
    "opinion_7230471_hassan_v_johnson":
        "Hassan v. Johnson",  # Hassan v. Johnson
    "opinion_7231393_martinez_v_johnson":
        "Martinez v. Johnson",  # Martinez v. Johnson
    "opinion_7238503_iqbal_v_secretary_us_department_of_homeland_security":
        "Iqbal v. Secretary, U.S. Department of Homeland Security",  # Iqbal v. Secretary U.S. Department of Homeland Security
    "opinion_7239370_dos_reis_v_mccleary":
        "Dos Reis v. McCleary",  # Dos Reis v. McCleary
    "opinion_7245087_shawuti_v_us_citizenship_immigration_services":
        "Shawuti v. USCIS",  # Shawuti v. U.S. Citizenship & Immigration Services
    "opinion_7252016_dilone_v_nielsen":
        "Dilone v. Nielsen",  # Dilone v. Nielsen
    "opinion_7252103_yemer_v_us_citizenship_immigration_servs":
        "Yemer v. USCIS",  # Yemer v. U.S. Citizenship & Immigration Servs.
    "opinion_7252751_de_dandrade_v_us_dept_of_homeland_sec":
        "De Dandrade v. U.S. Department of Homeland Security",  # De Dandrade v. U.S. Dep't of Homeland Sec.
    "opinion_7798290_hafils_akpovi_v_david_douglas":
        "Akpovi v. Douglas",  # Hafils Akpovi v. David Douglas
    "opinion_7798407_elvis_leonel_morfa_diaz_v_acting_secretary_department_of_homeland_security":
        "Morfa Diaz v. Acting Secretary, Department of Homeland Security",  # Elvis Leonel Morfa Diaz v. Acting Secretary, Department of Homeland Security
    "opinion_821040_anthony_kariuki_v_tracy_tarango":
        "Kariuki v. Tarango",  # Anthony Kariuki v. Tracy Tarango
    "opinion_8412814_aljabri_v_holder":
        "Aljabri v. Holder",  # Aljabri v. Holder
    "opinion_8709858_dar_v_olivares":
        "Dar v. Olivares",  # Dar v. Olivares
    "opinion_9781657_muhanad_alhasani_v_secretary_united_states_department_of_homeland_sec":
        "Al-Hasani v. Secretary, U.S. Department of Homeland Security",  # Muhanad Al-Hasani v. Secretary United States Department of Homeland Sec
}


def uscis_citation(url):
    """`12 USCIS-PM A.1` from a policy-manual URL, or None if the URL is not one."""
    match = USCIS_URL.search(url or "")
    if not match:
        return None
    volume, part, chapter = match.groups()
    return "{} USCIS-PM {}.{}".format(volume, part.upper(), chapter)


def court_label(court_id):
    """`ca8` -> `8th Cir.`. Unmapped ids pass through so the gap is visible in the output."""
    return COURTS.get(court_id, court_id)


def case_name(metadata):
    """The hand-checked normalized name, falling back to the CourtListener caption."""
    return CASE_NAMES.get(metadata.get("source_id")) or metadata.get("title") or "Unknown case"


def case_citation(metadata):
    """`Shweika v. Department of Homeland Security, 723 F.3d 710 (6th Cir. 2013)`.

    5 of the 26 opinions have no reporter citation and never will — too recent or
    unpublished — so the reporter is optional and the court/year parenthetical carries the
    identification on its own: `Ebu v. USCIS (6th Cir. 2025)`.
    """
    name = case_name(metadata)
    reporter = metadata.get("citation")
    court = court_label(metadata.get("court_id"))
    year = (metadata.get("date") or "")[:4]

    # Only the court and year go in parentheses; an empty parenthetical is worse than none.
    parenthetical = " ".join(part for part in (court, year) if part)
    cite = "{}, {}".format(name, reporter) if reporter else name
    return "{} ({})".format(cite, parenthetical) if parenthetical else cite


def footnote_suffix(footnote_numbers):
    """`, n.3` / `, nn.3, 4` from the comma-joined field, or "" when the chunk has none.

    A pin cite would name a page, but reporter page numbers were dropped on purpose, so the
    footnote number is the finest-grained pointer this corpus can honestly print. Chunks
    carry at most 4 notes, so there is nothing to truncate.
    """
    numbers = [n for n in (footnote_numbers or "").split(",") if n]
    if not numbers:
        return ""
    return ", {}{}".format("n." if len(numbers) == 1 else "nn.", ", ".join(numbers))


def format_citation(metadata, with_footnotes=True):
    """The citation for one chunk. Never raises and never returns empty — a result with a
    broken citation still has to be printable, since the alternative is hiding the result."""
    if metadata.get("source_type") == "uscis":
        base = uscis_citation(metadata.get("url")) or metadata.get("title") or "USCIS Policy Manual"
    else:
        base = case_citation(metadata)

    if with_footnotes:
        base += footnote_suffix(metadata.get("footnote_numbers"))
    return base
