#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env, load_histories, send_discord_message, summarize_history_sources
from liubang.positions import (
    DEFAULT_MANUAL_POSITIONS_PATH,
    format_position_discord_message,
    format_position_summary,
    load_manual_positions,
    monitor_positions,
    write_position_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor manually entered positions for exits and partial exits.")
    parser.add_argument("--positions", default=str(DEFAULT_MANUAL_POSITIONS_PATH))
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--use-cache", action="store_true", help="Do not refresh 5-minute history before monitoring.")
    parser.add_argument("--extended-hours", action="store_true", help="Request extended-hours history, still evaluates regular-hours bars.")
    parser.add_argument("--max-hold-days", type=int, default=5)
    parser.add_argument("--send-discord", action="store_true", help="Send required position actions to Discord webhook.")
    parser.add_argument("--send-empty-discord", action="store_true", help="Also send a Discord message when no position action is required.")
    parser.add_argument("--discord-webhook-url", default=None)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    positions_path = Path(args.positions)
    try:
        positions = load_manual_positions(positions_path)
        symbols = tuple(position.symbol for position in positions)
        history_by_symbol = load_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            extended_hours=args.extended_hours,
            refresh=not args.use_cache,
            history_provider=args.history_provider,
            fallback_history_provider=args.fallback_history_provider,
            yfinance_period=args.yfinance_period,
        ) if symbols else {}
        report = monitor_positions(
            positions=positions,
            history_by_symbol=history_by_symbol,
            max_hold_days=args.max_hold_days,
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        report_path = Path(args.output_dir) / f"positions_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_position_report(report_path, report)

        if args.send_discord and (report["attention_count"] > 0 or args.send_empty_discord):
            webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
            if not webhook_url:
                raise ValueError("Missing Discord webhook URL. Set DISCORD_WEBHOOK_URL or pass --discord-webhook-url.")
            send_discord_message(webhook_url, format_position_discord_message(report))

    except Exception as exc:
        print(f"monitor_positions failed: {exc}", file=sys.stderr)
        return 1

    print(format_position_summary(report, report_path, positions_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
