#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


REPORT_PREFIXES = (
    "signals_",
    "watchlist_",
    "triggers_",
    "manual_position_templates_",
    "paper_positions_update_",
    "paper_actions_",
    "positions_",
    "journal_",
    "paper_journal_",
    "workflow_",
    "backtest_",
    "hourly_proxy_",
    "daily_proxy_",
    "sweep_",
    "stress_",
    "ablation_",
    "theme_ablation_",
    "strategy_validation_",
    "research_suite_",
    "schwab_probe_",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prune old ignored report files from data/exports.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--keep", type=int, default=30, help="Number of newest files to keep per report prefix.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Default is dry-run.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    candidates = collect_prune_candidates(output_dir, keep=args.keep)
    for path in candidates:
        print(("delete " if args.apply else "would delete ") + str(path))
        if args.apply:
            path.unlink()
    print(f"Prune mode: {'apply' if args.apply else 'dry-run'}")
    print(f"Candidates: {len(candidates)}")
    return 0


def collect_prune_candidates(output_dir: Path, *, keep: int) -> list[Path]:
    if keep < 0:
        raise ValueError("--keep must be >= 0")
    candidates: list[Path] = []
    for prefix in REPORT_PREFIXES:
        files = sorted(output_dir.glob(f"{prefix}*"), key=lambda path: path.stat().st_mtime, reverse=True)
        candidates.extend(files[keep:])
    return sorted(candidates, key=lambda path: str(path))


if __name__ == "__main__":
    raise SystemExit(main())
