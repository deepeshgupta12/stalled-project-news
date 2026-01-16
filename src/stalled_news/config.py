from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv


def repo_root() -> Path:
    # Assumes this file lives at: repo/src/stalled_news/config.py
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class AppConfig:
    env: str
    openai_api_key_present: bool
    serpapi_api_key_present: bool
    whitelist_domains: List[str]
    settings: Dict[str, Any]


def load_config(
    settings_path: Optional[Path] = None,
    whitelist_path: Optional[Path] = None,
) -> AppConfig:
    load_dotenv(repo_root() / ".env")

    settings_path = settings_path or (repo_root() / "configs" / "settings.yaml")
    whitelist_path = whitelist_path or (repo_root() / "configs" / "whitelist.yaml")

    settings = load_yaml(settings_path)
    whitelist = load_yaml(whitelist_path)

    domains = whitelist.get("domains", [])
    if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
        raise ValueError("configs/whitelist.yaml must contain: domains: [..strings..]")

    env = os.getenv("APP_ENV", settings.get("app", {}).get("env", "local"))

    openai_key = os.getenv("OPENAI_API_KEY", "")
    serpapi_key = os.getenv("SERPAPI_API_KEY", "")

    return AppConfig(
        env=str(env),
        openai_api_key_present=bool(openai_key.strip()),
        serpapi_api_key_present=bool(serpapi_key.strip()),
        whitelist_domains=[d.strip().lower() for d in domains if d.strip()],
        settings=settings,
    )
