#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams
from liubang.cli_utils import (
    load_earnings_for_symbols,
    load_env,
    load_histories,
    parse_float_grid,
    summarize_history_sources,
)
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols
from scripts.run_hourly_proxy_backtest import load_hourly_histories
from scripts.search_scoring_strategies import (
    build_context,
    comparable_pf,
    generate_configs,
    safe_float,
    simulate_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-validate scoring filters on 5-minute and hourly histories.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--hourly-period", default="2y")
    parser.add_argument("--hourly-interval", default="1h")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--extended-hours", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=64)
    parser.add_argument("--target-pf", type=float, default=2.0)
    parser.add_argument("--min-5m-trades", type=int, default=30)
    parser.add_argument("--min-hourly-trades", type=int, default=50)
    parser.add_argument("--max-configs", type=int, default=5000)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--candidate-min-score", type=float, default=6.0)
    parser.add_argument("--candidate-min-pullback-pct", type=float, default=0.005)
    parser.add_argument("--candidate-max-pullback-pct", type=float, default=0.08)
    parser.add_argument("--min-classic-scores", default="7")
    parser.add_argument("--min-ranked-scores", default="7,7.5")
    parser.add_argument("--max-pullback-pcts", default="0.045,0.05,0.055,0.06")
    parser.add_argument("--hard-stop-pcts", default="0.043,0.044,0.045,0.046,0.047,0.048")
    parser.add_argument("--cooldowns", default="0")
    parser.add_argument("--min-rs20-ranks", default="0,0.5,0.65")
    parser.add_argument("--min-rs60-ranks", default="0,0.5")
    parser.add_argument("--min-overlay-scores", default="7.25,7.4,7.5,7.6")
    parser.add_argument("--max-atr20-pcts", default="0.08")
    parser.add_argument("--max-pullback-atrs", default="none")
    parser.add_argument("--regime-filters", default="strong")
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
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        five_minute_histories = load_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            extended_hours=args.extended_hours,
            refresh=args.refresh,
            history_provider=args.history_provider,
            fallback_history_provider=args.fallback_history_provider,
            yfinance_period=args.yfinance_period,
        )
        hourly_histories = load_hourly_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            period=args.hourly_period,
            interval=args.hourly_interval,
            refresh=args.refresh,
        )
        candidate_params = BacktestParams(
            min_score=args.candidate_min_score,
            min_pullback_pct=args.candidate_min_pullback_pct,
            max_pullback_pct=args.candidate_max_pullback_pct,
            hard_stop_pct=min(parse_float_grid(args.hard_stop_pcts)),
        )
        five_minute_context = build_context(
            history_by_symbol=five_minute_histories,
            symbols=symbols,
            earnings_calendar=earnings_calendar,
            candidate_params=candidate_params,
        )
        hourly_context = build_context(
            history_by_symbol=hourly_histories,
            symbols=symbols,
            earnings_calendar=earnings_calendar,
            candidate_params=candidate_params,
        )
        rows = run_cross_validation(args, five_minute_context, hourly_context)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"scoring_cross_validation_{timestamp}.json"
        csv_path = output_dir / f"scoring_cross_validation_{timestamp}.csv"
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "scoring_cross_validation",
            "symbols": list(symbols),
            "five_minute_history_sources": summarize_history_sources(five_minute_histories),
            "hourly_history_sources": summarize_history_sources(hourly_histories),
            "target_pf": args.target_pf,
            "min_5m_trades": args.min_5m_trades,
            "min_hourly_trades": args.min_hourly_trades,
            "searched_configs": len(rows),
            "target_hits": [row for row in rows if row["target_hit"]],
            "top_rows": sorted_rows(rows)[:50],
            "daily_proxy_note": (
                "Daily proxy is intentionally not included here because its next-open entry and stop-first "
                "daily-bar assumption are materially different from the 5-minute trigger engine."
            ),
        }
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_rows(csv_path, rows)
    except Exception as exc:
        print(f"cross_validate_scoring_strategies failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(rows, json_path, csv_path, args))
    return 0


def run_cross_validation(args: argparse.Namespace, five_minute_context: Any, hourly_context: Any) -> list[dict[str, Any]]:
    configs = generate_configs(args)
    rows = []
    progress_every = max(0, int(args.progress_every or 0))
    for index, config in enumerate(configs, start=1):
        five_minute_report = simulate_config(five_minute_context, config)
        hourly_report = simulate_config(hourly_context, config)
        row = build_row(config, five_minute_report, hourly_report, args)
        rows.append(row)
        if progress_every and (index % progress_every == 0 or index == len(configs)):
            hits = sum(1 for item in rows if item["target_hit"])
            best = sorted_rows(rows)[0] if rows else {}
            print(
                f"checked {index}/{len(configs)} configs; target_hits={hits}; "
                f"best_min_pf={best.get('min_profit_factor')}",
                file=sys.stderr,
            )
    return rows


def build_row(config: Any, five_minute_report: dict[str, Any], hourly_report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary_5m = five_minute_report["summary"]
    summary_hourly = hourly_report["summary"]
    pf_5m = comparable_pf(summary_5m.get("profit_factor"))
    pf_hourly = comparable_pf(summary_hourly.get("profit_factor"))
    row = config.to_dict() | {
        "candidate_count_5m": five_minute_report["candidate_count"],
        "trade_count_5m": summary_5m["trade_count"],
        "return_pct_5m": summary_5m["return_pct"],
        "profit_factor_5m": summary_5m.get("profit_factor"),
        "max_drawdown_pct_5m": summary_5m["max_drawdown_pct"],
        "average_r_5m": summary_5m.get("average_r"),
        "candidate_count_hourly": hourly_report["candidate_count"],
        "trade_count_hourly": summary_hourly["trade_count"],
        "return_pct_hourly": summary_hourly["return_pct"],
        "profit_factor_hourly": summary_hourly.get("profit_factor"),
        "max_drawdown_pct_hourly": summary_hourly["max_drawdown_pct"],
        "average_r_hourly": summary_hourly.get("average_r"),
        "min_profit_factor": round(min(pf_5m, pf_hourly), 4),
    }
    row["target_hit"] = (
        row["min_profit_factor"] >= args.target_pf
        and int(row["trade_count_5m"] or 0) >= args.min_5m_trades
        and int(row["trade_count_hourly"] or 0) >= args.min_hourly_trades
    )
    return row


def sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            safe_float(row.get("min_profit_factor")),
            comparable_pf(row.get("profit_factor_5m")),
            comparable_pf(row.get("profit_factor_hourly")),
            int(row.get("trade_count_5m") or 0),
        ),
        reverse=True,
    )


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def format_summary(rows: list[dict[str, Any]], json_path: Path, csv_path: Path, args: argparse.Namespace) -> str:
    ranked = sorted_rows(rows)
    hits = [row for row in ranked if row.get("target_hit")]
    lines = [
        "Scoring cross-validation summary",
        f"Rows: {len(rows)}",
        f"Target: min(5m_pf, hourly_pf)>={args.target_pf} 5m_trades>={args.min_5m_trades} hourly_trades>={args.min_hourly_trades}",
        f"Target hits: {len(hits)}",
        "Top configurations:",
    ]
    for row in ranked[:10]:
        lines.append(
            "  "
            f"min_pf={row.get('min_profit_factor')} "
            f"5m_pf={row.get('profit_factor_5m')} trades={row.get('trade_count_5m')} "
            f"hourly_pf={row.get('profit_factor_hourly')} trades={row.get('trade_count_hourly')} "
            f"pb<={row.get('max_pullback_pct')} stop={row.get('hard_stop_pct')} "
            f"rs20>={row.get('min_rs20_rank')} overlay>={row.get('min_overlay_score')} "
            f"regime={row.get('regime_filter')}"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
