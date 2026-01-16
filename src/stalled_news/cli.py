from __future__ import annotations

from rich import print
import typer

from .config import load_config, repo_root
from . import __version__

app = typer.Typer(add_completion=False)


@app.command()
def ping() -> None:
    """
    Sanity check: config files, env wiring, and basic repo paths.
    """
    cfg = load_config()
    root = repo_root()

    print(f"[bold]stalled-project-news[/bold] version={__version__}")
    print(f"env={cfg.env}")
    print(f"repo_root={root}")

    # Config presence
    settings_path = root / "configs" / "settings.yaml"
    whitelist_path = root / "configs" / "whitelist.yaml"
    print(f"settings.yaml exists={settings_path.exists()}")
    print(f"whitelist.yaml exists={whitelist_path.exists()}")

    # Key presence only (never print keys)
    print(f"OPENAI_API_KEY present={cfg.openai_api_key_present}")
    print(f"SERPAPI_API_KEY present={cfg.serpapi_api_key_present}")

    # Whitelist summary
    print(f"whitelist domains count={len(cfg.whitelist_domains)}")
    if cfg.whitelist_domains:
        print("first few domains:")
        for d in cfg.whitelist_domains[:12]:
            print(f"  - {d}")

    # Artifacts dir
    base_dir = cfg.settings.get("artifacts", {}).get("base_dir", "artifacts")
    artifacts_dir = (root / str(base_dir)).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"artifacts_dir={artifacts_dir}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
