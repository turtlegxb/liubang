from __future__ import annotations

from datetime import date
from typing import Any


def evaluate_data_quality(
    data_summary: dict[str, dict[str, Any]],
    *,
    expected_symbols: tuple[str, ...],
    min_daily_bars: int = 25,
) -> dict[str, Any]:
    end_dates = [
        date.fromisoformat(str(item["end"]))
        for item in data_summary.values()
        if item.get("end")
    ]
    latest_end = max(end_dates) if end_dates else None
    missing_symbols = [
        symbol
        for symbol in expected_symbols
        if symbol not in data_summary
    ]
    insufficient_symbols = [
        symbol
        for symbol, item in sorted(data_summary.items())
        if int(item.get("daily_bars") or 0) < min_daily_bars
    ]
    stale_symbols = []
    if latest_end is not None:
        for symbol, item in sorted(data_summary.items()):
            raw_end = item.get("end")
            if not raw_end:
                stale_symbols.append(symbol)
                continue
            if date.fromisoformat(str(raw_end)) < latest_end:
                stale_symbols.append(symbol)

    warnings = []
    if missing_symbols:
        warnings.append("missing_symbols")
    if insufficient_symbols:
        warnings.append("insufficient_daily_bars")
    if stale_symbols:
        warnings.append("stale_symbols")

    return {
        "status": "ok" if not warnings else "warning",
        "latest_end_date": latest_end.isoformat() if latest_end else None,
        "min_daily_bars": min_daily_bars,
        "expected_symbol_count": len(expected_symbols),
        "available_symbol_count": len(data_summary),
        "missing_symbols": missing_symbols,
        "insufficient_symbols": insufficient_symbols,
        "stale_symbols": stale_symbols,
        "warnings": warnings,
    }
