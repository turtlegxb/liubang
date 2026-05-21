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

from liubang.backtest import BacktestParams, SCORING_MODES, run_backtest
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories, summarize_history_sources
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH, EarningsCalendar
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols
from scripts.run_daily_proxy_backtest import load_daily_histories, run_daily_proxy_backtest
from scripts.run_hourly_proxy_backtest import load_hourly_histories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep single-symbol cooldown days across backtest modes.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--modes", default="backtest,hourly_proxy,daily_proxy")
    parser.add_argument("--cooldowns", default="0,5,10,20")
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--hourly-period", default="2y")
    parser.add_argument("--hourly-interval", default="1h")
    parser.add_argument("--daily-period", default="5y")
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--earnings-limit", type=int, default=64)
    parser.add_argument("--refresh", action="store_true", help="Refresh 5-minute history.")
    parser.add_argument("--refresh-hourly", action="store_true")
    parser.add_argument("--refresh-daily", action="store_true")
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
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
        cooldowns = parse_int_list(args.cooldowns)
        modes = parse_mode_list(args.modes)
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        report = run_sweep(args=args, symbols=symbols, cooldowns=cooldowns, modes=modes, earnings_calendar=earnings_calendar)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        json_path = output_dir / f"cooldown_sweep_{timestamp}.json"
        csv_path = output_dir / f"cooldown_sweep_{timestamp}.csv"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_rows_csv(csv_path, report["rows"])
    except Exception as exc:
        print(f"sweep_cooldown failed: {exc}", file=sys.stderr)
        return 1

    print(format_sweep_summary(report, json_path, csv_path))
    return 0


def parse_int_list(raw: str) -> tuple[int, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(max(0, int(item)))
    if not values:
        raise ValueError("--cooldowns must include at least one integer")
    return tuple(dict.fromkeys(values))


def parse_mode_list(raw: str) -> tuple[str, ...]:
    allowed = {"backtest", "hourly_proxy", "daily_proxy"}
    modes = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item not in allowed:
            raise ValueError(f"Unsupported mode {item!r}; expected one of {sorted(allowed)}")
        modes.append(item)
    if not modes:
        raise ValueError("--modes must include at least one mode")
    return tuple(dict.fromkeys(modes))


def run_sweep(
    *,
    args: argparse.Namespace,
    symbols: tuple[str, ...],
    cooldowns: tuple[int, ...],
    modes: tuple[str, ...],
    earnings_calendar: EarningsCalendar | None,
) -> dict[str, Any]:
    rows = []
    reports: dict[str, dict[str, Any]] = {}
    history_sources: dict[str, Any] = {}

    if "backtest" in modes:
        history_by_symbol = load_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            extended_hours=False,
            refresh=args.refresh,
            history_provider=args.history_provider,
            fallback_history_provider=args.fallback_history_provider,
            yfinance_period=args.yfinance_period,
        )
        history_sources["backtest"] = summarize_history_sources(history_by_symbol)
        for cooldown in cooldowns:
            report = run_backtest(
                history_by_symbol,
                symbols=symbols,
                params=BacktestParams(symbol_cooldown_days=cooldown, scoring_mode=args.scoring_mode),
                earnings_calendar=earnings_calendar,
            )
            append_report(rows, reports, mode="backtest", cooldown=cooldown, report=report)

    if "hourly_proxy" in modes:
        hourly_history_by_symbol = load_hourly_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            period=args.hourly_period,
            interval=args.hourly_interval,
            refresh=args.refresh_hourly,
        )
        history_sources["hourly_proxy"] = summarize_history_sources(hourly_history_by_symbol)
        for cooldown in cooldowns:
            report = run_backtest(
                hourly_history_by_symbol,
                symbols=symbols,
                params=BacktestParams(symbol_cooldown_days=cooldown, scoring_mode=args.scoring_mode),
                earnings_calendar=earnings_calendar,
            )
            report["mode"] = "hourly_proxy_backtest"
            report["config"]["period"] = args.hourly_period
            report["config"]["interval"] = args.hourly_interval
            append_report(rows, reports, mode="hourly_proxy", cooldown=cooldown, report=report)

    if "daily_proxy" in modes:
        daily_by_symbol = load_daily_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            period=args.daily_period,
            refresh=args.refresh_daily,
        )
        for cooldown in cooldowns:
            report = run_daily_proxy_backtest(
                daily_by_symbol=daily_by_symbol,
                symbols=symbols,
                params=BacktestParams(symbol_cooldown_days=cooldown, scoring_mode=args.scoring_mode),
                earnings_calendar=earnings_calendar,
                period=args.daily_period,
            )
            append_report(rows, reports, mode="daily_proxy", cooldown=cooldown, report=report)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "cooldown_sweep",
        "symbols": list(symbols),
        "cooldowns": list(cooldowns),
        "modes": list(modes),
        "scoring_mode": args.scoring_mode,
        "history_sources": history_sources,
        "rows": rows,
        "reports": reports,
    }


def append_report(
    rows: list[dict[str, Any]],
    reports: dict[str, dict[str, Any]],
    *,
    mode: str,
    cooldown: int,
    report: dict[str, Any],
) -> None:
    rows.append(row_from_report(mode, cooldown, report))
    reports[f"{mode}_{cooldown}"] = {
        "config": report.get("config", {}),
        "diagnostics": report.get("diagnostics", {}),
        "summary": report.get("summary", {}),
    }


def row_from_report(mode: str, cooldown: int, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    diagnostics = report.get("diagnostics") or {}
    concentration = symbol_concentration(summary)
    return {
        "mode": mode,
        "symbol_cooldown_days": cooldown,
        "candidate_count": report.get("candidate_count"),
        "trade_count": summary.get("trade_count"),
        "return_pct": summary.get("return_pct"),
        "total_pnl": summary.get("total_pnl"),
        "average_r": summary.get("average_r"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "top1_symbol": concentration.get("top1_symbol"),
        "top1_net_share": concentration.get("top1_net_share"),
        "top3_symbols": ",".join(concentration.get("top3_symbols") or []),
        "top3_net_share": concentration.get("top3_net_share"),
        "entry_attempts": diagnostics.get("entry_attempts"),
        "filled_entries": diagnostics.get("filled_entries"),
        "skipped_symbol_cooldown": diagnostics.get("skipped_symbol_cooldown"),
    }


def symbol_concentration(summary: dict[str, Any]) -> dict[str, Any]:
    by_symbol = summary.get("by_symbol") or {}
    net_pnl = float(summary.get("total_pnl") or 0.0)
    positive = sorted(
        (
            (symbol, float(item.get("pnl") or 0.0))
            for symbol, item in by_symbol.items()
            if float(item.get("pnl") or 0.0) > 0
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    if not positive or net_pnl <= 0:
        return {
            "top1_symbol": positive[0][0] if positive else None,
            "top1_net_share": None,
            "top3_symbols": [symbol for symbol, _ in positive[:3]],
            "top3_net_share": None,
        }
    top1_symbol, top1_pnl = positive[0]
    top3 = positive[:3]
    return {
        "top1_symbol": top1_symbol,
        "top1_net_share": round(top1_pnl / net_pnl, 4) if net_pnl else None,
        "top3_symbols": [symbol for symbol, _ in top3],
        "top3_net_share": round(sum(pnl for _, pnl in top3) / net_pnl, 4) if net_pnl else None,
    }


def write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mode",
        "symbol_cooldown_days",
        "candidate_count",
        "trade_count",
        "return_pct",
        "total_pnl",
        "average_r",
        "profit_factor",
        "max_drawdown_pct",
        "top1_symbol",
        "top1_net_share",
        "top3_symbols",
        "top3_net_share",
        "entry_attempts",
        "filled_entries",
        "skipped_symbol_cooldown",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_sweep_summary(report: dict[str, Any], json_path: Path, csv_path: Path) -> str:
    lines = ["Cooldown sweep summary"]
    for row in report["rows"]:
        lines.append(
            f"  {row['mode']} cooldown={row['symbol_cooldown_days']}: "
            f"return={row['return_pct']}% trades={row['trade_count']} "
            f"pf={row['profit_factor']} top1={row['top1_symbol']}:{row['top1_net_share']} "
            f"top3={row['top3_net_share']} skipped={row['skipped_symbol_cooldown']}"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
