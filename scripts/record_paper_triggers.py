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
    new_positions = []
    skipped_duplicates = []
    skipped_invalid = []
    for item in trigger_report.get("evaluations", []):
        action = item.get("action")
        if action == "observe_only_no_manual_entry":
            pass
        elif include_actionable and action == "prepare_manual_entry":
            pass
        else:
            continue
        position = build_paper_position(item, source_path=source_path)
        if position is None:
            skipped_invalid.append({"symbol": item.get("symbol"), "reason": "missing_position_fields"})
            continue
        symbol = position["symbol"]
        if symbol in open_symbols:
            skipped_duplicates.append({"symbol": symbol, "reason": "open_paper_position_exists"})
            continue
        entry_key = (symbol, str(position.get("entry_date") or ""))
        if entry_key in paper_journal_entries:
            skipped_duplicates.append(
                {
                    "symbol": symbol,
                    "entry_date": entry_key[1],
                    "reason": "symbol_already_recorded_for_entry_date",
                }
            )
            continue
        new_positions.append(position)
        open_symbols.add(symbol)
        paper_journal_entries.add(entry_key)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "paper_observation_positions",
        "source_triggers_report": str(source_path) if source_path else None,
        "new_positions": new_positions,
        "skipped_duplicates": skipped_duplicates,
        "skipped_invalid": skipped_invalid,
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
