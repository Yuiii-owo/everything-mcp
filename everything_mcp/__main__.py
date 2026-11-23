"""Entry point: run the MCP server, or `--selftest` to verify the local setup."""

from __future__ import annotations

import argparse
import json
import os
import sys


def run_selftest() -> int:
    from . import sdk
    from .errors import EverythingError

    try:
        instance = sdk.get_sdk()
    except EverythingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        return 1

    report: dict = {"status": instance.status()}
    try:
        report["sample_search"] = instance.query("ext:exe", max_results=5)
    except EverythingError as exc:
        report["sample_search_error"] = str(exc)
    report["ok"] = bool(report["status"].get("everything_running"))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="everything-mcp",
        description="MCP server exposing the Everything SDK for fast Windows file search.",
    )
    parser.add_argument(
        "--dll",
        help="Path to the Everything SDK DLL (overrides the EVERYTHING_MCP_DLL env var).",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Print SDK status and a sample search, then exit (does not start the server).",
    )
    args = parser.parse_args(argv)

    if args.dll:
        os.environ["EVERYTHING_MCP_DLL"] = args.dll

    if args.selftest:
        return run_selftest()

    from .server import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
