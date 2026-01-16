from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

import idna


@dataclass(frozen=True)
class WhitelistPolicy:
    allowed_domains: set[str]
    subdomain_allowed: set[str]

    @staticmethod
    def from_config(allowed_domains: Iterable[str], subdomain_allowed: Iterable[str]) -> "WhitelistPolicy":
        return WhitelistPolicy(
            allowed_domains={d.strip().lower() for d in allowed_domains if d.strip()},
            subdomain_allowed={d.strip().lower() for d in subdomain_allowed if d.strip()},
        )


def _normalize_host(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    # Convert unicode domains to ASCII punycode for consistent matching
    try:
        host = idna.encode(host).decode("ascii")
    except Exception:
        # If conversion fails, keep original lowercased
        pass
    return host


def host_from_url(url: str) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    # urlparse needs scheme to parse netloc reliably
    if "://" not in u:
        u = "http://" + u
    p = urlparse(u)
    if not p.netloc:
        return None
    host = p.hostname or ""
    host = _normalize_host(host)
    return host or None


def is_domain_allowed(host: str, policy: WhitelistPolicy) -> bool:
    """
    Exact-domain matching by default.
    Subdomain matching is allowed only when the parent domain is in policy.subdomain_allowed.

    Examples:
      - host = "economictimes.indiatimes.com" allowed if exact in allowed_domains
      - host = "foo.gov.in" allowed if "gov.in" in subdomain_allowed and "gov.in" in allowed_domains
    """
    if not host:
        return False

    host = _normalize_host(host)

    # Exact match first
    if host in policy.allowed_domains:
        return True

    # Subdomain allowance only for configured parent domains
    for parent in policy.subdomain_allowed:
        parent = parent.strip().lower()
        if not parent:
            continue
        if parent not in policy.allowed_domains:
            continue
        if host.endswith("." + parent):
            return True

    return False


def is_url_allowed(url: str, policy: WhitelistPolicy) -> bool:
    host = host_from_url(url)
    if not host:
        return False
    return is_domain_allowed(host, policy)
