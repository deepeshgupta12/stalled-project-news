from __future__ import annotations

from typing import Any, Dict, List, Optional
import os

from dotenv import load_dotenv
from serpapi import GoogleSearch

load_dotenv(".env", override=True)


def serpapi_key() -> str:
    k = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not k:
        raise RuntimeError("Missing SERPAPI_API_KEY in .env")
    return k


def _collect_links_from_section(data: Dict[str, Any], section_key: str) -> List[Dict[str, Any]]:
    """
    Normalize SerpAPI sections into a common list of dicts with keys:
      title, link, snippet, position, source, date
    """
    out: List[Dict[str, Any]] = []
    items = data.get(section_key) or []
    if not isinstance(items, list):
        return out

    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        link = (it.get("link") or it.get("url") or "").strip()
        title = it.get("title")
        snippet = it.get("snippet") or it.get("description")
        source = it.get("source") or it.get("publisher")
        date = it.get("date") or it.get("published_date") or it.get("timestamp")
        # Some top_stories items contain nested stories under "stories"
        if not link and "stories" in it and isinstance(it["stories"], list):
            for j, st in enumerate(it["stories"]):
                if not isinstance(st, dict):
                    continue
                l2 = (st.get("link") or st.get("url") or "").strip()
                if not l2:
                    continue
                out.append({
                    "title": st.get("title") or title,
                    "link": l2,
                    "snippet": st.get("snippet") or st.get("description") or snippet,
                    "position": j + 1,
                    "source": st.get("source") or st.get("publisher") or source,
                    "date": st.get("date") or st.get("published_date") or st.get("timestamp") or date,
                    "section": section_key,
                })
            continue

        if not link:
            continue

        out.append({
            "title": title,
            "link": link,
            "snippet": snippet,
            "position": it.get("position") or (idx + 1),
            "source": source,
            "date": date,
            "section": section_key,
        })
    return out


def fetch_serp_results_any(query: str, *, engine: str = "google", gl: str = "in", hl: str = "en", num: int = 10) -> Dict[str, Any]:
    params = {
        "engine": engine,
        "q": query,
        "gl": gl,
        "hl": hl,
        "num": num,
        "api_key": serpapi_key(),
    }
    return GoogleSearch(params).get_dict()


def fetch_serp_links(query: str, *, engine: str = "google", gl: str = "in", hl: str = "en", num: int = 10) -> List[Dict[str, Any]]:
    """
    Returns a combined list of candidate links from multiple SERP sections:
    - organic_results
    - top_stories
    - news_results (if present)
    - related_questions (sometimes has useful links)
    """
    data = fetch_serp_results_any(query, engine=engine, gl=gl, hl=hl, num=num)

    results: List[Dict[str, Any]] = []
    # Organic
    organic = data.get("organic_results") or []
    if isinstance(organic, list):
        for r in organic:
            if not isinstance(r, dict):
                continue
            link = (r.get("link") or "").strip()
            if not link:
                continue
            results.append({
                "title": r.get("title"),
                "link": link,
                "snippet": r.get("snippet"),
                "position": r.get("position"),
                "source": None,
                "date": None,
                "section": "organic_results",
            })

    # Top stories + news results (high impact for your use case)
    results.extend(_collect_links_from_section(data, "top_stories"))
    results.extend(_collect_links_from_section(data, "news_results"))

    # Related questions sometimes links out; keep it optional but cheap
    results.extend(_collect_links_from_section(data, "related_questions"))

    # De-dupe by link while preserving order
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in results:
        u = r.get("link")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def fetch_serp_organic_results(query: str, *, engine: str = "google", gl: str = "in", hl: str = "en", num: int = 10) -> List[Dict[str, Any]]:
    """
    Backward-compatible function used by older pipeline code.
    We now map 'fetch_serp_links' output to the older structure keys.
    """
    links = fetch_serp_links(query, engine=engine, gl=gl, hl=hl, num=num)
    # Convert to old format keys expected elsewhere
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(links):
        out.append({
            "title": r.get("title"),
            "link": r.get("link"),
            "snippet": r.get("snippet"),
            "position": r.get("position") or (i + 1),
            "section": r.get("section"),
            "source": r.get("source"),
            "date": r.get("date"),
            "domain": None,  # computed later via host_from_url in whitelist module
        })
    return out
