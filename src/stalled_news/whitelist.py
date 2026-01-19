from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Set, List
from urllib.parse import urlparse


def _norm_domain(d: str) -> str:
    return (d or "").strip().lower().rstrip(".")


def host_from_url(url: str) -> str:
    """
    Robust host extraction (no scheme -> still try).
    Returns lowercase host without trailing dot.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        host = urlparse(u).hostname or ""
    except Exception:
        host = ""
    return _norm_domain(host)


@dataclass
class WhitelistPolicy:
    """
    allowed_domains:
      Exact domains allowed (e.g. "haryanarera.gov.in", "www.magicbricks.com")

    allow_subdomains_for:
      Base domains for which any subdomain is allowed.
      Example: ["gov.in"] would allow foo.gov.in, bar.gov.in, etc.
      Keep this list tight to avoid junk.
    """
    allowed_domains: Set[str] = field(default_factory=set)
    allow_subdomains_for: List[str] = field(default_factory=list)


def is_url_allowed(url: str, policy: WhitelistPolicy) -> bool:
    """
    Returns True if URL host is allowed by:
      - exact match in allowed_domains, OR
      - subdomain match for any base in allow_subdomains_for
    """
    host = host_from_url(url)
    if not host:
        return False

    allowed = {_norm_domain(d) for d in (policy.allowed_domains or set()) if _norm_domain(d)}
    if host in allowed:
        return True

    sub_ok = [_norm_domain(d) for d in (policy.allow_subdomains_for or []) if _norm_domain(d)]
    for base in sub_ok:
        if host == base:
            return True
        if host.endswith("." + base):
            return True

    return False
