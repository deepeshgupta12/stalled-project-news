from __future__ import annotations

import argparse
from pathlib import Path

from .commands import cmd_ping, cmd_check_url
from .models import ProjectInput
from .serp_pipeline import run_serp_search_with_debug, store_serp_run_with_debug
from .evidence_pipeline import fetch_and_extract_from_serp
from .event_extractor import extract_events_from_evidence, store_events


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

    e = sub.add_parser("extract-events", help="Extract dated events from evidence.json (strict snippet-backed)")
    e.add_argument("--evidence", required=True, help="Path to evidence.json in artifacts run dir")
    e.add_argument("--min_conf", required=False, default="0.55", help="Minimum confidence threshold (default 0.55)")

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
    elif args.command == "extract-events":
        evp = Path(args.evidence).expanduser().resolve()
        min_conf = float(args.min_conf)
        raw, deduped = extract_events_from_evidence(evp, min_confidence=min_conf)
        raw_path, deduped_path, timeline_path = store_events(evp, raw, deduped)
        print(f"events_raw: {raw_path}")
        print(f"events_deduped: {deduped_path}")
        print(f"timeline: {timeline_path}")
        print(f"raw_count={len(raw)} deduped_count={len(deduped)}")
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
