from __future__ import annotations

from typing import Dict, List, Tuple
from pathlib import Path
import yaml

def load_whitelist_domains(path: str = "configs/whitelist.yaml") -> List[str]:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    domains = data.get("domains") or []
    # support both: {"domains":[...]} and plain list YAML
    if isinstance(domains, list) and domains:
        return [str(x).strip() for x in domains if str(x).strip()]
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
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
        elif any(x in dl for x in ["timesofindia", "indiatimes", "economictimes", "livemint", "moneycontrol",
                                   "business-standard", "financialexpress", "hindustantimes", "indianexpress",
                                   "thehindu", "ndtv", "indiatoday", "news"]):
            news.append(d)
        elif any(x in dl for x in ["99acres", "magicbricks", "housing.com", "nobroker", "squareyards",
                                   "makaan", "proptiger", "commonfloor"]):
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
