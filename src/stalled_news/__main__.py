from __future__ import annotations

import argparse
from pathlib import Path

from .commands import cmd_ping, cmd_check_url
from .models import ProjectInput
from .serp_pipeline import run_serp_search_with_debug, store_serp_run_with_debug
from .evidence_pipeline import fetch_and_extract_from_serp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stalled_news")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="Sanity check: configs + env wiring")

    c = sub.add_parser("check-url", help="Check if a URL is allowed by whitelist")
    c.add_argument("--url", required=True)

    s = sub.add_parser("serp-run", help="Run SerpAPI search + whitelist filter + store results (+debug files)")
    s.add_argument("--project_name", required=True)
    s.add_argument("--city", required=True)
    s.add_argument("--rera_id", required=False, default=None)

    f = sub.add_parser("fetch-extract", help="Fetch + extract content for a stored serp_results.json")
    f.add_argument("--serp_results", required=True, help="Path to serp_results.json from artifacts")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "ping":
        cmd_ping()
    elif args.command == "check-url":
        cmd_check_url(args.url)
    elif args.command == "serp-run":
        project = ProjectInput(project_name=args.project_name, city=args.city, rera_id=args.rera_id)
        run, all_debug, domain_counts, raw_debug = run_serp_search_with_debug(project)
        out_path = store_serp_run_with_debug(run, all_debug, domain_counts, raw_debug)
        print(f"stored: {out_path}")
        print(f"whitelisted_results: {run.results_whitelisted}")
    elif args.command == "fetch-extract":
        p = Path(args.serp_results).expanduser().resolve()
        out = fetch_and_extract_from_serp(p)
        print(f"evidence_stored: {out}")
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
