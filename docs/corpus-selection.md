# How These 26 Opinions Were Selected

My first search was `naturalization application denied`. It returned 904,882 results, and not one of the top twenty had anything to do with immigration — Ohio utility rate cases, Nevada name changes, a Maryland bar admission. The engine had locked onto the phrase pattern "In re Application of," which turns out to be how a great many things unrelated to citizenship get captioned.

That failure is the honest starting point for this document. The USCIS half of this corpus is exhaustive: 79 chapters, every part of Volume 12 plus the relevant parts of Volume 1. The case law half can't be. CourtListener indexes millions of opinions and its free API allows 125 requests a day, so what goes in is a choice — and an undocumented choice is indistinguishable from an arbitrary one. This is that choice written down.

Selected 2026-08-08. The machine-readable list lives in [`data/caselaw_opinion_ids.json`](../data/caselaw_opinion_ids.json); nothing here was picked by taking the top N.

## Method

I ran one search per barrier type — delay, procedural, character, linguistic, and financial — because the five share almost no vocabulary. A query tuned for testing exemptions will not surface delay cases, and vice versa.

Queries were iterated in the browser, which costs nothing, and only run against the API once final. I read the top 20 of each by hand. Every case that made it in carries a stated reason, and `fetch_caselaw.py` refuses to fetch a record without one, so the corpus cannot grow silently.

Leading with statutory citations is what fixed the opening disaster. No state utility case cites 8 U.S.C. § 1447(b).

**Table 1**

*The five committed queries*

| Barrier | Query | Results |
|---|---|---|
| Delay | `"1447(b)" AND naturalization AND dateFiled:[2005-01-01 TO 2026-08-07]` | 179 |
| Procedural | `"1421(c)" AND ("de novo" OR "denial of naturalization") AND dateFiled:[2010-01-01 TO 2026-08-07]` | 111 |
| Character | `"good moral character" AND ("1101(f)" OR "1427(a)") AND dateFiled:[2010-01-01 TO 2026-08-07]` | 200 |
| Linguistic | `("1423" OR "N-648" OR "Medical Certification for Disability Exception" OR "English and civics") AND naturaliz* AND dateFiled:[2000-01-01 TO 2026-08-07]` | 44 |
| Financial | `("I-912" OR "fee waiver") AND naturaliz* AND dateFiled:[2000-01-01 TO 2026-08-07]` | 6 |

All five use `type=o` with relevance ordering, no court filter and no precedential filter. The citations already do the court filtering for me — a state court can't cite the federal delay provision. And 41% of the delay results carry CourtListener's "Unknown" status, mostly district opinions harvested without a status flag; filtering to "Published" would have gutted the district tier, which is the tier that matters most here, since denials get *de novo* review there.

The 2005 floor on delay is deliberate. The 2006–2008 FBI name-check backlog produced the canonical wave of § 1447(b) litigation, and a later floor would cut out the cases the doctrine is built on. Linguistic and financial use 2000 instead, because those spaces are sparse — widening the window helps a starving query and hurts a flooding one.

### Phrase-selectivity isn't domain-selectivity

The character query took two attempts, and the failure is the more interesting one. My first version, `("good moral character" AND naturalization) NOT "cancellation of removal"`, returned 968 results and went 3-for-21 on target. Twelve of the misses were state licensing cases: deputy sheriff certification, bar admission, accountancy, nursing, a gaming commission, a concealed carry permit.

The query wasn't broken. Those opinions genuinely do contain both phrases, because good-moral-character doctrine is shared across licensing regimes and a bar admission opinion will cite naturalization precedent as authority. Quoting a phrase only buys precision when the phrase is domain-unique, and "good moral character" is a term of art that half the administrative state has borrowed. Anchoring to §§ 1101(f) and 1427(a) took it from 3-for-21 to 7-for-20.

The linguistic query also took two attempts, for a duller reason: two of its three phrases were ones I had invented. Courts write "Medical Certification for Disability Exception," not "medical disability exception." Write the phrase the field actually uses, or you are searching for your own paraphrase.

### Bare section numbers are ambiguous

I used `"1423"` without a subsection, and it matched *United States v. Santos* — 18 U.S.C. § 1423 is a criminal statute with no relation to 8 U.S.C. § 1423 — along with *State v. Aquino*, where `115 Ohio St.3d 1423` is a page number. The other four queries use the subsection form, which can't collide with pagination or with another title of the Code.

## The Selection

Opinion IDs live in the manifest, but case name and reporter citation are the durable identifiers, since a CourtListener cluster can be renumbered or merged.

That file carries two label fields, and keeping them separate is deliberate. `found_by` is mechanical — which searches returned the case. `barrier` is judgment — what the case actually holds, cross-checked against term frequency in the full text and, for most, against the opinion itself. They agree on 18 of 26. Where they diverge, `found_by` is the one to distrust: Rico surfaced in the § 1423 query but is a good-moral-character case, and Gizzo surfaced in the delay query but cites § 1447(b) only to distinguish it.

**Table 2**

*The 26 selected opinions*

| Case | Citation | Court | Filed | Cites | Barrier | Why |
|---|---|---|---|---|---|---|
| Ebu v. USCIS | — | ca6 | 2025-04-16 | — | delay, procedural | Concurrent naturalization and removal proceedings |
| Aljabri v. Holder | 745 F.3d 816 | ca7 | 2014-03-11 | 51 | delay | Foundational: 1447(b) gives the district court exclusive jurisdiction until remand; nine-year pending application |
| Haroun v. DHS | 929 F.3d 1007 | ca8 | 2019-07-15 | 37 | delay, procedural | Same exclusive-jurisdiction holding as Aljabri; USCIS denied for lack of good moral character five days after suit |
| Seanlim Yith v. Nielsen | 881 F.3d 1155 | ca9 | 2018-02-07 | 30 | delay, procedural | Section 1429 removal proceedings do not strip the district court's power to naturalize |
| Shawuti v. USCIS | 273 F. Supp. 3d 260 | dcd | 2017-08-08 | — | delay | District-level 1447(b) |
| Dilone v. Nielsen | 358 F. Supp. 3d 490 | mdd | 2019-02-01 | 9 | delay, procedural | Interviewed, passed the English and civics tests, then waited 328 days with no decision before suing |
| Iqbal v. Sec'y DHS | 190 F. Supp. 3d 322 | nywd | 2016-06-06 | 3 | delay, procedural | Lays out the three avenues of judicial review |
| Martinez v. Johnson | 104 F. Supp. 3d 835 | txwd | 2015-05-15 | 2 | delay | Suit to compel adjudication |
| Donnelly v. CARRP | — | ca2 | 2022-06-14 | — | procedural | Exhaustion is not jurisdictional but is enforceable; lost for missing his N-336 hearing |
| Shweika v. DHS | 723 F.3d 710 | ca6 | 2013-07-25 | 26 | procedural | Whether the administrative-hearing requirement is jurisdictional; same question as Donnelly, different circuit |
| Akpovi v. Douglas | 43 F.4th 832 | ca8 | 2022-08-05 | 17 | procedural | Clean de novo review of an N-400 denial |
| Miriyeva v. USCIS | — | cadc | 2021-08-17 | — | procedural | Section 1421(c) is the exclusive route to review; forecloses APA and constitutional claims |
| Gizzo v. INS | 510 F. Supp. 2d 210 | nysd | 2007-07-10 | 3 | procedural | CIS vacated its denial mid-appeal, so no final agency denial remained. Cites 1447(b) only to distinguish it — that provision divests the agency, 1421(c) does not |
| Morfa Diaz v. Acting Sec'y DHS | — | ca11 | 2022-08-05 | — | character | Aggravated felony as a permanent GMC bar under 1101(f)(8) and 1427(a) |
| Gonzalez v. Sec'y DHS | 678 F.3d 254 | ca3 | 2012-03-19 | 196 | character, procedural | Denied on GMC grounds for false testimony at his I-751 interview; separately holds that courts can hear a denial case with removal pending, since declaratory relief is available |
| Al-Hasani v. Sec'y DHS | 81 F.4th 291 | ca3 | 2023-08-30 | 5 | character, procedural | Polygamy as a statutory bar to good moral character; the court notes he becomes eligible in 2027, five years after his 2022 divorce |
| Kariuki v. Tarango | 709 F.3d 495 | ca5 | 2013-02-21 | 108 | character, procedural | Conduct predating the application by over a year counts toward GMC; oral affirmation of false written statements is false testimony |
| Dos Reis v. McCleary | 200 F. Supp. 3d 291 | mad | 2016-08-11 | — | character | Sham marriage and GMC across the five-year statutory period |
| Rico v. INS | 262 F. Supp. 2d 6 | nyed | 2003-05-09 | 3 | character | Term scan: 19 character hits against 1 linguistic. Surfaced in the 1423 query only because the character query's 2010 floor excludes a 2003 case |
| Rivera v. USCIS | 5 F. Supp. 3d 439 | nysd | 2014-03-10 | 6 | character | District-level GMC denial |
| Dar v. Olivares | 956 F. Supp. 2d 1287 | oknd | 2013-07-25 | 1 | character | GMC finding rendering the applicant ineligible |
| Hassan v. Johnson | 93 F. Supp. 3d 457 | vaed | 2015-02-20 | — | character | GMC preclusion during the statutory period |
| Yemer v. USCIS | 359 F. Supp. 3d 423 | vaed | 2019-02-12 | 5 | character, procedural | District-level de novo merits decision |
| Moya v. DHS | 975 F.3d 120 | ca2 | 2020-09-15 | 44 | linguistic, procedural | Denied disability exemptions from the English and civics tests. Review barred until administrative exhaustion, and the Rehabilitation Act gives no cause of action against executive agencies acting as regulators — closing the disability-discrimination route |
| De Dandrade v. DHS | 367 F. Supp. 3d 174 | nysd | 2019-02-15 | 7 | linguistic, procedural | Nine plaintiffs' individual N-648 facts; the trial level of Moya |
| NWIRP v. USCIS | — | dcd | 2020-10-08 | — | financial | Enjoined the 2020 fee rule that eliminated most fee waivers and raised naturalization fees |

Eleven of the 26 carry more than one barrier label, and six of those earned the second label mechanically by appearing in more than one search — which is stronger provenance than a label I assigned by hand.

One correction worth stating plainly: CourtListener lists De Dandrade's court as S.D. Ill. That is wrong. It is S.D.N.Y., docket 1:17-cv-09604 before Judge Castel, reported at 367 F. Supp. 3d 174. The manifest hardcodes the correct court, and this is why `court_id` gets checked against the reporter citation rather than trusted.

## What I Rejected, and Why

Ten records are in the manifest's `rejected` list. They fall into four groups.

**Cases whose cite counts mislead.** Cite count measures how often a case is cited, not what it is cited *for*. *Ge v. USCIS* (12 cites) is an EAJA attorney's-fees case about who pays the lawyer. *Asemani v. USCIS* (31) is a prisoner IFP case under the PLRA three-strikes rule, where naturalization is merely the underlying action. *Kariuki v. Tarango* (108) is mostly cited for summary judgment standards — the parallel cite `84 Fed. R. Serv. 3d 1458` gives it away — though I kept it anyway, for a genuine first-impression holding on good moral character. I use cite count as a floor, never as a ranking.

**Wrong direction or wrong tribunal.** I excluded eight denaturalization cases. Those are the government stripping citizenship from someone who already has it, and surfacing that text for someone asking about their own application is actively misleading rather than merely noisy. Cancellation of removal shares the good-moral-character standard but is different relief before a different decision-maker. I excluded five BIA and Attorney General decisions, because the Board is an administrative tribunal inside DOJ with no jurisdiction over naturalization at all — *Matter of Castillo-Perez* is the close call there, since USCIS issued guidance implementing it for naturalization GMC determinations. One OLC opinion on disability accommodation came out as an executive-branch memo rather than precedent. Those last two categories are why the pipeline checks court identity at parse time instead of assuming it.

**Redundant with a stronger pick.** Where one dispute produced both a district and a circuit opinion, I generally kept only the appellate one. *De Dandrade / Moya* is the exception: the district opinion carries nine plaintiffs' individual N-648 facts and the circuit opinion is pure exhaustion doctrine, so both earn their place. *Escaler* would have been a fifth exhaustion case in a circuit already covered twice, and *Kuzova* is unpublished where *Yith* covers the Ninth Circuit better.

**One case I dropped after fetching it.** *Avdeeva v. Tucker*, 138 F.4th 641 (1st Cir. 2025) went in labeled `delay`, on a reason — "failure to adjudicate within the statutory period" — that described the underlying suit rather than the holding. That suit settled, so the First Circuit never reached the delay merits; what it actually decided was that Avdeeva is not a "prevailing party" under EAJA and cannot recover attorney's fees. Term counts across the full text: `prevailing party` 13, `EAJA` 10, `1447` 4. It is *Ge* with a different caption, and it survived my first verification pass because the caption and the docket both look exactly like a delay case. I caught it on a re-read against the rule that a label means what a case *holds*, not what it mentions. Running the same screen across the other 26 came back clean — zero hits on EAJA, prevailing-party, fees, IFP/PLRA, denaturalization, or cancellation of removal, with on-target terms leading in every one.

There is also a case I let go for a process reason rather than a substantive one. *Grey v. Alfonso-Royals*, 140 F.4th 173 (4th Cir. 2025) is a good case: sued over delay, then lost on good moral character for false deposition testimony, so it spans two barriers in a single opinion. But it surfaced in the *first, discarded* version of the character query, not the one I kept. Recovering it would have meant an ad-hoc search outside the five committed queries, leaving one opinion in the corpus that no query accounts for. Reproducibility was worth more to me than the case.

## Where Each Barrier Actually Lives

Two of the five barriers came back nearly empty, and I want to be clear that this is a finding rather than a failure.

Linguistic case law barely exists, and the reason is doctrinal. *Moya v. DHS* holds that review of a denied disability exception is barred until administrative exhaustion is complete, and failing the English or civics test gets you a re-examination rather than a cause of action. These disputes rarely reach a publishing court. Two cases isn't a gap in my searching — it's the shape of the barrier.

The financial barrier produces structural litigation rather than individual litigation. The single result is an organization challenging a fee rule, not an applicant suing over a denied I-912. Nobody litigates their own fee waiver denial; they simply don't apply, which is precisely what makes the barrier so effective and so invisible.

So the case law carries delay and procedure, and the Policy Manual carries language and money. Volume 12 Part E and Volume 1 Part B Chapter 4 cover those two, and both are already in the corpus.

## Duplicate Clusters

CourtListener sometimes holds one opinion as two clusters, and the three pairs I found take three different shapes — which is exactly why deduplication isn't a one-line rule. In the Aljabri and Shawuti pairs, one member carries a reporter citation and the other doesn't. In the Escaler pair, both carry the same one, `582 F.3d 288`. In the NWIRP pair, neither does.

Case names differ across pairs (`Salem Aljabri v. Eric Holder, Jr.` against `Aljabri v. Holder`), and docket strings differ as text while meaning the same docket (`Civil Action No. 16-2292 (TSC)` against `Civil Action No. 2016-2292`). `citeCount` differs between the Escaler duplicates too, 4 against 45, so CourtListener is splitting the citation graph across them — which means deduplication has to run *before* any ranking by cite count, not after.

## Limits

26 opinions is a working corpus, not a survey. It reflects one reading of the top 20 per query; I evaluated nothing ranked 21st or below. Coverage is uneven by design, and the unevenness tracks the doctrine rather than correcting for it. Asylum is out of scope — the USCIS naturalization volumes contain almost no asylum content, so asylum case law would make the two halves of this corpus incommensurable.

Nothing here verifies that these opinions are still good law. No free citator exists, and the realistic exposure is district-court reversal rather than outright overruling.

## Building a Different Corpus

Edit `QUERIES` in `scripts/fetch_caselaw.py` or ignore them entirely, run `venv/bin/python scripts/fetch_caselaw.py search`, read the results yourself, then replace `selected` in `data/caselaw_opinion_ids.json` with your own records — each needs an `opinion_id`, a `barrier`, and a `why`. Then run the `fetch` stage.

Committing the ID list rather than the opinion text is deliberate. CourtListener's index grows and its ranking shifts, so re-running these searches next year would produce a different corpus and quietly call it the same one. Fetching by ID doesn't drift.
