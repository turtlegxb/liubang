#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
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

from liubang.backtest import BacktestParams, run_backtest
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories, summarize_history_sources
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH, EarningsCalendar
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a compact stress suite around the default backtest.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--earnings-limit", type=int, default=32)
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--extended-hours", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--min-score", type=float, default=BacktestParams().min_score)
    parser.add_argument("--min-pullback-pct", type=float, default=BacktestParams().min_pullback_pct)
    parser.add_argument("--max-pullback-pct", type=float, default=BacktestParams().max_pullback_pct)
    parser.add_argument("--hard-stop-pct", type=float, default=BacktestParams().hard_stop_pct)
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
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source="yfinance",
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        report = run_stress_suite(
            history_by_symbol=history_by_symbol,
            symbols=symbols,
            earnings_calendar=earnings_calendar,
            base_params=BacktestParams(
                min_score=args.min_score,
                min_pullback_pct=args.min_pullback_pct,
                max_pullback_pct=args.max_pullback_pct,
                hard_stop_pct=args.hard_stop_pct,
                symbol_cooldown_days=max(0, args.symbol_cooldown_days),
            ),
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        output_dir = Path(args.output_dir)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        json_path = output_dir / f"stress_{timestamp}.json"
        csv_path = output_dir / f"stress_{timestamp}.csv"
        write_stress_report(json_path, report)
        write_stress_csv(csv_path, report["rows"])
    except Exception as exc:
        print(f"stress_backtest failed: {exc}", file=sys.stderr)
        return 1

    print(format_stress_summary(report, json_path, csv_path))
    return 0


def run_stress_suite(
    *,
    history_by_symbol: dict[str, dict[str, Any]],
    symbols: tuple[str, ...],
    earnings_calendar: EarningsCalendar | None,
    base_params: BacktestParams | None = None,
    symbol_cooldown_days: int = 0,
) -> dict[str, Any]:
    base = base_params or BacktestParams(symbol_cooldown_days=symbol_cooldown_days)
    scenarios = [
        ("baseline", base, earnings_calendar),
        ("no_earnings_filter", base, None),
        ("strict_score_8", replace(base, min_score=8.0), earnings_calendar),
        ("strict_score_9", replace(base, min_score=9.0), earnings_calendar),
        ("narrow_pullback_4pct", replace(base, max_pullback_pct=0.04), earnings_calendar),
        ("tight_stop_3pct", replace(base, hard_stop_pct=0.03), earnings_calendar),
        ("loose_stop_5pct", replace(base, hard_stop_pct=0.05), earnings_calendar),
    ]
    rows = []
    reports_by_name = {}
    for name, params, calendar in scenarios:
        scenario_report = run_backtest(
            history_by_symbol,
            symbols=symbols,
            params=params,
            earnings_calendar=calendar,
        )
        reports_by_name[name] = {
            "config": scenario_report["config"],
            "summary": scenario_report["summary"],
            "diagnostics": scenario_report.get("diagnostics", {}),
        }
        rows.append(row_from_report(name, params, calendar is not None, scenario_report))

    baseline_return = rows[0]["return_pct"] if rows else 0.0
    for row in rows:
        row["delta_return_vs_baseline"] = round(row["return_pct"] - baseline_return, 3)

    returns = [row["return_pct"] for row in rows]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "backtest_stress_suite",
        "symbols": list(symbols),
        "rows": rows,
        "reports_by_name": reports_by_name,
        "stability": {
            "scenario_count": len(rows),
            "min_return_pct": min(returns) if returns else None,
            "max_return_pct": max(returns) if returns else None,
            "positive_scenarios": sum(1 for value in returns if value > 0),
            "negative_scenarios": sum(1 for value in returns if value < 0),
        },
    }


def row_from_report(
    name: str,
    params: BacktestParams,
    earnings_enabled: bool,
    report: dict[str, Any],
) -> dict[str, Any]:
    summary = report["summary"]
    diagnostics = report.get("diagnostics", {})
    return {
        "scenario": name,
        "earnings_filter": earnings_enabled,
        "min_score": params.min_score,
        "min_pullback_pct": params.min_pullback_pct,
        "max_pullback_pct": params.max_pullback_pct,
        "hard_stop_pct": params.hard_stop_pct,
        "symbol_cooldown_days": params.symbol_cooldown_days,
        "candidate_count": report["candidate_count"],
        "entry_attempts": diagnostics.get("entry_attempts"),
        "filled_entries": diagnostics.get("filled_entries"),
        "unfilled_entries": diagnostics.get("unfilled_entries"),
        "skipped_no_slot": diagnostics.get("skipped_no_slot"),
        "trade_count": summary["trade_count"],
        "win_rate": summary["win_rate"],
        "return_pct": summary["return_pct"],
        "total_pnl": summary["total_pnl"],
        "average_r": summary.get("average_r"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_pct": summary["max_drawdown_pct"],
    }


def write_stress_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_stress_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario",
        "earnings_filter",
        "min_score",
        "min_pullback_pct",
        "max_pullback_pct",
        "hard_stop_pct",
        "symbol_cooldown_days",
        "candidate_count",
        "entry_attempts",
        "filled_entries",
        "unfilled_entries",
        "skipped_no_slot",
        "trade_count",
        "win_rate",
        "return_pct",
        "delta_return_vs_baseline",
        "total_pnl",
        "average_r",
        "profit_factor",
        "max_drawdown_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_stress_summary(report: dict[str, Any], json_path: Path, csv_path: Path) -> str:
    lines = [
        "Backtest stress summary",
        f"Generated: {report['generated_at']}",
        f"Scenarios: {report['stability']['scenario_count']}",
        (
            "Return range: "
            f"{report['stability']['min_return_pct']}% to {report['stability']['max_return_pct']}% "
            f"positive={report['stability']['positive_scenarios']} "
            f"negative={report['stability']['negative_scenarios']}"
        ),
        "Rows:",
    ]
    for row in report["rows"]:
        lines.append(
            "  "
            f"{row['scenario']}: ret={row['return_pct']}% "
            f"delta={row['delta_return_vs_baseline']}% trades={row['trade_count']} "
            f"pf={row['profit_factor']} dd={row['max_drawdown_pct']}%"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
