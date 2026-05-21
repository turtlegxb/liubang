#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams, SCORING_MODES, run_backtest
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
    parser = argparse.ArgumentParser(description="Compare classic and ranked_v1 stock-selection scoring in backtest.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH), help="Universe JSON file.")
    parser.add_argument("--scoring-modes", default=",".join(SCORING_MODES))
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
        scoring_modes = parse_scoring_modes(args.scoring_modes)
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
        base_params = BacktestParams(
            initial_equity=args.initial_equity,
            min_score=args.min_score,
            min_pullback_pct=args.min_pullback_pct,
            max_pullback_pct=args.max_pullback_pct,
            hard_stop_pct=args.hard_stop_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            max_position_pct=args.max_position_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        rows = []
        reports = {}
        for scoring_mode in scoring_modes:
            params = replace(base_params, scoring_mode=scoring_mode)
            report = run_backtest(
                history_by_symbol,
                symbols=symbols,
                params=params,
                earnings_calendar=earnings_calendar,
            )
            rows.append(build_row(scoring_mode, report))
            reports[scoring_mode] = {
                "config": report.get("config", {}),
                "candidate_count": report.get("candidate_count"),
                "diagnostics": report.get("diagnostics", {}),
                "summary": report.get("summary", {}),
            }
        attach_deltas(rows)
        output_path = Path(args.output_dir) / f"scoring_compare_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "mode": "scoring_mode_comparison",
                    "symbols": list(symbols),
                    "history_sources": summarize_history_sources(history_by_symbol),
                    "rows": rows,
                    "reports": reports,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"compare_scoring_modes failed: {exc}", file=sys.stderr)
        return 1

    print(format_compare_summary(rows, output_path))
    return 0


def parse_scoring_modes(raw: str) -> tuple[str, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item not in SCORING_MODES:
            raise ValueError(f"Unsupported scoring mode {item!r}; expected one of {SCORING_MODES}")
        values.append(item)
    if not values:
        raise ValueError("--scoring-modes must include at least one mode")
    return tuple(dict.fromkeys(values))


def build_row(scoring_mode: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    diagnostics = report.get("diagnostics") or {}
    return {
        "scoring_mode": scoring_mode,
        "candidate_count": report.get("candidate_count"),
        "entry_attempts": diagnostics.get("entry_attempts"),
        "filled_entries": diagnostics.get("filled_entries"),
        "skipped_no_slot": diagnostics.get("skipped_no_slot"),
        "skipped_symbol_cooldown": diagnostics.get("skipped_symbol_cooldown"),
        "trade_count": summary.get("trade_count"),
        "win_rate": summary.get("win_rate"),
        "return_pct": summary.get("return_pct"),
        "total_pnl": summary.get("total_pnl"),
        "average_r": summary.get("average_r"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
    }


def attach_deltas(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    baseline = rows[0]
    baseline_return = float(baseline.get("return_pct") or 0.0)
    baseline_pnl = float(baseline.get("total_pnl") or 0.0)
    baseline_trade_count = int(baseline.get("trade_count") or 0)
    for row in rows:
        row["delta_return_vs_first"] = round(float(row.get("return_pct") or 0.0) - baseline_return, 3)
        row["delta_pnl_vs_first"] = round(float(row.get("total_pnl") or 0.0) - baseline_pnl, 2)
        row["delta_trades_vs_first"] = int(row.get("trade_count") or 0) - baseline_trade_count


def format_compare_summary(rows: list[dict[str, Any]], output_path: Path) -> str:
    lines = [
        "Scoring comparison",
        "Mode results:",
    ]
    for row in rows:
        lines.append(
            "  "
            f"{row['scoring_mode']}: trades={row['trade_count']} "
            f"return={row['return_pct']}% delta={row['delta_return_vs_first']}% "
            f"pf={row['profit_factor']} dd={row['max_drawdown_pct']}% "
            f"avgR={row['average_r']}"
        )
    lines.append(f"Report: {output_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
