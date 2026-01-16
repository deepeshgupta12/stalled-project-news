from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import repo_root, load_yaml
from .models import ProjectInput, SerpFetchMeta, SerpResult, SerpRun
from .query_pack import build_query_pack
from .serpapi_client import fetch_serp_organic_results
from .whitelist import WhitelistPolicy, is_url_allowed, host_from_url


def _slugify(s: str) -> str:
    s = s.strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "/"):
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def artifacts_dir_for_project(project: ProjectInput) -> Path:
    root = repo_root()
    base = load_yaml(root / "configs" / "settings.yaml").get("artifacts", {}).get("base_dir", "artifacts")

    rid = (project.rera_id or "").strip()
    key = f"{project.project_name}-{project.city}" + (f"-{rid}" if rid else "")
    project_slug = _slugify(key)

    return (root / str(base) / project_slug)


def load_whitelist_policy() -> WhitelistPolicy:
    root = repo_root()
    wl = load_yaml(root / "configs" / "whitelist.yaml")
    domains = wl.get("domains", [])
    sub_allowed = wl.get("subdomain_allowed", [])
    return WhitelistPolicy.from_config(domains, sub_allowed)


def run_serp_search_with_debug(project: ProjectInput) -> Tuple[SerpRun, List[Dict[str, Any]], Dict[str, int]]:
    """
    Returns:
      - SerpRun (whitelist-filtered results)
      - all_results_debug (all organic results with allowed flag + domain)
      - domain_counts (domain -> count across all organic results)
    """
    root = repo_root()
    settings = load_yaml(root / "configs" / "settings.yaml")
    search_cfg = settings.get("search", {})

    engine = str(search_cfg.get("serpapi_engine", "google"))
    max_results = int(search_cfg.get("max_results", 30))
    gl = str(search_cfg.get("gl", "in"))
    hl = str(search_cfg.get("hl", "en"))

    policy = load_whitelist_policy()
    queries = build_query_pack(project)

    collected_filtered: List[SerpResult] = []
    all_debug: List[Dict[str, Any]] = []
    domain_counter: Counter[str] = Counter()

    per_query_num = min(max_results, 20)

    for q in queries:
        organic = fetch_serp_organic_results(q, engine=engine, gl=gl, hl=hl, num=per_query_num)
        for r in organic:
            link = (r.get("link") or "").strip()
            title = (r.get("title") or "").strip()
            snippet = (r.get("snippet") or None)
            pos = r.get("position")

            if not link:
                continue

            domain = host_from_url(link) or ""
            if domain:
                domain_counter[domain] += 1

            allowed = is_url_allowed(link, policy)

            all_debug.append(
                {
                    "title": title,
                    "link": link,
                    "snippet": snippet,
                    "position": pos if isinstance(pos, int) else None,
                    "source_query": q,
                    "domain": domain,
                    "allowed": allowed,
                }
            )

            if not title:
                continue
            if not allowed:
                continue

            try:
                item = SerpResult(
                    title=title,
                    link=link,
                    snippet=snippet,
                    position=pos if isinstance(pos, int) else None,
                    source_query=q,
                )
                collected_filtered.append(item)
            except Exception:
                continue

    # De-dupe filtered by link
    seen = set()
    deduped: List[SerpResult] = []
    for item in collected_filtered:
        k = str(item.link)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(item)

    meta = SerpFetchMeta(engine=engine, max_results=max_results, gl=gl, hl=hl)
    run = SerpRun(
        project=project,
        meta=meta,
        results_total=len(deduped),
        results_whitelisted=len(deduped),
        results=deduped,
    )

    domain_counts = dict(domain_counter.most_common())
    return run, all_debug, domain_counts


def store_serp_run_with_debug(run: SerpRun, all_debug: List[Dict[str, Any]], domain_counts: Dict[str, int]) -> Path:
    out_dir = artifacts_dir_for_project(run.project)
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_dir = out_dir / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # Whitelisted only (used by later steps)
    out_path = target_dir / "serp_results.json"
    out_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

    # Debug only (NOT used as evidence unless whitelisted)
    (target_dir / "serp_results_all.json").write_text(
        __import__("json").dumps(all_debug, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target_dir / "serp_domains_summary.json").write_text(
        __import__("json").dumps(domain_counts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return out_path
