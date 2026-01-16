from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
import os

from dotenv import load_dotenv
from serpapi import GoogleSearch

load_dotenv(".env", override=True)


def serpapi_key() -> str:
    k = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not k:
        raise RuntimeError("Missing SERPAPI_API_KEY in .env")
    return k


# ---------------------------
# Backward compatibility layer
# ---------------------------
def fetch_serp_response(
    params_or_query: Union[Dict[str, Any], str],
    *,
    engine: str = "google",
    gl: str = "in",
    hl: str = "en",
    num: int = 10,
) -> Dict[str, Any]:
    """
    Legacy compatibility: older code imports fetch_serp_response.

    Supports BOTH call styles:
      1) fetch_serp_response({"engine":"google","q":"...","api_key":"..."})
      2) fetch_serp_response("query string", gl="in", hl="en", num=10)

    Always ensures api_key is present (from .env if missing).
    """
    if isinstance(params_or_query, dict):
        params = dict(params_or_query)
        params.setdefault("engine", engine)
        params.setdefault("gl", gl)
        params.setdefault("hl", hl)
        params.setdefault("num", num)
        params.setdefault("api_key", serpapi_key())
        return GoogleSearch(params).get_dict()

    # string query path
    q = str(params_or_query)
    params = {
        "engine": engine,
        "q": q,
        "gl": gl,
        "hl": hl,
        "num": num,
        "api_key": serpapi_key(),
    }
    return GoogleSearch(params).get_dict()


def fetch_serp_results_any(
    query: str, *, engine: str = "google", gl: str = "in", hl: str = "en", num: int = 10
) -> Dict[str, Any]:
    """
    New internal helper (explicit query -> raw SerpAPI dict).
    """
    return fetch_serp_response(query, engine=engine, gl=gl, hl=hl, num=num)


def _collect_links_from_section(data: Dict[str, Any], section_key: str) -> List[Dict[str, Any]]:
    """
    Normalize SerpAPI sections into a common list of dicts with keys:
      title, link, snippet, position, source, date, section
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

        # top_stories sometimes contains nested "stories"
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

    results.extend(_collect_links_from_section(data, "top_stories"))
    results.extend(_collect_links_from_section(data, "news_results"))
    results.extend(_collect_links_from_section(data, "related_questions"))

    # De-dupe by link preserving order
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
    Backward-compatible function for older pipeline code: returns list of results.
    Now it includes links from top_stories/news_results too, but mapped to older keys.
    """
    links = fetch_serp_links(query, engine=engine, gl=gl, hl=hl, num=num)
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
            "domain": None,  # later computed by whitelist module
        })
    return out
