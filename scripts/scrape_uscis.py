import requests
import re
import os
import json
import time
from bs4 import BeautifulSoup
from datetime import date

BASE_URL = "https://www.uscis.gov"
TOC_URL = "https://www.uscis.gov/policy-manual/table-of-contents"
OUTPUT_DIR = "data/raw"

# Identify this scraper instead of sending the default "python-requests/x.y.z" user agent
HEADERS = {
    "User-Agent": "naturalization-rag/0.1 (+https://github.com/stephliu27/naturalization-rag)"
}

# (connect, read) in seconds. USCIS answers in well under a second, so these only fire
# when something is genuinely broken.
TIMEOUT = (5, 30)

# Seconds between chapter requests. robots.txt asks for 10, which would turn a 64-chapter
# run into 11 minutes. Raise it manually if the summary reports rate_limited or forbidden.
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


class ScrapeError(Exception):
    """One failure type for the whole scrape, so the main loop catches one thing.

    The category, not just the message, is what the breaker and the summary act on.
    """

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class FetchError(ScrapeError):
    """Could not get the page: network problem, timeout, or an error status."""


class ParseError(ScrapeError):
    """Got a real page, but it was not shaped the way we expect."""


def make_safe_filename(text):
    """Lowercase, underscores, alphanumerics only."""
    text = text.lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def clean_text(element):
    """Visible text with word boundaries kept, then punctuation pulled back onto its word.
    Without the " " separator inline links weld to their neighbours: "an initialForm N-648as".
    """
    text = element.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:)\]])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text)


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


def backoff_seconds(attempt):
    """How long to wait before attempt N when the server gave us no guidance."""
    return min(BACKOFF_BASE * (2 ** (attempt - 1)), MAX_BACKOFF)


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
                wait = backoff_seconds(attempt)
            time.sleep(wait)

    raise last_error


def parse_chapter(html):
    """Pull the readable chapter text out of a page we already fetched.

    Only called on a 200, so anything missing is a real structural surprise.
    """
    soup = BeautifulSoup(html, "html.parser")

    guidance = soup.find("div", id="guidance")  # Find guidance container under which content lies
    if guidance is None:
        raise ParseError("structure_changed", "no 'guidance' div on a 200 page")

    body = guidance.find("div", class_="field--name-body")
    if body is None:
        raise ParseError("structure_changed", "no 'field--name-body' div inside guidance section")

    text_parts = []

    # Format by tag: paragraphs plain, list items bulleted, tables as markdown rows.
    for child in body.find_all(recursive=False):
        if child.name in ["p", "h2", "h3", "h4", "section"]:
            text = clean_text(child)
            if text:
                text_parts.append(text)

        elif child.name in ["ul", "ol"]:
            items = child.find_all("li")
            for item in items:
                item_text = clean_text(item)
                if item_text:
                    text_parts.append(f"- {item_text}")  # Reformat to reflect bullet list style

        elif child.name == "table":
            rows = child.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                cell_texts = [clean_text(cell) for cell in cells]
                row_line = "| " + " | ".join(cell_texts) + " |"   # Reformat for markdown table style
                text_parts.append(row_line)

    return "\n".join(text_parts)


def collect_volume_12_parts(toc_html):
    """Read the table of contents into a list of parts, each with its chapters."""
    soup = BeautifulSoup(toc_html, "html.parser")

    # Loop through all level 2 divs to find volume 12 (Citizenship and Naturalization)
    all_volumes = soup.find_all("div", class_="level--2")
    volume_12 = None

    for volume in all_volumes:
        title_tag = volume.find("div", class_="level__title")
        title_link = title_tag.find("a")
        if "Volume 12" in title_link.get_text(strip=True):
            volume_12 = volume
            break

    if volume_12 is None:
        raise ParseError("structure_changed", "Volume 12 not found in the table of contents")

    all_parts_raw = []
    current = volume_12.find_next_sibling()

    # Parts are siblings of the volume div, not children, so walk forward and stop at the
    # next level--2 — that is where the following volume starts.
    while current is not None and "level--2" not in current.get("class", []):
        if "level--3" in current.get("class", []):
            all_parts_raw.append(current)
        current = current.find_next_sibling()

    # One dict per part, each carrying its own list of chapter dicts.
    all_parts_master = []

    for part in all_parts_raw:
        part_title_tag = part.find("li", class_="level__item--3")
        part_link_tag = part_title_tag.find("a")
        part_url = BASE_URL + part_link_tag.get("href")
        part_title = part_link_tag.get_text(strip=True)

        # Extract chapter information for single part
        chapters = []
        chapter_wrapper = part.find("ul", class_="level--4")
        chapter_blocks = chapter_wrapper.find_all("li", class_="level__item--4")
        for chapter in chapter_blocks:
            chapter_link_tag = chapter.find("a")
            chapter_url = BASE_URL + chapter_link_tag.get("href")
            chapter_title = chapter_link_tag.get_text(strip=True)
            chapters.append({"chapter_title": chapter_title, "chapter_url": chapter_url})

        # Append part information to master list
        all_parts_master.append({
            "title": part_title,
            "url": part_url,
            "chapters": chapters  # Each chapter has its own chapter_title and chapter_url
        })

    return all_parts_master


def save_chapter(part, chapter, chapter_text):
    """Write the chapter body text and its metadata sidecar."""
    part_name = make_safe_filename(part["title"])
    chapter_name = make_safe_filename(chapter["chapter_title"])

    filename = f"{OUTPUT_DIR}/{part_name}_{chapter_name}.txt"
    metadata_filename = f"{OUTPUT_DIR}/{part_name}_{chapter_name}_metadata.json"

    with open(filename, "w") as f:
        f.write(chapter_text)

    metadata = {
        "part_title": part["title"],
        "chapter_title": chapter["chapter_title"],
        "chapter_url": chapter["chapter_url"],
        "scraped_date": str(date.today())
    }

    with open(metadata_filename, "w") as f:
        json.dump(metadata, f, indent=2)


def print_summary(saved, failed_chapters, retried_chapters, total, aborted):
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
    # all 65 requests. No retry adapter — fetch_page retries, so failures keep their status.
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        toc_html, _ = fetch_page(session, TOC_URL)
        all_parts_master = collect_volume_12_parts(toc_html)
    except ScrapeError as error:
        print(f"Could not read the table of contents [{error.category}]: {error}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    failed_chapters = []   # Chapters we could not save, with the reason why
    retried_chapters = []  # Chapters that succeeded but not on the first try
    saved = 0

    # Flatten into one work list: the breaker needs a single loop it can break out of.
    jobs = [(part, chapter) for part in all_parts_master for chapter in part["chapters"]]

    total = len(jobs)
    if CRAWL_DELAY:
        print(f"Found {total} chapter(s) in Volume 12. "
              f"Waiting {CRAWL_DELAY}s between requests (~{total * CRAWL_DELAY // 60} min).")
    else:
        print(f"Found {total} chapter(s) in Volume 12. Fetching at full speed.")

    consecutive_failures = 0  # Reset by anything that proves the server is still answering
    aborted = False

    for part, chapter in jobs:
        if CRAWL_DELAY:
            time.sleep(CRAWL_DELAY)

        # Fetching and parsing raise the same kind of error, so one handler covers all
        # hangs, throttling, missing pages and unexpected HTML.
        try:
            html, attempts = fetch_page(session, chapter["chapter_url"])
            chapter_text = parse_chapter(html)
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

        if attempts > 1:
            retried_chapters.append({
                "chapter_title": chapter["chapter_title"],
                "attempts": attempts
            })

    print_summary(saved, failed_chapters, retried_chapters, total, aborted)


if __name__ == "__main__":
    main()