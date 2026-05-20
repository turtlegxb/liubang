#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams, format_backtest_summary, run_backtest
from liubang.cli_utils import load_earnings_for_symbols, load_env, summarize_history_sources
from liubang.defaults import (
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_PULLBACK_PCT,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SCORE,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH
from liubang.market_data import YFinanceHistoryCache
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a yfinance 1-hour intraday proxy backtest for longer-history validation.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=64)
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    symbols = resolve_symbols(
        raw_symbols=args.symbols,
        universe_path=Path(args.universe),
        include_benchmarks=True,
    )
    try:
        history_by_symbol = load_hourly_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            period=args.period,
            interval=args.interval,
            refresh=args.refresh,
        )
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        params = BacktestParams(
            initial_equity=args.initial_equity,
            min_score=args.min_score,
            min_pullback_pct=args.min_pullback_pct,
            max_pullback_pct=args.max_pullback_pct,
            hard_stop_pct=args.hard_stop_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            max_position_pct=args.max_position_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        report = run_backtest(
            history_by_symbol,
            symbols=symbols,
            params=params,
            earnings_calendar=earnings_calendar,
        )
        report["generated_at"] = datetime.now(UTC).isoformat()
        report["mode"] = "hourly_proxy_backtest"
        report["limitations"] = [
            "Uses yfinance 1-hour bars; it is longer than the Schwab 5-minute sample but still not a 5-minute trigger test.",
            "The same intraday VWAP/prior-high trigger engine is applied to hourly bars.",
            "Intraday exits are evaluated only at hourly bar high/low granularity.",
        ]
        report["config"]["period"] = args.period
        report["config"]["interval"] = args.interval
        report["config"]["params"] = asdict(params)
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        output_path = Path(args.output_dir) / f"hourly_proxy_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"run_hourly_proxy_backtest failed: {exc}", file=sys.stderr)
        return 1

    print(format_hourly_proxy_summary(report, output_path))
    return 0


def load_hourly_histories(
    *,
    symbols: tuple[str, ...],
    cache_dir: Path,
    period: str,
    interval: str,
    refresh: bool,
) -> dict[str, dict]:
    cache = YFinanceHistoryCache(cache_dir=cache_dir, period=period, interval=interval)
    histories = {}
    for symbol in symbols:
        payload = cache.get_history(symbol, refresh=refresh)
        histories[symbol] = payload
    return histories


def format_hourly_proxy_summary(report: dict, path: Path) -> str:
    summary = report["summary"]
    diagnostics = report["diagnostics"]
    lines = [
        "Hourly proxy backtest summary",
        "Mode: yfinance 1-hour proxy, not 5-minute trigger verified",
        f"Symbols: {', '.join(report['config']['symbols'])}",
        f"Candidates: {report['candidate_count']}",
        f"Trades: {summary['trade_count']}",
        f"Entry attempts: {diagnostics.get('entry_attempts')} filled={diagnostics.get('filled_entries')} unfilled={diagnostics.get('unfilled_entries')}",
        f"Win rate: {summary['win_rate']:.2%}",
        f"Total PnL: {summary['total_pnl']}",
        f"Return: {summary['return_pct']}%",
        f"Average R: {summary.get('average_r')}",
        f"Profit factor: {summary.get('profit_factor')}",
        f"Max drawdown: {summary['max_drawdown_pct']}%",
        f"History sources: {format_history_sources(report.get('history_sources') or {})}",
        f"Report: {path}",
    ]
    return "\n".join(lines)


def format_history_sources(summary: dict) -> str:
    by_source = summary.get("by_source") or {}
    counts = ", ".join(f"{source}={count}" for source, count in sorted(by_source.items()))
    fallback_count = summary.get("fallback_count") or 0
    return f"{counts or 'none'} fallbacks={fallback_count}"


if __name__ == "__main__":
    raise SystemExit(main())
