from __future__ import annotations

import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from serpapi import GoogleSearch

from .config import repo_root


def _ensure_env_loaded() -> None:
    load_dotenv(repo_root() / ".env", override=True)


def serpapi_key() -> str:
    _ensure_env_loaded()
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing SERPAPI_API_KEY in .env (repo root)")
    return key


def fetch_serp_organic_results(
    query: str,
    *,
    engine: str = "google",
    gl: str = "in",
    hl: str = "en",
    num: int = 20,
) -> List[Dict[str, Any]]:
    """
    Returns the raw 'organic_results' list from SerpAPI response.
    """
    params = {
        "engine": engine,
        "q": query,
        "gl": gl,
        "hl": hl,
        "num": num,
        "api_key": serpapi_key(),
    }
    search = GoogleSearch(params)
    data = search.get_dict()
    organic = data.get("organic_results") or []
    if not isinstance(organic, list):
        return []
    return organic
