from __future__ import annotations

import hashlib
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def stable_id_for_url(url: str) -> str:
    """
    Stable ID for a URL for dedupe/storage.
    Normalizes scheme/host case and strips fragments.
    """
    u = (url or "").strip()
    if not u:
        return hashlib.sha256(b"").hexdigest()[:16]

    # Minimal normalization (safe, no heavy URL rewriting)
    try:
        parsed = httpx.URL(u)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.host or "").lower()
        path = parsed.path or "/"
        query = f"?{parsed.query.decode('utf-8', errors='ignore')}" if parsed.query else ""
        norm = f"{scheme}://{host}{path}{query}"
    except Exception:
        norm = u.split("#", 1)[0].strip().lower()

    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()[:16]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _fetch_once(url: str) -> httpx.Response:
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    with httpx.Client(headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r


def fetch_url(url: str) -> Optional[httpx.Response]:
    """
    Fetch a URL with retry. Returns None on final failure.
    IMPORTANT: Do NOT raise RetryError up the stack (keeps pipeline running).
    """
    try:
        return _fetch_once(url)
    except Exception:
        return None
