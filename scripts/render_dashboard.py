#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import UTC, date, datetime, time
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
MARKET_START_ET = time(9, 31)
MARKET_END_ET = time(15, 55)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a local HTML dashboard for Liubang monitoring.")
    parser.add_argument("--exports-dir", default="data/exports")
    parser.add_argument("--paper-positions", default="data/paper_positions.json")
    parser.add_argument("--paper-journal", default="data/paper_trade_journal.csv")
    parser.add_argument("--output", default="data/dashboard/liubang_dashboard.html")
    parser.add_argument("--refresh-seconds", type=int, default=30)
    parser.add_argument("--open", action="store_true", help="Open the rendered dashboard with macOS open.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    model = build_dashboard_model(
        exports_dir=Path(args.exports_dir),
        paper_positions_path=Path(args.paper_positions),
        paper_journal_path=Path(args.paper_journal),
    )
    html_text = render_dashboard_html(model, refresh_seconds=max(5, args.refresh_seconds))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text, encoding="utf-8")
    print(f"Dashboard: {output}")
    if args.open:
        subprocess.run(["open", str(output.resolve())], check=False)
    return 0


def build_dashboard_model(
    *,
    exports_dir: Path,
    paper_positions_path: Path,
    paper_journal_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    workflow_path, workflow = latest_json_report(exports_dir, "workflow_")
    workflow_reports = latest_json_reports(exports_dir, "workflow_", limit=10)
    signals_path, signals = latest_json_report(exports_dir, "signals_")
    triggers_path, triggers = latest_json_report(exports_dir, "triggers_")
    paper_update_path, paper_update = latest_json_report(exports_dir, "paper_positions_update_")
    paper_positions = load_paper_positions(paper_positions_path)
    paper_position_report_path, paper_position_report = latest_position_report_for_paper(
        exports_dir,
        paper_positions.get("open_symbols", set()),
    )
    paper_journal = load_paper_journal(paper_journal_path)

    signals_summary = summarize_signals(signals_path, signals, now=now)
    triggers_summary = summarize_triggers(triggers_path, triggers)

    return {
        "generated_at": now,
        "market": market_status(now),
        "workflow": summarize_workflow(workflow_path, workflow),
        "workflow_history": summarize_workflow_history(workflow_reports),
        "signals": signals_summary,
        "triggers": triggers_summary,
        "watchlist_market": summarize_watchlist_market(signals_summary, triggers_summary),
        "paper_positions": paper_positions,
        "paper_position_monitor": summarize_position_monitor(paper_position_report_path, paper_position_report),
        "paper_update": summarize_paper_update(paper_update_path, paper_update),
        "paper_journal": paper_journal,
        "recent_orders": summarize_recent_orders(paper_positions, paper_journal),
    }


def latest_json_report(exports_dir: Path, prefix: str) -> tuple[Path | None, dict[str, Any] | None]:
    paths = sorted(exports_dir.glob(f"{prefix}*.json"), key=lambda path: path.stat().st_mtime)
    for path in reversed(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return path, payload
    return None, None


def latest_json_reports(exports_dir: Path, prefix: str, *, limit: int) -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(exports_dir.glob(f"{prefix}*.json"), key=lambda path: path.stat().st_mtime)
    reports = []
    for path in reversed(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            reports.append((path, payload))
        if len(reports) >= limit:
            break
    return reports


def latest_position_report_for_paper(exports_dir: Path, open_symbols: set[str]) -> tuple[Path | None, dict[str, Any] | None]:
    if not open_symbols:
        return None, None
    paths = sorted(exports_dir.glob("positions_*.json"), key=lambda path: path.stat().st_mtime)
    for path in reversed(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        eval_symbols = {
            str(item.get("symbol") or "").upper()
            for item in payload.get("evaluations", [])
            if isinstance(item, dict) and item.get("symbol")
        }
        if eval_symbols and eval_symbols <= open_symbols:
            return path, payload
    return None, None


def load_paper_positions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "status": "missing",
            "positions": [],
            "open_positions": [],
            "open_symbols": set(),
            "open_count": 0,
            "total_position_value": 0.0,
            "total_initial_risk": 0.0,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "status": "error",
            "error": str(exc),
            "positions": [],
            "open_positions": [],
            "open_symbols": set(),
            "open_count": 0,
            "total_position_value": 0.0,
            "total_initial_risk": 0.0,
        }
    positions = [dict(item) for item in payload.get("positions", []) if isinstance(item, dict)]
    open_positions = [item for item in positions if as_int(item.get("remaining_shares", item.get("shares"))) > 0]
    for item in open_positions:
        item["entry_time_et"] = item.get("entry_time_et") or item.get("entry_bar_time_et")
    open_symbols = {str(item.get("symbol") or "").upper() for item in open_positions if item.get("symbol")}
    total_value = sum(as_float(item.get("entry_price")) * as_int(item.get("remaining_shares", item.get("shares"))) for item in open_positions)
    total_risk = sum(position_initial_risk(item) for item in open_positions)
    return {
        "path": str(path),
        "status": "loaded",
        "positions": positions,
        "open_positions": open_positions,
        "open_symbols": open_symbols,
        "open_count": len(open_positions),
        "total_position_value": round(total_value, 2),
        "total_initial_risk": round(total_risk, 2),
    }


def load_paper_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "status": "missing",
            "trade_count": 0,
            "lot_count": 0,
            "realized_pnl": 0.0,
            "trades": [],
            "lots": [],
        }
    try:
        lots = load_journal_lots(path)
        summary = summarize_journal(lots=lots, path=path)
    except Exception as exc:
        return {
            "path": str(path),
            "status": "error",
            "error": str(exc),
            "trade_count": 0,
            "lot_count": 0,
            "realized_pnl": 0.0,
            "trades": [],
            "lots": [],
        }
    summary["status"] = "loaded"
    summary["path"] = str(path)
    summary["lots"] = [journal_lot_to_order_source(lot) for lot in lots]
    return summary


def journal_lot_to_order_source(lot: Any) -> dict[str, Any]:
    return {
        "trade_id": lot.trade_id,
        "symbol": lot.symbol,
        "side": lot.side,
        "entry_date": lot.entry_date.isoformat(),
        "exit_date": lot.exit_date.isoformat(),
        "entry_price": lot.entry_price,
        "exit_price": lot.exit_price,
        "shares": lot.shares,
        "fees": lot.fees,
        "pnl": round(lot.pnl, 2),
        "r_multiple": round(lot.r_multiple, 4) if lot.r_multiple is not None else None,
        "notes": lot.notes,
    }


def summarize_workflow(path: Path | None, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return empty_report_summary(path)
    steps = [item for item in payload.get("steps", []) if isinstance(item, dict)]
    return {
        "path": str(path) if path else None,
        "status": payload.get("status") or "unknown",
        "generated_at": payload.get("generated_at"),
        "steps": steps,
        "failed_steps": [item for item in steps if item.get("status") not in {"ok", "skipped"}],
        "skipped_steps": [item for item in steps if item.get("status") == "skipped"],
    }


def summarize_workflow_history(reports: list[tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    history = []
    for path, payload in reports:
        steps = [item for item in payload.get("steps", []) if isinstance(item, dict)]
        history.append(
            {
                "path": str(path),
                "generated_at": payload.get("generated_at"),
                "status": payload.get("status") or "unknown",
                "step_count": len(steps),
                "failed_count": sum(1 for item in steps if item.get("status") not in {"ok", "skipped"}),
                "skipped_count": sum(1 for item in steps if item.get("status") == "skipped"),
                "summary": workflow_step_summary(steps),
            }
        )
    return history


def workflow_step_summary(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "no steps"
    parts = []
    for item in steps[:5]:
        name = str(item.get("name") or "step")
        status = str(item.get("status") or "unknown")
        parts.append(f"{name}:{status}")
    if len(steps) > 5:
        parts.append(f"+{len(steps) - 5}")
    return ", ".join(parts)


def summarize_signals(path: Path | None, payload: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    if payload is None:
        return empty_report_summary(path) | {"watchlist": [], "freshness": signal_freshness([], now=now, report_loaded=False)}
    watchlist = [item for item in payload.get("watchlist", []) if isinstance(item, dict)]
    return {
        "path": str(path) if path else None,
        "status": "loaded",
        "generated_at": payload.get("generated_at"),
        "market_regime": payload.get("market_regime") or {},
        "context_regimes": payload.get("context_regimes") or {},
        "watchlist_count": payload.get("watchlist_count", len(watchlist)),
        "watchlist": watchlist,
        "portfolio_guard": payload.get("portfolio_guard") or {},
        "risk_throttle": payload.get("risk_throttle") or {},
        "history_sources": payload.get("history_sources") or {},
        "data_quality": payload.get("data_quality") or {},
        "watchlist_concentration": payload.get("watchlist_concentration") or {},
        "freshness": signal_freshness(watchlist, now=now, report_loaded=True),
    }


def summarize_triggers(path: Path | None, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return empty_report_summary(path) | {"evaluations": [], "status_counts": {}}
    evaluations = [item for item in payload.get("evaluations", []) if isinstance(item, dict)]
    return {
        "path": str(path) if path else None,
        "status": "loaded",
        "generated_at": payload.get("generated_at"),
        "observation_only": bool(payload.get("observation_only")),
        "watchlist_count": payload.get("watchlist_count", len(evaluations)),
        "triggered_count": payload.get("triggered_count", 0),
        "actionable_triggered_count": payload.get("actionable_triggered_count", 0),
        "blocked_triggered_count": payload.get("blocked_triggered_count", 0),
        "history_sources": payload.get("history_sources") or {},
        "market_regime": payload.get("market_regime") or {},
        "evaluations": evaluations,
        "status_counts": count_by_key(evaluations, "status"),
        "reason_counts": count_untriggered_reasons(evaluations),
    }


def summarize_watchlist_market(signals: dict[str, Any], triggers: dict[str, Any]) -> dict[str, Any]:
    evaluations_by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in triggers.get("evaluations", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    rows = []
    with_market_data = 0
    triggered_count = 0
    for index, signal in enumerate(signals.get("watchlist", []) or [], start=1):
        symbol = str(signal.get("symbol") or "").upper()
        evaluation = evaluations_by_symbol.get(symbol, {})
        weekly_gex = ((signal.get("options_context") or {}).get("weekly_gex") or {})
        signal_close = optional_float(signal.get("close"))
        latest_close = optional_float(evaluation.get("last_close"))
        if latest_close is not None:
            with_market_data += 1
        if evaluation.get("triggered"):
            triggered_count += 1
        trigger_reference = optional_float(evaluation.get("trigger_price_reference"))
        stop_reference = optional_float(evaluation.get("stop_reference"))
        rows.append(
            {
                "rank": index,
                "symbol": symbol,
                "planned_entry_date": signal.get("planned_entry_date"),
                "signal_close": signal_close,
                "latest_close": latest_close,
                "intraday_change_pct": relative_change(latest_close, signal_close),
                "running_vwap": optional_float(evaluation.get("running_vwap")),
                "trigger_price_reference": trigger_reference,
                "distance_to_trigger_pct": relative_distance(latest_close, trigger_reference),
                "stop_reference": stop_reference,
                "distance_to_stop_pct": relative_distance(latest_close, stop_reference),
                "last_bar_time_et": evaluation.get("last_bar_time_et"),
                "status": evaluation.get("status") or "no_trigger_scan",
                "triggered": bool(evaluation.get("triggered")),
                "action": evaluation.get("action"),
                "reason": evaluation.get("reason"),
                "score": signal.get("total_score"),
                "pullback_pct": signal.get("pullback_pct"),
                "weekly_gex_regime": weekly_gex.get("regime"),
                "weekly_net_gex": optional_float(weekly_gex.get("net_gex")),
                "weekly_call_wall": optional_float(weekly_gex.get("call_wall")),
                "weekly_put_wall": optional_float(weekly_gex.get("put_wall")),
            }
        )
    return {
        "rows": rows,
        "watchlist_count": len(rows),
        "with_market_data": with_market_data,
        "triggered_count": triggered_count,
        "latest_trigger_report": triggers.get("path"),
        "latest_trigger_generated_at": triggers.get("generated_at"),
    }


def summarize_position_monitor(path: Path | None, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return empty_report_summary(path) | {"evaluations": [], "evaluations_by_symbol": {}}
    evaluations = [item for item in payload.get("evaluations", []) if isinstance(item, dict)]
    return {
        "path": str(path) if path else None,
        "status": "loaded",
        "generated_at": payload.get("generated_at"),
        "position_count": payload.get("position_count", len(evaluations)),
        "attention_count": payload.get("attention_count", 0),
        "history_sources": payload.get("history_sources") or {},
        "evaluations": evaluations,
        "evaluations_by_symbol": {
            str(item.get("symbol") or "").upper(): item
            for item in evaluations
            if item.get("symbol")
        },
    }


def summarize_paper_update(path: Path | None, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return empty_report_summary(path)
    return {
        "path": str(path) if path else None,
        "status": "loaded",
        "generated_at": payload.get("generated_at"),
        "new_count": len(payload.get("new_positions") or []),
        "duplicate_count": len(payload.get("skipped_duplicates") or []),
        "invalid_count": len(payload.get("skipped_invalid") or []),
        "source_triggers_report": payload.get("source_triggers_report"),
    }


def summarize_recent_orders(paper_positions: dict[str, Any], paper_journal: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
    orders = []
    for position in paper_positions.get("open_positions") or []:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            continue
        order_time = order_time_from_position(position)
        orders.append(
            {
                "time": order_time,
                "symbol": symbol,
                "side": "BUY",
                "action": "paper_entry",
                "status": "open",
                "quantity": as_int(position.get("shares")),
                "price": as_float(position.get("entry_price")),
                "pnl": None,
                "source": "paper_positions",
                "sort_key": order_sort_key(order_time),
            }
        )

    for lot in paper_journal.get("lots") or []:
        symbol = str(lot.get("symbol") or "").upper()
        if not symbol:
            continue
        notes = str(lot.get("notes") or "")
        order_time = report_time_from_text(notes) or exit_time_from_lot(lot)
        orders.append(
            {
                "time": order_time,
                "symbol": symbol,
                "side": "SELL",
                "action": exit_action_from_notes(notes),
                "status": "closed",
                "quantity": as_int(lot.get("shares")),
                "price": as_float(lot.get("exit_price")),
                "pnl": optional_float(lot.get("pnl")),
                "source": "paper_journal",
                "sort_key": order_sort_key(order_time),
            }
        )

    orders = sorted(orders, key=lambda item: item.get("sort_key", ""), reverse=True)
    for order in orders:
        order.pop("sort_key", None)
    return {
        "orders": orders[:limit],
        "order_count": len(orders),
    }


def empty_report_summary(path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path else None,
        "status": "missing",
        "generated_at": None,
    }


def market_status(now: datetime) -> dict[str, Any]:
    now_et = now.astimezone(EASTERN)
    is_weekday = now_et.weekday() < 5
    in_window = is_weekday and MARKET_START_ET <= now_et.time() <= MARKET_END_ET
    return {
        "now_et": now_et,
        "now_shanghai": now.astimezone(SHANGHAI),
        "in_regular_window": in_window,
        "label": "常规盘监控中" if in_window else "常规盘外",
        "window": "09:31-15:55 ET",
    }


def signal_freshness(watchlist: list[dict[str, Any]], *, now: datetime, report_loaded: bool) -> dict[str, Any]:
    today_et = now.astimezone(EASTERN).date()
    planned_dates = [
        parsed
        for parsed in (parse_date(item.get("planned_entry_date")) for item in watchlist)
        if parsed is not None
    ]
    planned_counts = count_dates(planned_dates)
    if not report_loaded:
        status = "missing"
        label = "无 signals"
        tone = "bad"
    elif not planned_dates:
        status = "unknown"
        label = "无计划日期"
        tone = "warn"
    elif today_et in planned_dates:
        status = "current"
        label = "今日有效"
        tone = "good"
    elif max(planned_dates) < today_et:
        status = "expired"
        label = "已过期"
        tone = "bad"
    elif min(planned_dates) > today_et:
        status = "future"
        label = "下一交易日"
        tone = "info"
    else:
        status = "mixed"
        label = "混合日期"
        tone = "warn"
    return {
        "today_et": today_et.isoformat(),
        "status": status,
        "label": label,
        "tone": tone,
        "planned_dates": planned_counts,
        "primary_planned_date": max(planned_counts, key=planned_counts.get) if planned_counts else None,
    }


def render_dashboard_html(model: dict[str, Any], *, refresh_seconds: int) -> str:
    generated_at = model["generated_at"]
    market = model["market"]
    workflow = model["workflow"]
    signals = model["signals"]
    triggers = model["triggers"]
    paper_positions = model["paper_positions"]
    position_monitor = model["paper_position_monitor"]
    paper_journal = model["paper_journal"]
    paper_update = model["paper_update"]
    signal_fresh = signals.get("freshness") or {}

    workflow_tone = tone_for_status(str(workflow.get("status")))
    trigger_tone = "good" if int(triggers.get("triggered_count") or 0) == 0 else "warn"
    attention_tone = "good" if int(position_monitor.get("attention_count") or 0) == 0 else "bad"
    paper_tone = "muted" if paper_positions.get("status") == "missing" else "good"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{int(refresh_seconds)}">
  <title>Liubang Monitor</title>
  <style>
    :root {{
      --bg: #f7f7f4;
      --panel: #ffffff;
      --panel-2: #fbfbf9;
      --text: #202124;
      --muted: #676c73;
      --line: #d9ded8;
      --teal: #0f766e;
      --blue: #2563eb;
      --amber: #b45309;
      --red: #b91c1c;
      --green: #15803d;
      --shadow: 0 1px 2px rgba(20, 24, 28, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .page {{
      width: min(1440px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 20px 0 28px;
    }}
    .topbar {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      font-weight: 700;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0;
      font-size: 16px;
      line-height: 1.25;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 12px;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      height: 32px;
      padding: 0 12px;
      border-radius: 6px;
      cursor: pointer;
      font: inherit;
    }}
    button:hover {{ border-color: #9ca3af; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .pill.good {{ color: var(--green); border-color: #bbf7d0; background: #f0fdf4; }}
    .pill.warn {{ color: var(--amber); border-color: #fde68a; background: #fffbeb; }}
    .pill.bad {{ color: var(--red); border-color: #fecaca; background: #fef2f2; }}
    .pill.info {{ color: var(--blue); border-color: #bfdbfe; background: #eff6ff; }}
    .pill.muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    .metrics {{
      grid-template-columns: repeat(5, minmax(0, 1fr));
      margin-bottom: 12px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: var(--shadow);
      min-height: 102px;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 26px;
      font-weight: 700;
      line-height: 1.1;
      letter-spacing: 0;
      word-break: break-word;
    }}
    .metric-meta {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .columns {{
      grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.75fr);
      align-items: start;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 14px 10px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-2);
    }}
    .panel-body {{ padding: 14px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}
    th, td {{
      text-align: left;
      padding: 9px 8px;
      border-bottom: 1px solid #ecefec;
      vertical-align: middle;
      white-space: nowrap;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      background: #fbfbf9;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    .empty {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      color: var(--muted);
      background: #fcfcfa;
    }}
    .stack {{ display: grid; gap: 12px; }}
    .kv {{
      display: grid;
      grid-template-columns: minmax(128px, 0.44fr) minmax(0, 1fr);
      gap: 8px 12px;
      font-size: 13px;
    }}
    .kv div:nth-child(odd) {{ color: var(--muted); }}
    .status-bars {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .command-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .command {{
      min-height: 72px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fcfcfa;
    }}
    .command-title {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .command code {{
      display: block;
      white-space: normal;
      overflow-wrap: anywhere;
    }}
    .footer {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .metrics, .columns, .command-grid {{ grid-template-columns: 1fr; }}
      .topbar {{ flex-direction: column; }}
      .actions {{ justify-content: flex-start; }}
      .page {{ width: min(100vw - 20px, 1440px); padding-top: 12px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <div class="topbar">
      <div>
        <h1>Liubang Monitor</h1>
        <div class="subtle">生成时间：{h(format_dt_pair(generated_at))}</div>
      </div>
      <div class="actions">
        <span class="pill {'good' if market['in_regular_window'] else 'muted'}">{h(market['label'])}</span>
        <span class="pill info">自动刷新 {int(refresh_seconds)}s</span>
        <button onclick="location.reload()">刷新</button>
      </div>
    </div>

    <section class="grid metrics">
      {metric_card("运行状态", workflow.get("status", "missing"), latest_time_meta(workflow), workflow_tone)}
      {metric_card("Signals", signal_fresh.get("label", "n/a"), "planned=" + str(signal_fresh.get("primary_planned_date") or "n/a"), str(signal_fresh.get("tone") or "muted"))}
      {metric_card("触发", f"{triggers.get('triggered_count', 0)} / {triggers.get('watchlist_count', 0)}", "actionable=" + str(triggers.get("actionable_triggered_count", 0)), trigger_tone)}
      {metric_card("Paper 持仓", str(paper_positions.get("open_count", 0)), money_meta("风险", paper_positions.get("total_initial_risk")), paper_tone)}
      {metric_card("持仓提醒", str(position_monitor.get("attention_count", 0)), latest_time_meta(position_monitor), attention_tone)}
    </section>

      {render_command_panel(model)}

    <section class="grid columns">
      <div class="stack">
        {render_watchlist_market_panel(model.get("watchlist_market") or {})}
        {render_trigger_panel(triggers)}
        {render_watchlist_panel(signals)}
      </div>
      <div class="stack">
        {render_market_panel(model)}
        {render_recent_orders_panel(model.get("recent_orders") or {})}
        {render_paper_positions_panel(paper_positions, position_monitor)}
        {render_paper_journal_panel(paper_journal, paper_update)}
      </div>
    </section>
    <section style="margin-top:12px">
      {render_workflow_panel(workflow, model.get("workflow_history") or [])}
    </section>
    <div class="footer">HTML 文件：data/dashboard/liubang_dashboard.html</div>
  </main>
</body>
</html>
"""


def render_market_panel(model: dict[str, Any]) -> str:
    market = model["market"]
    signals = model["signals"]
    guard = signals.get("portfolio_guard") or {}
    risk_throttle = signals.get("risk_throttle") or {}
    regime = signals.get("market_regime") or {}
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>市场和组合</h2>
          <span class="pill {'good' if market['in_regular_window'] else 'muted'}">{h(market['window'])}</span>
        </div>
        <div class="panel-body">
          <div class="kv">
            <div>当前 ET</div><div>{h(format_dt(market['now_et']))}</div>
            <div>当前北京时间</div><div>{h(format_dt(market['now_shanghai']))}</div>
            <div>Regime</div><div>{h(regime.get('regime', 'n/a'))} <span class="subtle">{h(regime.get('date', ''))}</span></div>
            <div>仓位槽位</div><div>{h(guard.get('available_slots', 'n/a'))} / {h(guard.get('max_positions', 'n/a'))}</div>
            <div>允许新开</div><div>{yes_no(guard.get('allow_new_entries'))}</div>
            <div>风险熔断</div><div>{h(risk_throttle.get('status', 'n/a'))} {h(','.join(risk_throttle.get('flags') or []))}</div>
          </div>
        </div>
      </section>
    """


def render_command_panel(model: dict[str, Any]) -> str:
    market = model["market"]
    freshness = (model["signals"].get("freshness") or {}).get("status")
    if freshness in {"missing", "expired", "unknown"}:
        primary = ("刷新信号", "sh scripts/liubang_live.sh signals")
    elif market.get("in_regular_window"):
        primary = ("盘中监控", "sh scripts/liubang_live.sh loop")
    else:
        primary = ("盘外自检", "sh scripts/liubang_live.sh loop --max-ticks 1")
    commands = [
        primary,
        ("打开面板", "sh scripts/liubang_live.sh dashboard-open"),
        ("自检一轮", "sh scripts/liubang_live.sh loop --max-ticks 1"),
    ]
    if primary[1] != "sh scripts/liubang_live.sh signals":
        commands.append(("刷新信号", "sh scripts/liubang_live.sh signals"))
    else:
        commands.append(("生成面板", "sh scripts/liubang_live.sh dashboard"))
    cards = "\n".join(
        f"""
        <div class="command">
          <div class="command-title">{h(title)}</div>
          <code>{h(command)}</code>
        </div>
        """
        for title, command in commands
    )
    return f"""
      <section class="panel" style="margin-bottom:12px">
        <div class="panel-head">
          <h2>当前操作</h2>
          <span class="pill info">shell</span>
        </div>
        <div class="panel-body">
          <div class="command-grid">{cards}</div>
        </div>
      </section>
    """


def render_workflow_panel(workflow: dict[str, Any], history: list[dict[str, Any]]) -> str:
    steps = workflow.get("steps") or []
    if not steps:
        body = '<div class="empty">还没有 workflow 报告。</div>'
    else:
        rows = "\n".join(
            f"""
            <tr>
              <td>{h(item.get('name'))}</td>
              <td>{status_pill(item.get('status'))}</td>
              <td>{h(item.get('reason') or compact_command(item.get('command') or []))}</td>
            </tr>
            """
            for item in steps
        )
        body = f"""
          <div class="table-wrap">
            <table>
              <thead><tr><th>步骤</th><th>状态</th><th>说明</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        """
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>Workflow</h2>
          <span class="pill {tone_for_status(str(workflow.get('status')))}">{h(workflow.get('status', 'missing'))}</span>
        </div>
        <div class="panel-body">
          <div class="subtle">最新报告：{h(workflow.get('path') or 'n/a')} · {h(format_dt_pair(parse_dt(workflow.get('generated_at'))))}</div>
          {body}
          {render_workflow_history_panel(history)}
        </div>
      </section>
    """


def render_workflow_history_panel(history: list[dict[str, Any]]) -> str:
    if not history:
        return '<div class="empty" style="margin-top:12px">还没有 workflow 历史。</div>'
    rows = "\n".join(
        f"""
        <tr>
          <td>{h(format_dt_pair(parse_dt(item.get('generated_at'))))}</td>
          <td>{status_pill(item.get('status'))}</td>
          <td>{h(item.get('step_count'))}</td>
          <td>{h(item.get('skipped_count'))}</td>
          <td>{h(item.get('summary'))}</td>
        </tr>
        """
        for item in history
    )
    return f"""
      <div style="margin-top:12px">
        <div class="subtle" style="margin-bottom:6px">最近 10 次 workflow/tick</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>时间</th><th>状态</th><th>步骤</th><th>跳过</th><th>摘要</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
    """


def render_trigger_panel(triggers: dict[str, Any]) -> str:
    evaluations = triggers.get("evaluations") or []
    status_counts = triggers.get("status_counts") or {}
    reason_counts = triggers.get("reason_counts") or {}
    bars = "".join(
        f'<span class="pill {tone_for_trigger_status(key)}">{h(key)} {count}</span>'
        for key, count in sorted(status_counts.items())
    ) or '<span class="pill muted">无触发扫描数据</span>'
    reason_bars = "".join(
        f'<span class="pill muted">{h(key)} {count}</span>'
        for key, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    ) or '<span class="pill good">无未触发原因</span>'
    rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{status_pill(item.get('status'))}</td>
          <td>{h(item.get('last_close'))}</td>
          <td>{h(item.get('trigger_price_reference'))}</td>
          <td>{h(item.get('stop_reference'))}</td>
          <td>{h(item.get('action') or item.get('reason'))}</td>
        </tr>
        """
        for item in evaluations[:16]
    )
    table = (
        f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>标的</th><th>状态</th><th>最新价</th><th>触发参考</th><th>止损</th><th>动作/原因</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
        if rows
        else '<div class="empty">还没有 trigger 报告。</div>'
    )
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>盘中触发</h2>
          <span class="pill {'info' if triggers.get('observation_only') else 'muted'}">observation={str(bool(triggers.get('observation_only'))).lower()}</span>
        </div>
        <div class="panel-body">
          <div class="status-bars">{bars}</div>
          <div class="status-bars" style="margin-top:8px">{reason_bars}</div>
          <div class="subtle">最新报告：{h(triggers.get('path') or 'n/a')} · {h(format_dt_pair(parse_dt(triggers.get('generated_at'))))}</div>
          {table}
        </div>
      </section>
    """


def render_watchlist_market_panel(market: dict[str, Any]) -> str:
    rows = market.get("rows") or []
    table_rows = "\n".join(
        f"""
        <tr>
          <td>{h(item.get('rank'))}</td>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{status_pill(item.get('status'))}</td>
          <td>{h(format_price(item.get('signal_close')))}</td>
          <td>{h(format_price(item.get('latest_close')))}</td>
          <td>{change_pill(item.get('intraday_change_pct'))}</td>
          <td>{h(format_price(item.get('running_vwap')))}</td>
          <td>{h(format_price(item.get('trigger_price_reference')))}</td>
          <td>{h(format_signed_pct(item.get('distance_to_trigger_pct')))}</td>
          <td>{h(format_signed_pct(item.get('distance_to_stop_pct')))}</td>
          <td>{gex_pill(item.get('weekly_gex_regime'))}</td>
          <td>{h(format_compact_money(item.get('weekly_net_gex'), signed=True))}</td>
          <td>{h(format_price(item.get('weekly_call_wall')))}</td>
          <td>{h(format_price(item.get('weekly_put_wall')))}</td>
          <td>{h(format_time_et(item.get('last_bar_time_et')))}</td>
        </tr>
        """
        for item in rows[:16]
    )
    body = (
        f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>#</th><th>标的</th><th>状态</th><th>信号收盘</th><th>最新 5m</th><th>涨跌</th><th>VWAP</th><th>触发参考</th><th>距触发</th><th>距止损</th><th>GEX</th><th>净 GEX</th><th>Call Wall</th><th>Put Wall</th><th>最后 K</th></tr></thead>
            <tbody>{table_rows}</tbody>
          </table>
        </div>
        """
        if table_rows
        else '<div class="empty">还没有 watchlist 或 trigger 行情数据。</div>'
    )
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>Watchlist 今日行情</h2>
          <span class="pill info">{h(market.get('with_market_data', 0))} / {h(market.get('watchlist_count', 0))} 有 5m 数据</span>
        </div>
        <div class="panel-body">
          <div class="subtle">来源：{h(market.get('latest_trigger_report') or 'n/a')} · {h(format_dt_pair(parse_dt(market.get('latest_trigger_generated_at'))))}</div>
          {body}
        </div>
      </section>
    """


def render_watchlist_panel(signals: dict[str, Any]) -> str:
    watchlist = signals.get("watchlist") or []
    freshness = signals.get("freshness") or {}
    rows = "\n".join(
        f"""
        <tr>
          <td>{index}</td>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{h(item.get('total_score'))}</td>
          <td>{format_pct(item.get('pullback_pct'))}</td>
          <td>{h(item.get('close'))}</td>
          <td>{h((item.get('trade_plan') or {}).get('suggested_shares'))}</td>
          <td>{h(item.get('source'))}</td>
        </tr>
        """
        for index, item in enumerate(watchlist[:12], start=1)
    )
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>Watchlist</h2>
          <span class="pill {h(freshness.get('tone') or 'info')}">{h(freshness.get('label') or signals.get('watchlist_count', 0))}</span>
        </div>
        <div class="panel-body">
          <div class="subtle">最新报告：{h(signals.get('path') or 'n/a')} · {h(format_dt_pair(parse_dt(signals.get('generated_at'))))} · planned={h(freshness.get('primary_planned_date'))}</div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>#</th><th>标的</th><th>分数</th><th>回撤</th><th>收盘</th><th>建议股数</th><th>来源</th></tr></thead>
              <tbody>{rows or '<tr><td colspan="7">暂无候选。</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>
    """


def render_paper_positions_panel(paper: dict[str, Any], monitor: dict[str, Any]) -> str:
    positions = paper.get("open_positions") or []
    evaluations_by_symbol = monitor.get("evaluations_by_symbol") or {}
    if not positions:
        extra = "文件不存在。" if paper.get("status") == "missing" else "暂无开放纸面持仓。"
        body = f'<div class="empty">{h(extra)}</div>'
    else:
        rows = "\n".join(
            render_paper_position_row(item, evaluations_by_symbol.get(str(item.get("symbol") or "").upper()))
            for item in positions
        )
        body = f"""
          <div class="table-wrap">
            <table>
              <thead><tr><th>标的</th><th>状态</th><th>入场日</th><th>入场时间</th><th>剩余</th><th>入场</th><th>止损</th><th>止损模式</th><th>Trail Ref</th><th>目标</th><th>最新价</th><th>浮动 R</th><th>距止损</th><th>距目标</th><th>动作</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        """
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>Paper 持仓</h2>
          <span class="pill {('good' if positions else 'muted')}">{len(positions)} open</span>
        </div>
        <div class="panel-body">
          <div class="kv" style="margin-bottom:12px">
            <div>持仓文件</div><div class="mono">{h(paper.get('path'))}</div>
            <div>名义市值</div><div>{h(format_money(paper.get('total_position_value')))}</div>
            <div>初始风险</div><div>{h(format_money(paper.get('total_initial_risk')))}</div>
            <div>监控报告</div><div class="mono">{h(monitor.get('path') or 'n/a')}</div>
          </div>
          {body}
        </div>
      </section>
    """


def render_recent_orders_panel(recent_orders: dict[str, Any]) -> str:
    orders = recent_orders.get("orders") or []
    rows = "\n".join(
        f"""
        <tr>
          <td>{h(format_order_time(item.get('time')))}</td>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{order_side_pill(item.get('side'))}</td>
          <td>{h(item.get('action'))}</td>
          <td>{h(item.get('quantity'))}</td>
          <td>{h(format_price(item.get('price')))}</td>
          <td>{h(format_money(item.get('pnl')) if item.get('pnl') is not None else 'n/a')}</td>
          <td>{status_pill(item.get('status'))}</td>
        </tr>
        """
        for item in orders
    )
    body = (
        f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>时间</th><th>标的</th><th>方向</th><th>类型</th><th>数量</th><th>价格</th><th>PnL</th><th>状态</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
        if rows
        else '<div class="empty">暂无 paper 订单。</div>'
    )
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>最近订单</h2>
          <span class="pill info">{h(recent_orders.get('order_count', 0))}</span>
        </div>
        <div class="panel-body">
          {body}
        </div>
      </section>
    """


def render_paper_position_row(position: dict[str, Any], evaluation: dict[str, Any] | None) -> str:
    symbol = str(position.get("symbol") or "").upper()
    status = evaluation.get("status") if evaluation else "open"
    action = evaluation.get("action") if evaluation else "waiting_monitor"
    latest_close = evaluation.get("latest_close") if evaluation else None
    remaining = as_int(position.get("remaining_shares", position.get("shares")))
    shares = as_int(position.get("shares"))
    live = position_live_metrics(position, latest_close)
    stop_mode = position_stop_mode(position, evaluation)
    trailing_stop = evaluation.get("trailing_stop_reference") if evaluation else None
    return f"""
      <tr>
        <td class="mono">{h(symbol)}</td>
        <td>{status_pill(status)}</td>
        <td>{h(position.get('entry_date'))}</td>
        <td>{entry_time_cell(position.get('entry_time_et'))}</td>
        <td>{remaining} / {shares}</td>
        <td>{h(position.get('entry_price'))}</td>
        <td>{h(position.get('current_stop_price') or position.get('initial_stop_price'))}</td>
        <td>{stop_mode}</td>
        <td>{h(format_price(trailing_stop))}</td>
        <td>{h(position.get('target_price'))}</td>
        <td>{h(latest_close)}</td>
        <td>{h(live.get('floating_r'))}</td>
        <td>{h(live.get('distance_to_stop_pct'))}</td>
        <td>{h(live.get('distance_to_target_pct'))}</td>
        <td>{h(action)}</td>
      </tr>
    """


def render_paper_journal_panel(journal: dict[str, Any], paper_update: dict[str, Any]) -> str:
    trades = journal.get("trades") or []
    rows = "\n".join(
        f"""
        <tr>
          <td class="mono">{h(item.get('symbol'))}</td>
          <td>{h(item.get('exit_date'))}</td>
          <td>{h(item.get('shares'))}</td>
          <td>{h(format_money(item.get('pnl')))}</td>
          <td>{h(item.get('r_multiple'))}</td>
        </tr>
        """
        for item in trades[-8:][::-1]
    )
    journal_body = (
        f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>标的</th><th>退出日</th><th>股数</th><th>PnL</th><th>R</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
        if rows
        else '<div class="empty">暂无已关闭 paper 交易。</div>'
    )
    return f"""
      <section class="panel">
        <div class="panel-head">
          <h2>Paper 日志</h2>
          <span class="pill {('good' if journal.get('trade_count') else 'muted')}">{h(journal.get('trade_count', 0))} trades</span>
        </div>
        <div class="panel-body">
          <div class="kv" style="margin-bottom:12px">
            <div>日志文件</div><div class="mono">{h(journal.get('path'))}</div>
            <div>已实现 PnL</div><div>{h(format_money(journal.get('realized_pnl')))}</div>
            <div>胜率</div><div>{format_pct(journal.get('win_rate'))}</div>
            <div>最新记录</div><div>{h(paper_update.get('new_count', 0))} new · {h(format_dt_pair(parse_dt(paper_update.get('generated_at'))))}</div>
          </div>
          {journal_body}
        </div>
      </section>
    """


def metric_card(label: str, value: Any, meta: str, tone: str) -> str:
    return f"""
      <div class="metric">
        <div class="metric-label">{h(label)}</div>
        <div class="metric-value">{h(value)}</div>
        <div class="metric-meta"><span class="pill {h(tone)}">{h(meta)}</span></div>
      </div>
    """


def status_pill(status: Any) -> str:
    raw = str(status or "n/a")
    return f'<span class="pill {tone_for_status(raw)}">{h(raw)}</span>'


def order_side_pill(side: Any) -> str:
    raw = str(side or "n/a").upper()
    tone = "good" if raw == "BUY" else "warn" if raw == "SELL" else "muted"
    return f'<span class="pill {tone}">{h(raw)}</span>'


def tone_for_status(status: str) -> str:
    normalized = status.lower()
    if normalized in {"ok", "loaded", "hold", "open", "triggered"}:
        return "good"
    if normalized in {"skipped", "missing", "waiting", "waiting_for_market_data", "waiting_after_first_candle", "pending_entry_date", "no_data"}:
        return "muted"
    if normalized in {"take_partial", "exit"}:
        return "warn"
    if normalized in {"failed", "error", "invalidated", "invalid_signal"}:
        return "bad"
    return "info"


def tone_for_trigger_status(status: str) -> str:
    if status == "triggered":
        return "warn"
    if status in {"waiting", "waiting_for_market_data", "waiting_after_first_candle", "pending_entry_date"}:
        return "muted"
    if status in {"invalidated", "expired", "expired_no_data"}:
        return "bad"
    return "info"


def latest_time_meta(report: dict[str, Any]) -> str:
    parsed = parse_dt(report.get("generated_at"))
    if parsed is None:
        return "no report"
    return parsed.astimezone(SHANGHAI).strftime("%H:%M:%S CST")


def money_meta(label: str, value: Any) -> str:
    return f"{label} {format_money(value)}"


def format_dt_pair(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.astimezone(SHANGHAI):%Y-%m-%d %H:%M:%S CST} / {value.astimezone(EASTERN):%H:%M:%S ET}"


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    suffix = "ET" if value.tzinfo == EASTERN else "CST"
    return value.strftime(f"%Y-%m-%d %H:%M:%S {suffix}")


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


def format_compact_money(value: Any, *, signed: bool = False) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    sign = ""
    if signed:
        sign = "+" if number > 0 else "-" if number < 0 else ""
    absolute = abs(number)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if absolute >= divisor:
            return f"{sign}${absolute / divisor:.2f}{suffix}"
    return f"{sign}${absolute:.0f}"


def format_price(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}"


def format_time_et(value: Any) -> str:
    parsed = parse_dt(value)
    if parsed is None:
        return "n/a"
    return parsed.astimezone(EASTERN).strftime("%H:%M")


def format_order_time(value: Any) -> str:
    parsed = parse_dt(value)
    if parsed is None:
        return str(value or "n/a")
    return parsed.astimezone(EASTERN).strftime("%m-%d %H:%M ET")


def change_pill(value: Any) -> str:
    number = optional_float(value)
    if number is None:
        return '<span class="pill muted">n/a</span>'
    tone = "good" if number > 0 else "bad" if number < 0 else "muted"
    return f'<span class="pill {tone}">{h(format_signed_pct(number))}</span>'


def entry_time_cell(value: Any) -> str:
    parsed = parse_dt(value)
    if parsed is None:
        return '<span class="pill warn">missing</span>'
    return h(parsed.astimezone(EASTERN).strftime("%H:%M"))


def gex_pill(value: Any) -> str:
    regime = str(value or "").lower()
    labels = {"positive": "正", "negative": "负", "neutral": "中性"}
    tones = {"positive": "good", "negative": "bad", "neutral": "muted"}
    if regime not in labels:
        return '<span class="pill muted">n/a</span>'
    return f'<span class="pill {tones[regime]}">{labels[regime]}</span>'


def yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "n/a"


def count_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def count_untriggered_reasons(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("triggered"):
            continue
        reason = str(row.get("reason") or row.get("status") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def count_dates(values: list[date]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value.isoformat()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def compact_command(command: list[Any]) -> str:
    if not command:
        return ""
    text = " ".join(str(item) for item in command)
    return text if len(text) <= 120 else text[:117] + "..."


def position_initial_risk(position: dict[str, Any]) -> float:
    entry = as_float(position.get("entry_price"))
    stop = as_float(position.get("initial_stop_price", position.get("current_stop_price")))
    shares = as_int(position.get("remaining_shares", position.get("shares")))
    return max(0.0, entry - stop) * shares


def position_live_metrics(position: dict[str, Any], latest_close: Any) -> dict[str, str]:
    latest = optional_float(latest_close)
    if latest is None or latest <= 0:
        return {
            "floating_r": "n/a",
            "distance_to_stop_pct": "n/a",
            "distance_to_target_pct": "n/a",
        }
    entry = as_float(position.get("entry_price"))
    initial_stop = as_float(position.get("initial_stop_price", position.get("current_stop_price")))
    current_stop = as_float(position.get("current_stop_price", initial_stop))
    target = as_float(position.get("target_price"))
    risk_per_share = entry - initial_stop
    floating_r = (latest - entry) / risk_per_share if risk_per_share > 0 else None
    distance_to_stop = (latest - current_stop) / latest if current_stop > 0 else None
    distance_to_target = (target - latest) / latest if target > 0 else None
    return {
        "floating_r": f"{floating_r:+.2f}R" if floating_r is not None else "n/a",
        "distance_to_stop_pct": format_signed_pct(distance_to_stop),
        "distance_to_target_pct": format_signed_pct(distance_to_target),
    }


def position_stop_mode(position: dict[str, Any], evaluation: dict[str, Any] | None) -> str:
    if not position.get("target_hit"):
        return '<span class="pill muted">initial</span>'
    entry = as_float(position.get("entry_price"))
    current_stop = as_float(position.get("current_stop_price", position.get("initial_stop_price")))
    trailing_stop = optional_float((evaluation or {}).get("trailing_stop_reference"))
    stop_reference = optional_float((evaluation or {}).get("stop_reference"))
    effective_stop = stop_reference if stop_reference is not None else current_stop
    if trailing_stop is not None and trailing_stop >= effective_stop and trailing_stop > entry:
        return '<span class="pill good">trailing</span>'
    if effective_stop > entry:
        return '<span class="pill good">locked</span>'
    return '<span class="pill info">breakeven</span>'


def format_signed_pct(value: float | None) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:+.2f}%"


def order_time_from_position(position: dict[str, Any]) -> str:
    for key in ("entry_time_et", "entry_bar_time_et"):
        parsed = parse_dt(position.get(key))
        if parsed is not None:
            return parsed.isoformat()
    entry_date = parse_date(position.get("entry_date"))
    if entry_date is None:
        return ""
    return datetime.combine(entry_date, time(9, 30), tzinfo=EASTERN).isoformat()


def exit_time_from_lot(lot: dict[str, Any]) -> str:
    exit_date = parse_date(lot.get("exit_date"))
    if exit_date is None:
        return ""
    return datetime.combine(exit_date, time(16, 0), tzinfo=EASTERN).isoformat()


def report_time_from_text(value: str) -> str | None:
    match = re.search(r"_(\d{8}_\d{6}_\d{6})\.json", value)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC).isoformat()


def exit_action_from_notes(notes: str) -> str:
    if "target_1" in notes:
        return "target_1"
    if "breakeven" in notes:
        return "breakeven_stop"
    if "stop" in notes:
        return "stop"
    if "time_exit" in notes:
        return "time_exit"
    return "exit"


def order_sort_key(value: Any) -> str:
    parsed = parse_dt(value)
    return parsed.astimezone(UTC).isoformat() if parsed else ""


def relative_change(current: Any, baseline: Any) -> float | None:
    current_number = optional_float(current)
    baseline_number = optional_float(baseline)
    if current_number is None or baseline_number is None or baseline_number <= 0:
        return None
    return (current_number - baseline_number) / baseline_number


def relative_distance(current: Any, reference: Any) -> float | None:
    current_number = optional_float(current)
    reference_number = optional_float(reference)
    if current_number is None or reference_number is None or current_number <= 0:
        return None
    return (current_number - reference_number) / current_number


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
