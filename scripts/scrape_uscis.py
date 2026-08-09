import requests
import re
import os
import json
import time
from bs4 import BeautifulSoup
from datetime import date
from scraping import (
    ScrapeError,
    FetchError,
    ParseError,
    make_safe_filename,
    category_for_status,
    retry_after_seconds,
    backoff_seconds,
)

BASE_URL = "https://www.uscis.gov"
TOC_URL = "https://www.uscis.gov/policy-manual/table-of-contents"
# Per-source subdirectory to separate from case law (which lands in data/raw/caselaw).
OUTPUT_DIR = "data/raw/uscis"

# Sentinel for "every part in this volume". Named so a typo is a NameError, not a silent miss.
ALL_PARTS = "all"

# What to scrape: volume label -> the parts we want, or ALL_PARTS for the whole volume.
# Adding a volume is one line; going full-manual is setting every value to ALL_PARTS.
TARGETS = {
    "Volume 12": ALL_PARTS,            # Citizenship and Naturalization — the core corpus
    "Volume 1": ["Part B", "Part E"],  # Fee waivers (part B ch 4) and adjudications
}

# TOC titles read "Volume 12 - Citizenship..." but also "Part I – Deferred Action" with an
# en dash. Capturing only the label keeps us out of the separator's business entirely.
# \b matters: without it "Volume 1" also matches "Volume 12".
TOC_LABEL_RE = re.compile(r"^(Volume\s+\d+|Part\s+[A-Z])\b")

# Identify this scraper instead of sending the default "python-requests/x.y.z" user agent
HEADERS = {
    "User-Agent": "naturalization-rag/0.1 (+https://github.com/stephliu27/naturalization-rag)"
}

# (connect, read) in seconds. USCIS answers in well under a second, so these only fire
# when something is genuinely broken.
TIMEOUT = (5, 30)

# Seconds between chapter requests. robots.txt asks for 10, which at 80 chapters would be
# 13 minutes. Raise it manually if the summary reports rate_limited or forbidden.
CRAWL_DELAY = 0

# Retry settings. Waits grow 5s, 10s, 20s.
MAX_ATTEMPTS = 4
BACKOFF_BASE = 5
MAX_BACKOFF = 60

# Statuses worth retrying: the server is overloaded or throttling us and may answer shortly.
# A 404 is a definitive answer, and a 403 is the bot wall — neither changes on a retry.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# The longest server-requested wait we will sleep through. Anything longer means come back
# later, so we stop and let the breaker end the run.
MAX_RETRY_AFTER = 60

# Circuit breaker: categories that mean the run is in trouble, not that one page is odd.
# not_found and structure_changed are absent — the server answered normally, so they cost
# one fast request and finishing tells us whether one part broke or all of them.
BREAKER_CATEGORIES = {
    "rate_limited",
    "forbidden",
    "server_error",
    "timeout",
    "connection_error",
}

# How many BREAKER_CATEGORIES failures in a row before we give up on the whole run.
# Low because each one already failed MAX_ATTEMPTS times: 3 here is ~12 failed requests.
CONSECUTIVE_FAILURE_LIMIT = 3

# Heading tag -> the marker written into the .txt. The tag itself does not survive into a
# text file, so without this a section title is indistinguishable from a paragraph — which
# is what the chunker splits on and what a citation names.
# A dict rather than "#" * int(name[1]) on purpose: an unexpected level should land in the
# unhandled report below, not silently produce a depth nothing downstream expects.
HEADING_MARKERS = {"h2": "##", "h3": "###", "h4": "####"}

# Top-level tags carrying no chapter text. Listed so they stay out of the unhandled report —
# <hr> sits above the footnotes on every page and would otherwise drown the real signal.
IGNORED_TAGS = {"hr"}

# Boxes, not content: their children are the p/ul/table we already know how to format.
# Flattening one with clean_text welds its paragraphs onto a single line, so we descend.
CONTAINER_TAGS = {"section", "div"}

# USCIS site banners arrive in the same <div> as real chapter content, so this class is the
# only thing separating them — and a class does not survive into a text file. Marked rather
# than dropped because they are not equivalent: one is a Volume 7 cross-reference worth
# discarding, another is a court order vacating the policy memos behind its own chapter.
# Processing decides; doing it here would bake a contestable call into a 105s network step.
ALERT_CLASS = "alert-message"
ALERT_PREFIX = "> "


def toc_label(title):
    """"Volume 12 - Citizenship..." -> "Volume 12"; None if the title is neither.

    Reduces a TOC heading to just its label so we can compare exactly. Simple substring matching
    would make "Volume 1" match Volume 10, 11 and 12.
    """
    match = TOC_LABEL_RE.match(title)
    if match is None:
        return None
    # Collapse odd spacing: the manual is full of non-breaking spaces.
    return re.sub(r"\s+", " ", match.group(1))


def clean_text(element):
    """Visible text with word boundaries kept, then punctuation pulled back onto its word.
    Without the " " separator inline links weld to their neighbours: "an initialForm N-648as".
    """
    text = element.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:)\]])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text)


def fetch_page(session, url):
    """Fetch one page, retrying only the failures worth retrying.

    Returns (html, attempts_used). Raises FetchError carrying a category.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = None

        try:
            response = session.get(url, timeout=TIMEOUT)
            # Explicit so we can categorize and decide on retry: 404 is final, 429 is not.
            response.raise_for_status()
            return response.text, attempt

        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            category = category_for_status(status)

            # Permanent answers (404 and friends) will not change on a retry
            if status not in RETRYABLE_STATUS:
                raise FetchError(category, f"HTTP {status}")

            wait = retry_after_seconds(error.response)

            # Honor a short Retry-After, refuse a long one. Should just fetch at a later time.
            if wait is not None and wait > MAX_RETRY_AFTER:
                raise FetchError(
                    category,
                    f"HTTP {status}, Retry-After {wait}s exceeds the {MAX_RETRY_AFTER}s we will wait"
                )

            last_error = FetchError(category, f"HTTP {status} after {attempt} attempt(s)")

        except requests.exceptions.Timeout:
            last_error = FetchError("timeout", f"timed out after {attempt} attempt(s)")

        except requests.exceptions.ConnectionError as error:
            last_error = FetchError("connection_error", f"{error.__class__.__name__} after {attempt} attempt(s)")

        # Only reached when the failure was retryable
        if attempt < MAX_ATTEMPTS:
            if wait is None:
                wait = backoff_seconds(attempt, BACKOFF_BASE, MAX_BACKOFF)
            time.sleep(wait)

    raise last_error


def extract_elements(elements, text_parts, unhandled_tags, prefix=""):
    """Format one level of the DOM into lines, descending into containers.

    Pre-order traversal: each element contributes its own lines before we look inside it, so
    document order survives. text_parts and unhandled_tags accumulate across whole recursion.

    prefix rides down so an alert marks every line beneath it, however deep it nests.
    """
    for child in elements:
        if child.name in HEADING_MARKERS:
            text = clean_text(child)
            if text:
                text_parts.append(f"{prefix}{HEADING_MARKERS[child.name]} {text}")

        elif child.name == "p":
            text = clean_text(child)
            if text:
                text_parts.append(prefix + text)

        elif child.name in ["ul", "ol"]:
            # find_all reaches the whole subtree, so nested lists are already covered here.
            # That is also why ul/ol and table must never be descended into: we would
            # extract their contents a second time.
            items = child.find_all("li")
            for item in items:
                item_text = clean_text(item)
                if item_text:
                    text_parts.append(f"{prefix}- {item_text}")  # Reformat to reflect bullet list style

        elif child.name == "table":
            # The caption is the table's title and is not a row, so find_all("tr") misses it.
            # Without it a reader — or a retrieved chunk — gets rows with nothing naming them.
            caption = child.find("caption")
            if caption:
                caption_text = clean_text(caption)
                if caption_text:
                    text_parts.append(prefix + caption_text)

            rows = child.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                cell_texts = [clean_text(cell) for cell in cells]
                row_line = "| " + " | ".join(cell_texts) + " |"   # Reformat for markdown table style
                text_parts.append(prefix + row_line)

        elif child.name in CONTAINER_TAGS:
            # Marked at the outermost alert only: an alert wraps a second div, and re-testing
            # inside would be harmless but re-prefixing an already-prefixed line would not.
            child_prefix = prefix
            if not child_prefix and ALERT_CLASS in (child.get("class") or []):
                child_prefix = ALERT_PREFIX
            extract_elements(child.find_all(recursive=False), text_parts, unhandled_tags,
                             child_prefix)

        elif child.name not in IGNORED_TAGS:
            # A set, not a list: one chapter mentioning <div> forty times is still one finding.
            unhandled_tags.add(child.name)


def parse_chapter(html):
    """Pull the readable chapter text out of a page we already fetched.

    Returns (text, unhandled_tags). unhandled_tags is every top-level tag we had no branch
    for: empty on a normal page, and evidence USCIS moved something when it is not. A parser
    that drops what it does not recognize reports 79/79 while shipping chapters with holes.

    Only called on a 200, so anything missing is a real structural surprise.
    """
    # lxml, not html.parser: USCIS ships malformed markup on some chapters and the two
    # parsers repair it differently. html.parser invents empty <body> elements and buries
    # real content inside them — 1495 chars of a court order in Vol 1 Part E Ch 8 alone.
    soup = BeautifulSoup(html, "lxml")

    guidance = soup.find("div", id="guidance")  # Find guidance container under which content lies
    if guidance is None:
        raise ParseError("structure_changed", "no 'guidance' div on a 200 page")

    body = guidance.find("div", class_="field--name-body")
    if body is None:
        raise ParseError("structure_changed", "no 'field--name-body' div inside guidance section")

    text_parts = []
    unhandled_tags = set()

    extract_elements(body.find_all(recursive=False), text_parts, unhandled_tags)

    return "\n".join(text_parts), unhandled_tags


def collect_volume_parts(toc_html, volume_label, wanted_parts):
    """Read one volume out of the table of contents into a list of parts with their chapters.

    wanted_parts is a list of labels like ["Part B"], or ALL_PARTS for the whole volume.
    """
    soup = BeautifulSoup(toc_html, "html.parser")

    volume_div = None
    volume_title = None

    for volume in soup.find_all("div", class_="level--2"):
        title_tag = volume.find("div", class_="level__title")
        title_link = title_tag.find("a") if title_tag else None
        # The TOC carries Search and Updates blocks at this level too; they have no label.
        if title_link is None:
            continue

        title = title_link.get_text(strip=True)
        if toc_label(title) == volume_label:
            volume_div = volume
            volume_title = title
            break

    if volume_div is None:
        raise ParseError("structure_changed", f"{volume_label} not found in the table of contents")

    all_parts_raw = []
    current = volume_div.find_next_sibling()

    # Parts are siblings of the volume div, not children, so walk forward and stop at the
    # next level--2 — that is where the following volume starts.
    while current is not None and "level--2" not in current.get("class", []):
        if "level--3" in current.get("class", []):
            all_parts_raw.append(current)
        current = current.find_next_sibling()

    # One dict per part, each carrying its own list of chapter dicts.
    all_parts_master = []
    seen_labels = []  # Every part label in this volume, so we can flag a target that never matched

    for part in all_parts_raw:
        part_title_tag = part.find("li", class_="level__item--3")
        if part_title_tag is None:
            raise ParseError("structure_changed", f"{volume_label}: a part has no title element")

        # Reserved and unpublished parts are listed without a link, so read the title off
        # whichever tag we have. The label still has to count as seen for the check below.
        part_link_tag = part_title_tag.find("a")
        part_title = (part_link_tag or part_title_tag).get_text(" ", strip=True)
        part_label = toc_label(part_title)
        seen_labels.append(part_label)

        if wanted_parts != ALL_PARTS and part_label not in wanted_parts:
            continue

        # An unlinked part always has zero chapters (checked across all 12 volumes), and
        # Vol 1 Part F has a link but no chapter list. Both are empty, neither is an error.
        chapter_wrapper = part.find("ul", class_="level--4")
        if part_link_tag is None or chapter_wrapper is None:
            print(f"  {volume_label} / {part_title}: no chapters listed, skipping.")
            continue

        chapters = []
        for chapter in chapter_wrapper.find_all("li", class_="level__item--4"):
            chapter_link_tag = chapter.find("a")
            if chapter_link_tag is None:
                # Reserved chapter numbers: Vol 1 Part E lists a slot for 7 with no page.
                continue
            chapters.append({
                "chapter_title": chapter_link_tag.get_text(strip=True),
                "chapter_url": BASE_URL + chapter_link_tag.get("href"),
            })

        # The volume rides on the part so save_chapter can name files and write metadata
        # without it being threaded through every call in between.
        all_parts_master.append({
            "volume_label": volume_label,  # "Volume 1" — short, used in filenames
            "volume_title": volume_title,  # full heading — used for citations
            "title": part_title,
            "url": BASE_URL + part_link_tag.get("href"),
            "chapters": chapters  # Each chapter has its own chapter_title and chapter_url
        })

    # A typo'd target would otherwise scrape nothing and still report success.
    if wanted_parts != ALL_PARTS:
        missing = [label for label in wanted_parts if label not in seen_labels]
        if missing:
            raise ParseError("structure_changed",
                             f"{volume_label}: no such part(s): {', '.join(missing)}")

    return all_parts_master


def save_chapter(part, chapter, chapter_text):
    """Write the chapter body text and its metadata sidecar."""
    # Volume leads the name because part and chapter titles repeat across volumes: both
    # Vol 1 and Vol 12 have a "Part B" and a "Chapter 1 - Purpose and Background". Without
    # it the second write silently overwrites the first.
    volume_name = make_safe_filename(part["volume_label"])
    part_name = make_safe_filename(part["title"])
    chapter_name = make_safe_filename(chapter["chapter_title"])

    stem = f"{OUTPUT_DIR}/{volume_name}_{part_name}_{chapter_name}"

    with open(f"{stem}.txt", "w") as f:
        f.write(chapter_text)

    metadata = {
        "volume_title": part["volume_title"],
        "part_title": part["title"],
        "chapter_title": chapter["chapter_title"],
        "chapter_url": chapter["chapter_url"],
        "scraped_date": str(date.today())
    }

    with open(f"{stem}_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)


def print_summary(saved, failed_chapters, retried_chapters, unhandled_tags, total, aborted):
    """Print what happened, grouped by category so the counts are diagnostic.

    An aborted run must say so: "12 saved, 3 failed" hides the 49 we never reached.
    """
    if aborted:
        skipped = total - saved - len(failed_chapters)
        print(f"\nRun ABORTED after {CONSECUTIVE_FAILURE_LIMIT} consecutive infrastructure "
              f"failures. {saved} chapter(s) saved, {len(failed_chapters)} failed, "
              f"{skipped} never attempted.")
        # We overwrite in place, so a partial run leaves fresh files next to stale ones.
        print(f"{OUTPUT_DIR}/ is now a mix of new and stale files — not a complete corpus.")
        print("Check the categories below; if they are rate_limited or forbidden, "
              "raise CRAWL_DELAY and re-run.")
    else:
        print(f"\nScrape complete. {saved} chapter(s) saved, {len(failed_chapters)} failed.")

    if retried_chapters:
        print(f"\n{len(retried_chapters)} chapter(s) needed more than one attempt:")
        for record in retried_chapters:
            print(f"  - {record['chapter_title']} (succeeded on attempt {record['attempts']})")

    # Louder than it looks: these chapters saved successfully with that content missing.
    if unhandled_tags:
        print(f"\n{len(unhandled_tags)} unhandled top-level tag(s) — that content was dropped:")
        for tag in sorted(unhandled_tags):
            chapters = unhandled_tags[tag]
            shown = ", ".join(chapters[:3]) + (", ..." if len(chapters) > 3 else "")
            print(f"  <{tag}> in {len(chapters)} chapter(s): {shown}")

    if not failed_chapters:
        return

    counts = {}
    for failure in failed_chapters:
        counts[failure["category"]] = counts.get(failure["category"], 0) + 1

    print("\nFailures by category:")
    for category in sorted(counts):
        print(f"  {counts[category]:3d}  {category}")

    print("\nDetail:")
    for failure in failed_chapters:
        print(f"  - [{failure['category']}] {failure['part_title']} / {failure['chapter_title']}")
        print(f"      {failure['url']}")
        print(f"      {failure['error']}")


def main():
    # One session for the whole run: user agent set once, one TCP connection reused across
    # every request. No retry adapter — fetch_page retries, so failures keep their status.
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        toc_html, _ = fetch_page(session, TOC_URL)
        # One TOC fetch feeds every volume — it is a single page listing all of them.
        all_parts_master = []
        for volume_label, wanted_parts in TARGETS.items():
            all_parts_master.extend(collect_volume_parts(toc_html, volume_label, wanted_parts))
    except ScrapeError as error:
        print(f"Could not read the table of contents [{error.category}]: {error}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    failed_chapters = []   # Chapters we could not save, with the reason why
    retried_chapters = []  # Chapters that succeeded but not on the first try
    unhandled_tags = {}    # Tag name -> the chapters it showed up in, so a hit is actionable
    saved = 0

    # Flatten into one work list: the breaker needs a single loop it can break out of.
    jobs = [(part, chapter) for part in all_parts_master for chapter in part["chapters"]]

    total = len(jobs)
    scope = ", ".join(
        label if parts == ALL_PARTS else f"{label} ({', '.join(parts)})"
        for label, parts in TARGETS.items()
    )
    if CRAWL_DELAY:
        print(f"Found {total} chapter(s) in {scope}. "
              f"Waiting {CRAWL_DELAY}s between requests (~{total * CRAWL_DELAY // 60} min).")
    else:
        print(f"Found {total} chapter(s) in {scope}. Fetching at full speed.")

    consecutive_failures = 0  # Reset by anything that proves the server is still answering
    aborted = False

    for part, chapter in jobs:
        if CRAWL_DELAY:
            time.sleep(CRAWL_DELAY)

        # Fetching and parsing raise the same kind of error, so one handler covers all
        # hangs, throttling, missing pages and unexpected HTML.
        try:
            html, attempts = fetch_page(session, chapter["chapter_url"])
            chapter_text, chapter_unhandled = parse_chapter(html)
        except ScrapeError as error:
            failed_chapters.append({
                "part_title": part["title"],
                "chapter_title": chapter["chapter_title"],
                "url": chapter["chapter_url"],
                "category": error.category,
                "error": str(error)
            })

            if error.category in BREAKER_CATEGORIES:
                # Consecutive is the whole signal. Failures back to back mean the pipe is broken and errors persist.
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    aborted = True
                    break
            else:
                # A not_found or a structure_changed still means the server answered us normally.
                consecutive_failures = 0

            continue

        save_chapter(part, chapter, chapter_text)
        saved += 1
        consecutive_failures = 0

        # setdefault ≈ std::map::operator[] — creates the empty list on first sight of a tag.
        for tag in chapter_unhandled:
            unhandled_tags.setdefault(tag, []).append(chapter["chapter_title"])

        if attempts > 1:
            retried_chapters.append({
                "chapter_title": chapter["chapter_title"],
                "attempts": attempts
            })

    print_summary(saved, failed_chapters, retried_chapters, unhandled_tags, total, aborted)


if __name__ == "__main__":
    main()