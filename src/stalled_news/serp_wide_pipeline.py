from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models import ProjectInput
from .serpapi_client import fetch_serp_links
from .whitelist import is_url_allowed, host_from_url


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

    # 1) Identity queries (highest precision)
    if rera:
        q.append(f"\"{rera}\" \"{pn}\" \"{city}\"")
        q.append(f"\"{rera}\" project status")
        q.append(f"\"{rera}\" rera order")
        q.append(f"\"{rera}\" registration")
        q.append(f"\"{rera}\" monitoring")
    q.append(f"\"{pn}\" \"{city}\" rera")
    q.append(f"\"{pn}\" \"{city}\" project status")

    # 2) Regulator intent WITHOUT hardcoding a state domain
    # Use gov.in / nic.in constraints (still must pass whitelist filter later).
    if rera:
        q.append(f"site:gov.in \"{rera}\"")
        q.append(f"site:nic.in \"{rera}\"")
    q.append(f"site:gov.in \"{pn}\" \"{city}\" rera")
    q.append(f"site:nic.in \"{pn}\" \"{city}\" rera")
    q.append(f"site:gov.in \"{pn}\" \"{city}\" order")
    q.append(f"site:nic.in \"{pn}\" \"{city}\" order")

    # 3) News intent (broad—top stories/news_results will be harvested)
    q.append(f"\"{pn}\" \"{city}\" news")
    q.append(f"\"{pn}\" \"{city}\" delayed possession")
    q.append(f"\"{pn}\" \"{city}\" construction update")
    q.append(f"\"{pn}\" \"{city}\" complaint buyers")
    q.append(f"\"{pn}\" \"{city}\" litigation")
    q.append(f"\"{pn}\" \"{city}\" investors")

    # 4) Generic “stalled” phrasing
    q.append(f"\"{pn}\" stalled project {city}")
    q.append(f"\"{pn}\" delayed project {city}")

    # De-dupe while keeping order
    seen = set()
    out: List[str] = []
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
) -> WideSerpRun:
    queries = build_wide_queries(project)

    all_results: List[Dict[str, Any]] = []
    whitelisted: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}

    for query in queries:
        links = fetch_serp_links(query, gl=gl, hl=hl, num=min(max_per_query, 10)) or []
        for idx, r in enumerate(links):
            url = (r.get("link") or "").strip()
            if not url:
                continue

            dom = host_from_url(url)
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1

            item = {
                "source_query": query,
                "title": r.get("title"),
                "link": url,
                "snippet": r.get("snippet"),
                "position": r.get("position") or (idx + 1),
                "domain": dom,
                "section": r.get("section"),
                "news_source": r.get("source"),
                "news_date": r.get("date"),
            }

            all_results.append(item)
            if is_url_allowed(url):
                whitelisted.append(item)

    return WideSerpRun(
        queries=queries,
        all_results=all_results,
        whitelisted=whitelisted,
        domain_counts=domain_counts,
    )
