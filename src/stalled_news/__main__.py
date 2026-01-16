from __future__ import annotations

import argparse
from pathlib import Path

from .commands import cmd_ping, cmd_check_url
from .models import ProjectInput
from .serp_pipeline import run_serp_search_with_debug, store_serp_run_with_debug
from .serp_wide_pipeline import run_serp_wide
from .evidence_pipeline import fetch_and_extract_from_serp
from .event_extractor import extract_events_from_evidence, store_events
from .news_generator import build_news_with_openai


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

    sw = sub.add_parser("serp-run-wide", help="Wider SERP sweep (adds news/general queries) + whitelist filter")
    sw.add_argument("--project_name", required=True)
    sw.add_argument("--city", required=True)
    sw.add_argument("--rera_id", required=False, default=None)

    f = sub.add_parser("fetch-extract", help="Fetch + extract content for a stored serp_results.json")
    f.add_argument("--serp_results", required=True, help="Path to serp_results.json from artifacts")

    e = sub.add_parser("extract-events", help="Extract dated events from evidence.json (strict snippet-backed)")
    e.add_argument("--evidence", required=True, help="Path to evidence.json in artifacts run dir")
    e.add_argument("--min_conf", required=False, default="0.55", help="Minimum confidence threshold (default 0.55)")

    n = sub.add_parser("render-news", help="Generate news.json + news.html using OpenAI (evidence-bounded)")
    n.add_argument("--project_name", required=True)
    n.add_argument("--city", required=True)
    n.add_argument("--rera_id", required=False, default=None)
    n.add_argument("--run_dir", required=True, help="Artifacts run dir containing events_deduped.json")
    n.add_argument("--events", required=False, default="events_deduped.json")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "ping":
        cmd_ping()
        return

    if args.command == "check-url":
        cmd_check_url(args.url)
        return

    if args.command == "serp-run":
        project = ProjectInput(project_name=args.project_name, city=args.city, rera_id=args.rera_id)
        run, all_debug, domain_counts, raw_debug = run_serp_search_with_debug(project)
        out_path = store_serp_run_with_debug(run, all_debug, domain_counts, raw_debug)
        print(f"stored: {out_path}")
        print(f"whitelisted_results: {run.results_whitelisted}")
        return

    if args.command == "serp-run-wide":
        project = ProjectInput(project_name=args.project_name, city=args.city, rera_id=args.rera_id)
        wide = run_serp_wide(project)
        # Store in the same serp_results.json shape expected by fetch-extract
        out_dir = Path("artifacts") / f"{project.project_name.lower().replace(' ','-')}-{project.city.lower().replace(' ','-')}" + (f"-{project.rera_id.lower().replace('/','-')}" if project.rera_id else "")
        out_dir = Path("artifacts") / out_dir.name
        run_id = __import__("datetime").datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        target = out_dir / run_id
        target.mkdir(parents=True, exist_ok=True)

        (target / "serp_results_all.json").write_text(__import__("json").dumps(wide.all_results, indent=2, ensure_ascii=False), encoding="utf-8")
        (target / "serp_domains_summary.json").write_text(__import__("json").dumps(wide.domain_counts, indent=2, ensure_ascii=False), encoding="utf-8")
        (target / "serp_results.json").write_text(__import__("json").dumps(wide.whitelisted, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"stored: {target / 'serp_results.json'}")
        print(f"whitelisted_results: {len(wide.whitelisted)}")
        return

    if args.command == "fetch-extract":
        p = Path(args.serp_results).expanduser().resolve()
        out = fetch_and_extract_from_serp(p)
        print(f"evidence_stored: {out}")
        return

    if args.command == "extract-events":
        evp = Path(args.evidence).expanduser().resolve()
        min_conf = float(args.min_conf)
        raw, deduped = extract_events_from_evidence(evp, min_confidence=min_conf)
        raw_path, deduped_path, timeline_path = store_events(evp, raw, deduped)
        print(f"events_raw: {raw_path}")
        print(f"events_deduped: {deduped_path}")
        print(f"timeline: {timeline_path}")
        print(f"raw_count={len(raw)} deduped_count={len(deduped)}")
        return

    if args.command == "render-news":
        project = ProjectInput(project_name=args.project_name, city=args.city, rera_id=args.rera_id)
        run_dir = Path(args.run_dir).expanduser().resolve()
        events_path = (run_dir / args.events).resolve()

        news_json, news_html, inputs_json, raw_json = build_news_with_openai(
            project=project,
            run_dir=run_dir,
            events_deduped_path=events_path,
        )
        print(f"news_json: {news_json}")
        print(f"news_html: {news_html}")
        print(f"news_inputs: {inputs_json}")
        print(f"news_llm_raw: {raw_json}")
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
