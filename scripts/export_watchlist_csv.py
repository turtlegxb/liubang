#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export the latest signal watchlist to a flat CSV.")
    parser.add_argument("--signals-report", default=None, help="Signal report JSON. Defaults to latest data/exports/signals_*.json.")
    parser.add_argument("--output", default=None, help="CSV output path. Defaults to data/exports/watchlist_TIMESTAMP.csv.")
    parser.add_argument("--exports-dir", default="data/exports")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        signal_path = Path(args.signals_report) if args.signals_report else latest_signal_report(Path(args.exports_dir))
        report = json.loads(signal_path.read_text(encoding="utf-8"))
        output_path = (
            Path(args.output)
            if args.output
            else Path(args.exports_dir) / f"watchlist_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.csv"
        )
        rows = build_rows(report)
        write_rows(output_path, rows)
    except Exception as exc:
        print(f"export_watchlist_csv failed: {exc}", file=sys.stderr)
        return 1

    print("Watchlist CSV export")
    print(f"Signal report: {signal_path}")
    print(f"Rows: {len(rows)}")
    print(f"CSV: {output_path}")
    return 0


def latest_signal_report(exports_dir: Path) -> Path:
    reports = sorted(exports_dir.glob("signals_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No signal reports found in {exports_dir}")
    return reports[-1]


def build_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    portfolio_guard = report.get("portfolio_guard") or {}
    risk_throttle = report.get("risk_throttle") or {}
    concentration = report.get("watchlist_concentration") or {}
    rows = []
    for rank, item in enumerate(report.get("watchlist", []), start=1):
        plan = item.get("trade_plan") or {}
        news = item.get("recent_news") or []
        news_risk = item.get("news_risk") or {}
        options_risk = item.get("options_risk") or {}
        options = item.get("options_context") or {}
        weekly_gex = options.get("weekly_gex") or {}
        source_metadata = item.get("source_metadata") or {}
        rows.append(
            {
                "rank": rank,
                "symbol": item.get("symbol"),
                "source": item.get("source"),
                "theme": source_metadata.get("theme"),
                "source_reason": source_metadata.get("reason"),
                "planned_entry_date": item.get("planned_entry_date"),
                "market_regime": item.get("market_regime"),
                "total_score": item.get("total_score"),
                "strength_score": item.get("strength_score"),
                "pullback_score": item.get("pullback_score"),
                "pullback_pct": item.get("pullback_pct"),
                "close": item.get("close"),
                "entry_price_reference": item.get("entry_price_reference"),
                "stop_price_reference": item.get("stop_price_reference"),
                "first_target_price_reference": item.get("first_target_price_reference"),
                "suggested_shares": plan.get("suggested_shares"),
                "suggested_position_value": plan.get("suggested_position_value"),
                "suggested_dollar_risk": plan.get("suggested_dollar_risk"),
                "binding_constraint": plan.get("binding_constraint"),
                "portfolio_allow_new_entries": portfolio_guard.get("allow_new_entries"),
                "portfolio_available_slots": portfolio_guard.get("available_slots"),
                "portfolio_available_exposure_value": portfolio_guard.get("available_exposure_value"),
                "risk_throttle_status": risk_throttle.get("status"),
                "risk_throttle_allow_new_entries": risk_throttle.get("allow_new_entries"),
                "watchlist_top_theme": concentration.get("top_theme"),
                "watchlist_top_theme_share": concentration.get("top_theme_share"),
                "watchlist_concentration_warnings": ",".join(concentration.get("warnings") or []),
                "news_risk": news_risk.get("level"),
                "options_risk": options_risk.get("level"),
                "put_call_oi_ratio": options.get("put_call_oi_ratio"),
                "weekly_gex_regime": weekly_gex.get("regime"),
                "weekly_net_gex": weekly_gex.get("net_gex"),
                "weekly_call_wall": weekly_gex.get("call_wall"),
                "weekly_put_wall": weekly_gex.get("put_wall"),
                "recent_news_count": len(news),
                "top_news_headline": news[0].get("title") if news else None,
            }
        )
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "symbol",
        "source",
        "theme",
        "source_reason",
        "planned_entry_date",
        "market_regime",
        "total_score",
        "strength_score",
        "pullback_score",
        "pullback_pct",
        "close",
        "entry_price_reference",
        "stop_price_reference",
        "first_target_price_reference",
        "suggested_shares",
        "suggested_position_value",
        "suggested_dollar_risk",
        "binding_constraint",
        "portfolio_allow_new_entries",
        "portfolio_available_slots",
        "portfolio_available_exposure_value",
        "risk_throttle_status",
        "risk_throttle_allow_new_entries",
        "watchlist_top_theme",
        "watchlist_top_theme_share",
        "watchlist_concentration_warnings",
        "news_risk",
        "options_risk",
        "put_call_oi_ratio",
        "weekly_gex_regime",
        "weekly_net_gex",
        "weekly_call_wall",
        "weekly_put_wall",
        "recent_news_count",
        "top_news_headline",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
