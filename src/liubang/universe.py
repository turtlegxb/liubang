from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_UNIVERSE_PATH = Path("config/core_universe.json")


@dataclass(frozen=True)
class Universe:
    name: str
    core_symbols: tuple[str, ...]
    benchmarks: tuple[str, ...]
    sector_etfs: tuple[str, ...]
    metadata: dict[str, Any]

    @property
    def tradable_symbols(self) -> tuple[str, ...]:
        return self.core_symbols

    @property
    def required_data_symbols(self) -> tuple[str, ...]:
        return unique_symbols((*self.core_symbols, *self.benchmarks, *self.sector_etfs))


def load_universe(path: Path = DEFAULT_UNIVERSE_PATH) -> Universe:
    payload = json.loads(path.read_text(encoding="utf-8"))
    core_symbols = tuple(
        item["symbol"].strip().upper()
        for item in payload.get("core_symbols", [])
        if isinstance(item, dict) and item.get("symbol")
    )
    benchmarks = tuple(str(item).strip().upper() for item in payload.get("benchmarks", []) if item)
    sector_etfs = tuple(str(item).strip().upper() for item in payload.get("sector_etfs", []) if item)
    if not core_symbols:
        raise ValueError(f"No core symbols found in universe file: {path}")
    if "SPY" not in benchmarks or "QQQ" not in benchmarks:
        raise ValueError("Universe benchmarks must include SPY and QQQ.")
    return Universe(
        name=str(payload.get("name") or path.stem),
        core_symbols=unique_symbols(core_symbols),
        benchmarks=unique_symbols(benchmarks),
        sector_etfs=unique_symbols(sector_etfs),
        metadata=payload,
    )


def resolve_symbols(
    *,
    raw_symbols: str | None,
    universe_path: Path,
    include_benchmarks: bool = True,
    include_sector_etfs: bool = True,
) -> tuple[str, ...]:
    universe = load_universe(universe_path)
    if raw_symbols:
        symbols = parse_symbols(raw_symbols)
    else:
        symbols = universe.core_symbols
        if include_benchmarks:
            symbols = (*symbols, *universe.benchmarks)
        if include_sector_etfs:
            symbols = (*symbols, *universe.sector_etfs)
    if include_benchmarks:
        symbols = (*symbols, "SPY", "QQQ")
    if include_sector_etfs and not raw_symbols:
        symbols = (*symbols, *universe.sector_etfs)
    return unique_symbols(symbols)


def parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = []
    for item in raw.replace(";", ",").split(","):
        symbol = item.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return tuple(symbols)


def unique_symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    output = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in output:
            output.append(normalized)
    return tuple(output)
