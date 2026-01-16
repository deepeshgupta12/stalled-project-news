from __future__ import annotations

import argparse

from .commands import cmd_ping, cmd_check_url
from .models import ProjectInput
from .serp_pipeline import run_serp_search, store_serp_run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stalled_news")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="Sanity check: configs + env wiring")

    c = sub.add_parser("check-url", help="Check if a URL is allowed by whitelist")
    c.add_argument("--url", required=True)

    s = sub.add_parser("serp-run", help="Run SerpAPI search + whitelist filter + store results")
    s.add_argument("--project_name", required=True)
    s.add_argument("--city", required=True)
    s.add_argument("--rera_id", required=False, default=None)

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
        run = run_serp_search(project)
        out_path = store_serp_run(run)
        print(f"stored: {out_path}")
        print(f"whitelisted_results: {run.results_whitelisted}")
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
