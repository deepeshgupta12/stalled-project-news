from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .models import ProjectInput
from .serpapi_client import fetch_serp_organic_results
from .whitelist import is_url_allowed


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

    q: List[str] = []

    # General intent (broad web)
    if rera:
        q.append(f"\"{pn}\" \"{city}\" \"{rera}\"")
        q.append(f"\"{rera}\" \"{pn}\"")
    q.append(f"\"{pn}\" \"{city}\"")
    q.append(f"\"{pn}\" {city} possession")
    q.append(f"\"{pn}\" {city} construction update")
    q.append(f"\"{pn}\" delayed")
    q.append(f"\"{pn}\" stalled project")

    # Regulator intent (still helpful)
    q.append(f"site:haryanarera.gov.in \"{pn}\"")
    if rera:
        q.append(f"site:haryanarera.gov.in \"{rera}\"")

    # News intent: add "news" token and common story angles
    q.append(f"\"{pn}\" \"{city}\" news")
    q.append(f"\"{pn}\" \"{city}\" rera order")
    q.append(f"\"{pn}\" \"{city}\" complaint")
    q.append(f"\"{pn}\" \"{city}\" investors")

    # De-dupe while keeping order
    seen = set()
    out = []
    for x in q:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def run_serp_wide(project: ProjectInput, *, gl: str = "in", hl: str = "en", max_per_query: int = 10) -> WideSerpRun:
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
            dom = (r.get("domain") or "").strip()
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1

            item = {
                "query": query,
                "title": r.get("title"),
                "link": url,
                "snippet": r.get("snippet"),
                "position": r.get("position"),
                "domain": dom,
            }
            all_results.append(item)

            if is_url_allowed(url):
                whitelisted.append(item)

    return WideSerpRun(queries=queries, all_results=all_results, whitelisted=whitelisted, domain_counts=domain_counts)
