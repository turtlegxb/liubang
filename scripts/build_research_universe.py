#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.dynamic_universe import (
    DEFAULT_DYNAMIC_SCREENERS,
    DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH,
    DynamicUniverseSelection,
    load_or_select_yfinance_dynamic_universe,
    parse_screeners,
)
from liubang.universe import DEFAULT_UNIVERSE_PATH, load_universe, parse_symbols, unique_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an expanded research universe from core symbols plus yfinance screeners.")
    parser.add_argument("--base-universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--output", default="config/research_universe_dynamic.json")
    parser.add_argument("--dynamic-source", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--dynamic-cache", default=str(DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH))
    parser.add_argument("--dynamic-screeners", default=",".join(DEFAULT_DYNAMIC_SCREENERS))
    parser.add_argument("--dynamic-screener-count", type=int, default=100)
    parser.add_argument("--dynamic-limit", type=int, default=30)
    parser.add_argument("--dynamic-min-price", type=float, default=5.0)
    parser.add_argument("--dynamic-min-market-cap", type=float, default=2_000_000_000.0)
    parser.add_argument("--dynamic-min-dollar-volume", type=float, default=1_000_000_000.0)
    parser.add_argument("--dynamic-min-avg-dollar-volume", type=float, default=300_000_000.0)
    parser.add_argument("--refresh-dynamic", action="store_true")
    parser.add_argument("--extra-symbols", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        base_path = Path(args.base_universe)
        base_payload = json.loads(base_path.read_text(encoding="utf-8"))
        base_universe = load_universe(base_path)
        dynamic_selection = None
        if args.dynamic_source == "yfinance":
            dynamic_selection = load_or_select_yfinance_dynamic_universe(
                cache_path=Path(args.dynamic_cache),
                refresh=args.refresh_dynamic,
                screeners=parse_screeners(args.dynamic_screeners),
                screener_count=args.dynamic_screener_count,
                excluded_symbols=base_universe.required_data_symbols,
                limit=args.dynamic_limit,
                min_price=args.dynamic_min_price,
                min_market_cap=args.dynamic_min_market_cap,
                min_dollar_volume=args.dynamic_min_dollar_volume,
                min_average_dollar_volume=args.dynamic_min_avg_dollar_volume,
            )
        payload = build_research_universe_payload(
            base_payload=base_payload,
            dynamic_selection=dynamic_selection,
            extra_symbols=parse_symbols(args.extra_symbols or ""),
        )
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"build_research_universe failed: {exc}", file=sys.stderr)
        return 1

    core_count = len(base_payload.get("core_symbols") or [])
    total_count = len(payload.get("core_symbols") or [])
    dynamic_count = len(dynamic_selection.items) if dynamic_selection else 0
    print("Research universe summary")
    print(f"Base core symbols: {core_count}")
    print(f"Dynamic symbols: {dynamic_count}")
    print(f"Total core symbols: {total_count}")
    if dynamic_selection:
        print(f"Dynamic selected: {', '.join(dynamic_selection.symbols)}")
    print(f"Output: {output_path}")
    return 0


def build_research_universe_payload(
    *,
    base_payload: dict[str, Any],
    dynamic_selection: DynamicUniverseSelection | None,
    extra_symbols: tuple[str, ...] = (),
) -> dict[str, Any]:
    base_core = [
        dict(item)
        for item in base_payload.get("core_symbols", [])
        if isinstance(item, dict) and item.get("symbol")
    ]
    existing = unique_symbols(tuple(str(item["symbol"]) for item in base_core))
    additions: list[dict[str, Any]] = []

    if dynamic_selection is not None:
        for item in dynamic_selection.items:
            if item.symbol in existing:
                continue
            additions.append(
                {
                    "symbol": item.symbol,
                    "theme": "dynamic_yfinance",
                    "source": "yfinance_screener",
                    "screeners": list(item.screeners),
                    "rank_score": round(item.rank_score, 4),
                    "short_name": item.short_name,
                }
            )
            existing = unique_symbols((*existing, item.symbol))

    for symbol in extra_symbols:
        if symbol in existing:
            continue
        additions.append({"symbol": symbol, "theme": "manual_extra", "source": "manual_extra"})
        existing = unique_symbols((*existing, symbol))

    return {
        "name": "liubang_research_dynamic_v1",
        "updated_at": datetime.now(UTC).date().isoformat(),
        "description": (
            "Expanded research universe generated from the fixed core universe plus "
            "high-liquidity yfinance screener additions. This is not a buy list."
        ),
        "benchmarks": base_payload.get("benchmarks", ["SPY", "QQQ"]),
        "sector_etfs": base_payload.get("sector_etfs", ["XLK", "SMH"]),
        "core_symbols": base_core + additions,
        "generation": {
            "base_name": base_payload.get("name"),
            "base_count": len(base_core),
            "dynamic_count": len(dynamic_selection.items) if dynamic_selection else 0,
            "extra_count": len(extra_symbols),
            "dynamic_summary": dynamic_selection.summary() if dynamic_selection else None,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
