from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Accept": "text/html,application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_url(url: str) -> httpx.Response:
    # Separate connect/read timeouts to reduce random ReadTimeout crashes.
    timeout = httpx.Timeout(connect=10.0, read=40.0, write=20.0, pool=20.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
        r = client.get(url)
        return r
