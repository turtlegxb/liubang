#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export actionable trigger manual-position templates to JSON.")
    parser.add_argument("--triggers-report", default=None, help="Trigger report JSON. Defaults to latest data/exports/triggers_*.json.")
    parser.add_argument("--output", default=None, help="Output JSON path. Defaults to data/exports/manual_position_templates_TIMESTAMP.json.")
    parser.add_argument("--exports-dir", default="data/exports")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        trigger_path = Path(args.triggers_report) if args.triggers_report else latest_trigger_report(Path(args.exports_dir))
        report = json.loads(trigger_path.read_text(encoding="utf-8"))
        output_path = (
            Path(args.output)
            if args.output
            else Path(args.exports_dir) / f"manual_position_templates_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        )
        payload = build_templates_payload(report, source_path=trigger_path)
        write_payload(output_path, payload)
    except Exception as exc:
        print(f"export_trigger_templates failed: {exc}", file=sys.stderr)
        return 1

    print("Trigger template export")
    print(f"Trigger report: {trigger_path}")
    print(f"Templates: {len(payload['positions'])}")
    print(f"JSON: {output_path}")
    return 0


def latest_trigger_report(exports_dir: Path) -> Path:
    reports = sorted(exports_dir.glob("triggers_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No trigger reports found in {exports_dir}")
    return reports[-1]


def build_templates_payload(report: dict[str, Any], *, source_path: Path | None = None) -> dict[str, Any]:
    positions = []
    blocked = []
    for item in report.get("evaluations", []):
        template = item.get("manual_position_template")
        if item.get("triggered") and template and item.get("action") == "prepare_manual_entry":
            positions.append(template)
        elif item.get("triggered") and item.get("action") != "prepare_manual_entry":
            blocked.append(
                {
                    "symbol": item.get("symbol"),
                    "action": item.get("action"),
                    "reason": item.get("reason"),
                }
            )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_triggers_report": str(source_path) if source_path else None,
        "note": "Review and adjust entry_price/shares to actual manual fill before copying into data/manual_positions.json.",
        "positions": positions,
        "blocked_triggers": blocked,
    }


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
