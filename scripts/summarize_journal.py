#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env
from liubang.defaults import DEFAULT_INITIAL_EQUITY
from liubang.journal import (
    DEFAULT_TRADE_JOURNAL_PATH,
    format_journal_summary,
    load_journal_lots,
    summarize_journal,
    write_journal_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize manually recorded closed trades.")
    parser.add_argument("--journal", default=str(DEFAULT_TRADE_JOURNAL_PATH))
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--report-prefix", default="journal")
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    journal_path = Path(args.journal)
    try:
        lots = load_journal_lots(journal_path)
        report = summarize_journal(
            lots=lots,
            initial_equity=args.initial_equity,
            path=journal_path,
        )
        report_path = Path(args.output_dir) / f"{safe_report_prefix(args.report_prefix)}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_journal_report(report_path, report)
    except Exception as exc:
        print(f"summarize_journal failed: {exc}", file=sys.stderr)
        return 1

    print(format_journal_summary(report, report_path))
    return 0


def safe_report_prefix(value: str) -> str:
    prefix = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value.strip())
    return prefix or "journal"


if __name__ == "__main__":
    raise SystemExit(main())
