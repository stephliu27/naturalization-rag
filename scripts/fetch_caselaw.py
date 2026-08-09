"""Fetch CourtListener case law: searches first, then opinions by ID.

Two stages with a human gate between them. Search writes raw JSON, someone reads it and
picks the opinions worth keeping, then fetch pulls their full text. Splitting them means
iterating on selection costs no API quota, which matters at 125 requests a day.

    venv/bin/python scripts/fetch_caselaw.py search
    venv/bin/python scripts/fetch_caselaw.py fetch
"""

import json
import os
import sys
import time

import requests

from scraping import (
    FetchError,
    ParseError,
    ScrapeError,
    backoff_seconds,
    category_for_status,
    make_safe_filename,
    retry_after_seconds,
)

API_ROOT = "https://www.courtlistener.com/api/rest/v4"
SEARCH_URL = f"{API_ROOT}/search/"
OPINION_URL = f"{API_ROOT}/opinions"

OUTPUT_DIR = "data/raw/caselaw"

# Committed: data/raw gets wiped, so this list is what makes the corpus reproducible.
IDS_FILE = "data/caselaw_opinion_ids.json"

HEADERS = {
    "User-Agent": "naturalization-rag/0.1 (+https://github.com/stephliu27/naturalization-rag)"
}

# 5/min, 50/hr, 125/day, rolling and concurrent. 12s clears the per-minute ceiling.
CRAWL_DELAY = 12
HOURLY_LIMIT = 50

# Opinion bodies run to 100 KB+.
TIMEOUT = (5, 60)

# Slower than USCIS: 429 is routine here, and we will wait out a rolling window.
MAX_ATTEMPTS = 4
BACKOFF_BASE = 15
MAX_BACKOFF = 120
MAX_RETRY_AFTER = 300

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

BREAKER_CATEGORIES = {
    "rate_limited",
    "forbidden",
    "server_error",
    "timeout",
    "connection_error",
}
CONSECUTIVE_FAILURE_LIMIT = 3

# A record without a stated reason does not get fetched, so the corpus cannot grow silently.
REQUIRED_FIELDS = ("opinion_id", "barrier", "why")

# Key names the barrier, doubles as the output filename, and seeds the tagging label.
QUERIES = {
    "delay":
        '"1447(b)" AND naturalization '
        'AND dateFiled:[2005-01-01 TO 2026-08-07]',
    "procedural":
        '"1421(c)" AND ("de novo" OR "denial of naturalization") '
        'AND dateFiled:[2010-01-01 TO 2026-08-07]',
    "character":
        '"good moral character" AND ("1101(f)" OR "1427(a)") '
        'AND dateFiled:[2010-01-01 TO 2026-08-07]',
    "linguistic":
        '("1423" OR "N-648" OR "Medical Certification for Disability Exception" '
        'OR "English and civics") AND naturaliz* '
        'AND dateFiled:[2000-01-01 TO 2026-08-07]',
    "financial":
        '("I-912" OR "fee waiver") AND naturaliz* '
        'AND dateFiled:[2000-01-01 TO 2026-08-07]',
}

# Counts from the browser pass on 2026-08-07. A different number means the search results
# have changed since then, so the same query would not rebuild the same corpus today.
EXPECTED_COUNTS = {
    "delay": 179,
    "procedural": 111,
    "character": 200,
    "linguistic": 44,
    "financial": 6,
}


def api_token():
    """Fail at the guess, not on a confusing 403 from an unauthenticated request."""
    token = os.environ.get("COURTLISTENER_API_TOKEN")
    if not token:
        sys.exit(
            "COURTLISTENER_API_TOKEN is not set.\n"
            "  set -a; . ./.env; set +a"
        )
    return token


def open_session():
    """One session for the run: auth and user agent set once, connection reused."""
    session = requests.Session()
    session.headers.update(HEADERS)
    # The literal word "Token" is required; omitting it is the usual 403.
    session.headers["Authorization"] = f"Token {api_token()}"
    return session


def fetch_json(session, url, params=None):
    """One GET with retries. Returns (payload, attempts, headers), raises FetchError."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = None

        try:
            response = session.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json(), attempt, response.headers

        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            category = category_for_status(status)

            if status not in RETRYABLE_STATUS:
                raise FetchError(category, f"HTTP {status}")

            wait = retry_after_seconds(error.response)
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

        except ValueError as error:
            # A 200 that is not JSON means the endpoint changed or we got an error page.
            raise ParseError("bad_json", f"response was not JSON: {error}")

        if attempt < MAX_ATTEMPTS:
            if wait is None:
                wait = backoff_seconds(attempt, BACKOFF_BASE, MAX_BACKOFF)
            time.sleep(wait)

    raise last_error


def rate_limit_headers(headers):
    """Whatever throttling headers the API returns — the docs do not say if any exist."""
    return {
        name: value for name, value in headers.items()
        if "ratelimit" in name.lower() or name.lower() == "retry-after"
    }


def save_json(payload, path):
    """Raw and untouched: re-fetching costs quota, so parsing reads from disk instead."""
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def run_searches(session):
    """Stage 1: the five queries to disk. Five requests, spent once."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    failures = []
    counts = {}
    observed_headers = {}
    consecutive_failures = 0

    print(f"{len(QUERIES)} searches, {CRAWL_DELAY}s apart.\n")

    for index, (barrier, query) in enumerate(QUERIES.items()):
        if index:
            time.sleep(CRAWL_DELAY)

        params = {"q": query, "type": "o", "order_by": "score desc"}

        try:
            payload, _, headers = fetch_json(session, SEARCH_URL, params)
        except ScrapeError as error:
            failures.append({"what": f"search:{barrier}", "category": error.category,
                             "error": str(error)})
            if error.category in BREAKER_CATEGORIES:
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    print(f"\nAborting: {CONSECUTIVE_FAILURE_LIMIT} consecutive failures.")
                    break
            else:
                consecutive_failures = 0
            continue

        consecutive_failures = 0
        observed_headers.update(rate_limit_headers(headers))

        path = f"{OUTPUT_DIR}/search_{barrier}.json"
        save_json(payload, path)

        count = payload.get("count")
        counts[barrier] = count
        expected = EXPECTED_COUNTS.get(barrier)
        drift = "" if count == expected else f"  (expected {expected} — index moved)"
        print(f"  {barrier:<11} {count:>5} results -> {path}{drift}")

    print_summary(failures, observed_headers)
    return counts


def load_selection():
    """Read the committed selection. Requiring a reason stops the corpus growing silently."""
    if not os.path.exists(IDS_FILE):
        sys.exit(f"{IDS_FILE} not found — run the search stage and build the selection first.")

    with open(IDS_FILE) as f:
        document = json.load(f)

    records = document.get("selected") if isinstance(document, dict) else None
    if not records:
        sys.exit(f"{IDS_FILE} needs a non-empty 'selected' list.")

    for position, record in enumerate(records):
        missing = [field for field in REQUIRED_FIELDS if not record.get(field)]
        if missing:
            label = record.get("case_name") or f"record {position}"
            sys.exit(f"{IDS_FILE}: {label} is missing {', '.join(missing)}.")

    return records


def opinion_path(record):
    """ID leads so the filename stays unique when two cases share a caption."""
    name = make_safe_filename(record.get("case_name") or "opinion")
    return f"{OUTPUT_DIR}/opinion_{record['opinion_id']}_{name}.json"


def run_fetches(session, records):
    """Stage 2: full text for each selected opinion, skipping anything already on disk."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Resumability: a half-finished run picks up without re-spending requests.
    todo = [r for r in records if not os.path.exists(opinion_path(r))]
    skipped = len(records) - len(todo)

    if skipped:
        print(f"{skipped} already on disk, skipping.")
    if not todo:
        print("Nothing to fetch.")
        return

    minutes = len(todo) * CRAWL_DELAY // 60
    print(f"{len(todo)} opinion(s) to fetch, {CRAWL_DELAY}s apart (~{minutes} min).")
    if len(todo) > HOURLY_LIMIT:
        print(f"WARNING: more than the {HOURLY_LIMIT}/hour limit. Expect 429s partway through.")
    print()

    failures = []
    observed_headers = {}
    saved = 0
    consecutive_failures = 0

    for index, record in enumerate(todo):
        if index:
            time.sleep(CRAWL_DELAY)

        url = f"{OPINION_URL}/{record['opinion_id']}/"
        label = record.get("case_name", record["opinion_id"])

        try:
            payload, _, headers = fetch_json(session, url)
        except ScrapeError as error:
            failures.append({"what": label, "category": error.category, "error": str(error)})
            if error.category in BREAKER_CATEGORIES:
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    print(f"\nAborting: {CONSECUTIVE_FAILURE_LIMIT} consecutive failures. "
                          f"Re-run later — finished opinions are skipped.")
                    break
            else:
                consecutive_failures = 0
            continue

        consecutive_failures = 0
        observed_headers.update(rate_limit_headers(headers))
        save_json(payload, opinion_path(record))
        saved += 1
        print(f"  [{saved}/{len(todo)}] {label}")

    print(f"\n{saved} saved, {len(failures)} failed, "
          f"{len(todo) - saved - len(failures)} not attempted.")
    print_summary(failures, observed_headers)


def print_summary(failures, observed_headers):
    """Categories first — the counts say whether to slow down or come back later."""
    if observed_headers:
        print("\nRate-limit headers seen:")
        for name in sorted(observed_headers):
            print(f"  {name}: {observed_headers[name]}")

    if not failures:
        return

    counts = {}
    for failure in failures:
        counts[failure["category"]] = counts.get(failure["category"], 0) + 1

    print("\nFailures by category:")
    for category in sorted(counts):
        print(f"  {counts[category]:3d}  {category}")

    print("\nDetail:")
    for failure in failures:
        print(f"  - [{failure['category']}] {failure['what']}")
        print(f"      {failure['error']}")


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else None
    if stage not in ("search", "fetch"):
        sys.exit(__doc__)

    if stage == "search":
        run_searches(open_session())
    else:
        # Validate the selection before asking for a token — the local check is free.
        records = load_selection()
        run_fetches(open_session(), records)


if __name__ == "__main__":
    main()
