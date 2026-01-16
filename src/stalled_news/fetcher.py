from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes


def _ua() -> str:
    return "stalled-project-news/0.0.0 (+https://squareyards.com; research bot)"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6))
def fetch_url(url: str, *, timeout_s: int = 25) -> FetchResult:
    headers = {
        "User-Agent": _ua(),
        "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout_s, headers=headers) as client:
        r = client.get(url)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        return FetchResult(
            url=url,
            final_url=str(r.url),
            status_code=int(r.status_code),
            content_type=ct,
            body=r.content,
        )


def stable_id_for_url(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return h[:16]
