"""CHARGEGUARD command-line interface.

Examples:
  # Scan a feed and print a table (exits non-zero if any breach is found)
  python -m chargeguard scan demos/01-basic/feed.csv

  # JSON output for CI / piping into jq
  python -m chargeguard scan feed.json --format json | jq '.findings'

  # Restrict to a rolling window and treat warnings as failures too
  python -m chargeguard scan feed.csv --window-days 30 --fail-on warning

Exit codes:
  0  clean (no findings at/above --fail-on level)
  1  findings present at/above --fail-on level (CI gate trips)
  2  usage / input error
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import TOOL_NAME, TOOL_VERSION
from .core import Report, analyze_file


def _pct(x: float) -> str:
    return f"{x * 100:.3f}%"


def _render_table(report: Report) -> str:
    lines: List[str] = []
    lines.append(f"CHARGEGUARD {TOOL_VERSION} — chargeback / fraud ratio monitor")
    win = f"{report.window_days}d" if report.window_days else "all-time"
    lines.append(
        f"window={win}  records={report.records_used}/{report.records_total}  "
        f"merchants={len(report.windows)}"
    )
    lines.append("")

    # Merchant table
    header = f"{'MERCHANT':<16}{'SETTLED':>9}{'CB':>6}{'CB%':>9}{'CB$%':>9}{'FRAUD%':>9}"
    lines.append(header)
    lines.append("-" * len(header))
    for w in report.windows:
        lines.append(
            f"{w.merchant_id[:16]:<16}{w.settled_count:>9}{w.chargeback_count:>6}"
            f"{_pct(w.cb_ratio):>9}{_pct(w.cb_ratio_amount):>9}{_pct(w.fraud_ratio):>9}"
        )
    lines.append("")

    if not report.findings:
        lines.append("OK: no threshold breaches or warnings.")
        return "\n".join(lines)

    lines.append("FINDINGS:")
    for f in report.findings:
        tag = "BREACH " if f.level == "breach" else "WARNING"
        over = "OVER" if f.headroom < 0 else "at"
        lines.append(
            f"  [{tag}] {f.merchant_id}: {f.threshold} — "
            f"{_pct(f.value)} {over} ceiling {_pct(f.ceiling)}"
        )
        if f.note:
            lines.append(f"           {f.note}")
    lines.append("")
    lines.append(
        f"SUMMARY: {len(report.breaches)} breach(es), {len(report.warnings)} warning(s)."
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Monitor chargeback feeds and flag fraud-rate threshold "
                    "breaches before you cross Visa VAMP ratio ceilings.",
        epilog="Example: python -m chargeguard scan feed.csv --format json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    scan = sub.add_parser(
        "scan",
        help="Scan a chargeback/transaction feed (CSV or JSON) for ratio breaches.",
        description="Aggregate a feed per-merchant and evaluate dispute/fraud "
                    "ratios against VAMP-style thresholds.",
    )
    scan.add_argument("feed", help="Path to feed file (.csv or .json).")
    scan.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="Output format (default: table).",
    )
    scan.add_argument(
        "--input-format", choices=("auto", "csv", "json"), default="auto",
        help="Force feed parser (default: auto-detect).",
    )
    scan.add_argument(
        "--window-days", type=int, default=None,
        help="Only consider records within N days of the most recent record.",
    )
    scan.add_argument(
        "--fail-on", choices=("breach", "warning", "never"), default="breach",
        help="Minimum finding level that causes a non-zero exit (default: breach).",
    )
    return parser


def _should_fail(report: Report, fail_on: str) -> bool:
    if fail_on == "never":
        return False
    if fail_on == "warning":
        return bool(report.findings)
    return bool(report.breaches)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "scan":
        try:
            report = analyze_file(
                args.feed,
                window_days=args.window_days,
                fmt=args.input_format,
            )
        except FileNotFoundError:
            print(f"error: feed not found: {args.feed}", file=sys.stderr)
            return 2
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"error: could not parse feed: {exc}", file=sys.stderr)
            return 2

        if args.format == "json":
            print(json.dumps(report.as_dict(), indent=2))
        else:
            print(_render_table(report))

        return 1 if _should_fail(report, args.fail_on) else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
