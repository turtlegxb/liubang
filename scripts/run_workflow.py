#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env


EASTERN = ZoneInfo("America/New_York")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Liubang daily market-data workflow.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--positions-file", default="data/manual_positions.json")
    parser.add_argument("--paper-positions-file", default="data/paper_positions.json")
    parser.add_argument("--journal-file", default="data/trade_journal.csv")
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
    parser.add_argument("--paper-journal-file", default="data/paper_trade_journal.csv")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--send-empty-discord", action="store_true")
    parser.add_argument("--universe", default=None)
    parser.add_argument("--dynamic-source", choices=["yfinance", "none"], default=None)
    parser.add_argument("--dynamic-limit", type=int, default=None)
    parser.add_argument("--refresh-history", action="store_true", help="Refresh history during signal generation.")
    parser.add_argument("--refresh-dynamic", action="store_true")
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--refresh-news", action="store_true")
    parser.add_argument("--refresh-options", action="store_true")
    parser.add_argument("--use-cache-for-triggers", action="store_true")
    parser.add_argument("--use-cache-for-positions", action="store_true")
    parser.add_argument("--observation-only", action="store_true", help="Pass --observation-only to trigger scanning.")
    parser.add_argument("--only-market-window", action="store_true", help="Skip the workflow outside the configured ET time window.")
    parser.add_argument("--market-window-start-et", default="09:31", help="ET start time for --only-market-window, HH:MM.")
    parser.add_argument("--market-window-end-et", default="15:55", help="ET end time for --only-market-window, HH:MM.")
    parser.add_argument("--account-equity", type=float, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--min-pullback-pct", type=float, default=None)
    parser.add_argument("--max-pullback-pct", type=float, default=None)
    parser.add_argument("--symbol-cooldown-days", type=int, default=None)
    parser.add_argument("--cooldown-journal-file", default=None)
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument("--max-position-pct", type=float, default=None)
    parser.add_argument("--hard-stop-pct", type=float, default=None)
    parser.add_argument("--first-target-r", type=float, default=None)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    if args.only_market_window and not is_within_market_window(
        datetime.now(EASTERN),
        start=parse_hhmm(args.market_window_start_et),
        end=parse_hhmm(args.market_window_end_et),
    ):
        return finish(
            args,
            [
                {
                    "name": "market_window",
                    "status": "skipped",
                    "reason": (
                        "outside ET market window "
                        f"{args.market_window_start_et}-{args.market_window_end_et}"
                    ),
                    "returncode": None,
                    "command": [],
                    "stdout": "",
                    "stderr": "",
                }
            ],
            status="skipped",
        )
    steps = []

    if not args.skip_signals:
        steps.append(run_step("signals", build_signal_command(args)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
        if args.export_watchlist_csv:
            steps.append(run_step("watchlist_csv", build_watchlist_csv_command(args)))
            if steps[-1]["returncode"] != 0:
                return finish(args, steps, status="failed")

    if not args.skip_triggers:
        steps.append(run_step("triggers", build_trigger_command(args)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
        if args.export_trigger_templates:
            steps.append(run_step("trigger_templates", build_trigger_templates_command(args)))
            if steps[-1]["returncode"] != 0:
                return finish(args, steps, status="failed")
        if args.record_paper_triggers:
            steps.append(run_step("paper_triggers", build_paper_trigger_command(args)))
            if steps[-1]["returncode"] != 0:
                return finish(args, steps, status="failed")
    else:
        if args.export_trigger_templates:
            steps.append(skipped_step("trigger_templates", "--skip-triggers was passed"))
        if args.record_paper_triggers:
            steps.append(skipped_step("paper_triggers", "--skip-triggers was passed"))

    positions_path = Path(args.positions_file)
    if not args.skip_positions and positions_path.exists():
        steps.append(run_step("positions", build_position_command(args)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
    elif not args.skip_positions:
        steps.append(skipped_step("positions", f"positions file not found: {positions_path}"))

    paper_positions_path = Path(args.paper_positions_file)
    if args.monitor_paper_positions and paper_positions_path.exists():
        steps.append(run_step("paper_positions", build_position_command(args, positions_file=args.paper_positions_file)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
        if args.apply_paper_actions:
            steps.append(run_step("paper_actions", build_paper_action_command(args)))
            if steps[-1]["returncode"] != 0:
                return finish(args, steps, status="failed")
    elif args.monitor_paper_positions:
        steps.append(skipped_step("paper_positions", f"paper positions file not found: {paper_positions_path}"))
        if args.apply_paper_actions:
            steps.append(skipped_step("paper_actions", f"paper positions file not found: {paper_positions_path}"))
    elif args.apply_paper_actions:
        steps.append(skipped_step("paper_actions", "--monitor-paper-positions was not passed"))

    paper_journal_path = Path(args.paper_journal_file)
    if args.summarize_paper_journal and paper_journal_path.exists():
        steps.append(run_step("paper_journal", build_paper_journal_command(args)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
    elif args.summarize_paper_journal:
        steps.append(skipped_step("paper_journal", f"paper journal file not found: {paper_journal_path}"))

    journal_path = Path(args.journal_file)
    if not args.skip_journal and journal_path.exists():
        steps.append(run_step("journal", build_journal_command(args)))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
    elif not args.skip_journal:
        steps.append(
            {
                "name": "journal",
                "status": "skipped",
                "reason": f"journal file not found: {journal_path}",
                "returncode": None,
                "command": [],
                "stdout": "",
                "stderr": "",
            }
        )

    return finish(args, steps, status="ok")


def build_signal_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "scripts/generate_signals.py", "--output-dir", args.output_dir]
    if args.universe:
        command.extend(["--universe", args.universe])
    if args.dynamic_source:
        command.extend(["--dynamic-source", args.dynamic_source])
    if args.dynamic_limit is not None:
        command.extend(["--dynamic-limit", str(args.dynamic_limit)])
    if args.refresh_history:
        command.append("--refresh")
    for flag in ("refresh_dynamic", "refresh_earnings", "refresh_news", "refresh_options", "send_discord"):
        if getattr(args, flag):
            command.append("--" + flag.replace("_", "-"))
    command.extend(["--positions-file", args.positions_file])
    command.extend(["--journal-file", args.journal_file])
    append_signal_strategy_args(command, args)
    append_sizing_args(command, args)
    return command


def build_trigger_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "scripts/scan_triggers.py", "--output-dir", args.output_dir]
    if args.use_cache_for_triggers:
        command.append("--use-cache")
    if args.send_discord:
        command.append("--send-discord")
    if args.send_empty_discord:
        command.append("--send-empty-discord")
    if args.observation_only:
        command.append("--observation-only")
    append_sizing_args(command, args)
    return command


def build_watchlist_csv_command(args: argparse.Namespace) -> list[str]:
    return [sys.executable, "scripts/export_watchlist_csv.py", "--exports-dir", args.output_dir]


def build_trigger_templates_command(args: argparse.Namespace) -> list[str]:
    return [sys.executable, "scripts/export_trigger_templates.py", "--exports-dir", args.output_dir]


def build_paper_trigger_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "scripts/record_paper_triggers.py",
        "--output-dir",
        args.output_dir,
        "--paper-positions",
        args.paper_positions_file,
        "--paper-journal",
        args.paper_journal_file,
    ]


def build_position_command(args: argparse.Namespace, *, positions_file: str | None = None) -> list[str]:
    command = [
        sys.executable,
        "scripts/monitor_positions.py",
        "--positions",
        positions_file or args.positions_file,
        "--output-dir",
        args.output_dir,
    ]
    if args.use_cache_for_positions:
        command.append("--use-cache")
    if args.send_discord:
        command.append("--send-discord")
    if args.send_empty_discord:
        command.append("--send-empty-discord")
    return command


def build_paper_action_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "scripts/apply_paper_actions.py",
        "--output-dir",
        args.output_dir,
        "--paper-positions",
        args.paper_positions_file,
        "--paper-journal",
        args.paper_journal_file,
    ]


def build_paper_journal_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "scripts/summarize_journal.py",
        "--journal",
        args.paper_journal_file,
        "--output-dir",
        args.output_dir,
        "--report-prefix",
        "paper_journal",
    ]
    account_equity = getattr(args, "account_equity", None)
    if account_equity is not None:
        command.extend(["--initial-equity", str(account_equity)])
    return command


def build_journal_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "scripts/summarize_journal.py",
        "--journal",
        args.journal_file,
        "--output-dir",
        args.output_dir,
    ]
    account_equity = getattr(args, "account_equity", None)
    if account_equity is not None:
        command.extend(["--initial-equity", str(account_equity)])
    return command


def append_sizing_args(command: list[str], args: argparse.Namespace) -> None:
    for key in (
        "account_equity",
        "risk_per_trade_pct",
        "max_position_pct",
        "hard_stop_pct",
        "first_target_r",
    ):
        value = getattr(args, key)
        if value is not None:
            command.extend(["--" + key.replace("_", "-"), str(value)])


def append_signal_strategy_args(command: list[str], args: argparse.Namespace) -> None:
    for key in (
        "min_score",
        "min_pullback_pct",
        "max_pullback_pct",
        "symbol_cooldown_days",
    ):
        value = getattr(args, key)
        if value is not None:
            command.extend(["--" + key.replace("_", "-"), str(value)])
    if args.cooldown_journal_file:
        command.extend(["--cooldown-journal-file", args.cooldown_journal_file])


def run_step(name: str, command: list[str]) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    return {
        "name": name,
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "command": redact_command(command),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def skipped_step(name: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "skipped",
        "reason": reason,
        "returncode": None,
        "command": [],
        "stdout": "",
        "stderr": "",
    }


def finish(args: argparse.Namespace, steps: list[dict[str, Any]], *, status: str) -> int:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "steps": steps,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"workflow_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Workflow status: {status}")
    print(f"Workflow report: {path}")
    return 0 if status in {"ok", "skipped"} else 1


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_within_market_window(now_et: datetime, *, start: time, end: time) -> bool:
    if now_et.weekday() >= 5:
        return False
    current = now_et.time().replace(second=0, microsecond=0)
    return start <= current <= end


def tail(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def redact_command(command: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--discord-webhook-url"}:
            skip_next = True
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
