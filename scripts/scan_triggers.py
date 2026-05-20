#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env, load_histories, send_discord_message, summarize_history_sources
from liubang.triggers import (
    format_trigger_discord_message,
    format_trigger_summary,
    load_latest_signal_report,
    scan_intraday_triggers,
    sizing_from_signal_report,
    write_trigger_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan watchlist symbols for intraday 5m entry triggers.")
    parser.add_argument("--signals-report", default=None, help="Signal report JSON. Defaults to latest data/exports/signals_*.json.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--use-cache", action="store_true", help="Do not refresh 5-minute history before scanning.")
    parser.add_argument("--extended-hours", action="store_true", help="Request extended-hours history, still evaluates regular-hours bars.")
    parser.add_argument("--current-session-date", default=None, help="Override current ET session date, YYYY-MM-DD.")
    parser.add_argument("--account-equity", type=float, default=None)
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument("--max-position-pct", type=float, default=None)
    parser.add_argument("--hard-stop-pct", type=float, default=None)
    parser.add_argument("--first-target-r", type=float, default=None)
    parser.add_argument("--weak-regime-size-multiplier", type=float, default=None)
    parser.add_argument("--observation-only", action="store_true", help="Record technical triggers but suppress manual entry templates.")
    parser.add_argument("--send-discord", action="store_true", help="Send triggered entries to Discord webhook.")
    parser.add_argument("--send-empty-discord", action="store_true", help="Also send a Discord message when no symbols triggered.")
    parser.add_argument("--discord-webhook-url", default=None)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    try:
        if args.signals_report:
            signal_path = Path(args.signals_report)
            signal_report = json.loads(signal_path.read_text(encoding="utf-8"))
        else:
            signal_path, signal_report = load_latest_signal_report(Path(args.output_dir))

        symbols = tuple(
            str(item.get("symbol") or "").upper()
            for item in signal_report.get("watchlist", [])
            if item.get("symbol")
        )
        if symbols:
            history_by_symbol = load_histories(
                symbols=symbols,
                cache_dir=Path(args.cache_dir),
                extended_hours=args.extended_hours,
                refresh=not args.use_cache,
                history_provider=args.history_provider,
                fallback_history_provider=args.fallback_history_provider,
                yfinance_period=args.yfinance_period,
            )
        else:
            history_by_symbol = {}

        current_session_date = date.fromisoformat(args.current_session_date) if args.current_session_date else None
        report = scan_intraday_triggers(
            signal_report=signal_report,
            history_by_symbol=history_by_symbol,
            current_session_date=current_session_date,
            sizing=resolve_sizing(args, signal_report),
            observation_only=args.observation_only,
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        report_path = Path(args.output_dir) / f"triggers_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_trigger_report(report_path, report)

        if args.send_discord and (report["triggered_count"] > 0 or args.send_empty_discord):
            webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
            if not webhook_url:
                raise ValueError("Missing Discord webhook URL. Set DISCORD_WEBHOOK_URL or pass --discord-webhook-url.")
            send_discord_message(webhook_url, format_trigger_discord_message(report))

    except Exception as exc:
        print(f"scan_triggers failed: {exc}", file=sys.stderr)
        return 1

    print(format_trigger_summary(report, report_path, signal_path))
    return 0


def resolve_sizing(args: argparse.Namespace, signal_report: dict):
    sizing = sizing_from_signal_report(signal_report)
    values = sizing.__dict__.copy()
    for key in (
        "account_equity",
        "risk_per_trade_pct",
        "max_position_pct",
        "hard_stop_pct",
        "first_target_r",
        "weak_regime_size_multiplier",
    ):
        override = getattr(args, key)
        if override is not None:
            values[key] = override
    return type(sizing)(**values)


if __name__ == "__main__":
    raise SystemExit(main())
