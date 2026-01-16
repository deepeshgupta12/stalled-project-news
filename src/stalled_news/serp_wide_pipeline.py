from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .models import ProjectInput
from .serpapi_client import fetch_serp_organic_results
from .whitelist import is_url_allowed, host_from_url
from .whitelist_helpers import load_whitelist_domains, bucket_domains


@dataclass
class WideSerpRun:
    queries: List[str]
    all_results: List[Dict[str, Any]]
    whitelisted: List[Dict[str, Any]]
    domain_counts: Dict[str, int]


def build_wide_queries(project: ProjectInput) -> List[str]:
    pn = project.project_name.strip()
    city = project.city.strip()
    rera = (project.rera_id or "").strip()

    wl = load_whitelist_domains()
    buckets = bucket_domains(wl)

    regulator_domains = buckets["regulators"][:6]  # keep small, avoid query explosion
    news_domains = buckets["news"][:6]
    courts_domains = buckets["courts"][:2]

    q: List[str] = []

    # 1) Broad web queries (no site restriction)
    if rera:
        q.append(f"\"{pn}\" \"{city}\" \"{rera}\"")
        q.append(f"\"{rera}\" \"{pn}\"")
        q.append(f"\"{rera}\" project status")
        q.append(f"\"{rera}\" rera order")
        q.append(f"\"{rera}\" extension")
        q.append(f"\"{rera}\" registration")
    q.append(f"\"{pn}\" \"{city}\"")
    q.append(f"\"{pn}\" {city} rera")
    q.append(f"\"{pn}\" {city} possession")
    q.append(f"\"{pn}\" {city} construction update")
    q.append(f"\"{pn}\" {city} delayed")
    q.append(f"\"{pn}\" stalled project")
    q.append(f"\"{pn}\" {city} complaint")
    q.append(f"\"{pn}\" {city} litigation")
    q.append(f"\"{pn}\" {city} investors")

    # 2) Regulator-focused site queries (dynamic)
    for d in regulator_domains:
        q.append(f"site:{d} \"{pn}\"")
        if rera:
            q.append(f"site:{d} \"{rera}\"")

    # 3) News site queries (dynamic)
    for d in news_domains:
        q.append(f"site:{d} \"{pn}\" \"{city}\"")
        if rera:
            q.append(f"site:{d} \"{rera}\"")

    # 4) Courts (dynamic)
    for d in courts_domains:
        if rera:
            q.append(f"site:{d} \"{rera}\"")
        q.append(f"site:{d} \"{pn}\" \"{city}\"")

    # De-dupe preserving order
    seen = set()
    out = []
    for x in q:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def run_serp_wide(
    project: ProjectInput,
    *,
    gl: str = "in",
    hl: str = "en",
    max_per_query: int = 10,
    policy: str = "allow",
) -> WideSerpRun:
    queries = build_wide_queries(project)

    all_results: List[Dict[str, Any]] = []
    whitelisted: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}

    for query in queries:
        organic = fetch_serp_organic_results(query, engine="google", gl=gl, hl=hl, num=min(max_per_query, 10)) or []
        for r in organic:
            url = (r.get("link") or "").strip()
            if not url:
                continue

            dom = host_from_url(url) or (r.get("domain") or "").strip()
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1

            item = {
                "source_query": query,
                "title": r.get("title"),
                "link": url,
                "snippet": r.get("snippet"),
                "position": r.get("position"),
                "domain": dom,
                "section": r.get("section"),
                "source": r.get("source"),
                "date": r.get("date"),
            }
            all_results.append(item)

            if is_url_allowed(url, policy):
                whitelisted.append(item)

    return WideSerpRun(queries=queries, all_results=all_results, whitelisted=whitelisted, domain_counts=domain_counts)
