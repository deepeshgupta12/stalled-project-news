from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .whitelist import WhitelistPolicy


def _default_whitelist_path() -> Path:
    # repo_root = .../stalled-project-news
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "configs" / "whitelist.yaml"


def load_whitelist_domains(path: Optional[str] = None) -> List[str]:
    """
    Returns the top-level 'domains' allowlist from configs/whitelist.yaml.

    Supports:
      - dict YAML: { domains: [...], subdomain_allowed: [...] }
      - legacy list YAML: [ "a.com", "b.com", ... ]
    """
    p = Path(path) if path else _default_whitelist_path()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # legacy: YAML is a list
    if isinstance(data, list):
        domains = [str(x).strip().lower().rstrip(".") for x in data if str(x).strip()]
        return sorted(set(domains))

    if not isinstance(data, dict):
        return []

    raw = data.get("domains") or []
    domains = [str(x).strip().lower().rstrip(".") for x in raw if str(x).strip()]
    # de-dupe preserve order
    seen = set()
    out: List[str] = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def load_whitelist_policy(path: Optional[str] = None) -> WhitelistPolicy:
    """
    Builds WhitelistPolicy from YAML.
    If subdomain_allowed is missing, defaults to [].
    """
    p = Path(path) if path else _default_whitelist_path()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # legacy: YAML is a list => treat as domains only
    if isinstance(data, list):
        domains = load_whitelist_domains(str(p))
        return WhitelistPolicy(allowed_domains=set(domains), allow_subdomains_for=[])

    if not isinstance(data, dict):
        return WhitelistPolicy(allowed_domains=set(), allow_subdomains_for=[])

    domains = data.get("domains") or []
    sub_ok = data.get("subdomain_allowed") or []

    domains_clean = [str(x).strip().lower().rstrip(".") for x in domains if str(x).strip()]
    sub_ok_clean = [str(x).strip().lower().rstrip(".") for x in sub_ok if str(x).strip()]

    return WhitelistPolicy(
        allowed_domains=set(domains_clean),
        allow_subdomains_for=sub_ok_clean,
    )


def bucket_domains(domains: List[str]) -> Dict[str, List[str]]:
    """
    Buckets whitelist domains to build a balanced wide-query set.
    Heuristic only — safe defaults.
    """
    regs: List[str] = []
    news: List[str] = []
    courts: List[str] = []
    portals: List[str] = []

    for d in domains:
        ld = d.lower()
        if "rera" in ld or ld.endswith(".gov.in") or ld.endswith("gov.in") or ld.endswith("nic.in"):
            regs.append(d)
        elif "indiankanoon" in ld or "supremecourt" in ld or "highcourt" in ld:
            courts.append(d)
        elif any(x in ld for x in ["reuters", "bloomberg", "express", "hindu", "mint", "ndtv", "times", "moneycontrol", "standard", "today", "hindustan"]):
            news.append(d)
        else:
            portals.append(d)

    return {
        "regulators": regs,
        "news": news,
        "courts": courts,
        "portals": portals,
    }
