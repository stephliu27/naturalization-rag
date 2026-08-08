"""Shared scraper infrastructure. Pure functions only, no network and no filesystem.

Fetching stays per-source: USCIS is anonymous HTML at full speed, CourtListener is
token-authed JSON where a 429 is routine.
"""

import re


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
