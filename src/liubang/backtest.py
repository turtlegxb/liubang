from __future__ import annotations

import json
import math
import csv
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from liubang.defaults import (
    DEFAULT_FIRST_TARGET_R,
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_PULLBACK_PCT,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SCORE,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from liubang.earnings import EarningsCalendar


EASTERN = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
NON_TRADABLE_CONTEXT_SYMBOLS = {"SPY", "QQQ", "XLK", "SMH"}
SCORING_MODE_CLASSIC = "classic"
SCORING_MODE_RANKED_V1 = "ranked_v1"
SCORING_MODE_RANKED_V2 = "ranked_v2"
SCORING_MODES = (SCORING_MODE_CLASSIC, SCORING_MODE_RANKED_V1, SCORING_MODE_RANKED_V2)
RESELECTION_EXIT_NONE = "none"
RESELECTION_EXIT_NEXT_OPEN_NOT_RESELECTED = "next_open_not_reselected"
RESELECTION_EXIT_NEXT_OPEN_WHEN_SLOT_NEEDED = "next_open_not_reselected_when_slot_needed"
RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED = "replace_weak_hold_when_slot_needed"
RESELECTION_EXIT_MODES = (
    RESELECTION_EXIT_NONE,
    RESELECTION_EXIT_NEXT_OPEN_NOT_RESELECTED,
    RESELECTION_EXIT_NEXT_OPEN_WHEN_SLOT_NEEDED,
    RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED,
)
REPLACEMENT_SCORE_ENTRY = "entry_score"
REPLACEMENT_SCORE_ENTRY_PLUS_RS = "entry_plus_rs"
REPLACEMENT_SCORE_ENTRY_PLUS_OVERLAY = "entry_plus_overlay"
REPLACEMENT_SCORE_ENTRY_PLUS_QUALITY = "entry_plus_quality"
REPLACEMENT_SCORE_MODES = (
    REPLACEMENT_SCORE_ENTRY,
    REPLACEMENT_SCORE_ENTRY_PLUS_RS,
    REPLACEMENT_SCORE_ENTRY_PLUS_OVERLAY,
    REPLACEMENT_SCORE_ENTRY_PLUS_QUALITY,
)
REPLACEMENT_COMPARE_CANDIDATE_VS_HOLD = "candidate_vs_hold"
REPLACEMENT_COMPARE_CANDIDATE_VS_ENTRY = "candidate_vs_entry"
REPLACEMENT_COMPARE_CANDIDATE_VS_BLEND = "candidate_vs_blend"
REPLACEMENT_COMPARE_MODES = (
    REPLACEMENT_COMPARE_CANDIDATE_VS_HOLD,
    REPLACEMENT_COMPARE_CANDIDATE_VS_ENTRY,
    REPLACEMENT_COMPARE_CANDIDATE_VS_BLEND,
)
DEFAULT_SCORING_MODE = SCORING_MODE_RANKED_V2
DEFAULT_REGIME_AWARE_V2_FILTERS = True
DEFAULT_STRONG_REGIME_MIN_RANKED_SCORE = 7.0
DEFAULT_STRONG_REGIME_MAX_PULLBACK_PCT = 0.05
DEFAULT_STRONG_REGIME_MIN_RS20_RANK = 0.65
DEFAULT_STRONG_REGIME_MIN_OVERLAY_SCORE = 7.4
DEFAULT_STRONG_REGIME_MAX_ATR20_PCT = 0.08
DEFAULT_REPLACEMENT_MIN_HOLD_SCORE = 7.0
DEFAULT_REPLACEMENT_MIN_CANDIDATE_SCORE_MARGIN = 0.5
DEFAULT_REPLACEMENT_SCORE_MODE = REPLACEMENT_SCORE_ENTRY
DEFAULT_REPLACEMENT_COMPARE_MODE = REPLACEMENT_COMPARE_CANDIDATE_VS_HOLD
DEFAULT_WEAK_MAX_POSITIONS = 1
DEFAULT_NEUTRAL_MAX_POSITIONS = 2
DEFAULT_STRONG_MAX_POSITIONS = 3


@dataclass(frozen=True)
class BacktestParams:
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    hard_stop_pct: float = DEFAULT_HARD_STOP_PCT
    first_target_r: float = DEFAULT_FIRST_TARGET_R
    first_target_exit_pct: float = 0.50
    max_hold_days: int = 5
    fixed_slippage_bps: float = 2.0
    dynamic_slippage_bps: float = 5.0
    min_pullback_pct: float = DEFAULT_MIN_PULLBACK_PCT
    max_pullback_pct: float = DEFAULT_MAX_PULLBACK_PCT
    min_score: float = DEFAULT_MIN_SCORE
    symbol_cooldown_days: int = 0
    scoring_mode: str = DEFAULT_SCORING_MODE
    regime_aware_v2_filters: bool = DEFAULT_REGIME_AWARE_V2_FILTERS
    strong_regime_min_ranked_score: float = DEFAULT_STRONG_REGIME_MIN_RANKED_SCORE
    strong_regime_max_pullback_pct: float = DEFAULT_STRONG_REGIME_MAX_PULLBACK_PCT
    strong_regime_min_rs20_rank: float = DEFAULT_STRONG_REGIME_MIN_RS20_RANK
    strong_regime_min_overlay_score: float = DEFAULT_STRONG_REGIME_MIN_OVERLAY_SCORE
    strong_regime_max_atr20_pct: float = DEFAULT_STRONG_REGIME_MAX_ATR20_PCT
    reselection_exit_mode: str = RESELECTION_EXIT_NONE
    replacement_min_hold_score: float = DEFAULT_REPLACEMENT_MIN_HOLD_SCORE
    replacement_min_candidate_score_margin: float = DEFAULT_REPLACEMENT_MIN_CANDIDATE_SCORE_MARGIN
    replacement_score_mode: str = DEFAULT_REPLACEMENT_SCORE_MODE
    replacement_compare_mode: str = DEFAULT_REPLACEMENT_COMPARE_MODE
    weak_max_positions: int = DEFAULT_WEAK_MAX_POSITIONS
    neutral_max_positions: int = DEFAULT_NEUTRAL_MAX_POSITIONS
    strong_max_positions: int = DEFAULT_STRONG_MAX_POSITIONS


@dataclass(frozen=True)
class Candle:
    dt: datetime
    dt_et: datetime
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DailyBar:
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    candle_count: int


@dataclass(frozen=True)
class Candidate:
    symbol: str
    signal_date: date
    entry_date: date
    score: float
    strength_score: float
    pullback_score: float
    pullback_pct: float
    prev_close: float
    technical_stop: float
    regime: str
    notes: list[str]
    factor_data: dict[str, float | None] | None = None


@dataclass
class TradeState:
    symbol: str
    regime: str
    signal_date: date
    entry_date: date
    entry_dt: datetime
    entry_price: float
    initial_stop_price: float
    stop_price: float
    target_price: float
    shares: int
    remaining_shares: int
    score: float
    target_hit: bool
    realized_pnl: float
    exits: list[dict[str, Any]]
    max_exit_date: date


def parse_schwab_candles(payload: dict[str, Any], *, regular_hours_only: bool = True) -> list[Candle]:
    raw_payload = payload.get("payload", payload)
    candles = raw_payload.get("candles") or []
    parsed = []
    for raw in candles:
        if not isinstance(raw, dict):
            continue
        try:
            dt = parse_epoch(raw["datetime"])
            dt_et = dt.astimezone(EASTERN)
            if regular_hours_only and not (REGULAR_OPEN <= dt_et.time() < REGULAR_CLOSE):
                continue
            parsed.append(
                Candle(
                    dt=dt,
                    dt_et=dt_et,
                    session_date=dt_et.date(),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(parsed, key=lambda candle: candle.dt)


def parse_epoch(value: int | float) -> datetime:
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000.0
    return datetime.fromtimestamp(timestamp, tz=UTC)


def aggregate_daily(candles: list[Candle]) -> list[DailyBar]:
    by_date: dict[date, list[Candle]] = {}
    for candle in candles:
        by_date.setdefault(candle.session_date, []).append(candle)

    daily = []
    for session_date in sorted(by_date):
        day_candles = sorted(by_date[session_date], key=lambda candle: candle.dt)
        daily.append(
            DailyBar(
                session_date=session_date,
                open=day_candles[0].open,
                high=max(candle.high for candle in day_candles),
                low=min(candle.low for candle in day_candles),
                close=day_candles[-1].close,
                volume=sum(candle.volume for candle in day_candles),
                candle_count=len(day_candles),
            )
        )
    return daily


def group_candles_by_date(candles: list[Candle]) -> dict[date, list[Candle]]:
    grouped: dict[date, list[Candle]] = {}
    for candle in candles:
        grouped.setdefault(candle.session_date, []).append(candle)
    return {key: sorted(value, key=lambda candle: candle.dt) for key, value in grouped.items()}


def generate_market_regimes(spy_daily: list[DailyBar], qqq_daily: list[DailyBar]) -> dict[date, str]:
    spy_by_date = {bar.session_date: bar for bar in spy_daily}
    qqq_by_date = {bar.session_date: bar for bar in qqq_daily}
    dates = sorted(set(spy_by_date) & set(qqq_by_date))
    spy_closes = [spy_by_date[item].close for item in dates]
    qqq_closes = [qqq_by_date[item].close for item in dates]
    spy_sma20 = rolling_mean(spy_closes, 20)
    qqq_sma20 = rolling_mean(qqq_closes, 20)

    regimes: dict[date, str] = {}
    for idx, item in enumerate(dates):
        if idx < 20 or spy_sma20[idx] is None or qqq_sma20[idx] is None:
            regimes[item] = "neutral"
            continue

        qqq_ret5 = qqq_closes[idx] / qqq_closes[idx - 5] - 1.0 if idx >= 5 else 0.0
        spy_above = spy_closes[idx] > float(spy_sma20[idx])
        qqq_above = qqq_closes[idx] > float(qqq_sma20[idx])

        if spy_above and qqq_above and qqq_ret5 > 0:
            regimes[item] = "strong"
        elif (not spy_above and not qqq_above) or qqq_ret5 < -0.02:
            regimes[item] = "weak"
        else:
            regimes[item] = "neutral"
    return regimes


def generate_single_asset_regimes(daily: list[DailyBar]) -> dict[date, str]:
    dates = [bar.session_date for bar in daily]
    closes = [bar.close for bar in daily]
    sma20 = rolling_mean(closes, 20)
    regimes: dict[date, str] = {}
    for idx, session_date in enumerate(dates):
        if idx < 20 or sma20[idx] is None:
            regimes[session_date] = "neutral"
            continue
        ret5 = closes[idx] / closes[idx - 5] - 1.0 if idx >= 5 else 0.0
        above = closes[idx] > float(sma20[idx])
        if above and ret5 > 0:
            regimes[session_date] = "strong"
        elif (not above) or ret5 < -0.02:
            regimes[session_date] = "weak"
        else:
            regimes[session_date] = "neutral"
    return regimes


def latest_context_regimes(daily_by_symbol: dict[str, list[DailyBar]], symbols: tuple[str, ...]) -> dict[str, dict[str, str | None]]:
    output = {}
    for symbol in symbols:
        daily = daily_by_symbol.get(symbol, [])
        if not daily:
            continue
        regimes = generate_single_asset_regimes(daily)
        latest_date = max(regimes) if regimes else None
        output[symbol] = {
            "date": latest_date.isoformat() if latest_date else None,
            "regime": regimes.get(latest_date) if latest_date else None,
        }
    return output


def validate_scoring_mode(scoring_mode: str) -> str:
    if scoring_mode not in SCORING_MODES:
        raise ValueError(f"Unsupported scoring_mode={scoring_mode!r}; expected one of {SCORING_MODES}")
    return scoring_mode


def validate_reselection_exit_mode(mode: str) -> str:
    if mode not in RESELECTION_EXIT_MODES:
        raise ValueError(f"Unsupported reselection_exit_mode={mode!r}; expected one of {RESELECTION_EXIT_MODES}")
    return mode


def validate_replacement_score_mode(mode: str) -> str:
    if mode not in REPLACEMENT_SCORE_MODES:
        raise ValueError(f"Unsupported replacement_score_mode={mode!r}; expected one of {REPLACEMENT_SCORE_MODES}")
    return mode


def validate_replacement_compare_mode(mode: str) -> str:
    if mode not in REPLACEMENT_COMPARE_MODES:
        raise ValueError(f"Unsupported replacement_compare_mode={mode!r}; expected one of {REPLACEMENT_COMPARE_MODES}")
    return mode


def use_regime_aware_v2_filters(params: BacktestParams) -> bool:
    return bool(params.regime_aware_v2_filters and params.scoring_mode == SCORING_MODE_RANKED_V2)


def regime_policy_summary(params: BacktestParams) -> dict[str, Any]:
    if not use_regime_aware_v2_filters(params):
        return {"enabled": False}
    return {
        "enabled": True,
        "mode": "regime_aware_v2",
        "strong": {
            "min_ranked_score": params.strong_regime_min_ranked_score,
            "max_pullback_pct": params.strong_regime_max_pullback_pct,
            "min_rs20_rank": params.strong_regime_min_rs20_rank,
            "min_overlay_score": params.strong_regime_min_overlay_score,
            "max_atr20_pct": params.strong_regime_max_atr20_pct,
        },
        "neutral": "ranked_v2_normal_filters",
        "weak": "block_new_entries",
    }


def filter_candidates_for_regime_policy(candidates: list[Candidate], params: BacktestParams) -> list[Candidate]:
    if not use_regime_aware_v2_filters(params):
        return list(candidates)
    output = []
    for candidate in candidates:
        rejection_reasons = regime_policy_rejection_reasons(
            regime=candidate.regime,
            score=candidate.score,
            pullback_pct=candidate.pullback_pct,
            factors=candidate.factor_data or {},
            params=params,
        )
        if rejection_reasons:
            continue
        notes = list(candidate.notes)
        if candidate.regime == "strong":
            notes.append("regime-aware v2 strong filter")
        elif candidate.regime == "neutral":
            notes.append("regime-aware v2 neutral filter")
        output.append(replace(candidate, notes=notes))
    return output


def filter_watchlist_for_regime_policy(watchlist: list[dict[str, Any]], params: BacktestParams) -> list[dict[str, Any]]:
    if not use_regime_aware_v2_filters(params):
        return list(watchlist)
    output = []
    for item in watchlist:
        rejection_reasons = regime_policy_rejection_reasons(
            regime=str(item.get("market_regime") or "neutral"),
            score=safe_numeric(item.get("total_score"), default=float("-inf")),
            pullback_pct=safe_numeric(item.get("pullback_pct"), default=float("inf")),
            factors=item.get("factor_data") or {},
            params=params,
        )
        if rejection_reasons:
            continue
        updated = dict(item)
        risk_notes = list(updated.get("risk_notes") or [])
        if updated.get("market_regime") == "strong":
            updated["regime_policy"] = "regime_aware_v2_strong"
            risk_notes.append("regime_aware_v2_strong_filter")
        else:
            updated["regime_policy"] = "regime_aware_v2_neutral"
            risk_notes.append("regime_aware_v2_neutral_filter")
        updated["risk_notes"] = risk_notes
        output.append(updated)
    return output


def regime_policy_rejection_reasons(
    *,
    regime: str,
    score: float,
    pullback_pct: float,
    factors: dict[str, Any],
    params: BacktestParams,
) -> list[str]:
    if not use_regime_aware_v2_filters(params):
        return []
    if regime == "weak":
        return ["weak_regime_block_new_entries"]
    if regime != "strong":
        return []

    reasons = []
    if score < params.strong_regime_min_ranked_score:
        reasons.append("strong_ranked_score_below_min")
    if pullback_pct > params.strong_regime_max_pullback_pct:
        reasons.append("strong_pullback_above_max")
    if safe_numeric(factors.get("ranked_v2_rs_20d_rank"), default=float("-inf")) < params.strong_regime_min_rs20_rank:
        reasons.append("strong_rs20_rank_below_min")
    if safe_numeric(factors.get("ranked_v2_overlay_score"), default=float("-inf")) < params.strong_regime_min_overlay_score:
        reasons.append("strong_overlay_below_min")
    atr20_pct = safe_numeric(factors.get("atr20_pct"), default=None)
    if atr20_pct is not None and atr20_pct > params.strong_regime_max_atr20_pct:
        reasons.append("strong_atr20_above_max")
    return reasons


def safe_numeric(value: Any, *, default: float | None) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def build_scoring_factor_data(
    *,
    daily: list[DailyBar],
    idx: int,
    qqq_return20_by_date: dict[date, float],
    qqq_return60_by_date: dict[date, float],
    recent_high: float,
    volume5_value: float | None,
    sma20_value: float | None,
    classic_score: float,
) -> dict[str, float | None]:
    bar = daily[idx]
    closes = [item.close for item in daily]
    highs = [item.high for item in daily]
    symbol_ret20 = lookback_return(closes, idx, 20)
    symbol_ret60 = lookback_return(closes, idx, 60)
    high20 = max(highs[max(0, idx - 19) : idx + 1])
    atr20 = average_true_range(daily, idx, 20)
    pullback_atr = (recent_high - bar.close) / atr20 if atr20 and atr20 > 0 else None
    close_range = bar.high - bar.low
    close_location = (bar.close - bar.low) / close_range if close_range > 0 else 0.5
    return {
        "classic_score": round(classic_score, 5),
        "rs_20d": round(symbol_ret20 - qqq_return20_by_date.get(bar.session_date, 0.0), 6)
        if symbol_ret20 is not None
        else None,
        "rs_60d": round(symbol_ret60 - qqq_return60_by_date.get(bar.session_date, 0.0), 6)
        if symbol_ret60 is not None
        else None,
        "sma20_distance_pct": round(bar.close / float(sma20_value) - 1.0, 6)
        if sma20_value and sma20_value > 0
        else None,
        "near_20d_high": round(bar.close / high20, 6) if high20 > 0 else None,
        "atr20_pct": round(atr20 / bar.close, 6) if atr20 and bar.close > 0 else None,
        "pullback_atr": round(pullback_atr, 6) if pullback_atr is not None else None,
        "volume_ratio_5d": round(bar.volume / float(volume5_value), 6)
        if volume5_value and volume5_value > 0
        else None,
        "close_location": round(clamp(close_location, 0.0, 1.0), 6),
    }


def lookback_return(values: list[float], idx: int, lookback: int) -> float | None:
    if idx < lookback:
        return None
    previous = values[idx - lookback]
    if previous <= 0:
        return None
    return values[idx] / previous - 1.0


def average_true_range(daily: list[DailyBar], idx: int, window: int) -> float | None:
    if idx <= 0:
        return None
    start = max(1, idx - window + 1)
    ranges = []
    for cursor in range(start, idx + 1):
        bar = daily[cursor]
        previous_close = daily[cursor - 1].close
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return sum(ranges) / len(ranges) if ranges else None


def apply_candidate_scoring_mode(candidates: list[Candidate], scoring_mode: str) -> list[Candidate]:
    scoring_mode = validate_scoring_mode(scoring_mode)
    if scoring_mode == SCORING_MODE_CLASSIC:
        return list(candidates)

    score_rows = build_score_rows_for_mode(
        [candidate.factor_data or {} for candidate in candidates],
        scoring_mode=scoring_mode,
    )
    ranked_candidates = []
    for candidate, score_row in zip(candidates, score_rows, strict=True):
        factor_data = dict(candidate.factor_data or {})
        factor_data[f"{scoring_mode}_score"] = score_row["score"]
        for key, value in score_row["components"].items():
            factor_data[f"{scoring_mode}_{key}"] = value
        notes = list(candidate.notes)
        notes.append(f"{scoring_mode} cross-sectional rank overlay")
        ranked_candidates.append(
            replace(
                candidate,
                score=score_row["score"],
                notes=notes,
                factor_data=factor_data,
            )
        )
    return ranked_candidates


def apply_watchlist_scoring_mode(watchlist: list[dict[str, Any]], scoring_mode: str) -> list[dict[str, Any]]:
    scoring_mode = validate_scoring_mode(scoring_mode)
    if scoring_mode == SCORING_MODE_CLASSIC:
        return [dict(item, scoring_mode=SCORING_MODE_CLASSIC) for item in watchlist]

    score_rows = build_score_rows_for_mode(
        [item.get("factor_data") or {} for item in watchlist],
        scoring_mode=scoring_mode,
    )
    output = []
    for item, score_row in zip(watchlist, score_rows, strict=True):
        updated = dict(item)
        updated["classic_total_score"] = item.get("classic_total_score", item.get("total_score"))
        updated["total_score"] = score_row["score"]
        updated["scoring_mode"] = scoring_mode
        updated["ranking_components"] = score_row["components"]
        factor_data = dict(updated.get("factor_data") or {})
        factor_data[f"{scoring_mode}_score"] = score_row["score"]
        updated["factor_data"] = factor_data
        risk_notes = list(updated.get("risk_notes") or [])
        risk_notes.append(f"{scoring_mode}_cross_sectional_score")
        updated["risk_notes"] = risk_notes
        output.append(updated)
    return output


def build_score_rows_for_mode(
    factors_by_item: list[dict[str, float | None]],
    *,
    scoring_mode: str,
) -> list[dict[str, Any]]:
    if scoring_mode == SCORING_MODE_RANKED_V1:
        return build_ranked_v1_score_rows(factors_by_item)
    if scoring_mode == SCORING_MODE_RANKED_V2:
        return build_ranked_v2_score_rows(factors_by_item)
    raise ValueError(f"Unsupported ranked scoring mode={scoring_mode!r}")


def build_ranked_v1_score_rows(factors_by_item: list[dict[str, float | None]]) -> list[dict[str, Any]]:
    factor_values = {
        "rs_20d": numeric_factor_values(factors_by_item, "rs_20d"),
        "rs_60d": numeric_factor_values(factors_by_item, "rs_60d"),
        "sma20_distance_pct": numeric_factor_values(factors_by_item, "sma20_distance_pct"),
        "near_20d_high": numeric_factor_values(factors_by_item, "near_20d_high"),
        "volume_ratio_5d": numeric_factor_values(factors_by_item, "volume_ratio_5d"),
        "close_location": numeric_factor_values(factors_by_item, "close_location"),
    }
    rows = []
    for factors in factors_by_item:
        components = {
            "rs_20d_rank": percentile_rank(
                factors.get("rs_20d"),
                factor_values["rs_20d"],
                higher_better=True,
            ),
            "rs_60d_rank": percentile_rank(
                factors.get("rs_60d"),
                factor_values["rs_60d"],
                higher_better=True,
            ),
            "sma20_distance_rank": percentile_rank(
                factors.get("sma20_distance_pct"),
                factor_values["sma20_distance_pct"],
                higher_better=True,
            ),
            "near_20d_high_rank": percentile_rank(
                factors.get("near_20d_high"),
                factor_values["near_20d_high"],
                higher_better=True,
            ),
            "atr_pullback_quality": atr_pullback_quality(factors.get("pullback_atr")),
            "volume_contraction_rank": percentile_rank(
                factors.get("volume_ratio_5d"),
                factor_values["volume_ratio_5d"],
                higher_better=False,
            ),
            "close_location_rank": percentile_rank(
                factors.get("close_location"),
                factor_values["close_location"],
                higher_better=True,
            ),
        }
        overlay_score = (
            components["rs_20d_rank"] * 3.0
            + components["rs_60d_rank"] * 1.5
            + components["sma20_distance_rank"] * 1.0
            + components["near_20d_high_rank"] * 1.0
            + components["atr_pullback_quality"] * 1.5
            + components["volume_contraction_rank"] * 1.0
            + components["close_location_rank"] * 1.0
        )
        classic_score = factors.get("classic_score")
        if isinstance(classic_score, (int, float)) and math.isfinite(float(classic_score)):
            score = float(classic_score) + (overlay_score - 5.0) * 0.15
        else:
            score = overlay_score
        components["overlay_score"] = clamp(overlay_score, 0.0, 10.0)
        rows.append(
            {
                "score": round(clamp(score, 0.0, 10.0), 3),
                "components": {
                    key: round(value, 4)
                    for key, value in components.items()
                },
            }
        )
    return rows


def build_ranked_v2_score_rows(factors_by_item: list[dict[str, float | None]]) -> list[dict[str, Any]]:
    factor_values = {
        "rs_20d": numeric_factor_values(factors_by_item, "rs_20d"),
        "rs_60d": numeric_factor_values(factors_by_item, "rs_60d"),
        "sma20_distance_pct": numeric_factor_values(factors_by_item, "sma20_distance_pct"),
    }
    rows = []
    for factors in factors_by_item:
        components = {
            "rs_20d_rank": percentile_rank(
                factors.get("rs_20d"),
                factor_values["rs_20d"],
                higher_better=True,
            ),
            "rs_60d_rank": percentile_rank(
                factors.get("rs_60d"),
                factor_values["rs_60d"],
                higher_better=True,
            ),
            "sma20_distance_rank": percentile_rank(
                factors.get("sma20_distance_pct"),
                factor_values["sma20_distance_pct"],
                higher_better=True,
            ),
        }
        overlay_score = (
            components["rs_20d_rank"] * 3.0
            + components["rs_60d_rank"] * 2.0
            + components["sma20_distance_rank"] * 1.0
        ) / 6.0 * 10.0
        classic_score = factors.get("classic_score")
        if isinstance(classic_score, (int, float)) and math.isfinite(float(classic_score)):
            score = float(classic_score) + (overlay_score - 5.0) * 0.20
        else:
            score = overlay_score
        components["overlay_score"] = clamp(overlay_score, 0.0, 10.0)
        rows.append(
            {
                "score": round(clamp(score, 0.0, 10.0), 3),
                "components": {
                    key: round(value, 4)
                    for key, value in components.items()
                },
            }
        )
    return rows


def numeric_factor_values(factors_by_item: list[dict[str, float | None]], key: str) -> list[float]:
    values = []
    for factors in factors_by_item:
        value = factors.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def percentile_rank(value: float | None, values: list[float], *, higher_better: bool) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not values:
        return 0.5
    sorted_values = sorted(values)
    if len(sorted_values) == 1 or sorted_values[0] == sorted_values[-1]:
        return 0.5
    numeric_value = float(value)
    equal_positions = [
        idx for idx, item in enumerate(sorted_values)
        if item == numeric_value
    ]
    if equal_positions:
        average_position = sum(equal_positions) / len(equal_positions)
    else:
        lower_count = sum(1 for item in sorted_values if item < numeric_value)
        average_position = lower_count
    percentile = average_position / (len(sorted_values) - 1)
    percentile = clamp(percentile, 0.0, 1.0)
    return percentile if higher_better else 1.0 - percentile


def atr_pullback_quality(value: float | None) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return 0.5
    ideal = 1.2
    tolerance = 1.2
    return clamp(1.0 - abs(float(value) - ideal) / tolerance, 0.0, 1.0)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def generate_candidates(
    symbol: str,
    daily: list[DailyBar],
    qqq_daily: list[DailyBar],
    regimes: dict[date, str],
    params: BacktestParams,
    earnings_calendar: EarningsCalendar | None = None,
) -> list[Candidate]:
    if len(daily) < 25:
        return []

    closes = [bar.close for bar in daily]
    highs = [bar.high for bar in daily]
    volumes = [bar.volume for bar in daily]
    sma5 = rolling_mean(closes, 5)
    sma10 = rolling_mean(closes, 10)
    sma20 = rolling_mean(closes, 20)
    volume5 = rolling_mean(volumes, 5)
    qqq_return_by_date = return_by_date(qqq_daily, 10)
    qqq_return20_by_date = return_by_date(qqq_daily, 20)
    qqq_return60_by_date = return_by_date(qqq_daily, 60)
    trading_dates = [bar.session_date for bar in daily]
    blocked_entry_dates = (
        earnings_calendar.blocked_entry_dates(symbol, trading_dates)
        if earnings_calendar is not None
        else set()
    )

    candidates = []
    for idx in range(21, len(daily) - 1):
        bar = daily[idx]
        next_bar = daily[idx + 1]
        if next_bar.session_date in blocked_entry_dates:
            continue
        if sma5[idx] is None or sma10[idx] is None or sma20[idx] is None:
            continue

        recent_high = max(highs[max(0, idx - 4) : idx + 1])
        if recent_high <= 0:
            continue
        pullback_pct = (recent_high - bar.close) / recent_high
        if not (params.min_pullback_pct <= pullback_pct <= params.max_pullback_pct):
            continue

        down_days = consecutive_down_days(daily, idx)
        symbol_ret10 = closes[idx] / closes[idx - 10] - 1.0 if idx >= 10 else 0.0
        qqq_ret10 = qqq_return_by_date.get(bar.session_date, 0.0)

        strength_score = 0.0
        notes: list[str] = []
        if bar.close > float(sma20[idx]):
            strength_score += 1
        if float(sma5[idx]) > float(sma10[idx]) > float(sma20[idx]):
            strength_score += 1
        if symbol_ret10 > 0:
            strength_score += 1
        if symbol_ret10 > qqq_ret10:
            strength_score += 1
        if bar.close > closes[idx - 20]:
            strength_score += 1

        pullback_score = 0.0
        pullback_score += 1.5
        if 1 <= down_days <= 3:
            pullback_score += 1
        if bar.close >= float(sma10[idx]) * 0.985:
            pullback_score += 1
        if volume5[idx] is not None and bar.volume <= float(volume5[idx]) * 1.10:
            pullback_score += 1
        if bar.close > bar.low + (bar.high - bar.low) * 0.35:
            pullback_score += 0.5

        score = strength_score + pullback_score
        regime = regimes.get(bar.session_date, "neutral")
        if regime == "weak":
            score -= 0.75
            notes.append("weak market regime penalty")

        if score < params.min_score:
            continue

        factor_data = build_scoring_factor_data(
            daily=daily,
            idx=idx,
            qqq_return20_by_date=qqq_return20_by_date,
            qqq_return60_by_date=qqq_return60_by_date,
            recent_high=recent_high,
            volume5_value=volume5[idx],
            sma20_value=sma20[idx],
            classic_score=score,
        )

        candidates.append(
            Candidate(
                symbol=symbol,
                signal_date=bar.session_date,
                entry_date=next_bar.session_date,
                score=round(score, 3),
                strength_score=round(strength_score, 3),
                pullback_score=round(pullback_score, 3),
                pullback_pct=round(pullback_pct, 5),
                prev_close=bar.close,
                technical_stop=bar.low,
                regime=regime,
                notes=notes,
                factor_data=factor_data,
            )
        )
    return candidates


def run_backtest(
    history_by_symbol: dict[str, dict[str, Any]],
    *,
    symbols: tuple[str, ...],
    params: BacktestParams,
    earnings_calendar: EarningsCalendar | None = None,
) -> dict[str, Any]:
    validate_scoring_mode(params.scoring_mode)
    validate_reselection_exit_mode(params.reselection_exit_mode)
    validate_replacement_score_mode(params.replacement_score_mode)
    validate_replacement_compare_mode(params.replacement_compare_mode)
    candles_by_symbol = {
        symbol: parse_schwab_candles(history_by_symbol[symbol], regular_hours_only=True)
        for symbol in symbols
        if symbol in history_by_symbol
    }
    daily_by_symbol = {
        symbol: aggregate_daily(candles)
        for symbol, candles in candles_by_symbol.items()
    }
    intraday_by_symbol = {
        symbol: group_candles_by_date(candles)
        for symbol, candles in candles_by_symbol.items()
    }
    earnings_exit_dates = (
        {
            symbol: earnings_calendar.exit_dates(
                symbol,
                [bar.session_date for bar in daily_by_symbol.get(symbol, [])],
            )
            for symbol in symbols
        }
        if earnings_calendar is not None
        else {}
    )

    if "SPY" not in daily_by_symbol or "QQQ" not in daily_by_symbol:
        raise ValueError("Backtest requires SPY and QQQ histories for market regime labels.")

    regimes = generate_market_regimes(daily_by_symbol["SPY"], daily_by_symbol["QQQ"])

    candidates_by_entry_date: dict[date, list[Candidate]] = {}
    all_candidates: list[Candidate] = []
    for symbol in symbols:
        if symbol in NON_TRADABLE_CONTEXT_SYMBOLS:
            continue
        candidates = generate_candidates(
            symbol,
            daily_by_symbol.get(symbol, []),
            daily_by_symbol["QQQ"],
            regimes,
            params,
            earnings_calendar,
        )
        all_candidates.extend(candidates)
        for candidate in candidates:
            candidates_by_entry_date.setdefault(candidate.entry_date, []).append(candidate)

    if params.scoring_mode != SCORING_MODE_CLASSIC:
        candidates_by_entry_date = {
            entry_date: apply_candidate_scoring_mode(candidates, params.scoring_mode)
            for entry_date, candidates in candidates_by_entry_date.items()
        }
    pre_policy_candidate_count = sum(len(candidates) for candidates in candidates_by_entry_date.values())
    candidates_by_entry_date = {
        entry_date: filter_candidates_for_regime_policy(candidates, params)
        for entry_date, candidates in candidates_by_entry_date.items()
    }
    all_candidates = [
        candidate
        for candidates in candidates_by_entry_date.values()
        for candidate in candidates
    ]
    selected_symbols_by_entry_date = {
        entry_date: {candidate.symbol for candidate in candidates}
        for entry_date, candidates in candidates_by_entry_date.items()
    }

    all_dates = sorted(
        set().union(*(set(grouped) for grouped in intraday_by_symbol.values()))
    )
    equity = params.initial_equity
    open_trades: list[TradeState] = []
    closed_trades: list[dict[str, Any]] = []
    equity_events: list[dict[str, Any]] = [
        {"date": all_dates[0].isoformat() if all_dates else None, "equity": equity}
    ]
    diagnostics = {
        "candidate_count": len(all_candidates),
        "entry_attempts": 0,
        "filled_entries": 0,
        "unfilled_entries": 0,
        "skipped_no_slot": 0,
        "skipped_duplicate_symbol": 0,
        "skipped_symbol_cooldown": 0,
        "skipped_missing_intraday": 0,
        "candidate_count_before_regime_policy": pre_policy_candidate_count,
        "skipped_regime_policy": pre_policy_candidate_count - len(all_candidates),
        "reselection_exit_count": 0,
        "reselection_exit_pnl": 0.0,
        "skipped_reselection_exit_missing_intraday": 0,
    }
    blocked_symbols_until: dict[str, date] = {}

    for session_date in all_dates:
        regime = regimes.get(previous_available_date(regimes, session_date), "neutral")
        open_trades = [trade for trade in open_trades if trade.remaining_shares > 0]
        todays_candidates = sorted(
            candidates_by_entry_date.get(session_date, []),
            key=lambda candidate: candidate.score,
            reverse=True,
        )

        if params.reselection_exit_mode in {
            RESELECTION_EXIT_NEXT_OPEN_NOT_RESELECTED,
            RESELECTION_EXIT_NEXT_OPEN_WHEN_SLOT_NEEDED,
            RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED,
        }:
            still_open_after_reselection = []
            selected_symbols = selected_symbols_by_entry_date.get(session_date, set())
            exit_decisions = None
            if params.reselection_exit_mode == RESELECTION_EXIT_NEXT_OPEN_WHEN_SLOT_NEEDED:
                exit_decisions = {
                    symbol: {"replacement_mode": params.reselection_exit_mode}
                    for symbol in reselection_exit_symbols_for_slot_need(
                        open_trades=open_trades,
                        todays_candidates=todays_candidates,
                        blocked_symbols_until=blocked_symbols_until,
                        session_date=session_date,
                        max_positions=max_position_count(regime, params),
                    )
                }
            elif params.reselection_exit_mode == RESELECTION_EXIT_REPLACE_WEAK_HOLD_WHEN_SLOT_NEEDED:
                hold_scores = {
                    trade.symbol: score_hold_position(
                        trade=trade,
                        daily=daily_by_symbol.get(trade.symbol, []),
                        qqq_daily=daily_by_symbol["QQQ"],
                        session_date=session_date,
                    )
                    for trade in open_trades
                }
                exit_decisions = reselection_exit_decisions_for_weak_holds(
                    open_trades=open_trades,
                    todays_candidates=todays_candidates,
                    blocked_symbols_until=blocked_symbols_until,
                    hold_scores=hold_scores,
                    session_date=session_date,
                    max_positions=max_position_count(regime, params),
                    params=params,
                )
            for trade in open_trades:
                stale = session_date > trade.entry_date and trade.symbol not in selected_symbols
                if not stale:
                    still_open_after_reselection.append(trade)
                    continue
                if exit_decisions is not None and trade.symbol not in exit_decisions:
                    still_open_after_reselection.append(trade)
                    continue
                day_candles = intraday_by_symbol.get(trade.symbol, {}).get(session_date, [])
                if not day_candles:
                    diagnostics["skipped_reselection_exit_missing_intraday"] += 1
                    still_open_after_reselection.append(trade)
                    continue
                realized_before = trade.realized_pnl
                exit_trade(trade, day_candles[0].open, session_date, "reselection_exit", params)
                realized = trade.realized_pnl - realized_before
                equity += realized
                diagnostics["reselection_exit_count"] += 1
                diagnostics["reselection_exit_pnl"] = round(
                    float(diagnostics["reselection_exit_pnl"]) + realized,
                    2,
                )
                equity_events.append({"date": session_date.isoformat(), "equity": round(equity, 2)})
                decision = (exit_decisions or {}).get(trade.symbol) or {"replacement_mode": params.reselection_exit_mode}
                if trade.exits:
                    trade.exits[-1].update(
                        {
                            key: value
                            for key, value in decision.items()
                            if value is not None
                        }
                    )
                update_symbol_cooldown(
                    blocked_symbols_until,
                    symbol=trade.symbol,
                    trading_dates=[bar.session_date for bar in daily_by_symbol.get(trade.symbol, [])],
                    exit_date=session_date,
                    cooldown_days=params.symbol_cooldown_days,
                )
                closed_trades.append(serialize_trade(trade))
            open_trades = still_open_after_reselection
        slots = max_position_count(regime, params) - len(open_trades)
        if slots <= 0:
            diagnostics["skipped_no_slot"] += len(todays_candidates)
        else:
            for idx, candidate in enumerate(todays_candidates):
                if slots <= 0:
                    diagnostics["skipped_no_slot"] += len(todays_candidates) - idx
                    break
                if any(trade.symbol == candidate.symbol for trade in open_trades):
                    diagnostics["skipped_duplicate_symbol"] += 1
                    continue
                blocked_until = blocked_symbols_until.get(candidate.symbol)
                if blocked_until is not None and session_date <= blocked_until:
                    diagnostics["skipped_symbol_cooldown"] += 1
                    continue
                entry_candles = intraday_by_symbol.get(candidate.symbol, {}).get(session_date, [])
                diagnostics["entry_attempts"] += 1
                if not entry_candles:
                    diagnostics["skipped_missing_intraday"] += 1
                    diagnostics["unfilled_entries"] += 1
                    continue
                entry = build_trade_from_candidate(
                    candidate,
                    entry_candles,
                    daily_by_symbol[candidate.symbol],
                    equity,
                    params,
                )
                if entry is None:
                    diagnostics["unfilled_entries"] += 1
                    continue
                if regime == "weak":
                    entry.shares = max(1, math.floor(entry.shares / 2))
                    entry.remaining_shares = entry.shares
                open_trades.append(entry)
                diagnostics["filled_entries"] += 1
                slots -= 1

        still_open: list[TradeState] = []
        for trade in open_trades:
            day_candles = intraday_by_symbol.get(trade.symbol, {}).get(session_date, [])
            realized = advance_trade_one_day(
                trade,
                day_candles,
                session_date,
                params,
                earnings_exit_dates=earnings_exit_dates.get(trade.symbol, set()),
            )
            if realized:
                equity += realized
                equity_events.append({"date": session_date.isoformat(), "equity": round(equity, 2)})
            if trade.remaining_shares > 0:
                still_open.append(trade)
            else:
                update_symbol_cooldown(
                    blocked_symbols_until,
                    symbol=trade.symbol,
                    trading_dates=[bar.session_date for bar in daily_by_symbol.get(trade.symbol, [])],
                    exit_date=session_date,
                    cooldown_days=params.symbol_cooldown_days,
                )
                closed_trades.append(serialize_trade(trade))
        open_trades = still_open

    if all_dates:
        final_date = all_dates[-1]
        for trade in open_trades:
            day_candles = intraday_by_symbol.get(trade.symbol, {}).get(final_date, [])
            if day_candles and trade.remaining_shares > 0:
                exit_trade(trade, day_candles[-1].close, final_date, "end_of_data", params)
                equity += trade.exits[-1]["pnl"]
                equity_events.append({"date": final_date.isoformat(), "equity": round(equity, 2)})
                closed_trades.append(serialize_trade(trade))

    return {
        "config": {
            "symbols": list(symbols),
            "params": asdict(params),
            "regime_policy": regime_policy_summary(params),
            "earnings_filter": earnings_calendar.summary() if earnings_calendar else None,
        },
        "data": summarize_data(daily_by_symbol),
        "context_regimes": latest_context_regimes(daily_by_symbol, ("XLK", "SMH")),
        "candidate_count": len(all_candidates),
        "diagnostics": diagnostics,
        "trades": closed_trades,
        "summary": summarize_trades(closed_trades, params.initial_equity, equity_events),
        "equity_events": equity_events,
    }


def build_trade_from_candidate(
    candidate: Candidate,
    entry_candles: list[Candle],
    daily: list[DailyBar],
    equity: float,
    params: BacktestParams,
) -> TradeState | None:
    entry_signal = find_intraday_entry(entry_candles, params)
    if entry_signal is None:
        return None

    entry_candle, entry_price = entry_signal
    hard_stop = entry_price * (1.0 - params.hard_stop_pct)
    technical_stop = candidate.technical_stop
    stop_price = max(hard_stop, technical_stop) if technical_stop < entry_price else hard_stop
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return None

    risk_shares = math.floor((equity * params.risk_per_trade_pct) / risk_per_share)
    cap_shares = math.floor((equity * params.max_position_pct) / entry_price)
    shares = min(risk_shares, cap_shares)
    if shares <= 0:
        return None

    max_exit_date = max_hold_date(daily, candidate.entry_date, params.max_hold_days)
    return TradeState(
        symbol=candidate.symbol,
        regime=candidate.regime,
        signal_date=candidate.signal_date,
        entry_date=candidate.entry_date,
        entry_dt=entry_candle.dt,
        entry_price=round(entry_price, 4),
        initial_stop_price=round(stop_price, 4),
        stop_price=round(stop_price, 4),
        target_price=round(entry_price + risk_per_share * params.first_target_r, 4),
        shares=shares,
        remaining_shares=shares,
        score=candidate.score,
        target_hit=False,
        realized_pnl=0.0,
        exits=[],
        max_exit_date=max_exit_date,
    )


def find_intraday_entry(candles: list[Candle], params: BacktestParams) -> tuple[Candle, float] | None:
    if len(candles) < 3:
        return None

    cumulative_volume = 0.0
    cumulative_price_volume = 0.0
    previous = candles[0]
    for idx, candle in enumerate(candles):
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        cumulative_volume += candle.volume
        cumulative_price_volume += typical_price * candle.volume
        if idx < 2 or cumulative_volume <= 0:
            previous = candle
            continue

        running_vwap = cumulative_price_volume / cumulative_volume
        if candle.close > running_vwap and candle.close > previous.high:
            entry_price = candle.close * (1.0 + params.fixed_slippage_bps / 10_000.0)
            return candle, entry_price
        previous = candle
    return None


def advance_trade_one_day(
    trade: TradeState,
    day_candles: list[Candle],
    session_date: date,
    params: BacktestParams,
    *,
    earnings_exit_dates: set[date] | None = None,
) -> float:
    if not day_candles or trade.remaining_shares <= 0:
        return 0.0

    start_idx = 0
    if session_date == trade.entry_date:
        for idx, candle in enumerate(day_candles):
            if candle.dt >= trade.entry_dt:
                start_idx = idx + 1
                break

    realized_before = trade.realized_pnl
    for candle in day_candles[start_idx:]:
        if trade.remaining_shares <= 0:
            break

        if candle.low <= trade.stop_price:
            exit_trade(trade, trade.stop_price, session_date, "stop", params)
            break

        if not trade.target_hit and candle.high >= trade.target_price:
            exit_shares = max(1, math.floor(trade.shares * params.first_target_exit_pct))
            exit_shares = min(exit_shares, trade.remaining_shares)
            exit_partial(trade, exit_shares, trade.target_price, session_date, "target_1", params)
            trade.target_hit = True
            trade.stop_price = trade.entry_price

    if trade.remaining_shares > 0 and session_date >= trade.max_exit_date:
        exit_trade(trade, day_candles[-1].close, session_date, "time_exit", params)

    if trade.remaining_shares > 0 and earnings_exit_dates and session_date in earnings_exit_dates:
        exit_trade(trade, day_candles[-1].close, session_date, "earnings_exit", params)

    return trade.realized_pnl - realized_before


def exit_partial(
    trade: TradeState,
    shares: int,
    price: float,
    session_date: date,
    reason: str,
    params: BacktestParams,
) -> None:
    fill_price = price * (1.0 - params.fixed_slippage_bps / 10_000.0)
    pnl = (fill_price - trade.entry_price) * shares
    trade.remaining_shares -= shares
    trade.realized_pnl += pnl
    trade.exits.append(
        {
            "date": session_date.isoformat(),
            "reason": reason,
            "shares": shares,
            "price": round(fill_price, 4),
            "pnl": round(pnl, 2),
        }
    )


def exit_trade(
    trade: TradeState,
    price: float,
    session_date: date,
    reason: str,
    params: BacktestParams,
) -> None:
    if trade.remaining_shares <= 0:
        return
    exit_partial(trade, trade.remaining_shares, price, session_date, reason, params)


def reselection_exit_symbols_for_slot_need(
    *,
    open_trades: list[TradeState],
    todays_candidates: list[Candidate],
    blocked_symbols_until: dict[str, date],
    session_date: date,
    max_positions: int,
) -> set[str]:
    open_symbols = {trade.symbol for trade in open_trades}
    selected_symbols = {candidate.symbol for candidate in todays_candidates}
    current_slots = max(0, max_positions - len(open_trades))
    eligible_new_symbols = []
    for candidate in todays_candidates:
        if candidate.symbol in open_symbols:
            continue
        blocked_until = blocked_symbols_until.get(candidate.symbol)
        if blocked_until is not None and session_date <= blocked_until:
            continue
        if candidate.symbol not in eligible_new_symbols:
            eligible_new_symbols.append(candidate.symbol)
    slots_to_free = max(0, len(eligible_new_symbols) - current_slots)
    if slots_to_free <= 0:
        return set()
    stale_trades = [
        trade
        for trade in open_trades
        if session_date > trade.entry_date and trade.symbol not in selected_symbols
    ]
    ranked_stale = sorted(stale_trades, key=lambda trade: (trade.score, trade.entry_date, trade.symbol))
    return {trade.symbol for trade in ranked_stale[:slots_to_free]}


def reselection_exit_decisions_for_weak_holds(
    *,
    open_trades: list[TradeState],
    todays_candidates: list[Candidate],
    blocked_symbols_until: dict[str, date],
    hold_scores: dict[str, dict[str, Any] | None],
    session_date: date,
    max_positions: int,
    params: BacktestParams,
) -> dict[str, dict[str, Any]]:
    open_symbols = {trade.symbol for trade in open_trades}
    selected_symbols = {candidate.symbol for candidate in todays_candidates}
    current_slots = max(0, max_positions - len(open_trades))
    eligible_new_candidates = []
    for candidate in todays_candidates:
        if candidate.symbol in open_symbols:
            continue
        blocked_until = blocked_symbols_until.get(candidate.symbol)
        if blocked_until is not None and session_date <= blocked_until:
            continue
        eligible_new_candidates.append(candidate)

    candidates_needing_slots = eligible_new_candidates[current_slots:]
    if not candidates_needing_slots:
        return {}

    stale_trades = [
        trade
        for trade in open_trades
        if session_date > trade.entry_date and trade.symbol not in selected_symbols
    ]
    ranked_stale = sorted(
        stale_trades,
        key=lambda trade: (
            hold_scores.get(trade.symbol, {}).get("score") if hold_scores.get(trade.symbol) else float("inf"),
            trade.score,
            trade.entry_date,
            trade.symbol,
        ),
    )
    decisions: dict[str, dict[str, Any]] = {}
    for trade, candidate in zip(ranked_stale, candidates_needing_slots, strict=False):
        hold_score = hold_scores.get(trade.symbol)
        if not hold_score:
            continue
        score = as_float(hold_score.get("score"))
        candidate_score = replacement_candidate_score(candidate, mode=params.replacement_score_mode)
        comparison_baseline = replacement_comparison_baseline(
            trade=trade,
            hold_score=score,
            mode=params.replacement_compare_mode,
        )
        candidate_advantage = candidate_score - comparison_baseline
        if score > params.replacement_min_hold_score:
            continue
        if candidate_advantage < params.replacement_min_candidate_score_margin:
            continue
        decisions[trade.symbol] = {
            "replacement_mode": params.reselection_exit_mode,
            "hold_score": round(score, 3),
            "hold_score_as_of_date": hold_score.get("as_of_date"),
            "hold_score_notes": ",".join(hold_score.get("notes") or []),
            "replacement_candidate_symbol": candidate.symbol,
            "replacement_candidate_score": round(candidate_score, 3),
            "replacement_candidate_entry_score": round(candidate.score, 3),
            "replacement_score_mode": params.replacement_score_mode,
            "replacement_compare_mode": params.replacement_compare_mode,
            "replacement_comparison_baseline": round(comparison_baseline, 3),
            "replacement_score_margin": round(candidate_advantage, 3),
        }
    return decisions


def replacement_candidate_score(candidate: Candidate, *, mode: str) -> float:
    mode = validate_replacement_score_mode(mode)
    factor_data = candidate.factor_data or {}
    score = candidate.score
    if mode == REPLACEMENT_SCORE_ENTRY:
        return round(clamp(score, 0.0, 10.0), 3)
    if mode == REPLACEMENT_SCORE_ENTRY_PLUS_RS:
        rs20_rank = safe_numeric(factor_data.get("ranked_v2_rs_20d_rank"), default=0.5) or 0.5
        return round(clamp(score + (rs20_rank - 0.5) * 1.0, 0.0, 10.0), 3)
    if mode == REPLACEMENT_SCORE_ENTRY_PLUS_OVERLAY:
        overlay = safe_numeric(factor_data.get("ranked_v2_overlay_score"), default=5.0) or 5.0
        return round(clamp(score + (overlay - 5.0) * 0.15, 0.0, 10.0), 3)
    if mode == REPLACEMENT_SCORE_ENTRY_PLUS_QUALITY:
        rs20_rank = safe_numeric(factor_data.get("ranked_v2_rs_20d_rank"), default=0.5) or 0.5
        overlay = safe_numeric(factor_data.get("ranked_v2_overlay_score"), default=5.0) or 5.0
        pullback_quality = clamp(1.0 - abs(candidate.pullback_pct - 0.03) / 0.03, 0.0, 1.0)
        quality_bonus = (rs20_rank - 0.5) * 0.6 + (overlay - 5.0) * 0.08 + (pullback_quality - 0.5) * 0.4
        return round(clamp(score + quality_bonus, 0.0, 10.0), 3)
    raise ValueError(f"Unsupported replacement score mode: {mode}")


def replacement_comparison_baseline(*, trade: TradeState, hold_score: float, mode: str) -> float:
    mode = validate_replacement_compare_mode(mode)
    if mode == REPLACEMENT_COMPARE_CANDIDATE_VS_HOLD:
        return hold_score
    if mode == REPLACEMENT_COMPARE_CANDIDATE_VS_ENTRY:
        return trade.score
    if mode == REPLACEMENT_COMPARE_CANDIDATE_VS_BLEND:
        return (hold_score + trade.score) / 2.0
    raise ValueError(f"Unsupported replacement compare mode: {mode}")


def score_hold_position(
    *,
    trade: TradeState,
    daily: list[DailyBar],
    qqq_daily: list[DailyBar],
    session_date: date,
) -> dict[str, Any] | None:
    idx = previous_daily_index(daily, session_date)
    if idx is None or idx < 20:
        return None
    bar = daily[idx]
    closes = [item.close for item in daily]
    sma5 = rolling_mean(closes, 5)
    sma10 = rolling_mean(closes, 10)
    sma20 = rolling_mean(closes, 20)
    qqq_ret5_by_date = return_by_date(qqq_daily, 5)
    qqq_ret10_by_date = return_by_date(qqq_daily, 10)
    symbol_ret5 = closes[idx] / closes[idx - 5] - 1.0 if idx >= 5 and closes[idx - 5] else None
    symbol_ret10 = closes[idx] / closes[idx - 10] - 1.0 if idx >= 10 and closes[idx - 10] else None
    qqq_ret5 = qqq_ret5_by_date.get(bar.session_date)
    qqq_ret10 = qqq_ret10_by_date.get(bar.session_date)
    risk_per_share = max(0.0, trade.entry_price - trade.initial_stop_price)
    floating_r = (bar.close - trade.entry_price) / risk_per_share if risk_per_share > 0 else None
    recent_high = max(item.high for item in daily[max(0, idx - 19) : idx + 1])
    close_location = (
        (bar.close - bar.low) / (bar.high - bar.low)
        if bar.high > bar.low
        else 0.5
    )

    score = 0.0
    notes = []
    if bar.close > trade.stop_price:
        score += 1.0
        notes.append("close_above_stop")
    else:
        score -= 2.0
        notes.append("close_below_stop")
    if bar.close > trade.entry_price:
        score += 1.0
        notes.append("close_above_entry")
    if sma10[idx] is not None and bar.close > float(sma10[idx]):
        score += 1.25
        notes.append("close_above_sma10")
    if sma20[idx] is not None and bar.close > float(sma20[idx]):
        score += 1.25
        notes.append("close_above_sma20")
    if (
        sma5[idx] is not None
        and sma10[idx] is not None
        and sma20[idx] is not None
        and float(sma5[idx]) > float(sma10[idx]) > float(sma20[idx])
    ):
        score += 1.0
        notes.append("sma_stack_positive")
    if symbol_ret5 is not None and qqq_ret5 is not None and symbol_ret5 > qqq_ret5:
        score += 1.0
        notes.append("outperforming_qqq_5d")
    if symbol_ret10 is not None and qqq_ret10 is not None and symbol_ret10 > qqq_ret10:
        score += 1.0
        notes.append("outperforming_qqq_10d")
    if recent_high > 0 and bar.close / recent_high >= 0.92:
        score += 0.75
        notes.append("near_20d_high")
    if close_location >= 0.55:
        score += 0.5
        notes.append("closed_upper_half")
    if floating_r is not None:
        if floating_r >= 1.0:
            score += 1.0
            notes.append("floating_r_at_least_1")
        elif floating_r >= 0.5:
            score += 0.5
            notes.append("floating_r_at_least_0_5")
        elif floating_r < 0.0:
            score -= 1.0
            notes.append("negative_floating_r")
    if trade.target_hit:
        score += 0.5
        notes.append("target_1_hit")

    return {
        "score": round(clamp(score, 0.0, 10.0), 3),
        "as_of_date": bar.session_date.isoformat(),
        "close": round(bar.close, 4),
        "floating_r": round(floating_r, 3) if floating_r is not None else None,
        "notes": notes,
    }


def previous_daily_index(daily: list[DailyBar], session_date: date) -> int | None:
    for idx in range(len(daily) - 1, -1, -1):
        if daily[idx].session_date < session_date:
            return idx
    return None


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def max_hold_date(daily: list[DailyBar], entry_date: date, max_hold_days: int) -> date:
    dates = [bar.session_date for bar in daily]
    if entry_date not in dates:
        return entry_date
    idx = dates.index(entry_date)
    return dates[min(len(dates) - 1, idx + max_hold_days - 1)]


def symbol_cooldown_end_date(trading_dates: list[date], exit_date: date, cooldown_days: int) -> date | None:
    if cooldown_days <= 0:
        return None
    future_dates = [item for item in sorted(set(trading_dates)) if item > exit_date]
    if not future_dates:
        return exit_date
    return future_dates[min(len(future_dates) - 1, cooldown_days - 1)]


def update_symbol_cooldown(
    blocked_symbols_until: dict[str, date],
    *,
    symbol: str,
    trading_dates: list[date],
    exit_date: date,
    cooldown_days: int,
) -> None:
    blocked_until = symbol_cooldown_end_date(trading_dates, exit_date, cooldown_days)
    if blocked_until is None:
        return
    current = blocked_symbols_until.get(symbol)
    if current is None or blocked_until > current:
        blocked_symbols_until[symbol] = blocked_until


def serialize_trade(trade: TradeState) -> dict[str, Any]:
    initial_risk = max(0.0, (trade.entry_price - trade.initial_stop_price) * trade.shares)
    r_multiple = trade.realized_pnl / initial_risk if initial_risk > 0 else None
    exit_dates = [
        date.fromisoformat(exit_item["date"])
        for exit_item in trade.exits
        if exit_item.get("date")
    ]
    final_exit_date = max(exit_dates) if exit_dates else trade.entry_date
    holding_weekdays = count_weekdays(trade.entry_date, final_exit_date)
    exit_reasons = [
        str(exit_item.get("reason"))
        for exit_item in trade.exits
        if exit_item.get("reason")
    ]
    return {
        "symbol": trade.symbol,
        "regime": trade.regime,
        "signal_date": trade.signal_date.isoformat(),
        "entry_date": trade.entry_date.isoformat(),
        "entry_dt": trade.entry_dt.isoformat(),
        "entry_price": trade.entry_price,
        "initial_stop": trade.initial_stop_price,
        "final_stop": round(trade.stop_price, 4),
        "target_price": trade.target_price,
        "shares": trade.shares,
        "score": trade.score,
        "target_hit": trade.target_hit,
        "pnl": round(trade.realized_pnl, 2),
        "initial_risk": round(initial_risk, 2),
        "r_multiple": round(r_multiple, 4) if r_multiple is not None else None,
        "final_exit_date": final_exit_date.isoformat(),
        "holding_weekdays": holding_weekdays,
        "exit_reasons": exit_reasons,
        "primary_exit_reason": exit_reasons[-1] if exit_reasons else None,
        "return_on_entry_value_pct": round(
            trade.realized_pnl / (trade.entry_price * trade.shares) * 100.0,
            3,
        ),
        "exits": trade.exits,
    }


def summarize_trades(
    trades: list[dict[str, Any]],
    initial_equity: float,
    equity_events: list[dict[str, Any]],
) -> dict[str, Any]:
    total_pnl = sum(float(trade["pnl"]) for trade in trades)
    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) <= 0]
    r_values = [
        float(trade["r_multiple"])
        for trade in trades
        if trade.get("r_multiple") is not None
    ]
    gross_profit = sum(float(trade["pnl"]) for trade in wins)
    gross_loss = abs(sum(float(trade["pnl"]) for trade in losses))
    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(total_pnl / initial_equity * 100.0, 3),
        "average_pnl": round(total_pnl / len(trades), 2) if trades else 0.0,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if gross_profit == 0 else float("inf")),
        "average_holding_weekdays": round(
            sum(int(trade.get("holding_weekdays") or 0) for trade in trades) / len(trades),
            2,
        ) if trades else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct(equity_events), 3),
        "by_symbol": summarize_by_key(trades, "symbol"),
        "by_regime": summarize_by_key(trades, "regime"),
        "by_exit_reason": summarize_by_key(trades, "primary_exit_reason"),
        "by_exit_month": summarize_by_exit_month(trades),
    }


def summarize_by_key(trades: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(str(trade.get(key)), []).append(trade)
    output = {}
    for group, items in sorted(grouped.items()):
        r_values = [
            float(item["r_multiple"])
            for item in items
            if item.get("r_multiple") is not None
        ]
        output[group] = {
            "trades": len(items),
            "pnl": round(sum(float(item["pnl"]) for item in items), 2),
            "win_rate": round(
                sum(1 for item in items if float(item["pnl"]) > 0) / len(items),
                4,
            ),
            "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        }
    return output


def summarize_by_exit_month(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        raw_date = trade.get("final_exit_date") or trade.get("entry_date")
        key = str(raw_date)[:7] if raw_date else "unknown"
        grouped.setdefault(key, []).append(trade)
    output = {}
    for month, items in sorted(grouped.items()):
        r_values = [
            float(item["r_multiple"])
            for item in items
            if item.get("r_multiple") is not None
        ]
        output[month] = {
            "trades": len(items),
            "pnl": round(sum(float(item["pnl"]) for item in items), 2),
            "win_rate": round(
                sum(1 for item in items if float(item["pnl"]) > 0) / len(items),
                4,
            ),
            "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        }
    return output


def max_drawdown_pct(equity_events: list[dict[str, Any]]) -> float:
    peak = None
    max_drawdown = 0.0
    for event in equity_events:
        equity = event.get("equity")
        if not isinstance(equity, (int, float)):
            continue
        if peak is None or equity > peak:
            peak = equity
        if peak:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
    return abs(max_drawdown) * 100.0


def summarize_data(daily_by_symbol: dict[str, list[DailyBar]]) -> dict[str, Any]:
    summary = {}
    for symbol, bars in daily_by_symbol.items():
        summary[symbol] = {
            "daily_bars": len(bars),
            "start": bars[0].session_date.isoformat() if bars else None,
            "end": bars[-1].session_date.isoformat() if bars else None,
        }
    return summary


def return_by_date(daily: list[DailyBar], lookback: int) -> dict[date, float]:
    output = {}
    for idx in range(lookback, len(daily)):
        previous = daily[idx - lookback].close
        output[daily[idx].session_date] = daily[idx].close / previous - 1.0 if previous else 0.0
    return output


def rolling_mean(values: list[float], window: int) -> list[float | None]:
    output: list[float | None] = []
    running = 0.0
    for idx, value in enumerate(values):
        running += value
        if idx >= window:
            running -= values[idx - window]
        if idx >= window - 1:
            output.append(running / window)
        else:
            output.append(None)
    return output


def consecutive_down_days(daily: list[DailyBar], idx: int) -> int:
    count = 0
    cursor = idx
    while cursor > 0 and daily[cursor].close < daily[cursor - 1].close:
        count += 1
        cursor -= 1
    return count


def previous_available_date(regimes: dict[date, str], session_date: date) -> date | None:
    available = [item for item in regimes if item <= session_date]
    return max(available) if available else None


def max_position_count(regime: str, params: BacktestParams | None = None) -> int:
    if params is not None:
        if regime == "strong":
            return max(0, int(params.strong_max_positions))
        if regime == "weak":
            return max(0, int(params.weak_max_positions))
        return max(0, int(params.neutral_max_positions))
    if regime == "strong":
        return 3
    if regime == "weak":
        return 1
    return 2


def write_backtest_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_backtest_trades_csv(path: Path, trades: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "symbol",
        "regime",
        "signal_date",
        "entry_date",
        "final_exit_date",
        "entry_price",
        "initial_stop",
        "target_price",
        "shares",
        "score",
        "target_hit",
        "pnl",
        "initial_risk",
        "r_multiple",
        "holding_weekdays",
        "primary_exit_reason",
        "return_on_entry_value_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow({field: trade.get(field) for field in fieldnames})


def format_backtest_summary(report: dict[str, Any], report_path: Path) -> str:
    summary = report["summary"]
    diagnostics = report.get("diagnostics") or {}
    lines = [
        "Backtest summary",
        f"Symbols: {', '.join(report['config']['symbols'])}",
        f"Scoring mode: {report['config']['params'].get('scoring_mode')}",
        f"Reselection exit: {report['config']['params'].get('reselection_exit_mode')}",
        f"Candidates: {report['candidate_count']}",
        f"Trades: {summary['trade_count']}",
        f"Entry attempts: {diagnostics.get('entry_attempts')} filled={diagnostics.get('filled_entries')} unfilled={diagnostics.get('unfilled_entries')}",
        f"Win rate: {summary['win_rate']:.2%}",
        f"Total PnL: {summary['total_pnl']}",
        f"Return: {summary['return_pct']}%",
        f"Average R: {summary.get('average_r')}",
        f"Profit factor: {summary.get('profit_factor')}",
        f"Max drawdown: {summary['max_drawdown_pct']}%",
        "Data:",
    ]
    regime_policy = report.get("config", {}).get("regime_policy") or {}
    if regime_policy.get("enabled"):
        lines.insert(3, f"Regime policy: {regime_policy.get('mode')}")
    skipped_regime_policy = diagnostics.get("skipped_regime_policy")
    if skipped_regime_policy:
        lines.insert(6, f"Regime policy skipped: {skipped_regime_policy}")
    if diagnostics.get("reselection_exit_count"):
        lines.insert(7, f"Reselection exits: {diagnostics.get('reselection_exit_count')} pnl={diagnostics.get('reselection_exit_pnl')}")
    history_sources = report.get("history_sources") or {}
    if history_sources:
        lines.insert(-1, f"History sources: {format_history_sources(history_sources)}")
    for symbol, item in report["data"].items():
        lines.append(
            f"  - {symbol}: bars={item['daily_bars']} start={item['start']} end={item['end']}"
        )
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)


def format_history_sources(summary: dict[str, Any]) -> str:
    by_source = summary.get("by_source") or {}
    counts = ", ".join(f"{source}={count}" for source, count in sorted(by_source.items()))
    fallback_count = summary.get("fallback_count") or 0
    return f"{counts or 'none'} fallbacks={fallback_count}"


def count_weekdays(start: date, end: date) -> int:
    if end < start:
        return 0
    count = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            count += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return count
