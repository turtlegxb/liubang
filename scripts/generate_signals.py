#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.backtest import (
    BacktestParams,
    REPLACEMENT_COMPARE_MODES,
    REPLACEMENT_SCORE_MODES,
    RESELECTION_EXIT_MODES,
    SCORING_MODES,
)
from liubang.cli_utils import (
    load_earnings_for_symbols,
    load_env,
    load_histories,
    load_news_for_symbols,
    send_discord_message,
    summarize_history_sources,
)
from liubang.defaults import (
    DEFAULT_FIRST_TARGET_R,
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_PULLBACK_PCT,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SCORE,
    DEFAULT_RISK_PER_TRADE_PCT,
    DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
)
from liubang.dynamic_universe import (
    DEFAULT_DYNAMIC_SCREENERS,
    DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH,
    load_or_select_yfinance_dynamic_universe,
    parse_screeners,
)
from liubang.earnings import DEFAULT_EARNINGS_CACHE_PATH
from liubang.journal import aggregate_trades, load_journal_lots
from liubang.gex import summarize_weekly_gex
from liubang.options import DEFAULT_OPTIONS_CACHE_DIR, SchwabOptionChainCache, summarize_option_chain
from liubang.portfolio import portfolio_guard_from_file
from liubang.risk_throttle import RiskThrottleInputs, risk_throttle_from_journal
from liubang.schwab_adapter import RetrySettings, SchwabAdapter
from liubang.signals import (
    attach_options_context,
    build_signal_selection_payload,
    format_discord_message,
    format_signal_summary,
    build_watchlist_concentration,
    generate_signal_report,
    write_signal_report,
)
from liubang.trade_plan import SizingInputs
from liubang.universe import DEFAULT_UNIVERSE_PATH, load_universe, parse_symbols, resolve_symbols, unique_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the Liubang daily watchlist.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols. Overrides --universe.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH), help="Universe JSON file.")
    parser.add_argument("--dynamic-source", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--dynamic-cache", default=str(DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH))
    parser.add_argument("--dynamic-screeners", default=",".join(DEFAULT_DYNAMIC_SCREENERS))
    parser.add_argument("--dynamic-screener-count", type=int, default=100)
    parser.add_argument("--dynamic-limit", type=int, default=8)
    parser.add_argument("--refresh-dynamic", action="store_true")
    parser.add_argument("--dynamic-min-price", type=float, default=5.0)
    parser.add_argument("--dynamic-min-market-cap", type=float, default=2_000_000_000.0)
    parser.add_argument("--dynamic-min-dollar-volume", type=float, default=1_000_000_000.0)
    parser.add_argument("--dynamic-min-avg-dollar-volume", type=float, default=300_000_000.0)
    parser.add_argument("--earnings-source", choices=["yfinance", "file", "none"], default="yfinance")
    parser.add_argument("--earnings-calendar", default=str(DEFAULT_EARNINGS_CACHE_PATH))
    parser.add_argument("--refresh-earnings", action="store_true")
    parser.add_argument("--earnings-limit", type=int, default=32)
    parser.add_argument("--history-provider", choices=["schwab", "yfinance"], default="schwab")
    parser.add_argument("--fallback-history-provider", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--yfinance-period", default="60d")
    parser.add_argument("--news-source", choices=["yfinance", "none"], default="yfinance")
    parser.add_argument("--refresh-news", action="store_true")
    parser.add_argument("--news-limit", type=int, default=10)
    parser.add_argument("--news-max-age-hours", type=int, default=72)
    parser.add_argument("--options-source", choices=["schwab", "none"], default="schwab")
    parser.add_argument("--refresh-options", action="store_true")
    parser.add_argument("--options-cache-dir", default=str(DEFAULT_OPTIONS_CACHE_DIR))
    parser.add_argument("--options-strike-count", type=int, default=10)
    parser.add_argument("--options-max-dte", type=int, default=45)
    parser.add_argument("--weekly-gex-source", choices=["schwab", "none"], default="schwab")
    parser.add_argument("--refresh-gex", action="store_true")
    parser.add_argument("--weekly-gex-strike-count", type=int, default=50)
    parser.add_argument("--weekly-gex-max-dte", type=int, default=7)
    parser.add_argument("--weekly-gex-max-strike-distance-pct", type=float, default=0.15)
    parser.add_argument("--refresh", action="store_true", help="Refresh Schwab cache before scoring.")
    parser.add_argument("--extended-hours", action="store_true", help="Request extended-hours history.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--skip-selection-export", action="store_true", help="Do not write the compact filtered signal selection JSON.")
    parser.add_argument("--selection-output", default=None, help="Compact filtered signal selection JSON path. Defaults to data/exports/signal_selection_TIMESTAMP.json.")
    parser.add_argument("--latest-selection-output", default=None, help="Stable compact selection JSON path. Defaults to data/exports/latest_signal_selection.json.")
    parser.add_argument("--positions-file", default="data/manual_positions.json")
    parser.add_argument("--ignore-positions", action="store_true")
    parser.add_argument("--journal-file", default="data/trade_journal.csv")
    parser.add_argument("--ignore-risk-throttle", action="store_true")
    parser.add_argument("--risk-throttle-lookback", type=int, default=5)
    parser.add_argument("--risk-throttle-max-recent-r-loss", type=float, default=3.0)
    parser.add_argument("--risk-throttle-max-month-r-loss", type=float, default=4.0)
    parser.add_argument("--risk-throttle-max-consecutive-losses", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT)
    parser.add_argument("--max-pullback-pct", type=float, default=DEFAULT_MAX_PULLBACK_PCT)
    parser.add_argument("--scoring-mode", choices=SCORING_MODES, default=BacktestParams().scoring_mode)
    parser.add_argument("--symbol-cooldown-days", type=int, default=0)
    parser.add_argument("--cooldown-journal-file", default=None)
    parser.add_argument("--reselection-exit-mode", choices=RESELECTION_EXIT_MODES, default=BacktestParams().reselection_exit_mode)
    parser.add_argument("--replacement-min-hold-score", type=float, default=BacktestParams().replacement_min_hold_score)
    parser.add_argument(
        "--replacement-min-candidate-score-margin",
        type=float,
        default=BacktestParams().replacement_min_candidate_score_margin,
    )
    parser.add_argument("--replacement-score-mode", choices=REPLACEMENT_SCORE_MODES, default=BacktestParams().replacement_score_mode)
    parser.add_argument("--replacement-compare-mode", choices=REPLACEMENT_COMPARE_MODES, default=BacktestParams().replacement_compare_mode)
    parser.add_argument("--weak-max-positions", type=int, default=BacktestParams().weak_max_positions)
    parser.add_argument("--neutral-max-positions", type=int, default=BacktestParams().neutral_max_positions)
    parser.add_argument("--strong-max-positions", type=int, default=BacktestParams().strong_max_positions)
    parser.add_argument("--account-equity", type=float, default=DEFAULT_INITIAL_EQUITY)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--max-position-pct", type=float, default=DEFAULT_MAX_POSITION_PCT)
    parser.add_argument("--hard-stop-pct", type=float, default=DEFAULT_HARD_STOP_PCT)
    parser.add_argument("--first-target-r", type=float, default=DEFAULT_FIRST_TARGET_R)
    parser.add_argument("--weak-regime-size-multiplier", type=float, default=DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER)
    parser.add_argument("--send-discord", action="store_true", help="Send summary to Discord webhook.")
    parser.add_argument("--discord-webhook-url", default=None)
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    symbols, symbol_sources, symbol_metadata, dynamic_universe = resolve_signal_universe(args)

    try:
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
        news_book = load_news_for_symbols(
            symbols=symbols,
            source=args.news_source,
            refresh=args.refresh_news,
            limit=args.news_limit,
        )
        report = generate_signal_report(
            history_by_symbol,
            symbols=symbols,
            params=BacktestParams(
                initial_equity=args.account_equity,
                risk_per_trade_pct=args.risk_per_trade_pct,
                max_position_pct=args.max_position_pct,
                hard_stop_pct=args.hard_stop_pct,
                first_target_r=args.first_target_r,
                min_score=args.min_score,
                min_pullback_pct=args.min_pullback_pct,
                max_pullback_pct=args.max_pullback_pct,
                scoring_mode=args.scoring_mode,
                symbol_cooldown_days=max(0, args.symbol_cooldown_days),
                reselection_exit_mode=args.reselection_exit_mode,
                replacement_min_hold_score=args.replacement_min_hold_score,
                replacement_min_candidate_score_margin=args.replacement_min_candidate_score_margin,
                replacement_score_mode=args.replacement_score_mode,
                replacement_compare_mode=args.replacement_compare_mode,
                weak_max_positions=max(0, args.weak_max_positions),
                neutral_max_positions=max(0, args.neutral_max_positions),
                strong_max_positions=max(0, args.strong_max_positions),
            ),
            earnings_calendar=earnings_calendar,
            news_book=news_book,
            news_max_age_hours=args.news_max_age_hours,
            symbol_sources=symbol_sources,
            symbol_metadata=symbol_metadata,
            dynamic_universe=dynamic_universe,
            sizing=SizingInputs(
                account_equity=args.account_equity,
                risk_per_trade_pct=args.risk_per_trade_pct,
                max_position_pct=args.max_position_pct,
                hard_stop_pct=args.hard_stop_pct,
                first_target_r=args.first_target_r,
                weak_regime_size_multiplier=args.weak_regime_size_multiplier,
            ),
        )
        report["history_sources"] = summarize_history_sources(history_by_symbol)
        if args.symbol_cooldown_days > 0:
            report = apply_symbol_cooldown(report, args)
        if not args.ignore_positions:
            report["portfolio_guard"] = portfolio_guard_from_file(
                market_regime=str(report["market_regime"]["regime"]),
                positions_path=Path(args.positions_file),
                account_equity=args.account_equity,
                max_position_pct=args.max_position_pct,
                weak_regime_size_multiplier=args.weak_regime_size_multiplier,
                weak_max_positions=max(0, args.weak_max_positions),
                neutral_max_positions=max(0, args.neutral_max_positions),
                strong_max_positions=max(0, args.strong_max_positions),
            )
        if not args.ignore_risk_throttle:
            report = apply_risk_throttle(report, args)
        if args.options_source == "schwab" and report.get("watchlist"):
            report = enrich_with_schwab_options(report, args)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        output_dir = Path(args.output_dir)
        report_path = output_dir / f"signals_{timestamp}.json"
        selection_path = (
            None
            if args.skip_selection_export
            else Path(args.selection_output)
            if args.selection_output
            else output_dir / f"signal_selection_{timestamp}.json"
        )
        latest_selection_path = (
            None
            if args.skip_selection_export
            else Path(args.latest_selection_output)
            if args.latest_selection_output
            else output_dir / "latest_signal_selection.json"
        )
        if selection_path is not None:
            report["signal_selection"] = {
                "path": str(selection_path),
                "latest_path": str(latest_selection_path) if latest_selection_path else None,
            }
        write_signal_report(report_path, report)
        if selection_path is not None:
            selection_payload = build_signal_selection_payload(report, signal_report_path=report_path)
            write_signal_report(selection_path, selection_payload)
            if latest_selection_path is not None and latest_selection_path != selection_path:
                write_signal_report(latest_selection_path, selection_payload)

        if args.send_discord:
            webhook_url = args.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
            if not webhook_url:
                raise ValueError("Missing Discord webhook URL. Set DISCORD_WEBHOOK_URL or pass --discord-webhook-url.")
            send_discord_message(webhook_url, format_discord_message(report))
    except Exception as exc:
        print(f"generate_signals failed: {exc}", file=sys.stderr)
        return 1

    print(format_signal_summary(report, report_path))
    return 0


def resolve_signal_universe(args: argparse.Namespace) -> tuple[tuple[str, ...], dict[str, str], dict[str, dict], dict]:
    universe_path = Path(args.universe)
    universe = load_universe(universe_path)
    symbols = resolve_symbols(
        raw_symbols=args.symbols,
        universe_path=universe_path,
        include_benchmarks=True,
    )

    symbol_sources: dict[str, str] = {}
    symbol_metadata: dict[str, dict] = {}
    if args.symbols:
        for symbol in parse_symbols(args.symbols):
            symbol_sources[symbol] = "manual_override"
    else:
        core_metadata = core_symbol_metadata(universe)
        for symbol in universe.core_symbols:
            symbol_sources[symbol] = "fixed_core"
            if symbol in core_metadata:
                symbol_metadata[symbol] = core_metadata[symbol]
    for symbol in universe.benchmarks:
        symbol_sources.setdefault(symbol, "benchmark_context")
    for symbol in universe.sector_etfs:
        symbol_sources.setdefault(symbol, "sector_context")

    dynamic_universe: dict = {
        "source": args.dynamic_source,
        "selected_count": 0,
        "symbols": [],
    }
    if args.dynamic_source == "none":
        return symbols, symbol_sources, symbol_metadata, dynamic_universe
    if args.symbols:
        dynamic_universe["skipped_reason"] = "--symbols overrides the configured universe"
        return symbols, symbol_sources, symbol_metadata, dynamic_universe

    selection = load_or_select_yfinance_dynamic_universe(
        cache_path=Path(args.dynamic_cache),
        refresh=args.refresh_dynamic,
        screeners=parse_screeners(args.dynamic_screeners),
        screener_count=args.dynamic_screener_count,
        excluded_symbols=symbols,
        limit=args.dynamic_limit,
        min_price=args.dynamic_min_price,
        min_market_cap=args.dynamic_min_market_cap,
        min_dollar_volume=args.dynamic_min_dollar_volume,
        min_average_dollar_volume=args.dynamic_min_avg_dollar_volume,
    )
    for symbol in selection.symbols:
        symbol_sources[symbol] = "dynamic_yfinance"
    symbol_metadata.update(selection.metadata_by_symbol())
    symbols = unique_symbols((*universe.core_symbols, *selection.symbols, *universe.benchmarks, *universe.sector_etfs))
    return symbols, symbol_sources, symbol_metadata, selection.summary()


def core_symbol_metadata(universe: Any) -> dict[str, dict]:
    metadata = {}
    for item in universe.metadata.get("core_symbols", []):
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        symbol = str(item["symbol"]).strip().upper()
        metadata[symbol] = {
            key: value
            for key, value in item.items()
            if key != "symbol"
        }
        metadata[symbol]["source"] = "config/core_universe.json"
    return metadata


def enrich_with_schwab_options(report: dict, args: argparse.Namespace) -> dict:
    adapter = SchwabAdapter(
        app_key=os.environ["SCHWAB_APP_KEY"],
        app_secret=os.environ["SCHWAB_APP_SECRET"],
        token_path=Path(os.environ["SCHWAB_TOKEN_PATH"]).expanduser().resolve(),
        retry_settings=RetrySettings(request_sleep_seconds=0.5),
    )
    cache = SchwabOptionChainCache(
        adapter=adapter,
        cache_dir=Path(args.options_cache_dir),
        strike_count=args.options_strike_count,
        max_dte=args.options_max_dte,
    )
    gex_cache = (
        SchwabOptionChainCache(
            adapter=adapter,
            cache_dir=Path(args.options_cache_dir),
            strike_count=args.weekly_gex_strike_count,
            max_dte=args.weekly_gex_max_dte,
        )
        if args.weekly_gex_source == "schwab"
        else None
    )
    summaries = {}
    errors = {}
    for item in report.get("watchlist", []):
        symbol = item["symbol"]
        try:
            chain = cache.get_chain(symbol, refresh=args.refresh_options)
            summary = summarize_option_chain(chain)
            if gex_cache is not None:
                try:
                    gex_chain = gex_cache.get_chain(symbol, refresh=args.refresh_options or args.refresh_gex)
                    summary["weekly_gex"] = summarize_weekly_gex(
                        gex_chain,
                        max_strike_distance_pct=args.weekly_gex_max_strike_distance_pct,
                    )
                except Exception as exc:
                    summary["weekly_gex_error"] = str(exc)[:300]
                    errors[f"{symbol}:weekly_gex"] = str(exc)[:300]
            summaries[symbol] = summary
        except Exception as exc:
            errors[symbol] = str(exc)[:300]
    updated = attach_options_context(
        report,
        summaries_by_symbol=summaries,
        errors_by_symbol=errors,
    )
    options_status = updated.setdefault("options", {})
    options_status["weekly_gex_source"] = args.weekly_gex_source
    options_status["weekly_gex_symbols"] = sorted(
        symbol for symbol, summary in summaries.items() if summary.get("weekly_gex")
    )
    return updated


def apply_symbol_cooldown(report: dict, args: argparse.Namespace) -> dict:
    journal_path = Path(args.cooldown_journal_file or args.journal_file)
    cooldown_days = max(0, int(args.symbol_cooldown_days))
    status: dict[str, Any] = {
        "enabled": True,
        "cooldown_days": cooldown_days,
        "journal_file": str(journal_path),
        "blocked_count": 0,
        "blocked_symbols": [],
    }
    if cooldown_days <= 0:
        status["enabled"] = False
        report["symbol_cooldown"] = status
        return report
    if not journal_path.exists():
        status["status"] = "skipped"
        status["reason"] = "journal_file_not_found"
        report["symbol_cooldown"] = status
        return report

    trades = aggregate_trades(load_journal_lots(journal_path))
    last_exit_by_symbol = {}
    for trade in trades:
        current = last_exit_by_symbol.get(trade.symbol)
        if current is None or trade.exit_date > current:
            last_exit_by_symbol[trade.symbol] = trade.exit_date

    kept = []
    blocked = []
    for item in report.get("watchlist", []):
        symbol = str(item.get("symbol") or "").upper()
        planned_entry = date_from_iso(item.get("planned_entry_date"))
        last_exit = last_exit_by_symbol.get(symbol)
        if planned_entry is not None and last_exit is not None:
            blocked_until = weekday_cooldown_end(last_exit, cooldown_days)
            if planned_entry <= blocked_until:
                blocked.append(
                    {
                        "symbol": symbol,
                        "last_exit_date": last_exit.isoformat(),
                        "blocked_until": blocked_until.isoformat(),
                        "planned_entry_date": planned_entry.isoformat(),
                    }
                )
                continue
        kept.append(item)

    report["watchlist"] = kept
    report["watchlist_count"] = len(kept)
    report["watchlist_concentration"] = build_watchlist_concentration(kept)
    status["status"] = "applied"
    status["blocked_count"] = len(blocked)
    status["blocked_symbols"] = blocked
    report["symbol_cooldown"] = status
    return report


def weekday_cooldown_end(exit_date: date, cooldown_days: int) -> date:
    cursor = exit_date
    remaining = cooldown_days
    while remaining > 0:
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def date_from_iso(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None


def apply_risk_throttle(report: dict, args: argparse.Namespace) -> dict:
    throttle = risk_throttle_from_journal(
        Path(args.journal_file),
        inputs=RiskThrottleInputs(
            recent_trade_lookback=args.risk_throttle_lookback,
            max_recent_r_loss=args.risk_throttle_max_recent_r_loss,
            max_month_r_loss=args.risk_throttle_max_month_r_loss,
            max_consecutive_losses=args.risk_throttle_max_consecutive_losses,
        ),
    )
    report["risk_throttle"] = throttle
    portfolio_guard = report.get("portfolio_guard")
    if isinstance(portfolio_guard, dict):
        portfolio_guard["risk_throttle_allows_new_entries"] = throttle.get("allow_new_entries")
        portfolio_guard["allow_new_entries"] = bool(
            portfolio_guard.get("allow_new_entries", True)
            and throttle.get("allow_new_entries", True)
        )
    return report


if __name__ == "__main__":
    raise SystemExit(main())
