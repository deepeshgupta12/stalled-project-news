from __future__ import annotations

from rich import print

from .config import load_config, repo_root, load_yaml
from . import __version__
from .whitelist import WhitelistPolicy, is_url_allowed


def cmd_ping() -> None:
    cfg = load_config()
    root = repo_root()

    print(f"[bold]stalled-project-news[/bold] version={__version__}")
    print(f"env={cfg.env}")
    print(f"repo_root={root}")

    settings_path = root / "configs" / "settings.yaml"
    whitelist_path = root / "configs" / "whitelist.yaml"
    print(f"settings.yaml exists={settings_path.exists()}")
    print(f"whitelist.yaml exists={whitelist_path.exists()}")

    print(f"OPENAI_API_KEY present={cfg.openai_api_key_present}")
    print(f"SERPAPI_API_KEY present={cfg.serpapi_api_key_present}")

    print(f"whitelist domains count={len(cfg.whitelist_domains)}")
    if cfg.whitelist_domains:
        print("first few domains:")
        for d in cfg.whitelist_domains[:12]:
            print(f"  - {d}")

    base_dir = cfg.settings.get("artifacts", {}).get("base_dir", "artifacts")
    artifacts_dir = (root / str(base_dir)).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"artifacts_dir={artifacts_dir}")


def cmd_check_url(url: str) -> None:
    root = repo_root()
    wl = load_yaml(root / "configs" / "whitelist.yaml")
    domains = wl.get("domains", [])
    sub_allowed = wl.get("subdomain_allowed", [])
    policy = WhitelistPolicy.from_config(domains, sub_allowed)

    allowed = is_url_allowed(url, policy)
    print(f"url={url}")
    print(f"allowed={allowed}")
