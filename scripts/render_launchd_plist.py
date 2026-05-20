#!/usr/bin/env python3
from __future__ import annotations

import argparse
import plistlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("data/launchd/com.liubang.workflow.plist")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a launchd plist for the Liubang workflow.")
    parser.add_argument("--label", default="com.liubang.workflow")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--python", default=str(PROJECT_ROOT / ".venv/bin/python"))
    parser.add_argument("--workflow-script", default=str(PROJECT_ROOT / "scripts/run_workflow.py"))
    parser.add_argument("--hour", type=int, default=9)
    parser.add_argument("--minute", type=int, default=35)
    parser.add_argument("--start-interval-seconds", type=int, default=None, help="Run repeatedly every N seconds instead of once per local day.")
    parser.add_argument("--only-market-window", action="store_true", help="Pass --only-market-window to run_workflow.py.")
    parser.add_argument("--market-window-start-et", default="09:31")
    parser.add_argument("--market-window-end-et", default="15:55")
    parser.add_argument("--skip-signals", action="store_true")
    parser.add_argument("--skip-triggers", action="store_true")
    parser.add_argument("--skip-positions", action="store_true")
    parser.add_argument("--skip-journal", action="store_true")
    parser.add_argument("--export-watchlist-csv", action="store_true")
    parser.add_argument("--export-trigger-templates", action="store_true")
    parser.add_argument("--record-paper-triggers", action="store_true")
    parser.add_argument("--monitor-paper-positions", action="store_true")
    parser.add_argument("--apply-paper-actions", action="store_true")
    parser.add_argument("--summarize-paper-journal", action="store_true")
    parser.add_argument("--use-cache-for-triggers", action="store_true")
    parser.add_argument("--observation-only", action="store_true")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--send-empty-discord", action="store_true")
    parser.add_argument("--account-equity", type=float, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = Path(args.output)
    program_arguments = [
        args.python,
        args.workflow_script,
    ]
    if args.use_cache_for_triggers:
        program_arguments.append("--use-cache-for-triggers")
    if args.only_market_window:
        program_arguments.append("--only-market-window")
        program_arguments.extend(["--market-window-start-et", args.market_window_start_et])
        program_arguments.extend(["--market-window-end-et", args.market_window_end_et])
    for flag in ("skip_signals", "skip_triggers", "skip_positions", "skip_journal"):
        if getattr(args, flag):
            program_arguments.append("--" + flag.replace("_", "-"))
    if args.export_watchlist_csv:
        program_arguments.append("--export-watchlist-csv")
    if args.export_trigger_templates:
        program_arguments.append("--export-trigger-templates")
    if args.record_paper_triggers:
        program_arguments.append("--record-paper-triggers")
    if args.monitor_paper_positions:
        program_arguments.append("--monitor-paper-positions")
    if args.apply_paper_actions:
        program_arguments.append("--apply-paper-actions")
    if args.summarize_paper_journal:
        program_arguments.append("--summarize-paper-journal")
    if args.observation_only:
        program_arguments.append("--observation-only")
    if args.send_discord:
        program_arguments.append("--send-discord")
    if args.send_empty_discord:
        program_arguments.append("--send-empty-discord")
    if args.account_equity is not None:
        program_arguments.extend(["--account-equity", str(args.account_equity)])

    payload = {
        "Label": args.label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": str(PROJECT_ROOT / "logs/launchd_workflow.out.log"),
        "StandardErrorPath": str(PROJECT_ROOT / "logs/launchd_workflow.err.log"),
        "RunAtLoad": False,
    }
    if args.start_interval_seconds is not None:
        payload["StartInterval"] = args.start_interval_seconds
    else:
        payload["StartCalendarInterval"] = {
            "Hour": args.hour,
            "Minute": args.minute,
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    print(f"Rendered launchd plist: {output}")
    print("Review it before copying to ~/Library/LaunchAgents/ and loading with launchctl.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
