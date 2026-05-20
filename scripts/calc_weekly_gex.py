#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env, required_env
from liubang.gex import DEFAULT_MAX_STRIKE_DISTANCE_PCT, summarize_weekly_gex
from liubang.options import DEFAULT_OPTIONS_CACHE_DIR, SchwabOptionChainCache
from liubang.schwab_adapter import RetrySettings, SchwabAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate current-week GEX from a Schwab option chain.")
    parser.add_argument("symbol", help="Ticker symbol, for example AMD.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_OPTIONS_CACHE_DIR))
    parser.add_argument("--strike-count", type=int, default=80)
    parser.add_argument("--max-dte", type=int, default=7)
    parser.add_argument("--max-strike-distance-pct", type=float, default=DEFAULT_MAX_STRIKE_DISTANCE_PCT)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of-date", default=None, help="Override ET as-of date, YYYY-MM-DD.")
    parser.add_argument("--json-output", default=None, help="Optional path to write the full GEX summary JSON.")
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    try:
        adapter = SchwabAdapter(
            app_key=required_env("SCHWAB_APP_KEY"),
            app_secret=required_env("SCHWAB_APP_SECRET"),
            token_path=Path(required_env("SCHWAB_TOKEN_PATH")).expanduser().resolve(),
            retry_settings=RetrySettings(
                request_sleep_seconds=env_float("SCHWAB_REQUEST_SLEEP_SECONDS", 0.5),
                max_retries=env_int("SCHWAB_MAX_RETRIES", 3),
                backoff_seconds=env_float("SCHWAB_BACKOFF_SECONDS", 2.0),
            ),
        )
        cache = SchwabOptionChainCache(
            adapter=adapter,
            cache_dir=Path(args.cache_dir),
            strike_count=max(1, args.strike_count),
            max_dte=max(0, args.max_dte),
        )
        chain = cache.get_chain(args.symbol.upper(), refresh=args.refresh)
        summary = summarize_weekly_gex(
            chain,
            as_of_date=args.as_of_date,
            max_strike_distance_pct=args.max_strike_distance_pct,
        )
        if args.json_output:
            output_path = Path(args.json_output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(format_weekly_gex(summary, json_output=args.json_output))
    except Exception as exc:
        print(f"calc_weekly_gex failed: {exc}", file=sys.stderr)
        return 1
    return 0


def format_weekly_gex(summary: dict, *, json_output: str | None = None) -> str:
    lines = [
        f"Weekly GEX | {summary.get('symbol')}",
        f"Source: {summary.get('source')} | unit={summary.get('unit')}",
        f"As of ET date: {summary.get('as_of_date')} | week_end={summary.get('week_end_date')}",
        f"Spot: {format_price(summary.get('spot'))}",
        (
            "Contracts: "
            f"{summary.get('contract_count')} used / {summary.get('available_contract_count')} available | "
            f"expirations={','.join(summary.get('expirations') or []) or 'none'}"
        ),
        f"Call GEX: {format_money(summary.get('call_gex'))}",
        f"Put GEX: {format_money(summary.get('put_gex'))}",
        f"Net GEX: {format_money(summary.get('net_gex'))} | regime={summary.get('regime')}",
        (
            "Walls: "
            f"call={format_price(summary.get('call_wall'))} "
            f"put={format_price(summary.get('put_wall'))} "
            f"max_abs={format_price(summary.get('max_abs_gamma_strike'))}"
        ),
        f"Risk notes: {','.join(summary.get('risk_notes') or []) or 'none'}",
    ]
    top_strikes = summary.get("top_strikes") or []
    if top_strikes:
        lines.append("Top strikes:")
        for item in top_strikes[:5]:
            lines.append(
                "  "
                f"{format_price(item.get('strike'))}: "
                f"net={format_money(item.get('net_gex'))} "
                f"call={format_money(item.get('call_gex'))} "
                f"put={format_money(item.get('put_gex'))} "
                f"oiC={item.get('call_open_interest')} oiP={item.get('put_open_interest')}"
            )
    if json_output:
        lines.append(f"JSON: {json_output}")
    return "\n".join(lines)


def format_money(value: object) -> str:
    number = as_float(value)
    if number is None:
        return "n/a"
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= divisor:
            return f"{sign}${absolute / divisor:.2f}{suffix}"
    return f"{sign}${absolute:,.2f}"


def format_price(value: object) -> str:
    number = as_float(value)
    return "n/a" if number is None else f"{number:.2f}"


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def as_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
