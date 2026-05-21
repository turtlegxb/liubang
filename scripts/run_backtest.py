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

from liubang.backtest import (
    BacktestParams,
    SCORING_MODES,
    format_backtest_summary,
    run_backtest,
    write_backtest_report,
    write_backtest_trades_csv,
)
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories, summarize_history_sources
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
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the baseline Liubang pullback backtest.")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols. Overrides --universe. SPY and QQQ are added automatically.",
    )
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH), help="Universe JSON file.")
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=32)
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--refresh", action="store_true", help="Refresh Schwab cache before running.")
    parser.add_argument("--extended-hours", action="store_true", help="Request extended-hours history.")
    parser.add_argument("--cache-dir", default="data/cache", help="History cache directory.")
    parser.add_argument("--output-dir", default="data/exports", help="Report output directory.")
    parser.add_argument("--trades-csv", default=None, help="Optional CSV path for flattened closed trades.")
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
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
        history_by_symbol = load_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            extended_hours=args.extended_hours,
            refresh=args.refresh,
            history_provider=args.history_provider,
            fallback_history_provider=args.fallback_history_provider,
            yfinance_period=args.yfinance_period,
        )
        params = BacktestParams(
            initial_equity=args.initial_equity,
            min_score=args.min_score,
            min_pullback_pct=args.min_pullback_pct,
            max_pullback_pct=args.max_pullback_pct,
            scoring_mode=args.scoring_mode,
            hard_stop_pct=args.hard_stop_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            max_position_pct=args.max_position_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        report = run_backtest(
            history_by_symbol,
            symbols=symbols,
            params=params,
            earnings_calendar=earnings_calendar,
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        report_path = Path(args.output_dir) / f"backtest_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_backtest_report(report_path, report)
        if args.trades_csv:
            write_backtest_trades_csv(Path(args.trades_csv), report["trades"])
    except Exception as exc:
        print(f"run_backtest failed: {exc}", file=sys.stderr)
        return 1

    print(format_backtest_summary(report, report_path))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
