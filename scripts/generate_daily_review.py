#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.journal import load_journal_lots, summarize_journal


EASTERN = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Liubang daily observation review.")
    parser.add_argument("--review-date", default=None, help="ET review date in YYYY-MM-DD. Defaults to today's ET date.")
    parser.add_argument("--exports-dir", default="data/exports")
    parser.add_argument("--paper-positions", default="data/paper_positions.json")
    parser.add_argument("--paper-journal", default="data/paper_trade_journal.csv")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--html-output", default=None)
    parser.add_argument("--open", action="store_true", help="Open the rendered HTML review with macOS open.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    now = datetime.now(UTC)
    review_date = date.fromisoformat(args.review_date) if args.review_date else now.astimezone(EASTERN).date()
    model = build_review_model(
        review_date=review_date,
        exports_dir=Path(args.exports_dir),
        paper_positions_path=Path(args.paper_positions),
        paper_journal_path=Path(args.paper_journal),
        now=now,
    )

    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    json_output = Path(args.json_output) if args.json_output else Path(args.exports_dir) / f"review_{timestamp}.json"
    html_output = Path(args.html_output) if args.html_output else Path("data/reviews") / f"liubang_review_{review_date.isoformat()}.html"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_output.write_text(render_review_html(model), encoding="utf-8")

    print(format_review_summary(model, json_output=json_output, html_output=html_output))
    if args.open:
        subprocess.run(["open", str(html_output.resolve())], check=False)
    return 0


def build_review_model(
    *,
    review_date: date,
    exports_dir: Path,
    paper_positions_path: Path,
    paper_journal_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    workflows = reports_for_date(load_json_reports(exports_dir, "workflow_"), review_date)
    signals_all = load_json_reports(exports_dir, "signals_")
    signals_path, signals_payload = select_signal_report(signals_all, review_date)
    triggers = reports_for_date(load_json_reports(exports_dir, "triggers_"), review_date)
    paper_updates = reports_for_date(load_json_reports(exports_dir, "paper_positions_update_"), review_date)
    paper_actions = reports_for_date(load_json_reports(exports_dir, "paper_actions_"), review_date)

    signals = summarize_signals(signals_path, signals_payload, review_date=review_date)
    trigger_summary = summarize_trigger_reports(triggers, review_date=review_date, watchlist=signals["watchlist"])
    workflow_summary = summarize_workflows(workflows)
    paper_update_summary = summarize_paper_updates(paper_updates)
    paper_action_summary = summarize_paper_actions(paper_actions)
    paper_positions = load_open_paper_positions(paper_positions_path)
    paper_journal = summarize_paper_journal(paper_journal_path, review_date=review_date)
    data_issues = build_data_issues(
        signals=signals,
        triggers=trigger_summary,
        workflows=workflow_summary,
        paper_positions=paper_positions,
        paper_journal=paper_journal,
    )
    metrics = {
        "watchlist_count": signals["watchlist_count"],
        "triggered_today_count": len(trigger_summary["triggered_symbols"]),
        "final_triggered_count": trigger_summary["final_status_counts"].get("triggered", 0),
        "open_paper_positions": paper_positions["open_count"],
        "new_paper_positions": len(paper_update_summary["new_positions"]),
        "today_closed_paper_trades": paper_journal["today_trade_count"],
        "today_realized_pnl": paper_journal["today_realized_pnl"],
        "data_issue_count": len(data_issues),
    }
    return {
        "generated_at": now.isoformat(),
        "review_date_et": review_date.isoformat(),
        "mode": "daily_observation_review",
        "metrics": metrics,
        "signals": signals,
        "triggers": trigger_summary,
        "workflows": workflow_summary,
        "paper_updates": paper_update_summary,
        "paper_actions": paper_action_summary,
        "paper_positions": paper_positions,
        "paper_journal": paper_journal,
        "data_issues": data_issues,
    }


def load_json_reports(exports_dir: Path, prefix: str) -> list[tuple[Path, dict[str, Any]]]:
    reports = []
    for path in sorted(exports_dir.glob(f"{prefix}*.json"), key=lambda item: item.stat().st_mtime):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            reports.append((path, payload))
    return reports


def reports_for_date(reports: list[tuple[Path, dict[str, Any]]], review_date: date) -> list[tuple[Path, dict[str, Any]]]:
    return [
        (path, payload)
        for path, payload in reports
        if generated_et_date(payload) == review_date
    ]


def generated_et_date(payload: dict[str, Any]) -> date | None:
    parsed = parse_dt(payload.get("generated_at"))
    return parsed.astimezone(EASTERN).date() if parsed else None


def select_signal_report(reports: list[tuple[Path, dict[str, Any]]], review_date: date) -> tuple[Path | None, dict[str, Any] | None]:
    matching_planned = [
        (path, payload)
        for path, payload in reports
        if any(parse_date(item.get("planned_entry_date")) == review_date for item in payload.get("watchlist", []) if isinstance(item, dict))
    ]
    if matching_planned:
        return sorted(matching_planned, key=lambda item: item[0].stat().st_mtime)[-1]
    matching_generated = reports_for_date(reports, review_date)
    if matching_generated:
        return sorted(matching_generated, key=lambda item: item[0].stat().st_mtime)[-1]
    if reports:
        return sorted(reports, key=lambda item: item[0].stat().st_mtime)[-1]
    return None, None


def summarize_signals(path: Path | None, payload: dict[str, Any] | None, *, review_date: date) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "missing",
            "path": None,
            "generated_at": None,
            "is_review_date_signal": False,
            "watchlist_count": 0,
            "watchlist": [],
            "market_regime": {},
            "data_quality": {},
            "history_sources": {},
            "portfolio_guard": {},
            "risk_throttle": {},
        }
    watchlist = [compact_watchlist_item(item) for item in payload.get("watchlist", []) if isinstance(item, dict)]
    is_review_date_signal = any(parse_date(item.get("planned_entry_date")) == review_date for item in watchlist)
    return {
        "status": "loaded",
        "path": str(path) if path else None,
        "generated_at": payload.get("generated_at"),
        "is_review_date_signal": is_review_date_signal,
        "watchlist_count": payload.get("watchlist_count", len(watchlist)),
        "watchlist": watchlist,
        "market_regime": payload.get("market_regime") or {},
        "data_quality": payload.get("data_quality") or {},
        "history_sources": payload.get("history_sources") or {},
        "portfolio_guard": payload.get("portfolio_guard") or {},
        "risk_throttle": payload.get("risk_throttle") or {},
        "watchlist_concentration": payload.get("watchlist_concentration") or {},
    }


def compact_watchlist_item(item: dict[str, Any]) -> dict[str, Any]:
    plan = item.get("trade_plan") or {}
    return {
        "symbol": str(item.get("symbol") or "").upper(),
        "planned_entry_date": item.get("planned_entry_date"),
        "as_of_date": item.get("as_of_date"),
        "source": item.get("source"),
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
        "news_risk": (item.get("news_risk") or {}).get("level"),
        "options_risk": (item.get("options_risk") or {}).get("level"),
        "risk_notes": item.get("risk_notes") or [],
    }


def summarize_trigger_reports(
    reports: list[tuple[Path, dict[str, Any]]],
    *,
    review_date: date,
    watchlist: list[dict[str, Any]],
) -> dict[str, Any]:
    sorted_reports = sorted(reports, key=lambda item: item[0].stat().st_mtime)
    final_by_symbol: dict[str, dict[str, Any]] = {}
    triggered_symbols: set[str] = set()
    for path, payload in sorted_reports:
        generated_at = payload.get("generated_at")
        for raw in payload.get("evaluations", []):
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "").upper()
            if not symbol:
                continue
            item = compact_trigger_item(raw)
            item["report_path"] = str(path)
            item["report_generated_at"] = generated_at
            final_by_symbol[symbol] = item
            if raw.get("triggered"):
                triggered_symbols.add(symbol)

    for signal in watchlist:
        symbol = str(signal.get("symbol") or "").upper()
        if symbol and symbol not in final_by_symbol:
            final_by_symbol[symbol] = {
                "symbol": symbol,
                "status": "no_trigger_report",
                "triggered": False,
                "reason": "no_trigger_evaluation_for_review_date",
            }
    final = [final_by_symbol[key] for key in sorted(final_by_symbol)]
    return {
        "status": "missing" if not sorted_reports else "loaded",
        "review_date_et": review_date.isoformat(),
        "report_count": len(sorted_reports),
        "latest_report_path": str(sorted_reports[-1][0]) if sorted_reports else None,
        "latest_generated_at": sorted_reports[-1][1].get("generated_at") if sorted_reports else None,
        "final_status_counts": count_by_key(final, "status"),
        "reason_counts": count_reasons(final),
        "triggered_symbols": sorted(triggered_symbols),
        "final_evaluations": final,
        "history_sources": sorted_reports[-1][1].get("history_sources") if sorted_reports else {},
    }


def compact_trigger_item(item: dict[str, Any]) -> dict[str, Any]:
    plan = item.get("trade_plan") or {}
    return {
        "symbol": str(item.get("symbol") or "").upper(),
        "status": item.get("status"),
        "triggered": bool(item.get("triggered")),
        "action": item.get("action"),
        "planned_entry_date": item.get("planned_entry_date"),
        "signal_score": item.get("signal_score"),
        "last_bar_time_et": item.get("last_bar_time_et"),
        "last_close": item.get("last_close"),
        "running_vwap": item.get("running_vwap"),
        "prior_candle_high": item.get("prior_candle_high"),
        "trigger_price_reference": item.get("trigger_price_reference"),
        "stop_reference": item.get("stop_reference"),
        "risk_per_share_reference": item.get("risk_per_share_reference"),
        "suggested_shares": plan.get("suggested_shares"),
        "reason": item.get("reason"),
    }


def summarize_workflows(reports: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    timeline = []
    for path, payload in sorted(reports, key=lambda item: item[0].stat().st_mtime):
        steps = [item for item in payload.get("steps", []) if isinstance(item, dict)]
        timeline.append(
            {
                "path": str(path),
                "generated_at": payload.get("generated_at"),
                "status": payload.get("status") or "unknown",
                "step_count": len(steps),
                "skipped_count": sum(1 for item in steps if item.get("status") == "skipped"),
                "failed_count": sum(1 for item in steps if item.get("status") not in {"ok", "skipped"}),
                "reasons": [str(item.get("reason")) for item in steps if item.get("reason")],
            }
        )
    return {
        "status": "missing" if not timeline else "loaded",
        "count": len(timeline),
        "latest": timeline[-1] if timeline else None,
        "timeline": timeline[-20:],
        "status_counts": count_by_key(timeline, "status"),
    }


def summarize_paper_updates(reports: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    new_positions = []
    skipped_duplicates = []
    skipped_invalid = []
    for path, payload in sorted(reports, key=lambda item: item[0].stat().st_mtime):
        generated_at = payload.get("generated_at")
        for item in payload.get("new_positions") or []:
            if isinstance(item, dict):
                new_positions.append(dict(item) | {"report_path": str(path), "report_generated_at": generated_at})
        for item in payload.get("skipped_duplicates") or []:
            if isinstance(item, dict):
                skipped_duplicates.append(dict(item) | {"report_path": str(path), "report_generated_at": generated_at})
        for item in payload.get("skipped_invalid") or []:
            if isinstance(item, dict):
                skipped_invalid.append(dict(item) | {"report_path": str(path), "report_generated_at": generated_at})
    return {
        "report_count": len(reports),
        "new_positions": new_positions,
        "skipped_duplicates": skipped_duplicates,
        "skipped_invalid": skipped_invalid,
    }


def summarize_paper_actions(reports: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    actions = []
    journal_rows = []
    for path, payload in sorted(reports, key=lambda item: item[0].stat().st_mtime):
        generated_at = payload.get("generated_at")
        for key in ("applied_actions", "applied", "actions", "skipped"):
            for item in payload.get(key) or []:
                if isinstance(item, dict):
                    actions.append(dict(item) | {"bucket": key, "report_path": str(path), "report_generated_at": generated_at})
        for item in payload.get("journal_rows") or []:
            if isinstance(item, dict):
                journal_rows.append(dict(item) | {"report_path": str(path), "report_generated_at": generated_at})
    return {
        "report_count": len(reports),
        "actions": actions,
        "journal_rows": journal_rows,
    }


def load_open_paper_positions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "open_count": 0,
            "open_positions": [],
            "total_position_value": 0.0,
            "total_initial_risk": 0.0,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "path": str(path),
            "error": str(exc),
            "open_count": 0,
            "open_positions": [],
            "total_position_value": 0.0,
            "total_initial_risk": 0.0,
        }
    positions = [dict(item) for item in payload.get("positions", []) if isinstance(item, dict)]
    open_positions = [
        compact_position(item)
        for item in positions
        if as_int(item.get("remaining_shares", item.get("shares"))) > 0
    ]
    total_value = sum(as_float(item.get("entry_price")) * as_int(item.get("remaining_shares")) for item in open_positions)
    total_risk = sum(position_initial_risk(item) for item in open_positions)
    return {
        "status": "loaded",
        "path": str(path),
        "open_count": len(open_positions),
        "open_positions": open_positions,
        "total_position_value": round(total_value, 2),
        "total_initial_risk": round(total_risk, 2),
    }


def compact_position(item: dict[str, Any]) -> dict[str, Any]:
    shares = as_int(item.get("shares"))
    remaining = as_int(item.get("remaining_shares", shares))
    return {
        "symbol": str(item.get("symbol") or "").upper(),
        "entry_date": item.get("entry_date"),
        "entry_price": as_float(item.get("entry_price")),
        "shares": shares,
        "remaining_shares": remaining,
        "initial_stop_price": as_float(item.get("initial_stop_price")),
        "current_stop_price": as_float(item.get("current_stop_price", item.get("initial_stop_price"))),
        "target_price": as_float(item.get("target_price")),
        "target_hit": bool(item.get("target_hit")),
        "notes": item.get("notes"),
    }


def summarize_paper_journal(path: Path, *, review_date: date) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "today_lot_count": 0,
            "today_trade_count": 0,
            "today_realized_pnl": 0.0,
            "today_trades": [],
            "all_trade_count": 0,
            "all_realized_pnl": 0.0,
        }
    try:
        lots = load_journal_lots(path)
        today_lots = tuple(lot for lot in lots if lot.exit_date == review_date)
        today_summary = summarize_journal(lots=today_lots, path=path)
        all_summary = summarize_journal(lots=lots, path=path)
    except Exception as exc:
        return {
            "status": "error",
            "path": str(path),
            "error": str(exc),
            "today_lot_count": 0,
            "today_trade_count": 0,
            "today_realized_pnl": 0.0,
            "today_trades": [],
            "all_trade_count": 0,
            "all_realized_pnl": 0.0,
        }
    return {
        "status": "loaded",
        "path": str(path),
        "today_lot_count": len(today_lots),
        "today_trade_count": today_summary["trade_count"],
        "today_realized_pnl": today_summary["realized_pnl"],
        "today_average_r": today_summary.get("average_r"),
        "today_win_rate": today_summary.get("win_rate"),
        "today_trades": today_summary["trades"],
        "all_trade_count": all_summary["trade_count"],
        "all_realized_pnl": all_summary["realized_pnl"],
        "all_average_r": all_summary.get("average_r"),
    }


def build_data_issues(
    *,
    signals: dict[str, Any],
    triggers: dict[str, Any],
    workflows: dict[str, Any],
    paper_positions: dict[str, Any],
    paper_journal: dict[str, Any],
) -> list[dict[str, Any]]:
    issues = []
    if signals["status"] == "missing":
        issues.append(issue("missing_signals", "未找到 signals 报告", "bad"))
    elif not signals["is_review_date_signal"]:
        issues.append(issue("signals_not_for_review_date", "signals 的 planned_entry_date 不匹配复盘日期", "warn"))
    data_quality = signals.get("data_quality") or {}
    for warning in data_quality.get("warnings") or []:
        issues.append(issue("signal_data_quality", str(warning), "warn"))
    for label, source_summary in (
        ("signals", signals.get("history_sources") or {}),
        ("triggers", triggers.get("history_sources") or {}),
    ):
        fallback_count = int(source_summary.get("fallback_count") or 0)
        if fallback_count:
            issues.append(issue(f"{label}_history_fallbacks", f"{label} history fallback count={fallback_count}", "warn"))
        errors = source_summary.get("error_symbols") or {}
        if errors:
            issues.append(issue(f"{label}_history_errors", f"{label} history errors: {','.join(sorted(errors))}", "bad"))
    if triggers["status"] == "missing":
        issues.append(issue("missing_triggers", "未找到当日 triggers 报告", "bad"))
    waiting_market = triggers.get("final_status_counts", {}).get("waiting_for_market_data", 0)
    if waiting_market:
        issues.append(issue("waiting_for_market_data", f"{waiting_market} 只标的仍在等待常规盘 5m 数据", "warn"))
    if workflows["status"] == "missing":
        issues.append(issue("missing_workflow", "未找到当日 workflow 报告", "warn"))
    latest_workflow = workflows.get("latest") or {}
    if latest_workflow.get("status") == "skipped":
        reasons = "; ".join(latest_workflow.get("reasons") or [])
        issues.append(issue("workflow_skipped", reasons or "workflow skipped", "warn"))
    if paper_positions["status"] == "error":
        issues.append(issue("paper_positions_error", paper_positions.get("error") or "paper positions parse failed", "bad"))
    if paper_journal["status"] == "error":
        issues.append(issue("paper_journal_error", paper_journal.get("error") or "paper journal parse failed", "bad"))
    return issues


def issue(name: str, detail: str, severity: str) -> dict[str, str]:
    return {"name": name, "detail": detail, "severity": severity}


def render_review_html(model: dict[str, Any]) -> str:
    metrics = model["metrics"]
    signals = model["signals"]
    triggers = model["triggers"]
    workflows = model["workflows"]
    paper_positions = model["paper_positions"]
    paper_journal = model["paper_journal"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Liubang Daily Review {h(model['review_date_et'])}</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --panel: #fff;
      --line: #d9ded8;
      --text: #202124;
      --muted: #676c73;
      --green: #15803d;
      --amber: #b45309;
      --red: #b91c1c;
      --blue: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .page {{ width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 22px 0 30px; }}
    h1 {{ margin: 0; font-size: 25px; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 16px; letter-spacing: 0; }}
    .topbar {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }}
    .subtle {{ color: var(--muted); font-size: 12px; }}
    .grid {{ display: grid; gap: 12px; }}
    .metrics {{ grid-template-columns: repeat(5, minmax(0, 1fr)); margin-bottom: 12px; }}
    .metric, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 1px 2px rgba(20,24,28,.06); }}
    .metric {{ padding: 14px; min-height: 94px; }}
    .metric-label {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .metric-value {{ font-size: 24px; font-weight: 700; line-height: 1.1; word-break: break-word; }}
    .columns {{ grid-template-columns: minmax(0, 1.2fr) minmax(360px, .8fr); align-items: start; }}
    .panel {{ overflow: hidden; }}
    .panel-head {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 13px 14px 10px; border-bottom: 1px solid var(--line); background: #fbfbf9; }}
    .panel-body {{ padding: 14px; }}
    .stack {{ display: grid; gap: 12px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
    th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #ecefec; white-space: nowrap; vertical-align: middle; }}
    th {{ color: var(--muted); font-size: 12px; background: #fbfbf9; }}
    .table-wrap {{ overflow-x: auto; }}
    .pill {{ display: inline-flex; align-items: center; min-height: 24px; border-radius: 999px; border: 1px solid var(--line); padding: 2px 8px; background: #fbfbf9; color: var(--muted); font-size: 12px; }}
    .pill.good {{ color: var(--green); background: #f0fdf4; border-color: #bbf7d0; }}
    .pill.warn {{ color: var(--amber); background: #fffbeb; border-color: #fde68a; }}
    .pill.bad {{ color: var(--red); background: #fef2f2; border-color: #fecaca; }}
    .pill.info {{ color: var(--blue); background: #eff6ff; border-color: #bfdbfe; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
    .empty {{ border: 1px dashed var(--line); border-radius: 8px; padding: 16px; color: var(--muted); background: #fcfcfa; }}
    @media (max-width: 980px) {{ .metrics, .columns {{ grid-template-columns: 1fr; }} .page {{ width: min(100vw - 20px, 1440px); }} .topbar {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <main class="page">
    <div class="topbar">
      <div>
        <h1>Liubang Daily Review</h1>
        <div class="subtle">复盘日期 ET：{h(model['review_date_et'])} · 生成：{h(format_dt_pair(parse_dt(model['generated_at'])))}</div>
      </div>
      <span class="pill info">paper observation</span>
    </div>
    <section class="grid metrics">
      {metric_card("Watchlist", metrics["watchlist_count"], "signals")}
      {metric_card("今日触发", metrics["triggered_today_count"], "ever triggered")}
      {metric_card("开放 Paper", metrics["open_paper_positions"], "positions")}
      {metric_card("今日已平", metrics["today_closed_paper_trades"], format_money(metrics["today_realized_pnl"]))}
      {metric_card("数据问题", metrics["data_issue_count"], "issues")}
    </section>
    <section class="grid columns">
      <div class="stack">
        {render_review_status_panel(model)}
        {render_review_trigger_panel(triggers)}
        {render_review_watchlist_panel(signals)}
      </div>
      <div class="stack">
        {render_review_paper_panel(model)}
        {render_review_workflow_panel(workflows)}
        {render_review_issue_panel(model["data_issues"])}
      </div>
    </section>
  </main>
</body>
</html>
"""


def render_review_status_panel(model: dict[str, Any]) -> str:
    signals = model["signals"]
    triggers = model["triggers"]
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>复盘概览</h2>
          <span class="pill {('good' if signals.get('is_review_date_signal') else 'warn')}">{'signals 日期匹配' if signals.get('is_review_date_signal') else 'signals 日期需检查'}</span>
        </div>
        <div class="panel-body">
          <div class="subtle">signals: {h(signals.get('path') or 'n/a')}</div>
          <div class="subtle">latest triggers: {h(triggers.get('latest_report_path') or 'n/a')}</div>
          <div class="subtle">market regime: {h((signals.get('market_regime') or {}).get('regime', 'n/a'))}</div>
        </div>
      </section>
    """


def render_review_trigger_panel(triggers: dict[str, Any]) -> str:
    status_pills = " ".join(
        f'<span class="pill {tone_for_status(status)}">{h(status)} {count}</span>'
        for status, count in sorted((triggers.get("final_status_counts") or {}).items())
    ) or '<span class="pill muted">无 trigger 数据</span>'
    reason_pills = " ".join(
        f'<span class="pill warn">{h(reason)} {count}</span>'
        for reason, count in sorted((triggers.get("reason_counts") or {}).items(), key=lambda item: (-item[1], item[0]))[:6]
    )
    rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{status_badge(item.get('status'))}</td>
          <td>{h(item.get('last_close'))}</td>
          <td>{h(item.get('trigger_price_reference'))}</td>
          <td>{h(item.get('stop_reference'))}</td>
          <td>{h(item.get('reason') or item.get('action'))}</td>
        </tr>
        """
        for item in triggers.get("final_evaluations", [])
    )
    return f"""
      <section class="panel">
        <div class="panel-head"><h2>最终触发状态</h2><span class="pill info">{h(triggers.get('report_count'))} reports</span></div>
        <div class="panel-body">
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">{status_pills}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">{reason_pills}</div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>标的</th><th>状态</th><th>最新价</th><th>触发参考</th><th>止损</th><th>原因</th></tr></thead>
              <tbody>{rows or '<tr><td colspan="6">无 trigger 记录。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def render_review_watchlist_panel(signals: dict[str, Any]) -> str:
    rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{h(item.get('total_score'))}</td>
          <td>{format_pct(item.get('pullback_pct'))}</td>
          <td>{h(item.get('close'))}</td>
          <td>{h(item.get('suggested_shares'))}</td>
          <td>{h(item.get('planned_entry_date'))}</td>
        </tr>
        """
        for item in signals.get("watchlist", [])
    )
    return f"""
      <section class="panel">
        <div class="panel-head"><h2>Watchlist</h2><span class="pill info">{h(signals.get('watchlist_count'))}</span></div>
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead><tr><th>标的</th><th>分数</th><th>回撤</th><th>收盘</th><th>建议股数</th><th>计划日期</th></tr></thead>
              <tbody>{rows or '<tr><td colspan="6">无候选。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def render_review_paper_panel(model: dict[str, Any]) -> str:
    positions = model["paper_positions"].get("open_positions") or []
    journal = model["paper_journal"]
    updates = model["paper_updates"]
    position_rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{h(item.get('entry_date'))}</td>
          <td>{h(item.get('remaining_shares'))} / {h(item.get('shares'))}</td>
          <td>{h(item.get('entry_price'))}</td>
          <td>{h(item.get('current_stop_price'))}</td>
          <td>{h(item.get('target_price'))}</td>
        </tr>
        """
        for item in positions
    )
    trade_rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{h(item.get('shares'))}</td>
          <td>{h(format_money(item.get('pnl')))}</td>
          <td>{h(item.get('r_multiple'))}</td>
          <td>{h(item.get('exit_date'))}</td>
        </tr>
        """
        for item in journal.get("today_trades", [])
    )
    new_symbols = ", ".join(str(item.get("symbol")) for item in updates.get("new_positions", []) if item.get("symbol")) or "none"
    return f"""
      <section class="panel">
        <div class="panel-head"><h2>Paper 状态</h2><span class="pill info">new: {h(new_symbols)}</span></div>
        <div class="panel-body">
          <div class="subtle">今日已实现：{h(format_money(journal.get('today_realized_pnl')))} · 全部已实现：{h(format_money(journal.get('all_realized_pnl')))}</div>
          <div class="table-wrap" style="margin-top:10px">
            <table>
              <thead><tr><th>开放标的</th><th>入场日</th><th>剩余</th><th>入场</th><th>止损</th><th>目标</th></tr></thead>
              <tbody>{position_rows or '<tr><td colspan="6">暂无开放 paper 持仓。</td></tr>'}</tbody>
            </table>
          </div>
          <div class="table-wrap" style="margin-top:10px">
            <table>
              <thead><tr><th>今日平仓</th><th>股数</th><th>PnL</th><th>R</th><th>退出日</th></tr></thead>
              <tbody>{trade_rows or '<tr><td colspan="5">今日暂无 paper 平仓。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def render_review_workflow_panel(workflows: dict[str, Any]) -> str:
    rows = "\n".join(
        f"""
        <tr>
          <td>{h(format_dt_pair(parse_dt(item.get('generated_at'))))}</td>
          <td>{status_badge(item.get('status'))}</td>
          <td>{h(item.get('step_count'))}</td>
          <td>{h(item.get('skipped_count'))}</td>
        </tr>
        """
        for item in workflows.get("timeline", [])[-10:][::-1]
    )
    return f"""
      <section class="panel">
        <div class="panel-head"><h2>Workflow 时间线</h2><span class="pill info">{h(workflows.get('count'))}</span></div>
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead><tr><th>时间</th><th>状态</th><th>步骤</th><th>跳过</th></tr></thead>
              <tbody>{rows or '<tr><td colspan="4">暂无 workflow。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def render_review_issue_panel(issues: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f"""
        <tr>
          <td>{status_badge(item.get('severity'))}</td>
          <td>{h(item.get('name'))}</td>
          <td>{h(item.get('detail'))}</td>
        </tr>
        """
        for item in issues
    )
    return f"""
      <section class="panel">
        <div class="panel-head"><h2>数据问题</h2><span class="pill {('good' if not issues else 'warn')}">{len(issues)}</span></div>
        <div class="panel-body">
          <div class="table-wrap">
            <table>
              <thead><tr><th>级别</th><th>名称</th><th>说明</th></tr></thead>
              <tbody>{rows or '<tr><td colspan="3">未发现复盘级数据问题。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def metric_card(label: str, value: Any, meta: str) -> str:
    return f"""
      <div class="metric">
        <div class="metric-label">{h(label)}</div>
        <div class="metric-value">{h(value)}</div>
        <div class="subtle">{h(meta)}</div>
      </div>
    """


def format_review_summary(model: dict[str, Any], *, json_output: Path, html_output: Path) -> str:
    metrics = model["metrics"]
    return "\n".join(
        [
            "Daily review",
            f"Review date ET: {model['review_date_et']}",
            f"Watchlist: {metrics['watchlist_count']}",
            f"Triggered today: {metrics['triggered_today_count']}",
            f"Open paper positions: {metrics['open_paper_positions']}",
            f"Today paper PnL: {metrics['today_realized_pnl']}",
            f"Data issues: {metrics['data_issue_count']}",
            f"JSON: {json_output}",
            f"HTML: {html_output}",
        ]
    )


def count_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def count_reasons(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("triggered"):
            continue
        value = str(row.get("reason") or row.get("status") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def format_dt_pair(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.astimezone(SHANGHAI):%Y-%m-%d %H:%M:%S CST} / {value.astimezone(EASTERN):%H:%M:%S ET}"


def format_pct(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def format_money(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    return f"${number:,.2f}"


def status_badge(status: Any) -> str:
    raw = str(status or "n/a")
    return f'<span class="pill {tone_for_status(raw)}">{h(raw)}</span>'


def tone_for_status(status: str) -> str:
    normalized = status.lower()
    if normalized in {"ok", "loaded", "hold", "triggered", "good"}:
        return "good"
    if normalized in {"warn", "warning", "waiting", "waiting_for_market_data", "skipped", "missing", "no_trigger_report"}:
        return "warn"
    if normalized in {"bad", "failed", "error", "invalidated"}:
        return "bad"
    return "info"


def position_initial_risk(position: dict[str, Any]) -> float:
    entry = as_float(position.get("entry_price"))
    stop = as_float(position.get("initial_stop_price", position.get("current_stop_price")))
    shares = as_int(position.get("remaining_shares", position.get("shares")))
    return max(0.0, entry - stop) * shares


def as_float(value: Any) -> float:
    number = optional_float(value)
    return number if number is not None else 0.0


def optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def h(value: Any) -> str:
    if value is None:
        return "n/a"
    return html.escape(str(value), quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
