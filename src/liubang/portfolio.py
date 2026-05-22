from __future__ import annotations

from pathlib import Path
from typing import Any

from liubang.defaults import (
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
)
from liubang.positions import DEFAULT_MANUAL_POSITIONS_PATH, ManualPosition, load_manual_positions


MAX_POSITIONS_BY_REGIME = {
    "strong": 3,
    "neutral": 2,
    "weak": 1,
}


def load_open_positions_if_available(
    path: Path = DEFAULT_MANUAL_POSITIONS_PATH,
) -> tuple[ManualPosition, ...]:
    if not path.exists():
        return ()
    return tuple(
        position
        for position in load_manual_positions(path)
        if position.remaining_shares > 0
    )


def build_portfolio_guard(
    *,
    market_regime: str,
    open_positions: tuple[ManualPosition, ...],
    positions_path: Path | None,
    account_equity: float = DEFAULT_INITIAL_EQUITY,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    weak_regime_size_multiplier: float = DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
    weak_max_positions: int = MAX_POSITIONS_BY_REGIME["weak"],
    neutral_max_positions: int = MAX_POSITIONS_BY_REGIME["neutral"],
    strong_max_positions: int = MAX_POSITIONS_BY_REGIME["strong"],
) -> dict[str, Any]:
    max_positions_by_regime = {
        "weak": max(0, int(weak_max_positions)),
        "neutral": max(0, int(neutral_max_positions)),
        "strong": max(0, int(strong_max_positions)),
    }
    normalized_regime = market_regime if market_regime in MAX_POSITIONS_BY_REGIME else "neutral"
    max_positions = max_positions_by_regime[normalized_regime]
    current_count = len(open_positions)
    available_slots = max(0, max_positions - current_count)
    size_multiplier = weak_regime_size_multiplier if normalized_regime == "weak" else 1.0
    max_gross_exposure_value = account_equity * max_position_pct * size_multiplier * max_positions
    current_gross_exposure_value = sum(
        position.entry_price * position.remaining_shares
        for position in open_positions
    )
    available_exposure_value = max(0.0, max_gross_exposure_value - current_gross_exposure_value)
    exposure_allows_new_entries = available_exposure_value > 0
    return {
        "source": "manual_positions_file" if positions_path else "not_loaded",
        "positions_path": str(positions_path) if positions_path else None,
        "market_regime": normalized_regime,
        "account_equity": round(account_equity, 2),
        "max_positions": max_positions,
        "current_positions": current_count,
        "open_symbols": [position.symbol for position in open_positions],
        "available_slots": available_slots,
        "max_position_pct": round(max_position_pct, 6),
        "size_multiplier": round(size_multiplier, 4),
        "max_gross_exposure_value": round(max_gross_exposure_value, 2),
        "current_gross_exposure_value": round(current_gross_exposure_value, 2),
        "current_gross_exposure_pct": round(
            current_gross_exposure_value / account_equity,
            6,
        ) if account_equity else None,
        "available_exposure_value": round(available_exposure_value, 2),
        "exposure_allows_new_entries": exposure_allows_new_entries,
        "allow_new_entries": available_slots > 0 and exposure_allows_new_entries,
        "rule": (
            f"strong={max_positions_by_regime['strong']} positions, "
            f"neutral={max_positions_by_regime['neutral']} positions, "
            f"weak={max_positions_by_regime['weak']} half-size position"
        ),
    }


def portfolio_guard_from_file(
    *,
    market_regime: str,
    positions_path: Path = DEFAULT_MANUAL_POSITIONS_PATH,
    account_equity: float = DEFAULT_INITIAL_EQUITY,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    weak_regime_size_multiplier: float = DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
    weak_max_positions: int = MAX_POSITIONS_BY_REGIME["weak"],
    neutral_max_positions: int = MAX_POSITIONS_BY_REGIME["neutral"],
    strong_max_positions: int = MAX_POSITIONS_BY_REGIME["strong"],
) -> dict[str, Any]:
    open_positions = load_open_positions_if_available(positions_path)
    return build_portfolio_guard(
        market_regime=market_regime,
        open_positions=open_positions,
        positions_path=positions_path if positions_path.exists() else None,
        account_equity=account_equity,
        max_position_pct=max_position_pct,
        weak_regime_size_multiplier=weak_regime_size_multiplier,
        weak_max_positions=weak_max_positions,
        neutral_max_positions=neutral_max_positions,
        strong_max_positions=strong_max_positions,
    )
