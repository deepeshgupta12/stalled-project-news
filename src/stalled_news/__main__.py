from __future__ import annotations

import argparse

from .commands import cmd_ping


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stalled_news")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="Sanity check: configs + env wiring")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "ping":
        cmd_ping()
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
