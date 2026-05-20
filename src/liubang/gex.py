from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from liubang.options import collect_option_rows, number


EASTERN = ZoneInfo("America/New_York")
DEFAULT_CONTRACT_MULTIPLIER = 100.0
DEFAULT_MAX_STRIKE_DISTANCE_PCT = 0.15


def summarize_weekly_gex(
    chain_wrapper: dict[str, Any],
    *,
    as_of_date: date | str | None = None,
    max_strike_distance_pct: float | None = DEFAULT_MAX_STRIKE_DISTANCE_PCT,
) -> dict[str, Any]:
    payload = chain_wrapper.get("payload", chain_wrapper)
    symbol = str(chain_wrapper.get("symbol") or payload.get("symbol") or "").upper()
    spot = underlying_price(payload)
    effective_as_of_date = normalize_date(as_of_date) or datetime.now(EASTERN).date()
    week_end = current_week_friday(effective_as_of_date)

    rows = (
        collect_option_rows(payload.get("callExpDateMap") or {}, "call")
        + collect_option_rows(payload.get("putExpDateMap") or {}, "put")
    )
    buckets: dict[float, dict[str, Any]] = {}
    expirations: set[str] = set()
    available_contract_count = 0
    used_contract_count = 0

    if spot is None or spot <= 0:
        return empty_weekly_gex_summary(
            symbol=symbol,
            spot=spot,
            as_of_date=effective_as_of_date,
            week_end=week_end,
            max_strike_distance_pct=max_strike_distance_pct,
            risk_note="missing_underlying_price",
        )

    for row in rows:
        expiration = normalize_date(row.get("expiration"))
        if expiration is None or expiration < effective_as_of_date or expiration > week_end:
            continue

        strike = number(row.get("strike"))
        if strike <= 0:
            continue
        if max_strike_distance_pct is not None and max_strike_distance_pct > 0:
            if abs(strike - spot) / spot > max_strike_distance_pct:
                continue

        expirations.add(expiration.isoformat())
        available_contract_count += 1
        open_interest = int(number(row.get("open_interest")))
        gamma = number(row.get("gamma"))
        multiplier = number(row.get("multiplier")) or DEFAULT_CONTRACT_MULTIPLIER
        if open_interest <= 0 or gamma <= 0 or multiplier <= 0:
            continue

        gross_exposure = gamma * open_interest * multiplier * spot * spot * 0.01
        bucket = buckets.setdefault(
            strike,
            {
                "strike": strike,
                "call_gex": 0.0,
                "put_gex": 0.0,
                "net_gex": 0.0,
                "abs_gex": 0.0,
                "call_open_interest": 0,
                "put_open_interest": 0,
            },
        )
        if row.get("side") == "call":
            bucket["call_gex"] += gross_exposure
            bucket["call_open_interest"] += open_interest
        else:
            bucket["put_gex"] -= gross_exposure
            bucket["put_open_interest"] += open_interest
        used_contract_count += 1

    for bucket in buckets.values():
        bucket["net_gex"] = bucket["call_gex"] + bucket["put_gex"]
        bucket["abs_gex"] = abs(bucket["call_gex"]) + abs(bucket["put_gex"])

    strike_rows = sorted(buckets.values(), key=lambda item: abs(item["net_gex"]), reverse=True)
    call_gex = sum(item["call_gex"] for item in buckets.values())
    put_gex = sum(item["put_gex"] for item in buckets.values())
    net_gex = call_gex + put_gex
    gross_gex = abs(call_gex) + abs(put_gex)
    regime = classify_gex_regime(net_gex, gross_gex)
    call_wall = wall_strike(buckets.values(), "call_gex", largest=True)
    put_wall = wall_strike(buckets.values(), "put_gex", largest=False)
    max_abs_gamma_strike = strike_rows[0]["strike"] if strike_rows else None
    distance_to_call_wall_pct = wall_distance_pct(call_wall, spot)
    distance_to_put_wall_pct = wall_distance_pct(put_wall, spot)
    risk_notes = weekly_gex_risk_notes(
        regime=regime,
        gross_gex=gross_gex,
        contract_count=used_contract_count,
        expiration_count=len(expirations),
        distance_to_call_wall_pct=distance_to_call_wall_pct,
        distance_to_put_wall_pct=distance_to_put_wall_pct,
    )

    return {
        "symbol": symbol,
        "source": "schwab_option_chain_gamma",
        "fetched_at": chain_wrapper.get("fetched_at"),
        "spot": round(spot, 4),
        "as_of_date": effective_as_of_date.isoformat(),
        "week_end_date": week_end.isoformat(),
        "max_strike_distance_pct": max_strike_distance_pct,
        "gamma_source": "schwab_gamma",
        "direction_convention": "calls_positive_puts_negative",
        "unit": "dollars_per_1pct_underlying_move",
        "available_contract_count": available_contract_count,
        "contract_count": used_contract_count,
        "expiration_count": len(expirations),
        "expirations": sorted(expirations),
        "call_gex": round(call_gex, 2),
        "put_gex": round(put_gex, 2),
        "net_gex": round(net_gex, 2),
        "gross_gex": round(gross_gex, 2),
        "net_gex_ratio": round(net_gex / gross_gex, 4) if gross_gex else None,
        "regime": regime,
        "call_wall": round(call_wall, 4) if call_wall is not None else None,
        "put_wall": round(put_wall, 4) if put_wall is not None else None,
        "max_abs_gamma_strike": round(max_abs_gamma_strike, 4) if max_abs_gamma_strike is not None else None,
        "distance_to_call_wall_pct": round(distance_to_call_wall_pct, 4)
        if distance_to_call_wall_pct is not None
        else None,
        "distance_to_put_wall_pct": round(distance_to_put_wall_pct, 4)
        if distance_to_put_wall_pct is not None
        else None,
        "risk_notes": risk_notes,
        "top_strikes": [round_strike_row(item) for item in strike_rows[:7]],
    }


def empty_weekly_gex_summary(
    *,
    symbol: str,
    spot: float | None,
    as_of_date: date,
    week_end: date,
    max_strike_distance_pct: float | None,
    risk_note: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "source": "schwab_option_chain_gamma",
        "spot": round(spot, 4) if spot is not None else None,
        "as_of_date": as_of_date.isoformat(),
        "week_end_date": week_end.isoformat(),
        "max_strike_distance_pct": max_strike_distance_pct,
        "gamma_source": "schwab_gamma",
        "direction_convention": "calls_positive_puts_negative",
        "unit": "dollars_per_1pct_underlying_move",
        "available_contract_count": 0,
        "contract_count": 0,
        "expiration_count": 0,
        "expirations": [],
        "call_gex": 0.0,
        "put_gex": 0.0,
        "net_gex": 0.0,
        "gross_gex": 0.0,
        "net_gex_ratio": None,
        "regime": "neutral",
        "call_wall": None,
        "put_wall": None,
        "max_abs_gamma_strike": None,
        "distance_to_call_wall_pct": None,
        "distance_to_put_wall_pct": None,
        "risk_notes": [risk_note],
        "top_strikes": [],
    }


def underlying_price(payload: dict[str, Any]) -> float | None:
    for key in ("underlyingPrice", "underlying_price"):
        value = number(payload.get(key))
        if value > 0:
            return value
    underlying = payload.get("underlying") or {}
    if isinstance(underlying, dict):
        for key in ("last", "mark", "close", "quotePrice"):
            value = number(underlying.get(key))
            if value > 0:
                return value
    return None


def current_week_friday(as_of: date) -> date:
    weekday = as_of.weekday()
    days_until_friday = 4 - weekday if weekday <= 4 else 11 - weekday
    return as_of + timedelta(days=days_until_friday)


def classify_gex_regime(net_gex: float, gross_gex: float) -> str:
    if gross_gex <= 0:
        return "neutral"
    ratio = net_gex / gross_gex
    if ratio >= 0.05:
        return "positive"
    if ratio <= -0.05:
        return "negative"
    return "neutral"


def wall_strike(strikes: Any, field: str, *, largest: bool) -> float | None:
    candidates = [item for item in strikes if abs(number(item.get(field))) > 0]
    if not candidates:
        return None
    if largest:
        return float(max(candidates, key=lambda item: number(item.get(field)))["strike"])
    return float(min(candidates, key=lambda item: number(item.get(field)))["strike"])


def wall_distance_pct(wall: float | None, spot: float) -> float | None:
    if wall is None or spot <= 0:
        return None
    return (wall - spot) / spot


def weekly_gex_risk_notes(
    *,
    regime: str,
    gross_gex: float,
    contract_count: int,
    expiration_count: int,
    distance_to_call_wall_pct: float | None,
    distance_to_put_wall_pct: float | None,
) -> list[str]:
    notes = []
    if expiration_count == 0:
        notes.append("no_current_week_expiration")
    if contract_count < 10:
        notes.append("sparse_weekly_chain")
    if gross_gex <= 0:
        notes.append("zero_weekly_gex")
    if regime == "positive":
        notes.append("positive_weekly_gamma_pin_risk")
    elif regime == "negative":
        notes.append("negative_weekly_gamma_vol_expansion")
    if distance_to_call_wall_pct is not None and abs(distance_to_call_wall_pct) <= 0.0075:
        notes.append("near_call_wall")
    if distance_to_put_wall_pct is not None and abs(distance_to_put_wall_pct) <= 0.0075:
        notes.append("near_put_wall")
    return notes


def round_strike_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "strike": round(number(item.get("strike")), 4),
        "call_gex": round(number(item.get("call_gex")), 2),
        "put_gex": round(number(item.get("put_gex")), 2),
        "net_gex": round(number(item.get("net_gex")), 2),
        "abs_gex": round(number(item.get("abs_gex")), 2),
        "call_open_interest": int(number(item.get("call_open_interest"))),
        "put_open_interest": int(number(item.get("put_open_interest"))),
    }


def normalize_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
