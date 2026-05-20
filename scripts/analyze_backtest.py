#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.universe import DEFAULT_UNIVERSE_PATH, load_universe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a Liubang backtest JSON report.")
    parser.add_argument("--report", default=None, help="Backtest report JSON. Defaults to latest data/exports/backtest_*.json.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--limit", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        path = Path(args.report) if args.report else latest_backtest_report(Path(args.output_dir))
        report = json.loads(path.read_text(encoding="utf-8"))
        theme_by_symbol = load_theme_map(Path(args.universe))
    except Exception as exc:
        print(f"analyze_backtest failed: {exc}", file=sys.stderr)
        return 1

    print(format_analysis(report, path, limit=args.limit, theme_by_symbol=theme_by_symbol))
    return 0


def latest_backtest_report(output_dir: Path) -> Path:
    reports = sorted(output_dir.glob("backtest_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No backtest reports found in {output_dir}")
    return reports[-1]


def format_analysis(
    report: dict[str, Any],
    path: Path,
    *,
    limit: int,
    theme_by_symbol: dict[str, str] | None = None,
) -> str:
    summary = report.get("summary") or {}
    diagnostics = report.get("diagnostics") or {}
    trades = report.get("trades") or []
    lines = [
        "Backtest analysis",
        f"Report: {path}",
        f"Trades: {summary.get('trade_count')} candidates={report.get('candidate_count')}",
        (
            "Entry funnel: "
            f"attempts={diagnostics.get('entry_attempts')} "
            f"filled={diagnostics.get('filled_entries')} "
            f"unfilled={diagnostics.get('unfilled_entries')} "
            f"no_slot={diagnostics.get('skipped_no_slot')}"
        ),
        (
            "Performance: "
            f"pnl={summary.get('total_pnl')} "
            f"return={summary.get('return_pct')}% "
            f"avgR={summary.get('average_r')} "
            f"pf={summary.get('profit_factor')} "
            f"dd={summary.get('max_drawdown_pct')}%"
        ),
    ]
    lines.extend(format_concentration(summary.get("by_symbol") or {}, summary.get("total_pnl")))
    if theme_by_symbol:
        lines.extend(format_theme_performance(trades, theme_by_symbol, limit=limit))
    lines.extend(format_group("Worst symbols", summary.get("by_symbol") or {}, limit=limit, reverse=False))
    lines.extend(format_group("Best symbols", summary.get("by_symbol") or {}, limit=limit, reverse=True))
    lines.extend(format_group("Exit reasons", summary.get("by_exit_reason") or {}, limit=limit, reverse=True))
    lines.extend(format_period_stability(trades))
    lines.extend(format_year_stability(trades))
    lines.extend(format_quarter_stability(trades, limit=limit))
    lines.extend(format_month_stability(summary.get("by_exit_month") or {}))
    lines.extend(format_group("Worst months", summary.get("by_exit_month") or {}, limit=limit, reverse=False))
    return "\n".join(lines)


def load_theme_map(universe_path: Path) -> dict[str, str]:
    universe = load_universe(universe_path)
    output = {}
    for item in universe.metadata.get("core_symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        theme = str(item.get("theme") or "").strip()
        if symbol and theme:
            output[symbol] = theme
    return output


def format_group(title: str, group: dict[str, dict[str, Any]], *, limit: int, reverse: bool) -> list[str]:
    rows = sorted(
        group.items(),
        key=lambda item: float(item[1].get("pnl") or 0.0),
        reverse=reverse,
    )[:limit]
    lines = [title + ":"]
    if not rows:
        lines.append("  n/a")
        return lines
    for key, item in rows:
        lines.append(
            f"  {key}: pnl={item.get('pnl')} trades={item.get('trades')} "
            f"win={as_pct(item.get('win_rate'))} avgR={item.get('average_r')}"
        )
    return lines


def as_pct(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def format_concentration(by_symbol: dict[str, dict[str, Any]], total_pnl: Any) -> list[str]:
    positive = [
        (symbol, float(item.get("pnl") or 0.0))
        for symbol, item in by_symbol.items()
        if float(item.get("pnl") or 0.0) > 0
    ]
    positive = sorted(positive, key=lambda item: item[1], reverse=True)
    if not positive:
        return ["Concentration:", "  n/a"]
    gross_profit = sum(pnl for _, pnl in positive)
    net_pnl = float(total_pnl or 0.0)
    top1 = positive[0][1]
    top3 = sum(pnl for _, pnl in positive[:3])
    lines = [
        "Concentration:",
        (
            f"  top1={positive[0][0]} gross_share={ratio(top1, gross_profit)} "
            f"net_share={ratio(top1, net_pnl)}"
        ),
        (
            f"  top3={','.join(symbol for symbol, _ in positive[:3])} "
            f"gross_share={ratio(top3, gross_profit)} net_share={ratio(top3, net_pnl)}"
        ),
    ]
    return lines


def format_theme_performance(
    trades: list[dict[str, Any]],
    theme_by_symbol: dict[str, str],
    *,
    limit: int,
) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        symbol = str(trade.get("symbol") or "").upper()
        theme = theme_by_symbol.get(symbol, "unknown")
        grouped.setdefault(theme, []).append(trade)
    summary = {
        theme: summarize_trade_subset(items)
        for theme, items in grouped.items()
    }
    lines = []
    lines.extend(format_group("Worst themes", summary, limit=limit, reverse=False))
    lines.extend(format_group("Best themes", summary, limit=limit, reverse=True))
    return lines


def format_period_stability(trades: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(
        trades,
        key=lambda trade: trade.get("final_exit_date") or trade.get("entry_date") or "",
    )
    if len(ordered) < 2:
        return ["Period stability:", "  n/a"]
    midpoint = len(ordered) // 2
    rows = [
        ("first_half", ordered[:midpoint]),
        ("second_half", ordered[midpoint:]),
    ]
    lines = ["Period stability:"]
    for name, items in rows:
        summary = summarize_trade_subset(items)
        start_date = subset_date(items[0]) if items else None
        end_date = subset_date(items[-1]) if items else None
        lines.append(
            f"  {name}: pnl={summary['pnl']} trades={summary['trades']} "
            f"win={as_pct(summary['win_rate'])} avgR={summary['average_r']} "
            f"dates={start_date}..{end_date}"
        )
    return lines


def format_month_stability(months: dict[str, dict[str, Any]]) -> list[str]:
    if not months:
        return ["Month stability:", "  n/a"]
    positive = sum(1 for item in months.values() if float(item.get("pnl") or 0.0) > 0)
    negative = sum(1 for item in months.values() if float(item.get("pnl") or 0.0) < 0)
    flat = len(months) - positive - negative
    return [
        "Month stability:",
        f"  positive={positive} negative={negative} flat={flat} total={len(months)}",
    ]


def format_year_stability(trades: list[dict[str, Any]]) -> list[str]:
    grouped = group_trades_by_period(trades, "year")
    if not grouped:
        return ["Year stability:", "  n/a"]
    lines = ["Year stability:"]
    for year, items in sorted(grouped.items()):
        summary = summarize_trade_subset(items)
        lines.append(
            f"  {year}: pnl={summary['pnl']} trades={summary['trades']} "
            f"win={as_pct(summary['win_rate'])} avgR={summary['average_r']}"
        )
    return lines


def format_quarter_stability(trades: list[dict[str, Any]], *, limit: int) -> list[str]:
    grouped = group_trades_by_period(trades, "quarter")
    summary = {
        quarter: summarize_trade_subset(items)
        for quarter, items in grouped.items()
    }
    return format_group("Worst quarters", summary, limit=limit, reverse=False)


def group_trades_by_period(trades: list[dict[str, Any]], period: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        raw = trade.get("final_exit_date") or trade.get("entry_date")
        if not raw:
            continue
        try:
            parsed = date.fromisoformat(str(raw))
        except ValueError:
            continue
        if period == "year":
            key = f"{parsed.year}"
        elif period == "quarter":
            key = f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
        else:
            raise ValueError(f"Unsupported period: {period}")
        grouped.setdefault(key, []).append(trade)
    return grouped


def summarize_trade_subset(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(float(trade.get("pnl") or 0.0) for trade in trades)
    wins = sum(1 for trade in trades if float(trade.get("pnl") or 0.0) > 0)
    r_values = [
        float(trade["r_multiple"])
        for trade in trades
        if trade.get("r_multiple") is not None
    ]
    return {
        "trades": len(trades),
        "pnl": round(pnl, 2),
        "win_rate": wins / len(trades) if trades else None,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
    }


def subset_date(trade: dict[str, Any]) -> str | None:
    raw = trade.get("final_exit_date") or trade.get("entry_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)).isoformat()
    except ValueError:
        return str(raw)


def ratio(numerator: float, denominator: float) -> str:
    if not denominator:
        return "n/a"
    return f"{numerator / denominator:.2%}"


if __name__ == "__main__":
    raise SystemExit(main())
