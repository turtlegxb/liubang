#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams, NON_TRADABLE_CONTEXT_SYMBOLS, SCORING_MODES, run_backtest
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories
from liubang.defaults import (
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_PULLBACK_PCT,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SCORE,
)
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH
from liubang.universe import DEFAULT_UNIVERSE_PATH, load_universe, resolve_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run theme-removal ablation tests against the current backtest.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--earnings-limit", type=int, default=32)
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    universe_path = Path(args.universe)
    symbols = resolve_symbols(
        raw_symbols=args.symbols,
        universe_path=universe_path,
        include_benchmarks=True,
    )
    theme_map = load_theme_map(universe_path)
    theme_symbols = group_symbols_by_theme(symbols, theme_map)

    try:
        history_by_symbol = load_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            extended_hours=False,
            refresh=False,
            history_provider=args.history_provider,
            fallback_history_provider=args.fallback_history_provider,
            yfinance_period=args.yfinance_period,
        )
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=False,
            limit=args.earnings_limit,
        )
        params = BacktestParams(
            initial_equity=args.initial_equity,
            min_score=args.min_score,
            min_pullback_pct=args.min_pullback_pct,
            max_pullback_pct=args.max_pullback_pct,
            scoring_mode=args.scoring_mode,
            hard_stop_pct=args.hard_stop_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        baseline = run_backtest(
            history_by_symbol,
            symbols=symbols,
            params=params,
            earnings_calendar=earnings_calendar,
        )
        rows = []
        for theme, removed_symbols in sorted(theme_symbols.items()):
            ablated_symbols = tuple(symbol for symbol in symbols if symbol not in removed_symbols)
            report = run_backtest(
                history_by_symbol,
                symbols=ablated_symbols,
                params=params,
                earnings_calendar=earnings_calendar,
            )
            rows.append(build_row(theme, removed_symbols, baseline, report))

        output_path = Path(args.output_dir) / f"theme_ablation_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.csv"
        write_rows(output_path, rows)
    except Exception as exc:
        print(f"ablate_themes failed: {exc}", file=sys.stderr)
        return 1

    ranked = sorted(rows, key=lambda row: row["delta_pnl"], reverse=True)
    print("Theme ablation summary")
    print(f"Baseline return={baseline['summary']['return_pct']}% pnl={baseline['summary']['total_pnl']} trades={baseline['summary']['trade_count']}")
    print("Removing improved most:")
    for row in ranked[:5]:
        print(
            f"  -{row['removed_theme']}: delta_pnl={row['delta_pnl']} "
            f"delta_return={row['delta_return_pct']}% symbols={row['removed_symbols']}"
        )
    print("Removing hurt most:")
    for row in ranked[-5:]:
        print(
            f"  -{row['removed_theme']}: delta_pnl={row['delta_pnl']} "
            f"delta_return={row['delta_return_pct']}% symbols={row['removed_symbols']}"
        )
    print(f"CSV: {output_path}")
    return 0


def load_theme_map(universe_path: Path) -> dict[str, str]:
    universe = load_universe(universe_path)
    theme_by_symbol = {}
    for item in universe.metadata.get("core_symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        theme = str(item.get("theme") or "").strip()
        if symbol and theme:
            theme_by_symbol[symbol] = theme
    return theme_by_symbol


def group_symbols_by_theme(symbols: tuple[str, ...], theme_map: dict[str, str]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol in NON_TRADABLE_CONTEXT_SYMBOLS:
            continue
        theme = theme_map.get(symbol)
        if not theme:
            continue
        grouped.setdefault(theme, []).append(symbol)
    return {theme: tuple(items) for theme, items in grouped.items()}


def build_row(removed_theme: str, removed_symbols: tuple[str, ...], baseline: dict, report: dict) -> dict:
    base = baseline["summary"]
    current = report["summary"]
    return {
        "removed_theme": removed_theme,
        "removed_symbols": ",".join(removed_symbols),
        "removed_symbol_count": len(removed_symbols),
        "baseline_pnl": base["total_pnl"],
        "pnl": current["total_pnl"],
        "delta_pnl": round(current["total_pnl"] - base["total_pnl"], 2),
        "baseline_return_pct": base["return_pct"],
        "return_pct": current["return_pct"],
        "delta_return_pct": round(current["return_pct"] - base["return_pct"], 3),
        "baseline_trade_count": base["trade_count"],
        "trade_count": current["trade_count"],
        "baseline_max_drawdown_pct": base["max_drawdown_pct"],
        "max_drawdown_pct": current["max_drawdown_pct"],
        "average_r": current.get("average_r"),
        "profit_factor": current.get("profit_factor"),
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "removed_theme",
        "removed_symbols",
        "removed_symbol_count",
        "baseline_pnl",
        "pnl",
        "delta_pnl",
        "baseline_return_pct",
        "return_pct",
        "delta_return_pct",
        "baseline_trade_count",
        "trade_count",
        "baseline_max_drawdown_pct",
        "max_drawdown_pct",
        "average_r",
        "profit_factor",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
