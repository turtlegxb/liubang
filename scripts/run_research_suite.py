#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import BacktestParams, SCORING_MODES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Liubang research validation suite.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--universe", default="config/core_universe.json")
    parser.add_argument("--trades-csv", default="data/exports/latest_backtest_trades.csv")
    parser.add_argument("--min-score", type=float, default=7.0)
    parser.add_argument("--min-pullback-pct", type=float, default=0.01)
    parser.add_argument("--max-pullback-pct", type=float, default=0.06)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--hard-stop-pct", type=float, default=0.04)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    parser.add_argument("--refresh", action="store_true", help="Refresh intraday history for 5-minute research steps.")
    parser.add_argument("--refresh-hourly", action="store_true", help="Refresh yfinance hourly proxy history.")
    parser.add_argument("--refresh-daily", action="store_true", help="Refresh yfinance daily proxy history.")
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--fail-on-gate-failure", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    steps = []
    for name, command in build_suite_commands(args):
        steps.append(run_step(name, command))
        if steps[-1]["returncode"] != 0:
            return finish(args, steps, status="failed")
    return finish(args, steps, status="ok")


def build_suite_commands(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = [
        (
            "backtest",
            [
                sys.executable,
                "scripts/run_backtest.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
                "--trades-csv",
                args.trades_csv,
            ],
        ),
        (
            "stress",
            [
                sys.executable,
                "scripts/stress_backtest.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
            ],
        ),
        (
            "hourly_proxy",
            [
                sys.executable,
                "scripts/run_hourly_proxy_backtest.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
            ],
        ),
        (
            "daily_proxy",
            [
                sys.executable,
                "scripts/run_daily_proxy_backtest.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
            ],
        ),
        (
            "symbol_ablation",
            [
                sys.executable,
                "scripts/ablate_symbols.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
            ],
        ),
        (
            "theme_ablation",
            [
                sys.executable,
                "scripts/ablate_themes.py",
                "--universe",
                args.universe,
                "--cache-dir",
                args.cache_dir,
                "--output-dir",
                args.output_dir,
            ],
        ),
        (
            "strategy_validation",
            [
                sys.executable,
                "scripts/validate_strategy.py",
                "--output-dir",
                args.output_dir,
            ],
        ),
    ]
    if args.refresh:
        for name, command in commands:
            if name in {"backtest", "stress"}:
                command.append("--refresh")
    if args.refresh_daily:
        command_by_name(commands, "daily_proxy").append("--refresh")
    if args.refresh_hourly:
        command_by_name(commands, "hourly_proxy").append("--refresh")
    if args.refresh_earnings:
        for name, command in commands:
            if name in {"backtest", "hourly_proxy", "daily_proxy"}:
                command.append("--refresh-earnings")
    if args.fail_on_gate_failure:
        command_by_name(commands, "strategy_validation").append("--fail-on-gate-failure")
    for name, command in commands:
        if name in {"backtest", "stress", "hourly_proxy", "daily_proxy", "symbol_ablation", "theme_ablation"}:
            append_strategy_args(command, args)
    return commands


def command_by_name(commands: list[tuple[str, list[str]]], target: str) -> list[str]:
    for name, command in commands:
        if name == target:
            return command
    raise KeyError(target)


def append_strategy_args(command: list[str], args: argparse.Namespace) -> None:
    for key in (
        "min_score",
        "min_pullback_pct",
        "max_pullback_pct",
        "scoring_mode",
        "hard_stop_pct",
        "symbol_cooldown_days",
    ):
        command.extend(["--" + key.replace("_", "-"), str(getattr(args, key))])


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
        "command": command,
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def finish(args: argparse.Namespace, steps: list[dict[str, Any]], *, status: str) -> int:
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "steps": steps,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"research_suite_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Research suite status: {status}")
    print(f"Research suite report: {path}")
    return 0 if status == "ok" else 1


def tail(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


if __name__ == "__main__":
    raise SystemExit(main())
