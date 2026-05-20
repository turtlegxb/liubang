from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from liubang.defaults import (
    DEFAULT_FIRST_TARGET_R,
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_RISK_PER_TRADE_PCT,
    DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
)


@dataclass(frozen=True)
class SizingInputs:
    account_equity: float = DEFAULT_INITIAL_EQUITY
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    hard_stop_pct: float = DEFAULT_HARD_STOP_PCT
    first_target_r: float = DEFAULT_FIRST_TARGET_R
    weak_regime_size_multiplier: float = DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER


@dataclass(frozen=True)
class TradePlan:
    entry_price_reference: float
    stop_price: float
    technical_stop_reference: float | None
    hard_stop_price: float
    risk_per_share: float
    first_target_price: float
    account_equity: float
    risk_per_trade_pct: float
    max_position_pct: float
    size_multiplier: float
    max_dollar_risk: float
    max_position_value: float
    risk_based_shares: int
    capital_cap_shares: int
    suggested_shares: int
    suggested_position_value: float
    suggested_dollar_risk: float
    binding_constraint: str
    invalid: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


def build_trade_plan(
    *,
    entry_price: float,
    technical_stop: float | None,
    market_regime: str,
    sizing: SizingInputs,
) -> TradePlan:
    entry = round(float(entry_price), 4)
    hard_stop = round(entry * (1.0 - sizing.hard_stop_pct), 4)
    technical = round(float(technical_stop), 4) if technical_stop is not None else None
    stop = max(value for value in (technical, hard_stop) if value is not None)
    risk_per_share = round(entry - stop, 4)
    size_multiplier = sizing.weak_regime_size_multiplier if market_regime == "weak" else 1.0
    max_dollar_risk = round(sizing.account_equity * sizing.risk_per_trade_pct * size_multiplier, 2)
    max_position_value = round(sizing.account_equity * sizing.max_position_pct * size_multiplier, 2)

    warnings = []
    if entry <= 0:
        warnings.append("invalid_entry_price")
    if risk_per_share <= 0:
        warnings.append("invalid_stop_not_below_entry")

    if entry > 0 and risk_per_share > 0:
        risk_based_shares = math.floor(max_dollar_risk / risk_per_share)
        capital_cap_shares = math.floor(max_position_value / entry)
    else:
        risk_based_shares = 0
        capital_cap_shares = 0

    suggested_shares = max(0, min(risk_based_shares, capital_cap_shares))
    if suggested_shares <= 0:
        warnings.append("no_shares_after_risk_constraints")

    if risk_based_shares < capital_cap_shares:
        binding_constraint = "risk"
    elif capital_cap_shares < risk_based_shares:
        binding_constraint = "capital_cap"
    elif suggested_shares > 0:
        binding_constraint = "risk_and_cap_equal"
    else:
        binding_constraint = "none"

    suggested_position_value = round(suggested_shares * entry, 2)
    suggested_dollar_risk = round(suggested_shares * max(0.0, risk_per_share), 2)
    first_target = round(entry + max(0.0, risk_per_share) * sizing.first_target_r, 4)

    return TradePlan(
        entry_price_reference=entry,
        stop_price=round(stop, 4),
        technical_stop_reference=technical,
        hard_stop_price=hard_stop,
        risk_per_share=risk_per_share,
        first_target_price=first_target,
        account_equity=round(sizing.account_equity, 2),
        risk_per_trade_pct=round(sizing.risk_per_trade_pct, 6),
        max_position_pct=round(sizing.max_position_pct, 6),
        size_multiplier=round(size_multiplier, 4),
        max_dollar_risk=max_dollar_risk,
        max_position_value=max_position_value,
        risk_based_shares=risk_based_shares,
        capital_cap_shares=capital_cap_shares,
        suggested_shares=suggested_shares,
        suggested_position_value=suggested_position_value,
        suggested_dollar_risk=suggested_dollar_risk,
        binding_constraint=binding_constraint,
        invalid=bool(warnings),
        warnings=tuple(warnings),
    )
