from __future__ import annotations

import json
import math
import csv
from dataclasses import asdict, dataclass
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
        slots = max_position_count(regime) - len(open_trades)
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


def max_position_count(regime: str) -> int:
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
