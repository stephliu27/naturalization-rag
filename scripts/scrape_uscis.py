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

# (connect, read) in seconds. Both values sit far above anything a healthy request needs
# (USCIS normally answers in well under a second), so they only fire when something is broken.
TIMEOUT = (5, 30)

# Seconds to wait between chapter requests.
# uscis.gov/robots.txt asks all crawlers for "Crawl-delay: 10", but then a full 64-chapter run would
# take 11 minutes when it should be negligible for a site this size.
# Raise this to 10 if the summary below ever reports rate_limited failures.
CRAWL_DELAY = 0

# Retry settings. Waits grow 5s, 10s, 20s.
MAX_ATTEMPTS = 4
BACKOFF_BASE = 5
MAX_BACKOFF = 60

# Statuses worth retrying: the server is overloaded or throttling us, and the same
# request may well succeed shortly. A 404 is a definitive answer, so it is not here.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# One failure type for the whole scrape, so the main loop only has to catch one thing.
# Each carries a short category (not just a message) so failures can be analyzed at the 
# end — "rate_limited" means slow down, "structure_changed" means USCIS redesigned the page.
class ScrapeError(Exception):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class FetchError(ScrapeError):
    """Could not get the page: network problem, timeout, or an error status."""


class ParseError(ScrapeError):
    """Got a real page, but it was not shaped the way we expect."""


# Function to create a safe streamlined filename from a given text
def make_safe_filename(text):
    text = text.lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


# Translate an HTTP status code into one of our failure categories
def category_for_status(status):
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "client_error"


# Check Retry-After header for how long to wait before retrying a request (429, 503 errors)
def retry_after_seconds(response):
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        # Retry-After may also be an HTTP date; fall back to our own backoff in that case.
        return None


# How long to wait before attempt N when the server gave us no guidance
def backoff_seconds(attempt):
    return min(BACKOFF_BASE * (2 ** (attempt - 1)), MAX_BACKOFF)


# Fetch one page, retrying only the failures that are worth retrying.
# Returns (html, attempts_used) or raises FetchError carrying a category.
def fetch_page(session, url):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = None

        try:
            response = session.get(url, timeout=TIMEOUT)
            # Check status explicitly so we can categorize the failure and retry if appropriate (e.g. 404 vs. 429)
            response.raise_for_status()
            return response.text, attempt

        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            category = category_for_status(status)

            # Permanent answers (404 and friends) will not change on a retry
            if status not in RETRYABLE_STATUS:
                raise FetchError(category, f"HTTP {status}")

            wait = retry_after_seconds(error.response)
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


# Pull the readable chapter text out of a page we successfully fetched.
# Reaching here means the server returned 200, so anything missing is a real
# structural surprise rather than a 404 or a throttling response.
def parse_chapter(html):
    soup = BeautifulSoup(html, "html.parser")

    guidance = soup.find("div", id="guidance")  # Find guidance container under which content lies
    if guidance is None:
        raise ParseError("structure_changed", "no 'guidance' div on a 200 page")

    body = guidance.find("div", class_="field--name-body")
    if body is None:
        raise ParseError("structure_changed", "no 'field--name-body' div inside guidance section")

    text_parts = []

    # Applying formatting to the extracted text based on HTML structure (paragraph, list, table, etc.)
    for child in body.find_all(recursive=False):
        if child.name in ["p", "h2", "h3", "h4", "section"]:
            text = child.get_text(strip=True)
            if text:
                text_parts.append(text)

        elif child.name in ["ul", "ol"]:
            items = child.find_all("li")
            for item in items:
                item_text = item.get_text(strip=True)
                if item_text:
                    text_parts.append(f"- {item_text}")  # Reformat to reflect bullet list style

        elif child.name == "table":
            rows = child.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                cell_texts = [cell.get_text(strip=True) for cell in cells]
                row_line = "| " + " | ".join(cell_texts) + " |"   # Reformat for markdown table style
                text_parts.append(row_line)

    return "\n".join(text_parts)


# Read the table of contents and build the list of parts and chapters in volume 12
def collect_volume_12_parts(toc_html):
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

    # Find all part blocks within volume 12
    all_parts_raw = []
    current = volume_12.find_next_sibling()

    # Ensure we are only collecting parts within volume 12
    while current is not None and "level--2" not in current.get("class", []):
        if "level--3" in current.get("class", []):
            all_parts_raw.append(current)
        current = current.find_next_sibling()

    # Extract part title, URL, and chapter information from each part block.
    # Build dictionary for each part and append to list of parts.
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


# Write the chapter body text and its metadata sidecar
def save_chapter(part, chapter, chapter_text):
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


# Print what happened, grouped by category so the counts are diagnostic
def print_summary(saved, failed_chapters, retried_chapters):
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
    # One session for the whole run: sets the user agent once and reuses a single TCP
    # connection across every request instead of redoing the handshake 65 times.
    # No retry adapter is mounted — retries are handled in fetch_page so that failures
    # keep their real status codes and every attempt can be reported below.
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        toc_html, _ = fetch_page(session, TOC_URL)
        all_parts_master = collect_volume_12_parts(toc_html)
    except ScrapeError as error:
        print(f"Could not read the table of contents [{error.category}]: {error}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)  # Create new directory to store scraped text files
    failed_chapters = []   # Chapters we could not save, with the reason why
    retried_chapters = []  # Chapters that succeeded but not on the first try
    saved = 0

    total = sum(len(part["chapters"]) for part in all_parts_master)
    if CRAWL_DELAY:
        print(f"Found {total} chapter(s) in Volume 12. "
              f"Waiting {CRAWL_DELAY}s between requests (~{total * CRAWL_DELAY // 60} min).")
    else:
        print(f"Found {total} chapter(s) in Volume 12. Fetching at full speed.")

    for part in all_parts_master:
        for chapter in part["chapters"]:
            if CRAWL_DELAY:
                time.sleep(CRAWL_DELAY)

            # Fetching and parsing raise the same kind of error, so one handler covers
            # hangs, throttling, missing pages and unexpected HTML alike.
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
                continue

            save_chapter(part, chapter, chapter_text)
            saved += 1

            if attempts > 1:
                retried_chapters.append({
                    "chapter_title": chapter["chapter_title"],
                    "attempts": attempts
                })

    print_summary(saved, failed_chapters, retried_chapters)


if __name__ == "__main__":
    main()
