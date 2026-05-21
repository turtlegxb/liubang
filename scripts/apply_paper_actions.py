#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


JOURNAL_FIELDS = [
    "trade_id",
    "symbol",
    "side",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "shares",
    "initial_stop_price",
    "fees",
    "setup",
    "source",
    "notes",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply paper-position monitor actions to paper positions and paper journal.")
    parser.add_argument("--positions-report", default=None, help="Position monitor report JSON. Defaults to latest data/exports/positions_*.json.")
    parser.add_argument("--paper-positions", default="data/paper_positions.json")
    parser.add_argument("--paper-journal", default="data/paper_trade_journal.csv")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--fees", type=float, default=0.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report_path = Path(args.positions_report) if args.positions_report else latest_position_report(Path(args.output_dir))
        positions_path = Path(args.paper_positions)
        journal_path = Path(args.paper_journal)
        positions_payload = load_positions_payload(positions_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        update = build_paper_action_update(
            positions_payload=positions_payload,
            position_report=report,
            source_path=report_path,
            fees=args.fees,
        )
        if update["journal_rows"]:
            append_journal_rows(journal_path, update["journal_rows"])
        if update["changed"]:
            write_json(positions_path, {"positions": update["positions_after"]})
        output_path = Path(args.output_dir) / f"paper_actions_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_json(output_path, update)
    except Exception as exc:
        print(f"apply_paper_actions failed: {exc}", file=sys.stderr)
        return 1

    print("Paper action applier")
    print(f"Position report: {report_path}")
    print(f"Paper positions: {positions_path}")
    print(f"Paper journal: {journal_path}")
    print(f"Applied actions: {len(update['applied_actions'])}")
    print(f"Journal rows: {len(update['journal_rows'])}")
    print(f"Report: {output_path}")
    return 0


def latest_position_report(exports_dir: Path) -> Path:
    reports = sorted(exports_dir.glob("positions_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No position reports found in {exports_dir}")
    return reports[-1]


def load_positions_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Paper positions file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("positions"), list):
        raise ValueError(f"Invalid paper positions payload: {path}")
    return payload


def build_paper_action_update(
    *,
    positions_payload: dict[str, Any],
    position_report: dict[str, Any],
    source_path: Path | None = None,
    fees: float = 0.0,
) -> dict[str, Any]:
    positions = [dict(item) for item in positions_payload.get("positions", []) if isinstance(item, dict)]
    positions_by_symbol = {str(item.get("symbol") or "").upper(): item for item in positions}
    journal_rows = []
    applied_actions = []
    skipped = []

    for evaluation in position_report.get("evaluations", []):
        if not evaluation.get("needs_attention"):
            continue
        symbol = str(evaluation.get("symbol") or "").upper()
        position = positions_by_symbol.get(symbol)
        if not position:
            skipped.append({"symbol": symbol, "reason": "paper_position_not_found"})
            continue
        action = str(evaluation.get("action") or "")
        if action == "sell_half_at_first_target":
            result = apply_partial_target(position, evaluation, fees=fees, source_path=source_path)
        elif action == "raise_trailing_stop":
            result = apply_raise_trailing_stop(position, evaluation)
        elif action in {"exit_remaining_stop", "exit_remaining_breakeven_or_stop", "exit_remaining_time_stop"}:
            result = apply_exit_remaining(position, evaluation, fees=fees, source_path=source_path)
        else:
            skipped.append({"symbol": symbol, "reason": f"unsupported_action:{action}"})
            continue
        if result is None:
            skipped.append({"symbol": symbol, "reason": "stale_or_invalid_action"})
            continue
        if result.get("journal_row"):
            journal_rows.append(result["journal_row"])
        applied_actions.append(result["applied_action"])

    positions_after = [
        normalize_position(item)
        for item in positions
        if int(item.get("remaining_shares", item.get("shares", 0)) or 0) > 0
    ]
    changed = bool(applied_actions) or bool(journal_rows) or len(positions_after) != len(positions)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "paper_action_update",
        "source_positions_report": str(source_path) if source_path else None,
        "changed": changed,
        "applied_actions": applied_actions,
        "journal_rows": journal_rows,
        "skipped": skipped,
        "positions_after": positions_after,
    }


def apply_raise_trailing_stop(position: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any] | None:
    hint = evaluation.get("update_hint") or {}
    new_stop = as_float(hint.get("set_current_stop_price") or evaluation.get("stop_reference"))
    current_stop = as_float(position.get("current_stop_price"))
    if new_stop <= 0 or new_stop <= current_stop:
        return None
    position["current_stop_price"] = round(new_stop, 4)
    return {
        "journal_row": None,
        "applied_action": {
            "symbol": position.get("symbol"),
            "action": "raise_trailing_stop",
            "previous_stop_price": round(current_stop, 4),
            "current_stop_price": round(new_stop, 4),
            "remaining_shares": int(position.get("remaining_shares", 0)),
        },
    }


def apply_partial_target(
    position: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    fees: float,
    source_path: Path | None,
) -> dict[str, Any] | None:
    if bool(position.get("target_hit")):
        return None
    hint = evaluation.get("update_hint") or {}
    shares = int(hint.get("sell_shares") or 0)
    if shares <= 0:
        shares = max(1, int(position.get("remaining_shares", 0)) // 2)
    shares = min(shares, int(position.get("remaining_shares", 0)))
    exit_price = as_float(evaluation.get("target_price"))
    if shares <= 0 or exit_price <= 0:
        return None
    remaining_after = int(position.get("remaining_shares", 0)) - shares
    position["remaining_shares"] = remaining_after
    position["target_hit"] = True
    position["current_stop_price"] = round(
        max(
            as_float(position.get("current_stop_price")),
            as_float(hint.get("move_stop_to_at_least")),
            as_float(position.get("entry_price")),
        ),
        4,
    )
    return {
        "journal_row": build_journal_row(
            position,
            evaluation,
            shares=shares,
            exit_price=exit_price,
            fees=fees,
            exit_reason="target_1",
            source_path=source_path,
        ),
        "applied_action": {
            "symbol": position.get("symbol"),
            "action": "sell_half_at_first_target",
            "shares": shares,
            "remaining_shares": remaining_after,
        },
    }


def apply_exit_remaining(
    position: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    fees: float,
    source_path: Path | None,
) -> dict[str, Any] | None:
    shares = int(position.get("remaining_shares", 0))
    if shares <= 0:
        return None
    action = str(evaluation.get("action") or "")
    if action in {"exit_remaining_stop", "exit_remaining_breakeven_or_stop"}:
        exit_price = as_float(evaluation.get("stop_reference"))
        exit_reason = "stop"
    else:
        exit_price = as_float(evaluation.get("latest_close"))
        exit_reason = "time_exit"
    if exit_price <= 0:
        return None
    position["remaining_shares"] = 0
    return {
        "journal_row": build_journal_row(
            position,
            evaluation,
            shares=shares,
            exit_price=exit_price,
            fees=fees,
            exit_reason=exit_reason,
            source_path=source_path,
        ),
        "applied_action": {
            "symbol": position.get("symbol"),
            "action": action,
            "shares": shares,
            "remaining_shares": 0,
        },
    }


def build_journal_row(
    position: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    shares: int,
    exit_price: float,
    fees: float,
    exit_reason: str,
    source_path: Path | None,
) -> dict[str, Any]:
    symbol = str(position.get("symbol") or "").upper()
    entry_date = str(position.get("entry_date"))
    exit_date = str(evaluation.get("latest_session_date") or datetime.now(UTC).date().isoformat())
    return {
        "trade_id": f"paper-{entry_date}-{symbol}",
        "symbol": symbol,
        "side": "long",
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": format_price(as_float(position.get("entry_price"))),
        "exit_price": format_price(exit_price),
        "shares": str(shares),
        "initial_stop_price": format_price(as_float(position.get("initial_stop_price"))),
        "fees": format_price(fees),
        "setup": "strong_pullback",
        "source": "paper_observation",
        "notes": f"{exit_reason} source_report={source_path or ''}".strip(),
    }


def normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    output = dict(position)
    for key in ("entry_price", "initial_stop_price", "current_stop_price", "target_price"):
        if key in output:
            output[key] = round(as_float(output[key]), 4)
    for key in ("shares", "remaining_shares"):
        if key in output:
            output[key] = int(output[key])
    output["target_hit"] = bool(output.get("target_hit", False))
    return output


def append_journal_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOURNAL_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
