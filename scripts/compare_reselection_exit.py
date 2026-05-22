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

from liubang.backtest import (
    BacktestParams,
    REPLACEMENT_COMPARE_MODES,
    REPLACEMENT_SCORE_MODES,
    RESELECTION_EXIT_MODES,
    SCORING_MODES,
    run_backtest,
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
    parser = argparse.ArgumentParser(description="Compare hold-vs-daily-reselection exits in 5m backtest.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH), help="Universe JSON file.")
    parser.add_argument(
        "--modes",
        default=(
            "none,next_open_not_reselected,next_open_not_reselected_when_slot_needed,"
            "replace_weak_hold_when_slot_needed"
        ),
    )
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
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    parser.add_argument("--replacement-min-hold-score", type=float, default=BacktestParams().replacement_min_hold_score)
    parser.add_argument(
        "--replacement-min-candidate-score-margin",
        type=float,
        default=BacktestParams().replacement_min_candidate_score_margin,
    )
    parser.add_argument("--replacement-score-mode", choices=REPLACEMENT_SCORE_MODES, default=BacktestParams().replacement_score_mode)
    parser.add_argument("--replacement-compare-mode", choices=REPLACEMENT_COMPARE_MODES, default=BacktestParams().replacement_compare_mode)
    parser.add_argument("--weak-max-positions", type=int, default=BacktestParams().weak_max_positions)
    parser.add_argument("--neutral-max-positions", type=int, default=BacktestParams().neutral_max_positions)
    parser.add_argument("--strong-max-positions", type=int, default=BacktestParams().strong_max_positions)
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
        modes = parse_modes(args.modes)
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
            scoring_mode=args.scoring_mode,
            hard_stop_pct=args.hard_stop_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            max_position_pct=args.max_position_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
            replacement_min_hold_score=args.replacement_min_hold_score,
            replacement_min_candidate_score_margin=args.replacement_min_candidate_score_margin,
            replacement_score_mode=args.replacement_score_mode,
            replacement_compare_mode=args.replacement_compare_mode,
            weak_max_positions=max(0, args.weak_max_positions),
            neutral_max_positions=max(0, args.neutral_max_positions),
            strong_max_positions=max(0, args.strong_max_positions),
        )
        rows = []
        reports = {}
        for mode in modes:
            params = replace(base_params, reselection_exit_mode=mode)
            report = run_backtest(
                history_by_symbol,
                symbols=symbols,
                params=params,
                earnings_calendar=earnings_calendar,
            )
            rows.append(build_row(mode, report))
            reports[mode] = {
                "config": report.get("config", {}),
                "candidate_count": report.get("candidate_count"),
                "diagnostics": report.get("diagnostics", {}),
                "summary": report.get("summary", {}),
            }
        attach_deltas(rows)
        output_dir = Path(args.output_dir)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        json_path = output_dir / f"reselection_exit_compare_{timestamp}.json"
        csv_path = output_dir / f"reselection_exit_compare_{timestamp}.csv"
        write_json(
            json_path,
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "reselection_exit_comparison",
                "symbols": list(symbols),
                "history_sources": summarize_history_sources(history_by_symbol),
                "rows": rows,
                "reports": reports,
                "note": (
                    "Daily reselection uses the final candidate set for each entry date. "
                    "Historical dynamic screener membership is not reconstructed."
                ),
            },
        )
        write_rows(csv_path, rows)
    except Exception as exc:
        print(f"compare_reselection_exit failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(rows, json_path, csv_path))
    return 0


def parse_modes(raw: str) -> tuple[str, ...]:
    values = []
    for item in raw.split(","):
        mode = item.strip()
        if not mode:
            continue
        if mode not in RESELECTION_EXIT_MODES:
            raise ValueError(f"Unsupported reselection exit mode {mode!r}; expected one of {RESELECTION_EXIT_MODES}")
        values.append(mode)
    if not values:
        raise ValueError("--modes must include at least one mode")
    return tuple(dict.fromkeys(values))


def build_row(mode: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    diagnostics = report.get("diagnostics") or {}
    params = (report.get("config") or {}).get("params") or {}
    exit_reasons = summary.get("by_exit_reason") or {}
    reselection_exit = exit_reasons.get("reselection_exit") or {}
    replacement_exits = [
        exit_item
        for trade in report.get("trades", [])
        for exit_item in trade.get("exits", [])
        if exit_item.get("reason") == "reselection_exit"
    ]
    hold_scores = [float(item["hold_score"]) for item in replacement_exits if item.get("hold_score") is not None]
    replacement_scores = [
        float(item["replacement_candidate_score"])
        for item in replacement_exits
        if item.get("replacement_candidate_score") is not None
    ]
    return {
        "reselection_exit_mode": mode,
        "replacement_min_hold_score": params.get("replacement_min_hold_score"),
        "replacement_min_candidate_score_margin": params.get("replacement_min_candidate_score_margin"),
        "replacement_score_mode": params.get("replacement_score_mode"),
        "replacement_compare_mode": params.get("replacement_compare_mode"),
        "weak_max_positions": params.get("weak_max_positions"),
        "neutral_max_positions": params.get("neutral_max_positions"),
        "strong_max_positions": params.get("strong_max_positions"),
        "candidate_count": report.get("candidate_count"),
        "entry_attempts": diagnostics.get("entry_attempts"),
        "filled_entries": diagnostics.get("filled_entries"),
        "skipped_no_slot": diagnostics.get("skipped_no_slot"),
        "trade_count": summary.get("trade_count"),
        "win_rate": summary.get("win_rate"),
        "return_pct": summary.get("return_pct"),
        "total_pnl": summary.get("total_pnl"),
        "average_r": summary.get("average_r"),
        "average_holding_weekdays": summary.get("average_holding_weekdays"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "reselection_exit_count": diagnostics.get("reselection_exit_count"),
        "reselection_exit_pnl": diagnostics.get("reselection_exit_pnl"),
        "reselection_exit_win_rate": reselection_exit.get("win_rate"),
        "reselection_exit_average_r": reselection_exit.get("average_r"),
        "reselection_exit_average_hold_score": round(sum(hold_scores) / len(hold_scores), 3) if hold_scores else None,
        "reselection_exit_average_replacement_score": round(sum(replacement_scores) / len(replacement_scores), 3) if replacement_scores else None,
    }


def attach_deltas(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    baseline = rows[0]
    baseline_return = float(baseline.get("return_pct") or 0.0)
    baseline_pnl = float(baseline.get("total_pnl") or 0.0)
    baseline_dd = float(baseline.get("max_drawdown_pct") or 0.0)
    for row in rows:
        row["delta_return_vs_first"] = round(float(row.get("return_pct") or 0.0) - baseline_return, 3)
        row["delta_pnl_vs_first"] = round(float(row.get("total_pnl") or 0.0) - baseline_pnl, 2)
        row["delta_drawdown_vs_first"] = round(float(row.get("max_drawdown_pct") or 0.0) - baseline_dd, 3)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "reselection_exit_mode",
        "replacement_min_hold_score",
        "replacement_min_candidate_score_margin",
        "replacement_score_mode",
        "replacement_compare_mode",
        "weak_max_positions",
        "neutral_max_positions",
        "strong_max_positions",
        "candidate_count",
        "entry_attempts",
        "filled_entries",
        "skipped_no_slot",
        "trade_count",
        "win_rate",
        "return_pct",
        "total_pnl",
        "average_r",
        "average_holding_weekdays",
        "profit_factor",
        "max_drawdown_pct",
        "reselection_exit_count",
        "reselection_exit_pnl",
        "reselection_exit_win_rate",
        "reselection_exit_average_r",
        "reselection_exit_average_hold_score",
        "reselection_exit_average_replacement_score",
        "delta_return_vs_first",
        "delta_pnl_vs_first",
        "delta_drawdown_vs_first",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_summary(rows: list[dict[str, Any]], json_path: Path, csv_path: Path) -> str:
    lines = [
        "Reselection exit comparison",
        "Mode results:",
    ]
    for row in rows:
        lines.append(
            "  "
            f"{row['reselection_exit_mode']}: trades={row['trade_count']} "
            f"return={row['return_pct']}% delta={row['delta_return_vs_first']}% "
            f"pf={row['profit_factor']} dd={row['max_drawdown_pct']}% "
            f"avgHold={row['average_holding_weekdays']} "
            f"reselectExits={row['reselection_exit_count']} pnl={row['reselection_exit_pnl']} "
            f"holdScore={row.get('reselection_exit_average_hold_score')}"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
