#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams, run_backtest
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories, parse_float_grid
from liubang.defaults import DEFAULT_INITIAL_EQUITY
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols
from scripts.sweep_cooldown import parse_int_list, symbol_concentration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep baseline pullback backtest parameters.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
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
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--min-scores", default="7,8,9")
    parser.add_argument("--max-pullback-pcts", default="0.04,0.05,0.06")
    parser.add_argument("--hard-stop-pcts", default="0.03,0.04,0.05")
    parser.add_argument("--symbol-cooldown-days", default="0")
    parser.add_argument("--min-trades", type=int, default=20)
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
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        rows = []
        for min_score in parse_float_grid(args.min_scores):
            for max_pullback_pct in parse_float_grid(args.max_pullback_pcts):
                for hard_stop_pct in parse_float_grid(args.hard_stop_pcts):
                    for cooldown_days in parse_int_list(args.symbol_cooldown_days):
                        params = BacktestParams(
                            initial_equity=args.initial_equity,
                            min_score=min_score,
                            max_pullback_pct=max_pullback_pct,
                            hard_stop_pct=hard_stop_pct,
                            symbol_cooldown_days=cooldown_days,
                        )
                        report = run_backtest(
                            history_by_symbol,
                            symbols=symbols,
                            params=params,
                            earnings_calendar=earnings_calendar,
                        )
                        summary = report["summary"]
                        diagnostics = report.get("diagnostics", {})
                        concentration = symbol_concentration(summary)
                        rows.append(
                            {
                                "min_score": min_score,
                                "max_pullback_pct": max_pullback_pct,
                                "hard_stop_pct": hard_stop_pct,
                                "symbol_cooldown_days": cooldown_days,
                                "candidate_count": report["candidate_count"],
                                "trade_count": summary["trade_count"],
                                "win_rate": summary["win_rate"],
                                "total_pnl": summary["total_pnl"],
                                "return_pct": summary["return_pct"],
                                "max_drawdown_pct": summary["max_drawdown_pct"],
                                "average_pnl": summary["average_pnl"],
                                "average_r": summary.get("average_r"),
                                "profit_factor": summary.get("profit_factor"),
                                "top1_symbol": concentration.get("top1_symbol"),
                                "top1_net_share": concentration.get("top1_net_share"),
                                "top3_symbols": ",".join(concentration.get("top3_symbols") or []),
                                "top3_net_share": concentration.get("top3_net_share"),
                                "entry_attempts": diagnostics.get("entry_attempts"),
                                "filled_entries": diagnostics.get("filled_entries"),
                                "unfilled_entries": diagnostics.get("unfilled_entries"),
                                "skipped_no_slot": diagnostics.get("skipped_no_slot"),
                                "skipped_symbol_cooldown": diagnostics.get("skipped_symbol_cooldown"),
                            }
                        )
        output_path = Path(args.output_dir) / f"sweep_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.csv"
        write_rows(output_path, rows)
    except Exception as exc:
        print(f"sweep_backtest failed: {exc}", file=sys.stderr)
        return 1

    ranked = sorted(
        rows,
        key=lambda row: (
            row["trade_count"] >= args.min_trades,
            row["return_pct"],
            -row["max_drawdown_pct"],
        ),
        reverse=True,
    )
    print("Sweep summary")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Rows: {len(rows)}")
    print("Top configurations:")
    for row in ranked[:10]:
        print(
            "  "
            f"score>={row['min_score']} max_pb={row['max_pullback_pct']:.3f} "
            f"stop={row['hard_stop_pct']:.3f} cooldown={row['symbol_cooldown_days']} "
            f"trades={row['trade_count']} "
            f"win={row['win_rate']:.2%} ret={row['return_pct']}% "
            f"dd={row['max_drawdown_pct']}% avgR={row['average_r']} "
            f"pf={row['profit_factor']} top1={row['top1_net_share']} top3={row['top3_net_share']}"
        )
    print(f"CSV: {output_path}")
    return 0


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "min_score",
        "max_pullback_pct",
        "hard_stop_pct",
        "symbol_cooldown_days",
        "candidate_count",
        "trade_count",
        "win_rate",
        "total_pnl",
        "return_pct",
        "max_drawdown_pct",
        "average_pnl",
        "average_r",
        "profit_factor",
        "top1_symbol",
        "top1_net_share",
        "top3_symbols",
        "top3_net_share",
        "entry_attempts",
        "filled_entries",
        "unfilled_entries",
        "skipped_no_slot",
        "skipped_symbol_cooldown",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
