#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import replace
from datetime import UTC, datetime
from itertools import product
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
    RESELECTION_EXIT_NONE,
    RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED,
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
    parser = argparse.ArgumentParser(description="Optimize weak-hold replacement logic in 5m backtest.")
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
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    parser.add_argument("--hold-score-thresholds", default="6,7,8")
    parser.add_argument("--candidate-score-margins", default="0,0.5")
    parser.add_argument("--replacement-score-modes", default=",".join(REPLACEMENT_SCORE_MODES))
    parser.add_argument("--replacement-compare-modes", default=",".join(REPLACEMENT_COMPARE_MODES))
    parser.add_argument("--weak-slots", default=str(BacktestParams().weak_max_positions))
    parser.add_argument("--neutral-slots", default="2,3")
    parser.add_argument("--strong-slots", default="3,4")
    parser.add_argument("--no-baseline", action="store_true", help="Do not run no-replacement baseline per slot profile.")
    parser.add_argument("--top", type=int, default=10, help="Number of top configurations to print.")
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    try:
        symbols = resolve_symbols(
            raw_symbols=args.symbols,
            universe_path=Path(args.universe),
            include_benchmarks=True,
        )
        hold_thresholds = parse_float_list(args.hold_score_thresholds, name="--hold-score-thresholds")
        candidate_margins = parse_float_list(args.candidate_score_margins, name="--candidate-score-margins")
        score_modes = parse_choice_list(
            args.replacement_score_modes,
            choices=REPLACEMENT_SCORE_MODES,
            name="--replacement-score-modes",
        )
        compare_modes = parse_choice_list(
            args.replacement_compare_modes,
            choices=REPLACEMENT_COMPARE_MODES,
            name="--replacement-compare-modes",
        )
        weak_slots = parse_int_list(args.weak_slots, name="--weak-slots")
        neutral_slots = parse_int_list(args.neutral_slots, name="--neutral-slots")
        strong_slots = parse_int_list(args.strong_slots, name="--strong-slots")

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
        )
        report = run_optimization(
            history_by_symbol=history_by_symbol,
            symbols=symbols,
            base_params=base_params,
            earnings_calendar=earnings_calendar,
            hold_thresholds=hold_thresholds,
            candidate_margins=candidate_margins,
            score_modes=score_modes,
            compare_modes=compare_modes,
            weak_slots=weak_slots,
            neutral_slots=neutral_slots,
            strong_slots=strong_slots,
            include_baseline=not args.no_baseline,
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        output_dir = Path(args.output_dir)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        json_path = output_dir / f"replacement_logic_optimization_{timestamp}.json"
        csv_path = output_dir / f"replacement_logic_optimization_{timestamp}.csv"
        write_json(json_path, report)
        write_rows(csv_path, report["rows"])
    except Exception as exc:
        print(f"optimize_replacement_logic failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(report, json_path, csv_path, top_n=max(1, args.top)))
    return 0


def parse_float_list(raw: str, *, name: str) -> tuple[float, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    if not values:
        raise ValueError(f"{name} must include at least one number")
    return tuple(dict.fromkeys(values))


def parse_int_list(raw: str, *, name: str) -> tuple[int, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(max(0, int(item)))
    if not values:
        raise ValueError(f"{name} must include at least one integer")
    return tuple(dict.fromkeys(values))


def parse_choice_list(raw: str, *, choices: tuple[str, ...], name: str) -> tuple[str, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item not in choices:
            raise ValueError(f"Unsupported value {item!r} for {name}; expected one of {choices}")
        values.append(item)
    if not values:
        raise ValueError(f"{name} must include at least one value")
    return tuple(dict.fromkeys(values))


def run_optimization(
    *,
    history_by_symbol: dict[str, dict[str, Any]],
    symbols: tuple[str, ...],
    base_params: BacktestParams,
    earnings_calendar: Any,
    hold_thresholds: tuple[float, ...],
    candidate_margins: tuple[float, ...],
    score_modes: tuple[str, ...],
    compare_modes: tuple[str, ...],
    weak_slots: tuple[int, ...],
    neutral_slots: tuple[int, ...],
    strong_slots: tuple[int, ...],
    include_baseline: bool,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reports: dict[str, dict[str, Any]] = {}
    baseline_by_slot_profile: dict[str, dict[str, Any]] = {}
    run_index = 0

    for weak, neutral, strong in product(weak_slots, neutral_slots, strong_slots):
        slot_params = replace(
            base_params,
            weak_max_positions=weak,
            neutral_max_positions=neutral,
            strong_max_positions=strong,
        )
        slot_profile = format_slot_profile(weak=weak, neutral=neutral, strong=strong)
        if include_baseline:
            baseline_params = replace(slot_params, reselection_exit_mode=RESELECTION_EXIT_NONE)
            baseline_report = run_backtest(
                history_by_symbol,
                symbols=symbols,
                params=baseline_params,
                earnings_calendar=earnings_calendar,
            )
            baseline_row = build_row(
                run_id=f"{run_index:04d}_baseline_{slot_profile}",
                slot_profile=slot_profile,
                report=baseline_report,
                is_baseline=True,
            )
            rows.append(baseline_row)
            reports[baseline_row["run_id"]] = compact_report(baseline_report)
            baseline_by_slot_profile[slot_profile] = baseline_row
            run_index += 1

        for hold_threshold, margin, score_mode, compare_mode in product(
            hold_thresholds,
            candidate_margins,
            score_modes,
            compare_modes,
        ):
            params = replace(
                slot_params,
                reselection_exit_mode=RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED,
                replacement_min_hold_score=hold_threshold,
                replacement_min_candidate_score_margin=margin,
                replacement_score_mode=score_mode,
                replacement_compare_mode=compare_mode,
            )
            strategy_report = run_backtest(
                history_by_symbol,
                symbols=symbols,
                params=params,
                earnings_calendar=earnings_calendar,
            )
            row = build_row(
                run_id=f"{run_index:04d}_{slot_profile}_h{hold_threshold:g}_m{margin:g}_{score_mode}_{compare_mode}",
                slot_profile=slot_profile,
                report=strategy_report,
                is_baseline=False,
            )
            rows.append(row)
            reports[row["run_id"]] = compact_report(strategy_report)
            run_index += 1

    attach_deltas(rows, baseline_by_slot_profile)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "replacement_logic_optimization",
        "symbols": list(symbols),
        "grid": {
            "hold_score_thresholds": list(hold_thresholds),
            "candidate_score_margins": list(candidate_margins),
            "replacement_score_modes": list(score_modes),
            "replacement_compare_modes": list(compare_modes),
            "weak_slots": list(weak_slots),
            "neutral_slots": list(neutral_slots),
            "strong_slots": list(strong_slots),
            "include_baseline": include_baseline,
        },
        "rows": rows,
        "reports": reports,
    }


def build_row(*, run_id: str, slot_profile: str, report: dict[str, Any], is_baseline: bool) -> dict[str, Any]:
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
        "run_id": run_id,
        "is_baseline": is_baseline,
        "slot_profile": slot_profile,
        "reselection_exit_mode": params.get("reselection_exit_mode"),
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
        "unfilled_entries": diagnostics.get("unfilled_entries"),
        "skipped_no_slot": diagnostics.get("skipped_no_slot"),
        "skipped_duplicate_symbol": diagnostics.get("skipped_duplicate_symbol"),
        "skipped_symbol_cooldown": diagnostics.get("skipped_symbol_cooldown"),
        "skipped_missing_intraday": diagnostics.get("skipped_missing_intraday"),
        "skipped_regime_policy": diagnostics.get("skipped_regime_policy"),
        "trade_count": summary.get("trade_count"),
        "win_rate": summary.get("win_rate"),
        "return_pct": summary.get("return_pct"),
        "total_pnl": summary.get("total_pnl"),
        "average_pnl": summary.get("average_pnl"),
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


def attach_deltas(rows: list[dict[str, Any]], baseline_by_slot_profile: dict[str, dict[str, Any]]) -> None:
    for row in rows:
        baseline = baseline_by_slot_profile.get(str(row.get("slot_profile")))
        if baseline is None:
            row["delta_return_vs_slot_baseline"] = None
            row["delta_pnl_vs_slot_baseline"] = None
            row["delta_drawdown_vs_slot_baseline"] = None
            row["delta_profit_factor_vs_slot_baseline"] = None
            row["delta_trades_vs_slot_baseline"] = None
            continue
        row["delta_return_vs_slot_baseline"] = round(
            as_number(row.get("return_pct")) - as_number(baseline.get("return_pct")),
            3,
        )
        row["delta_pnl_vs_slot_baseline"] = round(
            as_number(row.get("total_pnl")) - as_number(baseline.get("total_pnl")),
            2,
        )
        row["delta_drawdown_vs_slot_baseline"] = round(
            as_number(row.get("max_drawdown_pct")) - as_number(baseline.get("max_drawdown_pct")),
            3,
        )
        row["delta_profit_factor_vs_slot_baseline"] = round(
            as_number(row.get("profit_factor")) - as_number(baseline.get("profit_factor")),
            4,
        )
        row["delta_trades_vs_slot_baseline"] = int(row.get("trade_count") or 0) - int(baseline.get("trade_count") or 0)


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": report.get("config", {}),
        "candidate_count": report.get("candidate_count"),
        "diagnostics": report.get("diagnostics", {}),
        "summary": report.get("summary", {}),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "is_baseline",
        "slot_profile",
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
        "unfilled_entries",
        "skipped_no_slot",
        "skipped_duplicate_symbol",
        "skipped_symbol_cooldown",
        "skipped_missing_intraday",
        "skipped_regime_policy",
        "trade_count",
        "win_rate",
        "return_pct",
        "total_pnl",
        "average_pnl",
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
        "delta_return_vs_slot_baseline",
        "delta_pnl_vs_slot_baseline",
        "delta_drawdown_vs_slot_baseline",
        "delta_profit_factor_vs_slot_baseline",
        "delta_trades_vs_slot_baseline",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_summary(report: dict[str, Any], json_path: Path, csv_path: Path, *, top_n: int) -> str:
    rows = report.get("rows") or []
    strategy_rows = [row for row in rows if not row.get("is_baseline")]
    top_rows = sorted(strategy_rows, key=optimization_sort_key, reverse=True)[:top_n]
    lines = [
        "Replacement logic optimization",
        f"Runs: {len(rows)} total, {len(strategy_rows)} replacement configs",
        "Top configs:",
    ]
    for row in top_rows:
        lines.append(
            "  "
            f"{row['run_id']}: return={row['return_pct']}% "
            f"delta={row['delta_return_vs_slot_baseline']}% "
            f"pf={row['profit_factor']} dd={row['max_drawdown_pct']}% "
            f"trades={row['trade_count']} reselect={row['reselection_exit_count']} "
            f"slots={row['slot_profile']} hold<={row['replacement_min_hold_score']} "
            f"margin={row['replacement_min_candidate_score_margin']} "
            f"score={row['replacement_score_mode']} compare={row['replacement_compare_mode']}"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


def optimization_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        as_number(row.get("return_pct")),
        as_number(row.get("profit_factor")),
        -as_number(row.get("max_drawdown_pct")),
        as_number(row.get("average_r")),
    )


def as_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number):
        return 0.0
    return number


def format_slot_profile(*, weak: int, neutral: int, strong: int) -> str:
    return f"w{weak}_n{neutral}_s{strong}"


if __name__ == "__main__":
    raise SystemExit(main())
