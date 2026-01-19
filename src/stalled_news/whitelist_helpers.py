from __future__ import annotations

from typing import Dict, List, Optional
from pathlib import Path
import yaml

from .whitelist import WhitelistPolicy


def _repo_root() -> Path:
    # .../stalled-project-news/src/stalled_news/whitelist_helpers.py
    # parents[0]=stalled_news, [1]=src, [2]=repo root
    return Path(__file__).resolve().parents[2]


def _default_whitelist_path() -> Path:
    return _repo_root() / "configs" / "whitelist.yaml"


def load_whitelist_domains(path: Optional[str] = None) -> List[str]:
    """
    Loads domains from configs/whitelist.yaml by default.
    Supports:
      1) Mapping YAML: {"domains": ["a.com", "b.com"]}
      2) List YAML: ["a.com", "b.com"]
    """
    if path is None:
        p = _default_whitelist_path()
    else:
        p = Path(path)
        if not p.is_absolute():
            # treat relative paths as repo-root relative
            p = _repo_root() / p

    if not p.exists():
        return []

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # support both: {"domains":[...]} and plain list YAML
    if isinstance(data, dict):
        domains = data.get("domains") or []
        if isinstance(domains, list):
            return [str(x).strip().lower().rstrip(".") for x in domains if str(x).strip()]
        return []

    if isinstance(data, list):
        return [str(x).strip().lower().rstrip(".") for x in data if str(x).strip()]

    return []


def bucket_domains(domains: List[str]) -> Dict[str, List[str]]:
    """
    Buckets to generate smarter queries:
    - regulators: *.rera.*, *.gov.in, nic.in (and similar official portals)
    - courts: indiankanoon.org
    - news: common Indian news sites + business sites (heuristic)
    - realestate: property portals (heuristic)
    """
    regulators: List[str] = []
    courts: List[str] = []
    news: List[str] = []
    realestate: List[str] = []
    other: List[str] = []

    for d in domains:
        dl = d.lower()
        if dl == "indiankanoon.org":
            courts.append(d)
        elif dl.endswith(".gov.in") or dl.endswith(".nic.in") or dl == "nic.in" or "rera" in dl:
            regulators.append(d)
        elif any(
            x in dl
            for x in [
                "timesofindia",
                "indiatimes",
                "economictimes",
                "livemint",
                "moneycontrol",
                "business-standard",
                "financialexpress",
                "hindustantimes",
                "indianexpress",
                "thehindu",
                "ndtv",
                "indiatoday",
                "news",
            ]
        ):
            news.append(d)
        elif any(
            x in dl
            for x in [
                "99acres",
                "magicbricks",
                "housing.com",
                "nobroker",
                "squareyards",
                "makaan",
                "proptiger",
                "commonfloor",
            ]
        ):
            realestate.append(d)
        else:
            other.append(d)

    return {
        "regulators": regulators,
        "courts": courts,
        "news": news,
        "realestate": realestate,
        "other": other,
    }


def load_whitelist_policy(path: Optional[str] = None) -> WhitelistPolicy:
    """
    Builds the WhitelistPolicy expected by whitelist.is_url_allowed().
    Enables subdomain matching for gov.in and nic.in if they are present in allowed domains.
    """
    domains = load_whitelist_domains(path)

    # Allow subdomain matching ONLY for these suffixes (safe + intended).
    subdomain_allowed = []
    for parent in ("gov.in", "nic.in"):
        if parent in domains:
            subdomain_allowed.append(parent)

    return WhitelistPolicy.from_config(domains, subdomain_allowed)