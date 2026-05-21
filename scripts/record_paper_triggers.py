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
    parser = argparse.ArgumentParser(description="Record observation-mode trigger events into a local paper positions file.")
    parser.add_argument("--triggers-report", default=None, help="Trigger report JSON. Defaults to latest data/exports/triggers_*.json.")
    parser.add_argument("--paper-positions", default="data/paper_positions.json")
    parser.add_argument("--paper-journal", default="data/paper_trade_journal.csv")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--include-actionable", action="store_true", help="Also paper-record prepare_manual_entry triggers.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        trigger_path = Path(args.triggers_report) if args.triggers_report else latest_trigger_report(Path(args.output_dir))
        trigger_report = json.loads(trigger_path.read_text(encoding="utf-8"))
        positions_path = Path(args.paper_positions)
        current_payload = load_positions_payload(positions_path)
        paper_journal_entries = load_paper_journal_entries(Path(args.paper_journal))
        update = build_paper_update(
            trigger_report,
            existing_payload=current_payload,
            paper_journal_entries=paper_journal_entries,
            source_path=trigger_path,
            include_actionable=args.include_actionable,
        )
        if update["new_positions"]:
            current_payload["positions"].extend(update["new_positions"])
            write_json(positions_path, current_payload)
        report_path = Path(args.output_dir) / f"paper_positions_update_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_json(report_path, update)
    except Exception as exc:
        print(f"record_paper_triggers failed: {exc}", file=sys.stderr)
        return 1

    print("Paper trigger recorder")
    print(f"Trigger report: {trigger_path}")
    print(f"Paper positions: {positions_path}")
    print(f"Paper journal: {args.paper_journal}")
    print(f"New positions: {len(update['new_positions'])}")
    print(f"Skipped duplicates: {len(update['skipped_duplicates'])}")
    print(f"Skipped portfolio full: {len(update['skipped_portfolio_full'])}")
    print(f"Missed triggers: {len(update['missed_triggers'])}")
    print(f"Report: {report_path}")
    return 0


def latest_trigger_report(exports_dir: Path) -> Path:
    reports = sorted(exports_dir.glob("triggers_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No trigger reports found in {exports_dir}")
    return reports[-1]


def load_positions_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"positions": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = payload.get("positions")
    if not isinstance(positions, list):
        raise ValueError(f"Invalid positions payload: {path}")
    return payload


def load_paper_journal_entries(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    entries: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "").strip().upper()
            entry_date = str(row.get("entry_date") or "").strip()
            if symbol and entry_date:
                entries.add((symbol, entry_date))
    return entries


def build_paper_update(
    trigger_report: dict[str, Any],
    *,
    existing_payload: dict[str, Any],
    paper_journal_entries: set[tuple[str, str]] | None = None,
    source_path: Path | None = None,
    include_actionable: bool = False,
) -> dict[str, Any]:
    paper_journal_entries = paper_journal_entries or set()
    open_symbols = {
        str(item.get("symbol") or "").upper()
        for item in existing_payload.get("positions", [])
        if int(item.get("remaining_shares", item.get("shares", 0)) or 0) > 0
    }
    portfolio_guard = paper_portfolio_guard_status(trigger_report.get("portfolio_guard") or {}, len(open_symbols))
    remaining_slots = portfolio_guard.get("remaining_slots")
    new_positions = []
    skipped_duplicates = []
    skipped_portfolio_full = []
    skipped_invalid = []
    missed_triggers = []
    candidate_results = []
    candidates = sorted(
        (
            item
            for item in trigger_report.get("evaluations", [])
            if isinstance(item, dict) and paper_recordable_action(item, include_actionable=include_actionable)
        ),
        key=paper_trigger_sort_key,
    )
    for rank, item in enumerate(candidates, start=1):
        action = item.get("action")
        context = paper_candidate_context(item, rank=rank)
        position = build_paper_position(item, source_path=source_path)
        if position is None:
            skipped = context | {"reason": "missing_position_fields"}
            skipped_invalid.append(skipped)
            missed_triggers.append(missed_trigger_record(skipped))
            candidate_results.append(candidate_result_record(context, selected=False, reason="missing_position_fields"))
            continue
        symbol = position["symbol"]
        if symbol in open_symbols:
            skipped = context | {"symbol": symbol, "reason": "open_paper_position_exists"}
            skipped_duplicates.append(skipped)
            missed_triggers.append(missed_trigger_record(skipped))
            candidate_results.append(candidate_result_record(context, selected=False, reason="open_paper_position_exists"))
            continue
        entry_key = (symbol, str(position.get("entry_date") or ""))
        if entry_key in paper_journal_entries:
            skipped = context | {
                "symbol": symbol,
                "entry_date": entry_key[1],
                "reason": "symbol_already_recorded_for_entry_date",
            }
            skipped_duplicates.append(skipped)
            missed_triggers.append(missed_trigger_record(skipped))
            candidate_results.append(
                candidate_result_record(context, selected=False, reason="symbol_already_recorded_for_entry_date")
            )
            continue
        if remaining_slots is not None and remaining_slots <= 0:
            reason = portfolio_guard.get("block_reason") or "no_opening_slots_available"
            skipped = context | {
                "symbol": symbol,
                "entry_date": entry_key[1],
                "reason": reason,
                "max_positions": portfolio_guard.get("max_positions"),
                "open_positions": len(open_symbols),
            }
            skipped_portfolio_full.append(skipped)
            missed_triggers.append(missed_trigger_record(skipped))
            candidate_results.append(candidate_result_record(context, selected=False, reason=reason))
            continue
        position.update(context)
        position["paper_slot_selected"] = True
        position["paper_fill_status"] = "filled"
        new_positions.append(position)
        candidate_results.append(candidate_result_record(context, selected=True, reason=None))
        open_symbols.add(symbol)
        paper_journal_entries.add(entry_key)
        if remaining_slots is not None:
            remaining_slots -= 1
            portfolio_guard["remaining_slots"] = remaining_slots
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "paper_observation_positions",
        "source_triggers_report": str(source_path) if source_path else None,
        "portfolio_guard": portfolio_guard,
        "new_positions": new_positions,
        "skipped_duplicates": skipped_duplicates,
        "skipped_portfolio_full": skipped_portfolio_full,
        "skipped_invalid": skipped_invalid,
        "missed_triggers": missed_triggers,
        "candidate_results": candidate_results,
    }


def paper_recordable_action(item: dict[str, Any], *, include_actionable: bool) -> bool:
    action = item.get("action")
    return action == "observe_only_no_manual_entry" or (include_actionable and action == "prepare_manual_entry")


def paper_trigger_sort_key(item: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        -as_float(item.get("trigger_rank_score")),
        -as_float(item.get("signal_score")),
        -as_float(item.get("confirmation_margin_pct")),
        str(item.get("symbol") or ""),
    )


def paper_candidate_context(item: dict[str, Any], *, rank: int) -> dict[str, Any]:
    return {
        "symbol": str(item.get("symbol") or "").upper(),
        "entry_date": item.get("planned_entry_date"),
        "last_bar_time_et": item.get("last_bar_time_et"),
        "paper_candidate_rank": rank,
        "trigger_rank_score": item.get("trigger_rank_score"),
        "signal_score": item.get("signal_score"),
        "confirmation_margin_pct": item.get("confirmation_margin_pct"),
    }


def missed_trigger_record(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item) | {
        "paper_fill_status": "triggered_but_not_filled",
        "paper_slot_selected": False,
        "paper_skip_reason": item.get("reason"),
    }


def candidate_result_record(context: dict[str, Any], *, selected: bool, reason: str | None) -> dict[str, Any]:
    return dict(context) | {
        "paper_slot_selected": selected,
        "paper_fill_status": "filled" if selected else "triggered_but_not_filled",
        "paper_skip_reason": reason,
    }


def paper_portfolio_guard_status(raw_guard: dict[str, Any], open_position_count: int) -> dict[str, Any]:
    max_positions = optional_int(raw_guard.get("max_positions"))
    available_slots = optional_int(raw_guard.get("available_slots"))
    block_reason = None
    if raw_guard.get("risk_throttle_allows_new_entries") is False:
        remaining_slots = 0
        block_reason = "risk_throttle_blocks_new_entries"
    elif max_positions is not None:
        remaining_slots = max(0, max_positions - open_position_count)
        if remaining_slots <= 0:
            block_reason = "no_opening_slots_available"
    elif available_slots is not None:
        remaining_slots = max(0, available_slots)
        if remaining_slots <= 0:
            block_reason = "no_opening_slots_available"
    elif raw_guard.get("allow_new_entries") is False:
        remaining_slots = 0
        block_reason = "portfolio_guard_disallows_new_entries"
    else:
        remaining_slots = None
    return {
        "source": raw_guard.get("source"),
        "max_positions": max_positions,
        "signal_available_slots": available_slots,
        "open_paper_positions": open_position_count,
        "remaining_slots": remaining_slots,
        "block_reason": block_reason,
    }


def build_paper_position(item: dict[str, Any], *, source_path: Path | None = None) -> dict[str, Any] | None:
    symbol = str(item.get("symbol") or "").upper()
    plan = item.get("trade_plan") or {}
    entry_price = as_float(plan.get("entry_price_reference", item.get("trigger_price_reference")))
    shares = int(plan.get("suggested_shares") or 0)
    stop = as_float(plan.get("stop_price", item.get("stop_reference")))
    target = as_float(plan.get("first_target_price"))
    entry_date = item.get("planned_entry_date")
    if not symbol or not entry_date or entry_price <= 0 or shares <= 0 or stop <= 0 or target <= 0:
        return None
    position = {
        "symbol": symbol,
        "entry_date": entry_date,
        "entry_price": round(entry_price, 4),
        "shares": shares,
        "remaining_shares": shares,
        "initial_stop_price": round(stop, 4),
        "current_stop_price": round(stop, 4),
        "target_price": round(target, 4),
        "target_hit": False,
        "notes": (
            "paper_observation "
            f"trigger_action={item.get('action')} "
            f"trigger_report={source_path or ''}"
        ).strip(),
    }
    if item.get("last_bar_time_et"):
        position["entry_time_et"] = item.get("last_bar_time_et")
    return position


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
