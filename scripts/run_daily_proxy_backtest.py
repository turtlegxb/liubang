#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import (
    BacktestParams,
    DailyBar,
    NON_TRADABLE_CONTEXT_SYMBOLS,
    SCORING_MODE_CLASSIC,
    SCORING_MODES,
    apply_candidate_scoring_mode,
    count_weekdays,
    filter_candidates_for_regime_policy,
    generate_candidates,
    generate_market_regimes,
    max_drawdown_pct,
    max_position_count,
    previous_available_date,
    regime_policy_summary,
    summarize_by_exit_month,
    summarize_by_key,
    update_symbol_cooldown,
    validate_scoring_mode,
)
from liubang.cli_utils import load_earnings_for_symbols, load_env
from liubang.defaults import (
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_PULLBACK_PCT,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SCORE,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH, EarningsCalendar
from liubang.market_data import read_json, write_json
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a yfinance daily-bar proxy backtest for longer-history validation.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--period", default="5y")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=64)
    parser.add_argument("--initial-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    symbols = resolve_symbols(
        raw_symbols=args.symbols,
        universe_path=Path(args.universe),
        include_benchmarks=True,
    )
    try:
        daily_by_symbol = load_daily_histories(
            symbols=symbols,
            cache_dir=Path(args.cache_dir),
            period=args.period,
            refresh=args.refresh,
        )
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        params = BacktestParams(
            initial_equity=args.initial_equity,
            min_score=args.min_score,
            min_pullback_pct=args.min_pullback_pct,
            max_pullback_pct=args.max_pullback_pct,
            scoring_mode=args.scoring_mode,
            hard_stop_pct=args.hard_stop_pct,
            risk_per_trade_pct=args.risk_per_trade_pct,
            max_position_pct=args.max_position_pct,
            symbol_cooldown_days=max(0, args.symbol_cooldown_days),
        )
        report = run_daily_proxy_backtest(
            daily_by_symbol=daily_by_symbol,
            symbols=symbols,
            params=params,
            earnings_calendar=earnings_calendar,
            period=args.period,
        )
        output_path = Path(args.output_dir) / f"daily_proxy_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"run_daily_proxy_backtest failed: {exc}", file=sys.stderr)
        return 1

    print(format_daily_proxy_summary(report, output_path))
    return 0


def load_daily_histories(
    *,
    symbols: tuple[str, ...],
    cache_dir: Path,
    period: str,
    refresh: bool,
) -> dict[str, list[DailyBar]]:
    output = {}
    for symbol in symbols:
        payload = load_or_fetch_yfinance_daily(symbol, cache_dir=cache_dir, period=period, refresh=refresh)
        bars = parse_daily_bars(payload)
        if bars:
            output[symbol] = bars
    return output


def load_or_fetch_yfinance_daily(symbol: str, *, cache_dir: Path, period: str, refresh: bool) -> dict[str, Any]:
    cache_path = cache_dir / f"yfinance_1d_{period}_{symbol.upper()}.json"
    if cache_path.exists() and not refresh:
        return read_json(cache_path)
    payload = fetch_yfinance_daily(symbol, period=period)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(cache_path, payload)
    return payload


def fetch_yfinance_daily(symbol: str, *, period: str) -> dict[str, Any]:
    import pandas as pd
    import yfinance as yf

    frame = yf.download(
        symbol,
        period=period,
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return {
            "symbol": symbol.upper(),
            "source": "yfinance",
            "period": period,
            "interval": "1d",
            "fetched_at": datetime.now(UTC).isoformat(),
            "bars": [],
        }
    if isinstance(frame.columns, pd.MultiIndex):
        ticker = symbol.upper()
        if ticker in frame.columns.get_level_values(-1):
            frame = frame.xs(ticker, axis=1, level=-1)
        elif ticker in frame.columns.get_level_values(0):
            frame = frame.xs(ticker, axis=1, level=0)
        else:
            frame.columns = frame.columns.get_level_values(0)
    bars = []
    for index, row in frame.iterrows():
        try:
            session_date = pd.Timestamp(index).date().isoformat()
            bars.append(
                {
                    "date": session_date,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume") or 0.0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "symbol": symbol.upper(),
        "source": "yfinance",
        "period": period,
        "interval": "1d",
        "fetched_at": datetime.now(UTC).isoformat(),
        "bars": bars,
    }


def parse_daily_bars(payload: dict[str, Any]) -> list[DailyBar]:
    bars = []
    for raw in payload.get("bars", []):
        if not isinstance(raw, dict):
            continue
        try:
            bars.append(
                DailyBar(
                    session_date=date.fromisoformat(str(raw["date"])),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    candle_count=1,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(bars, key=lambda bar: bar.session_date)


def run_daily_proxy_backtest(
    *,
    daily_by_symbol: dict[str, list[DailyBar]],
    symbols: tuple[str, ...],
    params: BacktestParams,
    earnings_calendar: EarningsCalendar | None,
    period: str,
) -> dict[str, Any]:
    if "SPY" not in daily_by_symbol or "QQQ" not in daily_by_symbol:
        raise ValueError("Daily proxy requires SPY and QQQ histories for market regime labels.")
    validate_scoring_mode(params.scoring_mode)
    regimes = generate_market_regimes(daily_by_symbol["SPY"], daily_by_symbol["QQQ"])
    bars_by_symbol_date = {
        symbol: {bar.session_date: bar for bar in bars}
        for symbol, bars in daily_by_symbol.items()
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
    candidates_by_entry_date: dict[date, list[Any]] = {}
    all_candidates = []
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

    all_dates = sorted(set().union(*(set(item) for item in bars_by_symbol_date.values())))
    equity = params.initial_equity
    open_trades: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []
    equity_events = [{"date": all_dates[0].isoformat() if all_dates else None, "equity": equity}]
    diagnostics = {
        "candidate_count": len(all_candidates),
        "entry_attempts": 0,
        "filled_entries": 0,
        "unfilled_entries": 0,
        "skipped_no_slot": 0,
        "skipped_duplicate_symbol": 0,
        "skipped_symbol_cooldown": 0,
        "skipped_missing_daily_bar": 0,
        "candidate_count_before_regime_policy": pre_policy_candidate_count,
        "skipped_regime_policy": pre_policy_candidate_count - len(all_candidates),
    }
    blocked_symbols_until: dict[str, date] = {}
    for session_date in all_dates:
        regime = regimes.get(previous_available_date(regimes, session_date), "neutral")
        open_trades = [trade for trade in open_trades if trade["remaining_shares"] > 0]
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
                if any(trade["symbol"] == candidate.symbol for trade in open_trades):
                    diagnostics["skipped_duplicate_symbol"] += 1
                    continue
                blocked_until = blocked_symbols_until.get(candidate.symbol)
                if blocked_until is not None and session_date <= blocked_until:
                    diagnostics["skipped_symbol_cooldown"] += 1
                    continue
                diagnostics["entry_attempts"] += 1
                entry_bar = bars_by_symbol_date.get(candidate.symbol, {}).get(session_date)
                if entry_bar is None:
                    diagnostics["skipped_missing_daily_bar"] += 1
                    diagnostics["unfilled_entries"] += 1
                    continue
                entry = build_daily_proxy_trade(
                    candidate=candidate,
                    entry_bar=entry_bar,
                    daily=daily_by_symbol[candidate.symbol],
                    equity=equity,
                    params=params,
                )
                if entry is None:
                    diagnostics["unfilled_entries"] += 1
                    continue
                if regime == "weak":
                    entry["shares"] = max(1, math.floor(entry["shares"] / 2))
                    entry["remaining_shares"] = entry["shares"]
                open_trades.append(entry)
                diagnostics["filled_entries"] += 1
                slots -= 1

        still_open = []
        for trade in open_trades:
            bar = bars_by_symbol_date.get(trade["symbol"], {}).get(session_date)
            if bar is None:
                still_open.append(trade)
                continue
            realized = advance_daily_proxy_trade(
                trade,
                bar,
                session_date,
                params,
                earnings_exit_dates=earnings_exit_dates.get(trade["symbol"], set()),
            )
            if realized:
                equity += realized
                equity_events.append({"date": session_date.isoformat(), "equity": round(equity, 2)})
            if trade["remaining_shares"] > 0:
                still_open.append(trade)
            else:
                update_symbol_cooldown(
                    blocked_symbols_until,
                    symbol=trade["symbol"],
                    trading_dates=[bar.session_date for bar in daily_by_symbol.get(trade["symbol"], [])],
                    exit_date=session_date,
                    cooldown_days=params.symbol_cooldown_days,
                )
                closed_trades.append(serialize_daily_proxy_trade(trade))
        open_trades = still_open

    if all_dates:
        final_date = all_dates[-1]
        for trade in open_trades:
            bar = bars_by_symbol_date.get(trade["symbol"], {}).get(final_date)
            if bar and trade["remaining_shares"] > 0:
                daily_exit(trade, bar.close, final_date, "end_of_data", params)
                equity += trade["exits"][-1]["pnl"]
                equity_events.append({"date": final_date.isoformat(), "equity": round(equity, 2)})
                closed_trades.append(serialize_daily_proxy_trade(trade))

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "daily_proxy_backtest",
        "limitations": [
            "Uses daily bars from yfinance; does not verify 5-minute VWAP/prior-high trigger.",
            "Entry is approximated at next daily open with configured slippage.",
            "When stop and target are both touched in one day, the proxy assumes stop first.",
        ],
        "config": {
            "symbols": list(symbols),
            "period": period,
            "params": asdict(params),
            "regime_policy": regime_policy_summary(params),
            "earnings_filter": earnings_calendar.summary() if earnings_calendar else None,
        },
        "data": summarize_daily_data(daily_by_symbol),
        "candidate_count": len(all_candidates),
        "diagnostics": diagnostics,
        "trades": closed_trades,
        "summary": summarize_daily_proxy_trades(closed_trades, params.initial_equity, equity_events),
        "equity_events": equity_events,
    }


def build_daily_proxy_trade(
    *,
    candidate: Any,
    entry_bar: DailyBar,
    daily: list[DailyBar],
    equity: float,
    params: BacktestParams,
) -> dict[str, Any] | None:
    entry_price = entry_bar.open * (1.0 + params.fixed_slippage_bps / 10_000.0)
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
    return {
        "symbol": candidate.symbol,
        "regime": candidate.regime,
        "signal_date": candidate.signal_date,
        "entry_date": candidate.entry_date,
        "entry_price": round(entry_price, 4),
        "initial_stop_price": round(stop_price, 4),
        "stop_price": round(stop_price, 4),
        "target_price": round(entry_price + risk_per_share * params.first_target_r, 4),
        "shares": shares,
        "remaining_shares": shares,
        "score": candidate.score,
        "target_hit": False,
        "realized_pnl": 0.0,
        "exits": [],
        "max_exit_date": proxy_max_hold_date(daily, candidate.entry_date, params.max_hold_days),
    }


def advance_daily_proxy_trade(
    trade: dict[str, Any],
    bar: DailyBar,
    session_date: date,
    params: BacktestParams,
    *,
    earnings_exit_dates: set[date],
) -> float:
    if trade["remaining_shares"] <= 0:
        return 0.0
    realized_before = trade["realized_pnl"]
    if session_date >= trade["entry_date"]:
        if bar.low <= trade["stop_price"]:
            daily_exit(trade, trade["stop_price"], session_date, "stop", params)
        elif not trade["target_hit"] and bar.high >= trade["target_price"]:
            exit_shares = max(1, math.floor(trade["shares"] * params.first_target_exit_pct))
            exit_shares = min(exit_shares, trade["remaining_shares"])
            daily_exit_partial(trade, exit_shares, trade["target_price"], session_date, "target_1", params)
            trade["target_hit"] = True
            trade["stop_price"] = trade["entry_price"]
    if trade["remaining_shares"] > 0 and session_date >= trade["max_exit_date"]:
        daily_exit(trade, bar.close, session_date, "time_exit", params)
    if trade["remaining_shares"] > 0 and session_date in earnings_exit_dates:
        daily_exit(trade, bar.close, session_date, "earnings_exit", params)
    return trade["realized_pnl"] - realized_before


def daily_exit_partial(
    trade: dict[str, Any],
    shares: int,
    price: float,
    session_date: date,
    reason: str,
    params: BacktestParams,
) -> None:
    fill_price = price * (1.0 - params.fixed_slippage_bps / 10_000.0)
    pnl = (fill_price - trade["entry_price"]) * shares
    trade["remaining_shares"] -= shares
    trade["realized_pnl"] += pnl
    trade["exits"].append(
        {
            "date": session_date.isoformat(),
            "reason": reason,
            "shares": shares,
            "price": round(fill_price, 4),
            "pnl": round(pnl, 2),
        }
    )


def daily_exit(trade: dict[str, Any], price: float, session_date: date, reason: str, params: BacktestParams) -> None:
    if trade["remaining_shares"] <= 0:
        return
    daily_exit_partial(trade, trade["remaining_shares"], price, session_date, reason, params)


def proxy_max_hold_date(daily: list[DailyBar], entry_date: date, max_hold_days: int) -> date:
    dates = [bar.session_date for bar in daily]
    if entry_date not in dates:
        return entry_date
    idx = dates.index(entry_date)
    return dates[min(len(dates) - 1, idx + max_hold_days - 1)]


def serialize_daily_proxy_trade(trade: dict[str, Any]) -> dict[str, Any]:
    initial_risk = max(0.0, (trade["entry_price"] - trade["initial_stop_price"]) * trade["shares"])
    r_multiple = trade["realized_pnl"] / initial_risk if initial_risk > 0 else None
    exit_dates = [
        date.fromisoformat(exit_item["date"])
        for exit_item in trade["exits"]
        if exit_item.get("date")
    ]
    final_exit_date = max(exit_dates) if exit_dates else trade["entry_date"]
    exit_reasons = [str(exit_item.get("reason")) for exit_item in trade["exits"] if exit_item.get("reason")]
    return {
        "symbol": trade["symbol"],
        "regime": trade["regime"],
        "signal_date": trade["signal_date"].isoformat(),
        "entry_date": trade["entry_date"].isoformat(),
        "entry_price": trade["entry_price"],
        "initial_stop": trade["initial_stop_price"],
        "final_stop": round(trade["stop_price"], 4),
        "target_price": trade["target_price"],
        "shares": trade["shares"],
        "score": trade["score"],
        "target_hit": trade["target_hit"],
        "pnl": round(trade["realized_pnl"], 2),
        "initial_risk": round(initial_risk, 2),
        "r_multiple": round(r_multiple, 4) if r_multiple is not None else None,
        "final_exit_date": final_exit_date.isoformat(),
        "holding_weekdays": count_weekdays(trade["entry_date"], final_exit_date),
        "exit_reasons": exit_reasons,
        "primary_exit_reason": exit_reasons[-1] if exit_reasons else None,
        "return_on_entry_value_pct": round(
            trade["realized_pnl"] / (trade["entry_price"] * trade["shares"]) * 100.0,
            3,
        ),
        "exits": trade["exits"],
    }


def summarize_daily_proxy_trades(
    trades: list[dict[str, Any]],
    initial_equity: float,
    equity_events: list[dict[str, Any]],
) -> dict[str, Any]:
    total_pnl = sum(float(trade["pnl"]) for trade in trades)
    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) <= 0]
    r_values = [float(trade["r_multiple"]) for trade in trades if trade.get("r_multiple") is not None]
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
        "max_drawdown_pct": round(max_drawdown_pct(equity_events), 3),
        "by_symbol": summarize_by_key(trades, "symbol"),
        "by_regime": summarize_by_key(trades, "regime"),
        "by_exit_reason": summarize_by_key(trades, "primary_exit_reason"),
        "by_exit_month": summarize_by_exit_month(trades),
    }


def summarize_daily_data(daily_by_symbol: dict[str, list[DailyBar]]) -> dict[str, Any]:
    return {
        symbol: {
            "daily_bars": len(bars),
            "start": bars[0].session_date.isoformat() if bars else None,
            "end": bars[-1].session_date.isoformat() if bars else None,
        }
        for symbol, bars in daily_by_symbol.items()
    }


def format_daily_proxy_summary(report: dict[str, Any], path: Path) -> str:
    summary = report["summary"]
    diagnostics = report["diagnostics"]
    lines = [
        "Daily proxy backtest summary",
        "Mode: daily proxy, not 5-minute trigger verified",
        f"Symbols: {', '.join(report['config']['symbols'])}",
        f"Scoring mode: {report['config']['params'].get('scoring_mode')}",
        f"Candidates: {report['candidate_count']}",
        f"Trades: {summary['trade_count']}",
        f"Entry attempts: {diagnostics.get('entry_attempts')} filled={diagnostics.get('filled_entries')} unfilled={diagnostics.get('unfilled_entries')}",
        f"Win rate: {summary['win_rate']:.2%}",
        f"Total PnL: {summary['total_pnl']}",
        f"Return: {summary['return_pct']}%",
        f"Average R: {summary.get('average_r')}",
        f"Profit factor: {summary.get('profit_factor')}",
        f"Max drawdown: {summary['max_drawdown_pct']}%",
        f"Report: {path}",
    ]
    regime_policy = report.get("config", {}).get("regime_policy") or {}
    if regime_policy.get("enabled"):
        lines.insert(4, f"Regime policy: {regime_policy.get('mode')}")
    skipped_regime_policy = diagnostics.get("skipped_regime_policy")
    if skipped_regime_policy:
        lines.insert(6, f"Regime policy skipped: {skipped_regime_policy}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
