#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import islice, product
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import (
    BacktestParams,
    Candidate,
    NON_TRADABLE_CONTEXT_SYMBOLS,
    aggregate_daily,
    advance_trade_one_day,
    apply_candidate_scoring_mode,
    build_trade_from_candidate,
    exit_trade,
    generate_candidates,
    generate_market_regimes,
    group_candles_by_date,
    max_position_count,
    parse_schwab_candles,
    previous_available_date,
    serialize_trade,
    summarize_trades,
    update_symbol_cooldown,
)
from liubang.cli_utils import load_earnings_for_symbols, load_env, load_histories, parse_float_grid, summarize_history_sources
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH, EarningsCalendar
from liubang.universe import DEFAULT_UNIVERSE_PATH, resolve_symbols


@dataclass(frozen=True)
class SearchContext:
    symbols: tuple[str, ...]
    history_sources: dict[str, Any]
    daily_by_symbol: dict[str, list[Any]]
    intraday_by_symbol: dict[str, dict[date, list[Any]]]
    regimes: dict[date, str]
    earnings_exit_dates: dict[str, set[date]]
    candidates_by_entry_date: dict[date, list[Candidate]]
    all_dates: list[date]


@dataclass(frozen=True)
class StrategyConfig:
    min_classic_score: float
    min_ranked_score: float
    max_pullback_pct: float
    hard_stop_pct: float
    symbol_cooldown_days: int
    min_rs20_rank: float
    min_rs60_rank: float
    min_overlay_score: float
    max_atr20_pct: float | None
    max_pullback_atr: float | None
    regime_filter: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_classic_score": self.min_classic_score,
            "min_ranked_score": self.min_ranked_score,
            "max_pullback_pct": self.max_pullback_pct,
            "hard_stop_pct": self.hard_stop_pct,
            "symbol_cooldown_days": self.symbol_cooldown_days,
            "min_rs20_rank": self.min_rs20_rank,
            "min_rs60_rank": self.min_rs60_rank,
            "min_overlay_score": self.min_overlay_score,
            "max_atr20_pct": self.max_atr20_pct,
            "max_pullback_atr": self.max_pullback_atr,
            "regime_filter": self.regime_filter,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search Liubang scoring/filter strategies in memory.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    parser.add_argument("--history-mode", choices=["5m", "hourly"], default="5m")
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--hourly-period", default="2y")
    parser.add_argument("--hourly-interval", default="1h")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--extended-hours", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=64)
    parser.add_argument("--target-pf", type=float, default=2.0)
    parser.add_argument("--min-trades", type=int, default=50)
    parser.add_argument("--max-configs", type=int, default=30000)
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--candidate-min-score", type=float, default=6.0)
    parser.add_argument("--candidate-min-pullback-pct", type=float, default=0.005)
    parser.add_argument("--candidate-max-pullback-pct", type=float, default=0.08)
    parser.add_argument("--min-classic-scores", default="7,8,9")
    parser.add_argument("--min-ranked-scores", default="7,8,8.5,9")
    parser.add_argument("--max-pullback-pcts", default="0.025,0.035,0.05,0.06")
    parser.add_argument("--hard-stop-pcts", default="0.02,0.025,0.03,0.04")
    parser.add_argument("--cooldowns", default="0,3,5,10")
    parser.add_argument("--min-rs20-ranks", default="0,0.5,0.65,0.8")
    parser.add_argument("--min-rs60-ranks", default="0,0.5,0.7")
    parser.add_argument("--min-overlay-scores", default="0,6,7,8")
    parser.add_argument("--max-atr20-pcts", default="none,0.06,0.08")
    parser.add_argument("--max-pullback-atrs", default="none,1.5,2.0,2.5")
    parser.add_argument("--regime-filters", default="all,strong_neutral,strong")
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
        if args.history_mode == "hourly":
            from scripts.run_hourly_proxy_backtest import load_hourly_histories

            history_by_symbol = load_hourly_histories(
                symbols=symbols,
                cache_dir=Path(args.cache_dir),
                period=args.hourly_period,
                interval=args.hourly_interval,
                refresh=args.refresh,
            )
        else:
            history_by_symbol = load_histories(
                symbols=symbols,
                cache_dir=Path(args.cache_dir),
                extended_hours=args.extended_hours,
                refresh=args.refresh,
                history_provider=args.history_provider,
                fallback_history_provider=args.fallback_history_provider,
                yfinance_period=args.yfinance_period,
            )
        earnings_calendar = load_earnings_for_symbols(
            symbols=symbols,
            source=args.earnings_source,
            calendar_path=Path(args.earnings_calendar),
            refresh=args.refresh_earnings,
            limit=args.earnings_limit,
        )
        context = build_context(
            history_by_symbol=history_by_symbol,
            symbols=symbols,
            earnings_calendar=earnings_calendar,
            candidate_params=BacktestParams(
                min_score=args.candidate_min_score,
                min_pullback_pct=args.candidate_min_pullback_pct,
                max_pullback_pct=args.candidate_max_pullback_pct,
                hard_stop_pct=min(parse_float_grid(args.hard_stop_pcts)),
            ),
        )
        rows = run_search(args, context)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"scoring_search_{timestamp}.json"
        csv_path = output_dir / f"scoring_search_{timestamp}.csv"
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "scoring_strategy_search",
            "history_mode": args.history_mode,
            "symbols": list(symbols),
            "history_sources": summarize_history_sources(history_by_symbol),
            "target_pf": args.target_pf,
            "min_trades": args.min_trades,
            "searched_configs": len(rows),
            "candidate_count": sum(len(items) for items in context.candidates_by_entry_date.values()),
            "target_hits": [row for row in rows if row["target_hit"]],
            "top_rows": sorted_rows(rows)[:50],
        }
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_rows(csv_path, rows)
    except Exception as exc:
        print(f"search_scoring_strategies failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(rows, json_path, csv_path, target_pf=args.target_pf, min_trades=args.min_trades))
    return 0


def build_context(
    *,
    history_by_symbol: dict[str, dict[str, Any]],
    symbols: tuple[str, ...],
    earnings_calendar: EarningsCalendar | None,
    candidate_params: BacktestParams,
) -> SearchContext:
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
    if "SPY" not in daily_by_symbol or "QQQ" not in daily_by_symbol:
        raise ValueError("Search requires SPY and QQQ histories.")
    regimes = generate_market_regimes(daily_by_symbol["SPY"], daily_by_symbol["QQQ"])
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
    candidates_by_entry_date: dict[date, list[Candidate]] = {}
    for symbol in symbols:
        if symbol in NON_TRADABLE_CONTEXT_SYMBOLS:
            continue
        candidates = generate_candidates(
            symbol,
            daily_by_symbol.get(symbol, []),
            daily_by_symbol["QQQ"],
            regimes,
            candidate_params,
            earnings_calendar,
        )
        for candidate in candidates:
            candidates_by_entry_date.setdefault(candidate.entry_date, []).append(candidate)
    candidates_by_entry_date = {
        entry_date: apply_candidate_scoring_mode(candidates, "ranked_v2")
        for entry_date, candidates in candidates_by_entry_date.items()
    }
    all_dates = sorted(set().union(*(set(grouped) for grouped in intraday_by_symbol.values())))
    return SearchContext(
        symbols=symbols,
        history_sources=summarize_history_sources(history_by_symbol),
        daily_by_symbol=daily_by_symbol,
        intraday_by_symbol=intraday_by_symbol,
        regimes=regimes,
        earnings_exit_dates=earnings_exit_dates,
        candidates_by_entry_date=candidates_by_entry_date,
        all_dates=all_dates,
    )


def run_search(args: argparse.Namespace, context: SearchContext) -> list[dict[str, Any]]:
    configs = generate_configs(args)
    rows = []
    progress_every = max(0, int(getattr(args, "progress_every", 0) or 0))
    total_configs = len(configs)
    for index, config in enumerate(configs, start=1):
        report = simulate_config(context, config)
        summary = report["summary"]
        row = config.to_dict() | {
            "candidate_count": report["candidate_count"],
            "trade_count": summary["trade_count"],
            "win_rate": summary["win_rate"],
            "return_pct": summary["return_pct"],
            "total_pnl": summary["total_pnl"],
            "average_r": summary.get("average_r"),
            "profit_factor": summary.get("profit_factor"),
            "max_drawdown_pct": summary["max_drawdown_pct"],
            "entry_attempts": report["diagnostics"]["entry_attempts"],
            "filled_entries": report["diagnostics"]["filled_entries"],
        }
        row["target_hit"] = (
            comparable_pf(row["profit_factor"]) >= args.target_pf
            and int(row["trade_count"] or 0) >= args.min_trades
        )
        rows.append(row)
        if progress_every and (index % progress_every == 0 or index == total_configs):
            hits = sum(1 for item in rows if item["target_hit"])
            best_pf = sorted_rows(rows)[0].get("profit_factor") if rows else None
            print(
                f"searched {index}/{total_configs} configs; target_hits={hits}; best_pf={best_pf}",
                file=sys.stderr,
            )
    return rows


def generate_configs(args: argparse.Namespace) -> list[StrategyConfig]:
    all_configs = (
        StrategyConfig(
            min_classic_score=min_classic_score,
            min_ranked_score=min_ranked_score,
            max_pullback_pct=max_pullback_pct,
            hard_stop_pct=hard_stop_pct,
            symbol_cooldown_days=cooldown,
            min_rs20_rank=min_rs20_rank,
            min_rs60_rank=min_rs60_rank,
            min_overlay_score=min_overlay_score,
            max_atr20_pct=max_atr20_pct,
            max_pullback_atr=max_pullback_atr,
            regime_filter=regime_filter,
        )
        for (
            min_classic_score,
            min_ranked_score,
            max_pullback_pct,
            hard_stop_pct,
            cooldown,
            min_rs20_rank,
            min_rs60_rank,
            min_overlay_score,
            max_atr20_pct,
            max_pullback_atr,
            regime_filter,
        ) in product(
            parse_float_grid(args.min_classic_scores),
            parse_float_grid(args.min_ranked_scores),
            parse_float_grid(args.max_pullback_pcts),
            parse_float_grid(args.hard_stop_pcts),
            parse_int_grid(args.cooldowns),
            parse_float_grid(args.min_rs20_ranks),
            parse_float_grid(args.min_rs60_ranks),
            parse_float_grid(args.min_overlay_scores),
            parse_optional_float_grid(args.max_atr20_pcts),
            parse_optional_float_grid(args.max_pullback_atrs),
            parse_text_grid(args.regime_filters),
        )
    )
    return list(islice(all_configs, max(1, int(args.max_configs))))


def simulate_config(context: SearchContext, config: StrategyConfig) -> dict[str, Any]:
    params = BacktestParams(
        hard_stop_pct=config.hard_stop_pct,
        symbol_cooldown_days=config.symbol_cooldown_days,
        scoring_mode="ranked_v2",
    )
    equity = params.initial_equity
    open_trades = []
    closed_trades: list[dict[str, Any]] = []
    equity_events = [{"date": context.all_dates[0].isoformat() if context.all_dates else None, "equity": equity}]
    diagnostics = {
        "candidate_count": 0,
        "entry_attempts": 0,
        "filled_entries": 0,
        "unfilled_entries": 0,
        "skipped_no_slot": 0,
        "skipped_duplicate_symbol": 0,
        "skipped_symbol_cooldown": 0,
        "skipped_missing_intraday": 0,
    }
    blocked_symbols_until: dict[str, date] = {}
    allowed_regimes = regimes_for_filter(config.regime_filter)

    for session_date in context.all_dates:
        regime = context.regimes.get(previous_available_date(context.regimes, session_date), "neutral")
        open_trades = [trade for trade in open_trades if trade.remaining_shares > 0]
        todays_candidates = [
            candidate
            for candidate in context.candidates_by_entry_date.get(session_date, [])
            if candidate_passes(candidate, config, allowed_regimes)
        ]
        diagnostics["candidate_count"] += len(todays_candidates)
        todays_candidates = sorted(todays_candidates, key=lambda candidate: candidate.score, reverse=True)

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
                entry_candles = context.intraday_by_symbol.get(candidate.symbol, {}).get(session_date, [])
                diagnostics["entry_attempts"] += 1
                if not entry_candles:
                    diagnostics["skipped_missing_intraday"] += 1
                    diagnostics["unfilled_entries"] += 1
                    continue
                entry = build_trade_from_candidate(
                    candidate,
                    entry_candles,
                    context.daily_by_symbol[candidate.symbol],
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

        still_open = []
        for trade in open_trades:
            day_candles = context.intraday_by_symbol.get(trade.symbol, {}).get(session_date, [])
            realized = advance_trade_one_day(
                trade,
                day_candles,
                session_date,
                params,
                earnings_exit_dates=context.earnings_exit_dates.get(trade.symbol, set()),
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
                    trading_dates=[bar.session_date for bar in context.daily_by_symbol.get(trade.symbol, [])],
                    exit_date=session_date,
                    cooldown_days=params.symbol_cooldown_days,
                )
                closed_trades.append(serialize_trade(trade))
        open_trades = still_open

    if context.all_dates:
        final_date = context.all_dates[-1]
        for trade in open_trades:
            day_candles = context.intraday_by_symbol.get(trade.symbol, {}).get(final_date, [])
            if day_candles and trade.remaining_shares > 0:
                exit_trade(
                    trade,
                    day_candles[-1].close,
                    final_date,
                    "end_of_data",
                    params,
                )
                equity += trade.exits[-1]["pnl"]
                equity_events.append({"date": final_date.isoformat(), "equity": round(equity, 2)})
                closed_trades.append(serialize_trade(trade))

    return {
        "candidate_count": diagnostics["candidate_count"],
        "diagnostics": diagnostics,
        "summary": summarize_trades(closed_trades, params.initial_equity, equity_events),
    }


def candidate_passes(candidate: Candidate, config: StrategyConfig, allowed_regimes: set[str]) -> bool:
    factors = candidate.factor_data or {}
    if candidate.regime not in allowed_regimes:
        return False
    if safe_float(factors.get("classic_score")) < config.min_classic_score:
        return False
    if candidate.score < config.min_ranked_score:
        return False
    if candidate.pullback_pct > config.max_pullback_pct:
        return False
    if safe_float(factors.get("ranked_v2_rs_20d_rank")) < config.min_rs20_rank:
        return False
    if safe_float(factors.get("ranked_v2_rs_60d_rank")) < config.min_rs60_rank:
        return False
    if safe_float(factors.get("ranked_v2_overlay_score")) < config.min_overlay_score:
        return False
    if config.max_atr20_pct is not None and safe_float(factors.get("atr20_pct")) > config.max_atr20_pct:
        return False
    if config.max_pullback_atr is not None and safe_float(factors.get("pullback_atr")) > config.max_pullback_atr:
        return False
    return True


def regimes_for_filter(value: str) -> set[str]:
    if value == "all":
        return {"strong", "neutral", "weak"}
    if value == "strong_neutral":
        return {"strong", "neutral"}
    if value == "strong":
        return {"strong"}
    raise ValueError(f"Unsupported regime filter={value!r}")


def parse_int_grid(raw: str) -> tuple[int, ...]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(max(0, int(item)))
    return tuple(dict.fromkeys(values))


def parse_optional_float_grid(raw: str) -> tuple[float | None, ...]:
    values: list[float | None] = []
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        values.append(None if item in {"none", "null", "na"} else float(item))
    return tuple(dict.fromkeys(values))


def parse_text_grid(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))


def safe_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    return numeric if math.isfinite(numeric) else float("-inf")


def comparable_pf(value: Any) -> float:
    if value == float("inf") or value == "inf":
        return float("inf")
    return safe_float(value)


def sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            comparable_pf(row.get("profit_factor")),
            int(row.get("trade_count") or 0),
            safe_float(row.get("return_pct")),
            -safe_float(row.get("max_drawdown_pct")),
        ),
        reverse=True,
    )


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_summary(rows: list[dict[str, Any]], json_path: Path, csv_path: Path, *, target_pf: float, min_trades: int) -> str:
    ranked = sorted_rows(rows)
    hits = [row for row in ranked if row.get("target_hit")]
    lines = [
        "Scoring search summary",
        f"Rows: {len(rows)}",
        f"Target: pf>={target_pf} trades>={min_trades}",
        f"Target hits: {len(hits)}",
        "Top configurations:",
    ]
    for row in ranked[:10]:
        lines.append(
            "  "
            f"pf={row.get('profit_factor')} trades={row.get('trade_count')} "
            f"ret={row.get('return_pct')}% dd={row.get('max_drawdown_pct')}% "
            f"classic>={row.get('min_classic_score')} ranked>={row.get('min_ranked_score')} "
            f"pb<={row.get('max_pullback_pct')} stop={row.get('hard_stop_pct')} "
            f"rs20>={row.get('min_rs20_rank')} rs60>={row.get('min_rs60_rank')} "
            f"overlay>={row.get('min_overlay_score')} regime={row.get('regime_filter')} "
            f"cooldown={row.get('symbol_cooldown_days')}"
        )
    lines.append(f"JSON: {json_path}")
    lines.append(f"CSV: {csv_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
