#!/usr/bin/env python3
from __future__ import annotations

import sys
import os
import json
import shutil
from argparse import Namespace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.earnings import EarningsCalendar, EarningsEvent
from liubang.data_quality import evaluate_data_quality
from liubang.dynamic_universe import select_dynamic_universe
from liubang.backtest import (
    BacktestParams,
    Candidate,
    apply_candidate_scoring_mode,
    filter_candidates_for_regime_policy,
    filter_watchlist_for_regime_policy,
    summarize_trades,
    symbol_cooldown_end_date,
    write_backtest_trades_csv,
)
from liubang.cli_utils import load_zshrc_env_vars, parse_null_delimited_env, summarize_history_sources
from liubang.defaults import DEFAULT_HARD_STOP_PCT, DEFAULT_MAX_PULLBACK_PCT, DEFAULT_MIN_SCORE
from liubang.journal import aggregate_trades, parse_journal_lot, summarize_journal
from liubang.gex import summarize_weekly_gex
from liubang.market_data import YFinanceHistoryCache, payload_has_candles
import liubang.market_data as market_data_module
from liubang.news import parse_datetime
import liubang.news as news_module
from liubang.options import summarize_option_chain
from liubang.portfolio import build_portfolio_guard
from liubang.positions import ManualPosition, evaluate_position
from liubang.risk_throttle import RiskThrottleInputs, evaluate_risk_throttle
from liubang.risk_context import evaluate_news_risk, evaluate_options_risk
from liubang.signals import attach_options_context, build_watchlist_concentration
from liubang.trade_plan import SizingInputs, build_trade_plan
from liubang.triggers import evaluate_intraday_trigger, scan_intraday_triggers
from liubang.universe import load_universe, resolve_symbols
from scripts.prune_reports import collect_prune_candidates
from scripts.run_research_suite import build_suite_commands
from scripts.run_workflow import (
    build_signal_command,
    build_paper_action_command,
    build_paper_journal_command,
    build_paper_trigger_command,
    build_position_command,
    build_trigger_templates_command,
    is_within_market_window,
    parse_hhmm,
)
from scripts.analyze_backtest import format_analysis
from scripts.ablate_themes import group_symbols_by_theme
from scripts.build_research_universe import build_research_universe_payload
from scripts.export_watchlist_csv import build_rows as build_watchlist_rows
from scripts.export_trigger_templates import build_templates_payload
from scripts.generate_signals import apply_symbol_cooldown, weekday_cooldown_end
from scripts.apply_paper_actions import build_paper_action_update
from scripts.record_paper_triggers import build_paper_update
from scripts.render_dashboard import build_dashboard_model, render_dashboard_html
from scripts.generate_daily_review import build_review_model, build_review_sample_rows, render_review_html
from scripts.run_daily_proxy_backtest import parse_daily_bars
from scripts.summarize_journal import safe_report_prefix
from scripts.sweep_cooldown import parse_int_list, parse_mode_list, symbol_concentration as cooldown_symbol_concentration
from scripts.validate_strategy import build_validation_report, symbol_concentration


def main() -> int:
    test_universe()
    test_zshrc_env_loader()
    test_report_prefix_sanitizer()
    test_default_parameters()
    test_ranked_scoring_mode_reranks_candidates()
    test_symbol_cooldown_helpers()
    test_history_source_summary()
    test_earnings_rules()
    test_payload_detection()
    test_yfinance_stale_cache_on_refresh_error()
    test_data_quality()
    test_backtest_trade_summary()
    test_backtest_analysis_period_stability()
    test_theme_ablation_grouping()
    test_research_universe_payload()
    test_strategy_validation_concentration_gate()
    test_daily_proxy_parse_bars()
    test_options_summary()
    test_weekly_gex_summary()
    test_signal_option_attachment()
    test_watchlist_concentration()
    test_signal_symbol_cooldown()
    test_watchlist_csv_rows()
    test_news_datetime_parser()
    test_news_stale_cache_on_refresh_error()
    test_dynamic_universe_selection()
    test_trade_plan_sizing()
    test_portfolio_guard()
    test_context_risk_flags()
    test_journal_summary()
    test_risk_throttle_blocks_loss_cluster()
    test_prune_candidates(tmp_path=Path("data/cache"))
    test_market_window()
    test_research_suite_commands()
    test_workflow_signal_strategy_command()
    test_workflow_trigger_templates_command()
    test_intraday_trigger_evaluation()
    test_duplicate_position_blocks_actionable_trigger()
    test_observation_only_blocks_manual_trigger_template()
    test_paper_trigger_update_payload()
    test_paper_trigger_update_respects_opening_slots()
    test_paper_action_update_partial_and_exit()
    test_trigger_template_export_payload()
    test_position_monitor_target()
    test_position_monitor_post_target_uses_close_stop()
    test_position_monitor_ignores_pre_entry_stop()
    test_dashboard_model_and_html()
    test_daily_review_model_and_html()
    print("smoke tests passed")
    return 0


def test_universe() -> None:
    universe = load_universe(Path("config/core_universe.json"))
    assert len(universe.core_symbols) == 25
    assert "SPY" in universe.required_data_symbols
    assert "QQQ" in universe.required_data_symbols
    assert resolve_symbols(
        raw_symbols="AAPL,NVDA",
        universe_path=Path("config/core_universe.json"),
    ) == ("AAPL", "NVDA", "SPY", "QQQ")


def test_zshrc_env_loader() -> None:
    parsed = parse_null_delimited_env(b"LIUBANG_TEST\0value\0")
    assert parsed["LIUBANG_TEST"] == "value"

    env_name = "LIUBANG_SMOKE_ENV"
    previous = os.environ.pop(env_name, None)
    zshrc_path = Path("data/cache/zshrc_smoke.zsh")
    zshrc_path.parent.mkdir(parents=True, exist_ok=True)
    zshrc_path.write_text(f"{env_name}=from_zshrc\n", encoding="utf-8")
    try:
        loaded = load_zshrc_env_vars((env_name,), path=zshrc_path)
        assert loaded[env_name] == "from_zshrc"
        assert os.environ[env_name] == "from_zshrc"
    finally:
        if previous is not None:
            os.environ[env_name] = previous
        else:
            os.environ.pop(env_name, None)
        zshrc_path.unlink(missing_ok=True)


def test_report_prefix_sanitizer() -> None:
    assert safe_report_prefix("paper_journal") == "paper_journal"
    assert safe_report_prefix("paper journal!") == "paper_journal_"
    assert safe_report_prefix(" ") == "journal"


def test_history_source_summary() -> None:
    summary = summarize_history_sources(
        {
            "AAPL": {
                "source": "schwab",
                "payload": {"candles": [{"close": 1}]},
            },
            "MSFT": {
                "source": "yfinance",
                "payload": {"candles": [{"close": 1}]},
                "fallback_from": "schwab",
                "fallback_reason": "primary_returned_no_candles",
            },
        }
    )
    assert summary["by_source"] == {"schwab": 1, "yfinance": 1}
    assert summary["fallback_count"] == 1
    assert summary["fallback_symbols"]["MSFT"]["from"] == "schwab"


def test_default_parameters() -> None:
    params = BacktestParams()
    sizing = SizingInputs()
    assert params.min_score == DEFAULT_MIN_SCORE
    assert params.max_pullback_pct == DEFAULT_MAX_PULLBACK_PCT
    assert params.hard_stop_pct == DEFAULT_HARD_STOP_PCT
    assert params.symbol_cooldown_days == 0
    assert params.scoring_mode == "ranked_v2"
    assert params.regime_aware_v2_filters is True
    assert params.strong_regime_min_rs20_rank == 0.65
    assert params.strong_regime_min_overlay_score == 7.4
    assert sizing.hard_stop_pct == DEFAULT_HARD_STOP_PCT


def test_ranked_scoring_mode_reranks_candidates() -> None:
    signal_date = date(2026, 5, 18)
    entry_date = date(2026, 5, 19)
    candidates = [
        Candidate(
            symbol="OLD_HIGH_SCORE",
            signal_date=signal_date,
            entry_date=entry_date,
            score=8.0,
            strength_score=5.0,
            pullback_score=4.0,
            pullback_pct=0.03,
            prev_close=100.0,
            technical_stop=98.0,
            regime="neutral",
            notes=[],
            factor_data={
                "classic_score": 8.0,
                "rs_20d": -0.02,
                "rs_60d": -0.01,
                "sma20_distance_pct": 0.01,
                "near_20d_high": 0.94,
                "pullback_atr": 3.0,
                "volume_ratio_5d": 1.4,
                "close_location": 0.2,
            },
        ),
        Candidate(
            symbol="BETTER_RANKED",
            signal_date=signal_date,
            entry_date=entry_date,
            score=7.9,
            strength_score=4.0,
            pullback_score=3.1,
            pullback_pct=0.025,
            prev_close=100.0,
            technical_stop=98.0,
            regime="neutral",
            notes=[],
            factor_data={
                "classic_score": 7.9,
                "rs_20d": 0.08,
                "rs_60d": 0.12,
                "sma20_distance_pct": 0.08,
                "near_20d_high": 0.99,
                "pullback_atr": 1.2,
                "volume_ratio_5d": 0.65,
                "close_location": 0.9,
            },
        ),
        Candidate(
            symbol="MIDDLE",
            signal_date=signal_date,
            entry_date=entry_date,
            score=8.0,
            strength_score=4.5,
            pullback_score=3.5,
            pullback_pct=0.028,
            prev_close=100.0,
            technical_stop=98.0,
            regime="neutral",
            notes=[],
            factor_data={
                "classic_score": 8.0,
                "rs_20d": 0.02,
                "rs_60d": 0.04,
                "sma20_distance_pct": 0.04,
                "near_20d_high": 0.97,
                "pullback_atr": 1.2,
                "volume_ratio_5d": 1.0,
                "close_location": 0.5,
            },
        ),
    ]
    ranked = apply_candidate_scoring_mode(candidates, "ranked_v1")
    by_symbol = {candidate.symbol: candidate for candidate in ranked}
    assert by_symbol["BETTER_RANKED"].score > by_symbol["OLD_HIGH_SCORE"].score
    assert by_symbol["BETTER_RANKED"].factor_data["ranked_v1_score"] == by_symbol["BETTER_RANKED"].score
    ranked_v2 = apply_candidate_scoring_mode(candidates, "ranked_v2")
    by_symbol_v2 = {candidate.symbol: candidate for candidate in ranked_v2}
    assert by_symbol_v2["BETTER_RANKED"].score > by_symbol_v2["OLD_HIGH_SCORE"].score
    assert by_symbol_v2["BETTER_RANKED"].factor_data["ranked_v2_score"] == by_symbol_v2["BETTER_RANKED"].score
    assert candidates[0].score == 8.0


def test_regime_aware_v2_candidate_filters() -> None:
    params = BacktestParams()
    signal_date = date(2026, 5, 18)
    entry_date = date(2026, 5, 19)

    def candidate(symbol: str, *, regime: str, score: float = 7.5, rs20: float = 0.7, overlay: float = 7.5, atr: float = 0.06, pullback: float = 0.04) -> Candidate:
        return Candidate(
            symbol=symbol,
            signal_date=signal_date,
            entry_date=entry_date,
            score=score,
            strength_score=4.0,
            pullback_score=3.5,
            pullback_pct=pullback,
            prev_close=100.0,
            technical_stop=98.0,
            regime=regime,
            notes=[],
            factor_data={
                "ranked_v2_rs_20d_rank": rs20,
                "ranked_v2_overlay_score": overlay,
                "atr20_pct": atr,
            },
        )

    filtered = filter_candidates_for_regime_policy(
        [
            candidate("STRONG_PASS", regime="strong"),
            candidate("STRONG_WEAK_RS", regime="strong", rs20=0.6),
            candidate("STRONG_HIGH_ATR", regime="strong", atr=0.09),
            candidate("NEUTRAL_PASS", regime="neutral", rs20=0.0, overlay=0.0, atr=0.2, pullback=0.06),
            candidate("WEAK_BLOCK", regime="weak"),
        ],
        params,
    )

    assert [item.symbol for item in filtered] == ["STRONG_PASS", "NEUTRAL_PASS"]
    assert any("regime-aware v2 strong filter" in note for note in filtered[0].notes)


def test_regime_aware_v2_watchlist_filters() -> None:
    params = BacktestParams()
    watchlist = [
        {
            "symbol": "STRONG_PASS",
            "market_regime": "strong",
            "total_score": 7.5,
            "pullback_pct": 0.04,
            "risk_notes": [],
            "factor_data": {
                "ranked_v2_rs_20d_rank": 0.7,
                "ranked_v2_overlay_score": 7.5,
                "atr20_pct": 0.06,
            },
        },
        {
            "symbol": "WEAK_BLOCK",
            "market_regime": "weak",
            "total_score": 9.0,
            "pullback_pct": 0.02,
            "risk_notes": [],
            "factor_data": {
                "ranked_v2_rs_20d_rank": 1.0,
                "ranked_v2_overlay_score": 9.0,
                "atr20_pct": 0.02,
            },
        },
    ]

    filtered = filter_watchlist_for_regime_policy(watchlist, params)
    assert [item["symbol"] for item in filtered] == ["STRONG_PASS"]
    assert filtered[0]["regime_policy"] == "regime_aware_v2_strong"


def test_symbol_cooldown_helpers() -> None:
    dates = [
        date(2026, 5, 18),
        date(2026, 5, 19),
        date(2026, 5, 20),
        date(2026, 5, 21),
    ]
    assert symbol_cooldown_end_date(dates, date(2026, 5, 18), 0) is None
    assert symbol_cooldown_end_date(dates, date(2026, 5, 18), 2) == date(2026, 5, 20)
    assert symbol_cooldown_end_date(dates, date(2026, 5, 21), 3) == date(2026, 5, 21)
    assert parse_int_list("0,5,5,10") == (0, 5, 10)
    assert parse_mode_list("backtest,hourly_proxy") == ("backtest", "hourly_proxy")
    concentration = cooldown_symbol_concentration(
        {
            "total_pnl": 100.0,
            "by_symbol": {
                "AAPL": {"pnl": 60.0},
                "MSFT": {"pnl": 30.0},
                "NVDA": {"pnl": 10.0},
            },
        }
    )
    assert concentration["top1_symbol"] == "AAPL"
    assert concentration["top3_net_share"] == 1.0


def test_earnings_rules() -> None:
    calendar = EarningsCalendar(
        events=(EarningsEvent("AAPL", date(2026, 5, 20), "amc"),),
        source="test",
    )
    trading_dates = [date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 20)]
    assert calendar.blocked_entry_dates("AAPL", trading_dates) == {
        date(2026, 5, 19),
        date(2026, 5, 20),
    }
    assert calendar.exit_dates("AAPL", trading_dates) == {date(2026, 5, 20)}


def test_payload_detection() -> None:
    assert payload_has_candles({"payload": {"candles": [{"close": 1}]}})
    assert not payload_has_candles({"payload": {"candles": []}})


def test_yfinance_stale_cache_on_refresh_error() -> None:
    cache_dir = Path("data/cache/yfinance_smoke")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = YFinanceHistoryCache(
        cache_dir=cache_dir,
        period="60d",
        interval="5m",
        max_retries=0,
        retry_sleep_seconds=0,
    )
    cache_path = cache.cache_path("AAPL")
    cache_path.write_text(
        json.dumps(
            {
                "symbol": "AAPL",
                "source": "yfinance",
                "payload": {"candles": [{"close": 1.0}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = market_data_module.fetch_yfinance_history
    market_data_module.fetch_yfinance_history = (
        lambda symbol, period, interval: (_ for _ in ()).throw(RuntimeError("temporary yfinance outage"))
    )
    try:
        payload = cache.get_history("AAPL", refresh=True)
        assert payload["payload"]["candles"][0]["close"] == 1.0
        assert payload["stale_due_to_empty_refresh"]
        assert payload["stale_refresh_error"] == "yfinance_fetch_failed"
    finally:
        market_data_module.fetch_yfinance_history = original
        cache_path.unlink(missing_ok=True)
        cache_dir.rmdir()


def test_data_quality() -> None:
    quality = evaluate_data_quality(
        {
            "AAPL": {"daily_bars": 30, "start": "2026-04-01", "end": "2026-05-20"},
            "MSFT": {"daily_bars": 10, "start": "2026-04-01", "end": "2026-05-19"},
        },
        expected_symbols=("AAPL", "MSFT", "NVDA"),
    )
    assert quality["status"] == "warning"
    assert "NVDA" in quality["missing_symbols"]
    assert "MSFT" in quality["insufficient_symbols"]
    assert "MSFT" in quality["stale_symbols"]


def test_backtest_trade_summary() -> None:
    trades = [
        {
            "symbol": "AAPL",
            "regime": "neutral",
            "primary_exit_reason": "target_1",
            "final_exit_date": "2026-05-21",
            "pnl": 100.0,
            "r_multiple": 1.0,
            "holding_weekdays": 2,
        },
        {
            "symbol": "MSFT",
            "regime": "neutral",
            "primary_exit_reason": "stop",
            "final_exit_date": "2026-05-21",
            "pnl": -50.0,
            "r_multiple": -0.5,
            "holding_weekdays": 1,
        },
    ]
    summary = summarize_trades(trades, 100_000.0, [{"equity": 100_000.0}, {"equity": 99_950.0}])
    assert summary["average_r"] == 0.25
    assert summary["profit_factor"] == 2.0
    assert summary["by_exit_reason"]["stop"]["trades"] == 1
    assert summary["by_symbol"]["AAPL"]["average_r"] == 1.0
    assert summary["by_exit_month"]["2026-05"]["trades"] == 2
    csv_path = Path("data/cache/backtest_trades_smoke.csv")
    write_backtest_trades_csv(csv_path, trades)
    assert csv_path.exists()
    csv_path.unlink()


def test_backtest_analysis_period_stability() -> None:
    report = {
        "candidate_count": 2,
        "diagnostics": {
            "entry_attempts": 2,
            "filled_entries": 2,
            "unfilled_entries": 0,
            "skipped_no_slot": 0,
        },
        "summary": {
            "trade_count": 2,
            "total_pnl": 50.0,
            "return_pct": 0.05,
            "average_r": 0.25,
            "profit_factor": 2.0,
            "max_drawdown_pct": 1.0,
            "by_symbol": {},
            "by_exit_reason": {},
            "by_exit_month": {
                "2026-05": {"pnl": 50.0, "trades": 2, "win_rate": 0.5, "average_r": 0.25}
            },
        },
        "trades": [
            {"final_exit_date": "2026-05-20", "pnl": 100.0, "r_multiple": 1.0},
            {"final_exit_date": "2026-05-21", "pnl": -50.0, "r_multiple": -0.5},
        ],
    }
    output = format_analysis(
        report,
        Path("backtest_test.json"),
        limit=5,
        theme_by_symbol={"AAPL": "megacap_tech", "MSFT": "megacap_tech"},
    )
    assert "Concentration:" in output
    assert "Best themes:" in output
    assert "Period stability:" in output
    assert "Year stability:" in output
    assert "Worst quarters:" in output
    assert "Month stability:" in output


def test_theme_ablation_grouping() -> None:
    grouped = group_symbols_by_theme(
        ("AAPL", "MSFT", "SPY", "UNKNOWN"),
        {"AAPL": "megacap_tech", "MSFT": "megacap_tech"},
    )
    assert grouped == {"megacap_tech": ("AAPL", "MSFT")}


def test_research_universe_payload() -> None:
    payload = build_research_universe_payload(
        base_payload={
            "name": "base",
            "benchmarks": ["SPY", "QQQ"],
            "sector_etfs": ["XLK"],
            "core_symbols": [{"symbol": "AAPL", "theme": "core"}],
        },
        dynamic_selection=None,
        extra_symbols=("MSFT", "AAPL"),
    )
    assert payload["generation"]["base_count"] == 1
    assert [item["symbol"] for item in payload["core_symbols"]] == ["AAPL", "MSFT"]


def test_strategy_validation_concentration_gate() -> None:
    summary = {
        "total_pnl": 100.0,
        "trade_count": 10,
        "return_pct": 1.0,
        "average_r": 0.1,
        "profit_factor": 1.5,
        "max_drawdown_pct": 2.0,
        "by_symbol": {
            "AAPL": {"pnl": 90.0},
            "MSFT": {"pnl": 10.0},
            "TSLA": {"pnl": -5.0},
        },
        "by_exit_month": {
            "2026-01": {"pnl": 50.0},
            "2026-02": {"pnl": -10.0},
        },
    }
    concentration = symbol_concentration(summary)
    assert concentration["top1_symbol"] == "AAPL"
    assert concentration["top1_net_share"] == 0.9

    report = build_validation_report(
        backtest={
            "candidate_count": 10,
            "config": {"params": {"max_hold_days": 5}},
            "summary": summary,
        },
        stress={
            "stability": {
                "scenario_count": 1,
                "positive_scenarios": 1,
                "negative_scenarios": 0,
                "min_return_pct": 1.0,
                "max_return_pct": 1.0,
            },
            "rows": [{"profit_factor": 1.2}],
        },
        hourly_proxy={
            "candidate_count": 15,
            "config": {"params": {"max_hold_days": 5}},
            "summary": summary | {
                "trade_count": 15,
                "by_symbol": {
                    "AAPL": {"pnl": 30.0},
                    "MSFT": {"pnl": 25.0},
                    "NVDA": {"pnl": 20.0},
                    "AMD": {"pnl": 25.0},
                },
            },
        },
        daily_proxy={
            "candidate_count": 20,
            "summary": summary | {
                "trade_count": 20,
                "by_symbol": {
                    "AAPL": {"pnl": 20.0},
                    "MSFT": {"pnl": 15.0},
                    "NVDA": {"pnl": 10.0},
                    "AMD": {"pnl": 55.0},
                },
                "by_exit_month": {
                    "2026-01": {"pnl": 50.0},
                    "2026-02": {"pnl": 10.0},
                },
            },
        },
        paths={"backtest": "b.json", "stress": "s.json", "hourly_proxy": "h.json", "daily_proxy": "d.json"},
        symbol_ablation=[
            {
                "removed_symbol": "AAPL",
                "return_pct": "-1.0",
                "delta_return_pct": "-2.0",
            }
        ],
        theme_ablation=[
            {
                "removed_theme": "megacap_tech",
                "removed_symbols": "AAPL,MSFT",
                "return_pct": "1.0",
                "delta_return_pct": "-0.5",
            }
        ],
        args=Namespace(
            min_5m_trades=5,
            min_hourly_trades=10,
            min_daily_trades=10,
            min_profit_factor=1.2,
            max_5m_drawdown_pct=10.0,
            max_hourly_drawdown_pct=20.0,
            max_daily_drawdown_pct=20.0,
            min_positive_month_share=0.5,
            max_5m_top1_net_share=0.5,
            max_5m_top3_net_share=1.0,
            max_hourly_top1_net_share=0.5,
            max_hourly_top3_net_share=1.0,
            max_daily_top1_net_share=0.7,
            max_daily_top3_net_share=1.0,
        ),
    )
    assert report["overall_status"] == "fail"
    assert "5m_backtest_top1_net_concentration" in report["failed_gates"]
    assert "symbol_ablation_keeps_positive_without_top_dependency" in report["failed_gates"]


def test_daily_proxy_parse_bars() -> None:
    bars = parse_daily_bars(
        {
            "bars": [
                {
                    "date": "2026-05-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1000,
                }
            ]
        }
    )
    assert len(bars) == 1
    assert bars[0].session_date == date(2026, 5, 20)
    assert bars[0].close == 10.5


def test_options_summary() -> None:
    chain = {
        "symbol": "AAPL",
        "source": "test",
        "payload": {
            "callExpDateMap": {
                "2026-05-22:2": {
                    "300.0": [
                        {"bid": 1.0, "ask": 1.2, "totalVolume": 10, "openInterest": 100}
                    ]
                }
            },
            "putExpDateMap": {
                "2026-05-22:2": {
                    "295.0": [
                        {"bid": 1.5, "ask": 1.8, "totalVolume": 5, "openInterest": 50}
                    ]
                }
            },
        },
    }
    summary = summarize_option_chain(chain)
    assert summary["call_volume"] == 10
    assert summary["put_volume"] == 5
    assert summary["put_call_volume_ratio"] == 0.5
    assert summary["put_call_oi_ratio"] == 0.5


def test_weekly_gex_summary() -> None:
    chain = {
        "symbol": "AAPL",
        "source": "test",
        "payload": {
            "underlyingPrice": 100.0,
            "callExpDateMap": {
                "2026-05-22:2": {
                    "100.0": [{"gamma": 0.05, "openInterest": 10, "multiplier": 100}],
                    "105.0": [{"gamma": 0.03, "openInterest": 30, "multiplier": 100}],
                }
            },
            "putExpDateMap": {
                "2026-05-22:2": {
                    "95.0": [{"gamma": 0.04, "openInterest": 20, "multiplier": 100}]
                }
            },
        },
    }
    summary = summarize_weekly_gex(chain, as_of_date=date(2026, 5, 20), max_strike_distance_pct=0.2)
    assert summary["week_end_date"] == "2026-05-22"
    assert summary["contract_count"] == 3
    assert summary["call_gex"] == 14000.0
    assert summary["put_gex"] == -8000.0
    assert summary["net_gex"] == 6000.0
    assert summary["regime"] == "positive"
    assert summary["call_wall"] == 105.0
    assert summary["put_wall"] == 95.0


def test_signal_option_attachment() -> None:
    report = {"watchlist": [{"symbol": "AAPL", "risk_notes": []}]}
    attach_options_context(
        report,
        summaries_by_symbol={
            "AAPL": {
                "symbol": "AAPL",
                "put_call_oi_ratio": 0.5,
                "weekly_gex": {
                    "regime": "negative",
                    "net_gex": -1000.0,
                    "risk_notes": ["negative_weekly_gamma_vol_expansion"],
                },
            }
        },
    )
    assert report["watchlist"][0]["options_context"]["put_call_oi_ratio"] == 0.5
    assert "options_pc_oi=0.5" in report["watchlist"][0]["risk_notes"]
    assert "weekly_gex=negative" in report["watchlist"][0]["risk_notes"]
    assert "weekly_gex_negative_weekly_gamma_vol_expansion" in report["watchlist"][0]["risk_notes"]


def test_watchlist_concentration() -> None:
    concentration = build_watchlist_concentration(
        [
            {"symbol": "AAPL", "source": "fixed_core", "source_metadata": {"theme": "megacap_tech"}},
            {"symbol": "MSFT", "source": "fixed_core", "source_metadata": {"theme": "megacap_tech"}},
            {"symbol": "NVDA", "source": "fixed_core", "source_metadata": {"theme": "ai_semiconductor"}},
        ]
    )
    assert concentration["top_theme"] == "megacap_tech"
    assert concentration["top_theme_share"] == 0.6667
    assert "watchlist_theme_concentration" in concentration["warnings"]


def test_signal_symbol_cooldown() -> None:
    assert weekday_cooldown_end(date(2026, 5, 15), 3) == date(2026, 5, 20)
    journal_path = Path("data/cache/symbol_cooldown_journal.csv")
    journal_path.write_text(
        "\n".join(
            [
                "trade_id,symbol,side,entry_date,exit_date,entry_price,exit_price,shares,initial_stop_price,fees",
                "AAPL-1,AAPL,long,2026-05-14,2026-05-15,100,101,10,99,0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        report = {
            "watchlist": [
                {"symbol": "AAPL", "planned_entry_date": "2026-05-19", "source": "fixed_core"},
                {"symbol": "MSFT", "planned_entry_date": "2026-05-19", "source": "fixed_core"},
            ],
            "watchlist_count": 2,
        }
        updated = apply_symbol_cooldown(
            report,
            Namespace(symbol_cooldown_days=3, cooldown_journal_file=str(journal_path), journal_file="missing.csv"),
        )
        assert updated["watchlist_count"] == 1
        assert updated["watchlist"][0]["symbol"] == "MSFT"
        assert updated["symbol_cooldown"]["blocked_symbols"][0]["symbol"] == "AAPL"
    finally:
        journal_path.unlink(missing_ok=True)


def test_watchlist_csv_rows() -> None:
    rows = build_watchlist_rows(
        {
            "portfolio_guard": {"allow_new_entries": True, "available_slots": 2},
            "risk_throttle": {"status": "ok", "allow_new_entries": True},
            "watchlist_concentration": {
                "top_theme": "megacap_tech",
                "top_theme_share": 1.0,
                "warnings": ["watchlist_theme_concentration"],
            },
            "watchlist": [
                {
                    "symbol": "AAPL",
                    "source": "fixed_core",
                    "source_metadata": {"theme": "megacap_tech"},
                    "planned_entry_date": "2026-05-20",
                    "market_regime": "neutral",
                    "total_score": 9.0,
                    "trade_plan": {"suggested_shares": 10},
                    "recent_news": [{"title": "Headline"}],
                    "news_risk": {"level": "low"},
                    "options_context": {
                        "put_call_oi_ratio": 0.5,
                        "weekly_gex": {
                            "regime": "positive",
                            "net_gex": 1200000.0,
                            "call_wall": 105.0,
                            "put_wall": 95.0,
                        },
                    },
                    "options_risk": {"level": "low"},
                }
            ],
        }
    )
    assert rows[0]["rank"] == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["theme"] == "megacap_tech"
    assert rows[0]["suggested_shares"] == 10
    assert rows[0]["watchlist_top_theme"] == "megacap_tech"
    assert rows[0]["top_news_headline"] == "Headline"
    assert rows[0]["weekly_gex_regime"] == "positive"
    assert rows[0]["weekly_call_wall"] == 105.0


def test_news_datetime_parser() -> None:
    parsed = parse_datetime("2026-05-19T21:07:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_news_stale_cache_on_refresh_error() -> None:
    cache_dir = Path("data/cache/news_smoke")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "AAPL.json"
    cache_path.write_text(
        json.dumps(
            {
                "symbol": "AAPL",
                "source": "yfinance",
                "items": [
                    {
                        "symbol": "AAPL",
                        "title": "Cached headline",
                        "published_at": "2026-05-20T13:30:00Z",
                        "provider": "test",
                        "url": None,
                        "source": "yfinance.news",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = news_module.fetch_yfinance_news
    news_module.fetch_yfinance_news = lambda symbol, limit: {
        "symbol": symbol,
        "source": "yfinance",
        "error": "temporary failure",
        "items": [],
    }
    try:
        items = news_module.load_or_fetch_symbol_news(
            "AAPL",
            cache_dir=cache_dir,
            refresh=True,
            limit=3,
        )
        assert items[0].title == "Cached headline"
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached["stale_due_to_fetch_error"] == "temporary failure"
    finally:
        news_module.fetch_yfinance_news = original
        cache_path.unlink(missing_ok=True)
        cache_dir.rmdir()


def test_dynamic_universe_selection() -> None:
    payload = {
        "source": "yfinance.screen",
        "fetched_at": "2026-05-20T00:00:00+00:00",
        "screeners": ["most_actives"],
        "quotes": [
            {
                "symbol": "INTC",
                "quoteType": "EQUITY",
                "market": "us_market",
                "region": "US",
                "exchange": "NMS",
                "regularMarketPrice": 50.0,
                "regularMarketVolume": 30_000_000,
                "averageDailyVolume10Day": 10_000_000,
                "marketCap": 200_000_000_000,
                "screeners": ["most_actives"],
            },
            {
                "symbol": "AAPL",
                "quoteType": "EQUITY",
                "market": "us_market",
                "region": "US",
                "exchange": "NMS",
                "regularMarketPrice": 200.0,
                "regularMarketVolume": 20_000_000,
                "averageDailyVolume10Day": 50_000_000,
                "marketCap": 3_000_000_000_000,
                "screeners": ["most_actives"],
            },
            {
                "symbol": "LOWLIQ",
                "quoteType": "EQUITY",
                "market": "us_market",
                "region": "US",
                "exchange": "NMS",
                "regularMarketPrice": 20.0,
                "regularMarketVolume": 100_000,
                "averageDailyVolume10Day": 100_000,
                "marketCap": 10_000_000_000,
                "screeners": ["most_actives"],
            },
        ],
        "errors": {},
    }
    selection = select_dynamic_universe(
        payload,
        cache_path=None,
        excluded_symbols=("AAPL",),
        limit=5,
        min_price=5.0,
        min_market_cap=2_000_000_000.0,
        min_dollar_volume=1_000_000_000.0,
        min_average_dollar_volume=300_000_000.0,
    )
    assert selection.symbols == ("INTC",)
    assert selection.metadata_by_symbol()["INTC"]["source"] == "dynamic_yfinance"


def test_trade_plan_sizing() -> None:
    plan = build_trade_plan(
        entry_price=100.0,
        technical_stop=96.0,
        market_regime="neutral",
        sizing=SizingInputs(account_equity=100_000.0, hard_stop_pct=0.03),
    )
    assert plan.stop_price == 97.0
    assert plan.risk_per_share == 3.0
    assert plan.risk_based_shares == 333
    assert plan.capital_cap_shares == 200
    assert plan.suggested_shares == 200
    assert plan.binding_constraint == "capital_cap"


def test_portfolio_guard() -> None:
    position = ManualPosition(
        symbol="AAPL",
        entry_date=date(2026, 5, 20),
        entry_price=100.0,
        shares=10,
        remaining_shares=10,
        initial_stop_price=97.0,
        current_stop_price=97.0,
        target_price=103.0,
    )
    guard = build_portfolio_guard(
        market_regime="neutral",
        open_positions=(position,),
        positions_path=None,
    )
    assert guard["max_positions"] == 2
    assert guard["current_positions"] == 1
    assert guard["available_slots"] == 1
    assert guard["current_gross_exposure_value"] == 1000.0
    assert guard["available_exposure_value"] == 39000.0
    assert guard["allow_new_entries"]

    oversized = ManualPosition(
        symbol="MSFT",
        entry_date=date(2026, 5, 20),
        entry_price=100.0,
        shares=500,
        remaining_shares=500,
        initial_stop_price=96.0,
        current_stop_price=96.0,
        target_price=104.0,
    )
    blocked = build_portfolio_guard(
        market_regime="neutral",
        open_positions=(oversized,),
        positions_path=None,
    )
    assert blocked["available_slots"] == 1
    assert blocked["available_exposure_value"] == 0.0
    assert not blocked["exposure_allows_new_entries"]
    assert not blocked["allow_new_entries"]


def test_context_risk_flags() -> None:
    news_risk = evaluate_news_risk(
        [{"title": "Example Corp faces SEC investigation and lawsuit", "provider": "test"}]
    )
    assert news_risk["level"] == "high"
    assert "legal" in news_risk["categories"]
    assert evaluate_news_risk(
        [{"title": "Cybersecurity company announces new platform", "provider": "test"}]
    )["level"] == "low"

    options_risk = evaluate_options_risk(
        {
            "put_call_oi_ratio": 2.5,
            "put_call_volume_ratio": 0.8,
            "avg_spread_pct": 5.0,
            "contracts": 20,
        }
    )
    assert options_risk["level"] == "elevated"
    assert options_risk["flags"][0]["name"] == "high_put_call_open_interest_ratio"


def test_journal_summary() -> None:
    lot1 = parse_journal_lot(
        {
            "trade_id": "T1",
            "symbol": "AAPL",
            "side": "long",
            "entry_date": "2026-05-20",
            "exit_date": "2026-05-21",
            "entry_price": "100",
            "exit_price": "106",
            "shares": "10",
            "initial_stop_price": "97",
            "fees": "0",
            "setup": "strong_pullback",
            "source": "test",
        },
        index=1,
    )
    lot2 = parse_journal_lot(
        {
            "trade_id": "T2",
            "symbol": "MSFT",
            "side": "long",
            "entry_date": "2026-05-20",
            "exit_date": "2026-05-21",
            "entry_price": "50",
            "exit_price": "48",
            "shares": "10",
            "initial_stop_price": "47",
            "fees": "0",
            "setup": "strong_pullback",
            "source": "test",
        },
        index=2,
    )
    trades = aggregate_trades((lot1, lot2))
    assert len(trades) == 2
    report = summarize_journal(lots=(lot1, lot2), initial_equity=100_000.0)
    assert report["trade_count"] == 2
    assert report["realized_pnl"] == 40.0
    assert report["win_rate"] == 0.5
    assert report["average_r"] == 0.6667


def test_risk_throttle_blocks_loss_cluster() -> None:
    lots = tuple(
        parse_journal_lot(
            {
                "trade_id": f"T{idx}",
                "symbol": "AAPL",
                "side": "long",
                "entry_date": "2026-05-20",
                "exit_date": f"2026-05-2{idx}",
                "entry_price": "100",
                "exit_price": "97",
                "shares": "10",
                "initial_stop_price": "97",
                "fees": "0",
                "setup": "strong_pullback",
                "source": "test",
            },
            index=idx,
        )
        for idx in range(1, 4)
    )
    trades = aggregate_trades(lots)
    throttle = evaluate_risk_throttle(
        trades=trades,
        inputs=RiskThrottleInputs(max_consecutive_losses=3),
        as_of_date=date(2026, 5, 25),
    )
    assert not throttle["allow_new_entries"]
    assert "consecutive_loss_limit" in throttle["flags"]


def test_prune_candidates(tmp_path: Path) -> None:
    test_dir = tmp_path / "prune_smoke"
    test_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for idx in range(3):
        path = test_dir / f"signals_test_{idx}.json"
        path.write_text("{}\n", encoding="utf-8")
        created.append(path)
    candidates = collect_prune_candidates(test_dir, keep=1)
    assert len(candidates) == 2
    for path in created:
        path.unlink(missing_ok=True)
    test_dir.rmdir()


def test_market_window() -> None:
    eastern = ZoneInfo("America/New_York")
    assert is_within_market_window(
        datetime(2026, 5, 20, 9, 35, tzinfo=eastern),
        start=parse_hhmm("09:31"),
        end=parse_hhmm("15:55"),
    )
    assert not is_within_market_window(
        datetime(2026, 5, 20, 8, 30, tzinfo=eastern),
        start=parse_hhmm("09:31"),
        end=parse_hhmm("15:55"),
    )
    assert not is_within_market_window(
        datetime(2026, 5, 23, 10, 0, tzinfo=eastern),
        start=parse_hhmm("09:31"),
        end=parse_hhmm("15:55"),
    )


def test_research_suite_commands() -> None:
    commands = build_suite_commands(
        Namespace(
            output_dir="data/exports",
            cache_dir="data/cache",
            universe="config/research_universe_dynamic.json",
            trades_csv="data/exports/latest_backtest_trades.csv",
            min_score=7.0,
            min_pullback_pct=0.01,
            max_pullback_pct=0.06,
            scoring_mode="classic",
            hard_stop_pct=0.03,
            symbol_cooldown_days=3,
            refresh=False,
            refresh_hourly=False,
            refresh_daily=False,
            refresh_earnings=False,
            fail_on_gate_failure=True,
        )
    )
    names = [name for name, _ in commands]
    assert names == [
        "backtest",
        "stress",
        "hourly_proxy",
        "daily_proxy",
        "symbol_ablation",
        "theme_ablation",
        "strategy_validation",
    ]
    assert "--fail-on-gate-failure" in commands[-1][1]
    assert "config/research_universe_dynamic.json" in commands[0][1]
    assert "--symbol-cooldown-days" in commands[0][1]
    assert "--scoring-mode" in commands[0][1]


def test_workflow_signal_strategy_command() -> None:
    command = build_signal_command(
        Namespace(
            output_dir="data/exports",
            universe="config/research_universe_dynamic.json",
            dynamic_source="none",
            dynamic_limit=None,
            refresh_history=False,
            refresh_dynamic=False,
            refresh_earnings=False,
            refresh_news=False,
            refresh_options=False,
            send_discord=False,
            positions_file="data/manual_positions.json",
            journal_file="data/trade_journal.csv",
            min_score=7.0,
            min_pullback_pct=None,
            max_pullback_pct=0.06,
            scoring_mode=None,
            symbol_cooldown_days=3,
            cooldown_journal_file="data/paper_trade_journal.csv",
            account_equity=None,
            risk_per_trade_pct=None,
            max_position_pct=None,
            hard_stop_pct=0.03,
            first_target_r=None,
        )
    )
    assert "--universe" in command
    assert "config/research_universe_dynamic.json" in command
    assert "--dynamic-source" in command
    assert "--symbol-cooldown-days" in command
    assert "--cooldown-journal-file" in command
    assert "--hard-stop-pct" in command


def test_workflow_trigger_templates_command() -> None:
    command = build_trigger_templates_command(Namespace(output_dir="data/exports"))
    assert command[1] == "scripts/export_trigger_templates.py"
    assert command[-2:] == ["--exports-dir", "data/exports"]
    args = Namespace(
        output_dir="data/exports",
        paper_positions_file="data/paper_positions.json",
        paper_journal_file="data/paper_trade_journal.csv",
    )
    paper_command = build_paper_trigger_command(args)
    assert paper_command[1] == "scripts/record_paper_triggers.py"
    assert "--paper-positions" in paper_command
    assert "data/paper_positions.json" in paper_command
    assert "--paper-journal" in paper_command
    assert "data/paper_trade_journal.csv" in paper_command
    action_command = build_paper_action_command(args)
    assert action_command[1] == "scripts/apply_paper_actions.py"
    assert action_command[-2:] == ["--paper-journal", "data/paper_trade_journal.csv"]
    journal_command = build_paper_journal_command(args)
    assert journal_command[1] == "scripts/summarize_journal.py"
    assert "--report-prefix" in journal_command
    assert "paper_journal" in journal_command
    position_command = build_position_command(
        Namespace(
            positions_file="data/manual_positions.json",
            output_dir="data/exports",
            use_cache_for_positions=True,
            send_discord=False,
            send_empty_discord=False,
        ),
        positions_file="data/paper_positions.json",
    )
    assert position_command[3] == "data/paper_positions.json"
    assert "--use-cache" in position_command


def test_intraday_trigger_evaluation() -> None:
    signal = {
        "symbol": "AAPL",
        "planned_entry_date": "2026-05-20",
        "total_score": 8.0,
        "source": "fixed_core",
        "technical_stop_reference": 9.9,
    }
    payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 1_000),
                candle("2026-05-20T13:35:00+00:00", 10.1, 10.3, 10.0, 10.2, 1_000),
                candle("2026-05-20T13:40:00+00:00", 10.2, 10.6, 10.2, 10.5, 2_000),
            ]
        }
    }
    result = evaluate_intraday_trigger(
        signal=signal,
        history_payload=payload,
        current_session_date=date(2026, 5, 20),
    )
    assert result.triggered
    assert result.status == "triggered"
    assert result.conditions is not None
    assert result.conditions["close_above_prior_5m_high"]
    assert result.trade_plan is not None
    assert result.trade_plan["suggested_shares"] > 0
    assert result.trade_plan["stop_price"] > 0
    assert result.manual_position_template is not None
    assert result.manual_position_template["symbol"] == "AAPL"
    assert result.manual_position_template["remaining_shares"] == result.trade_plan["suggested_shares"]

    suspect = evaluate_intraday_trigger(
        signal=signal,
        history_payload={
            "payload": {
                "candles": [
                    candle("2026-05-20T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 1_000),
                    candle("2026-05-20T13:35:00+00:00", 10.1, 10.3, 10.0, 10.2, 0),
                    candle("2026-05-20T13:40:00+00:00", 10.2, 10.6, 10.2, 10.5, 0),
                ]
            }
        },
        current_session_date=date(2026, 5, 20),
    )
    assert suspect.status == "data_suspect"
    assert not suspect.triggered
    assert suspect.data_quality_warnings == ["zero_volume_latest_two_bars"]


def test_duplicate_position_blocks_actionable_trigger() -> None:
    signal = {
        "symbol": "AAPL",
        "planned_entry_date": "2026-05-20",
        "total_score": 8.0,
        "source": "fixed_core",
        "technical_stop_reference": 9.9,
        "market_regime": "neutral",
    }
    history = {
        "AAPL": {
            "payload": {
                "candles": [
                    candle("2026-05-20T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 1_000),
                    candle("2026-05-20T13:35:00+00:00", 10.1, 10.3, 10.0, 10.2, 1_000),
                    candle("2026-05-20T13:40:00+00:00", 10.2, 10.6, 10.2, 10.5, 2_000),
                ]
            }
        }
    }
    report = scan_intraday_triggers(
        signal_report={
            "generated_at": "2026-05-20T00:00:00+00:00",
            "watchlist": [signal],
            "portfolio_guard": {
                "allow_new_entries": True,
                "open_symbols": ["AAPL"],
            },
        },
        history_by_symbol=history,
        current_session_date=date(2026, 5, 20),
    )
    item = report["evaluations"][0]
    assert report["triggered_count"] == 1
    assert report["actionable_triggered_count"] == 0
    assert item["triggered"]
    assert item["duplicate_open_position"]
    assert item["action"] == "do_not_open_duplicate_symbol_position"
    assert "manual_position_template" not in item


def test_observation_only_blocks_manual_trigger_template() -> None:
    signal = {
        "symbol": "AAPL",
        "planned_entry_date": "2026-05-20",
        "total_score": 8.0,
        "source": "fixed_core",
        "technical_stop_reference": 9.9,
        "market_regime": "neutral",
    }
    history = {
        "AAPL": {
            "payload": {
                "candles": [
                    candle("2026-05-20T13:30:00+00:00", 10.0, 10.2, 9.9, 10.1, 1_000),
                    candle("2026-05-20T13:35:00+00:00", 10.1, 10.3, 10.0, 10.2, 1_000),
                    candle("2026-05-20T13:40:00+00:00", 10.2, 10.6, 10.2, 10.5, 2_000),
                ]
            }
        }
    }
    report = scan_intraday_triggers(
        signal_report={
            "generated_at": "2026-05-20T00:00:00+00:00",
            "watchlist": [signal],
            "portfolio_guard": {
                "allow_new_entries": True,
                "open_symbols": [],
            },
        },
        history_by_symbol=history,
        current_session_date=date(2026, 5, 20),
        observation_only=True,
    )
    item = report["evaluations"][0]
    assert report["triggered_count"] == 1
    assert report["actionable_triggered_count"] == 0
    assert report["observation_only"]
    assert item["action"] == "observe_only_no_manual_entry"
    assert item["trigger_rank_score"] > 0
    assert "weekly_gex_risk_tags" in item
    assert "manual_position_template" not in item


def test_paper_trigger_update_payload() -> None:
    update = build_paper_update(
        {
            "evaluations": [
                {
                    "symbol": "AAPL",
                    "triggered": True,
                    "action": "observe_only_no_manual_entry",
                    "planned_entry_date": "2026-05-20",
                    "last_bar_time_et": "2026-05-20T09:35:00-04:00",
                    "trigger_rank_score": 80.0,
                    "signal_score": 8.0,
                    "confirmation_margin_pct": 0.01,
                    "trade_plan": {
                        "entry_price_reference": 10.5,
                        "stop_price": 9.9,
                        "first_target_price": 11.1,
                        "suggested_shares": 100,
                    },
                },
                {
                    "symbol": "MSFT",
                    "triggered": True,
                    "action": "observe_only_no_manual_entry",
                    "planned_entry_date": "2026-05-20",
                    "trade_plan": {
                        "entry_price_reference": 20.5,
                        "stop_price": 19.9,
                        "first_target_price": 21.1,
                        "suggested_shares": 100,
                    },
                },
            ]
        },
        existing_payload={"positions": [{"symbol": "MSFT", "shares": 10, "remaining_shares": 10}]},
        paper_journal_entries=set(),
        source_path=Path("triggers_test.json"),
    )
    assert len(update["new_positions"]) == 1
    assert update["new_positions"][0]["symbol"] == "AAPL"
    assert update["new_positions"][0]["entry_price"] == 10.5
    assert update["new_positions"][0]["entry_time_et"] == "2026-05-20T09:35:00-04:00"
    assert update["new_positions"][0]["paper_candidate_rank"] == 1
    assert update["new_positions"][0]["paper_slot_selected"] is True
    assert update["skipped_duplicates"][0]["symbol"] == "MSFT"
    assert update["missed_triggers"][0]["symbol"] == "MSFT"
    assert update["missed_triggers"][0]["paper_fill_status"] == "triggered_but_not_filled"
    assert len(update["candidate_results"]) == 2

    reentry_blocked = build_paper_update(
        {
            "evaluations": [
                {
                    "symbol": "AAPL",
                    "triggered": True,
                    "action": "observe_only_no_manual_entry",
                    "planned_entry_date": "2026-05-20",
                    "last_bar_time_et": "2026-05-20T09:40:00-04:00",
                    "trade_plan": {
                        "entry_price_reference": 10.8,
                        "stop_price": 10.2,
                        "first_target_price": 11.4,
                        "suggested_shares": 100,
                    },
                }
            ]
        },
        existing_payload={"positions": []},
        paper_journal_entries={("AAPL", "2026-05-20")},
        source_path=Path("triggers_test.json"),
    )
    assert reentry_blocked["new_positions"] == []
    assert reentry_blocked["skipped_duplicates"][0]["reason"] == "symbol_already_recorded_for_entry_date"
    assert reentry_blocked["missed_triggers"][0]["paper_skip_reason"] == "symbol_already_recorded_for_entry_date"


def test_paper_trigger_update_respects_opening_slots() -> None:
    trigger_report = {
        "portfolio_guard": {"max_positions": 2, "available_slots": 1},
        "evaluations": [
            {
                "symbol": "AAPL",
                "triggered": True,
                "action": "observe_only_no_manual_entry",
                "planned_entry_date": "2026-05-20",
                "trigger_rank_score": 50.0,
                "trade_plan": {
                    "entry_price_reference": 10.5,
                    "stop_price": 9.9,
                    "first_target_price": 11.1,
                    "suggested_shares": 100,
                },
            },
            {
                "symbol": "MSFT",
                "triggered": True,
                "action": "observe_only_no_manual_entry",
                "planned_entry_date": "2026-05-20",
                "trigger_rank_score": 90.0,
                "trade_plan": {
                    "entry_price_reference": 20.5,
                    "stop_price": 19.9,
                    "first_target_price": 21.1,
                    "suggested_shares": 100,
                },
            },
        ],
    }
    update = build_paper_update(
        trigger_report,
        existing_payload={"positions": [{"symbol": "NVDA", "shares": 10, "remaining_shares": 10}]},
        paper_journal_entries=set(),
        source_path=Path("triggers_test.json"),
    )
    assert len(update["new_positions"]) == 1
    assert update["new_positions"][0]["symbol"] == "MSFT"
    assert update["new_positions"][0]["paper_candidate_rank"] == 1
    assert update["skipped_portfolio_full"][0]["symbol"] == "AAPL"
    assert update["skipped_portfolio_full"][0]["paper_candidate_rank"] == 2
    assert update["skipped_portfolio_full"][0]["reason"] == "no_opening_slots_available"
    assert update["missed_triggers"][0]["symbol"] == "AAPL"

    full_update = build_paper_update(
        trigger_report,
        existing_payload={
            "positions": [
                {"symbol": "NVDA", "shares": 10, "remaining_shares": 10},
                {"symbol": "TSLA", "shares": 10, "remaining_shares": 10},
            ]
        },
        paper_journal_entries=set(),
        source_path=Path("triggers_test.json"),
    )
    assert full_update["new_positions"] == []
    assert len(full_update["skipped_portfolio_full"]) == 2


def test_paper_action_update_partial_and_exit() -> None:
    update = build_paper_action_update(
        positions_payload={
            "positions": [
                {
                    "symbol": "AAPL",
                    "entry_date": "2026-05-20",
                    "entry_price": 10.0,
                    "shares": 100,
                    "remaining_shares": 100,
                    "initial_stop_price": 9.5,
                    "current_stop_price": 9.5,
                    "target_price": 10.5,
                    "target_hit": False,
                },
                {
                    "symbol": "MSFT",
                    "entry_date": "2026-05-20",
                    "entry_price": 20.0,
                    "shares": 50,
                    "remaining_shares": 50,
                    "initial_stop_price": 19.0,
                    "current_stop_price": 20.0,
                    "target_price": 21.0,
                    "target_hit": True,
                },
                {
                    "symbol": "GOOG",
                    "entry_date": "2026-05-20",
                    "entry_price": 30.0,
                    "shares": 20,
                    "remaining_shares": 10,
                    "initial_stop_price": 29.0,
                    "current_stop_price": 30.0,
                    "target_price": 31.0,
                    "target_hit": True,
                },
            ]
        },
        position_report={
            "evaluations": [
                {
                    "symbol": "AAPL",
                    "needs_attention": True,
                    "action": "sell_half_at_first_target",
                    "entry_date": "2026-05-20",
                    "target_price": 10.5,
                    "latest_session_date": "2026-05-21",
                    "update_hint": {
                        "sell_shares": 50,
                        "remaining_shares_after_action": 50,
                        "set_target_hit": True,
                        "move_stop_to_at_least": 10.0,
                    },
                },
                {
                    "symbol": "MSFT",
                    "needs_attention": True,
                    "action": "exit_remaining_breakeven_or_stop",
                    "entry_date": "2026-05-20",
                    "latest_session_date": "2026-05-22",
                    "stop_reference": 20.0,
                },
                {
                    "symbol": "GOOG",
                    "needs_attention": True,
                    "action": "raise_trailing_stop",
                    "entry_date": "2026-05-20",
                    "latest_session_date": "2026-05-22",
                    "stop_reference": 30.8,
                    "update_hint": {
                        "set_current_stop_price": 30.8,
                        "previous_stop_price": 30.0,
                    },
                },
            ]
        },
        source_path=Path("positions_test.json"),
    )
    assert len(update["journal_rows"]) == 2
    assert update["journal_rows"][0]["exit_price"] == "10.5"
    assert update["positions_after"][0]["symbol"] == "AAPL"
    assert update["positions_after"][0]["remaining_shares"] == 50
    assert update["positions_after"][0]["target_hit"]
    assert update["positions_after"][1]["symbol"] == "GOOG"
    assert update["positions_after"][1]["current_stop_price"] == 30.8
    assert len(update["positions_after"]) == 2
    assert update["applied_actions"][2]["action"] == "raise_trailing_stop"


def test_trigger_template_export_payload() -> None:
    payload = build_templates_payload(
        {
            "evaluations": [
                {
                    "symbol": "AAPL",
                    "triggered": True,
                    "action": "prepare_manual_entry",
                    "manual_position_template": {"symbol": "AAPL", "shares": 10},
                },
                {
                    "symbol": "MSFT",
                    "triggered": True,
                    "action": "do_not_open_duplicate_symbol_position",
                    "reason": "duplicate",
                },
            ]
        },
        source_path=Path("triggers_test.json"),
    )
    assert payload["positions"] == [{"symbol": "AAPL", "shares": 10}]
    assert payload["blocked_triggers"][0]["symbol"] == "MSFT"


def test_position_monitor_target() -> None:
    position = ManualPosition(
        symbol="AAPL",
        entry_date=date(2026, 5, 20),
        entry_price=10.0,
        shares=10,
        remaining_shares=10,
        initial_stop_price=9.7,
        current_stop_price=9.7,
        target_price=10.5,
        target_hit=False,
    )
    payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:30:00+00:00", 10.0, 10.2, 9.95, 10.1, 1_000),
                candle("2026-05-20T13:35:00+00:00", 10.1, 10.6, 10.1, 10.55, 1_000),
            ]
        }
    }
    result = evaluate_position(position=position, history_payload=payload)
    assert result.status == "take_partial"
    assert result.action == "sell_half_at_first_target"
    assert result.initial_risk_per_share == 0.3
    assert result.unrealized_pnl == 5.5
    assert result.floating_r == 1.833
    assert result.distance_to_stop_pct == 0.0806
    assert result.distance_to_target_pct == -0.0047
    assert result.update_hint is not None
    assert result.update_hint["set_target_hit"]


def test_position_monitor_post_target_uses_close_stop() -> None:
    position = ManualPosition(
        symbol="AAPL",
        entry_date=date(2026, 5, 20),
        entry_time_et=datetime(2026, 5, 20, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        entry_price=10.0,
        shares=10,
        remaining_shares=5,
        initial_stop_price=9.7,
        current_stop_price=10.0,
        target_price=10.5,
        target_hit=True,
    )
    hold_payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:40:00+00:00", 10.1, 10.2, 9.8, 10.05, 1_000),
            ]
        }
    }
    hold_result = evaluate_position(position=position, history_payload=hold_payload)
    assert hold_result.status == "hold"
    assert hold_result.reason == "no_exit_or_partial_condition_met"
    assert hold_result.floating_r == 0.167
    assert hold_result.distance_to_stop_pct == 0.005

    trail_payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:40:00+00:00", 10.1, 10.5, 10.1, 10.45, 1_000),
                candle("2026-05-20T13:45:00+00:00", 10.45, 10.9, 10.4, 10.8, 1_000),
                candle("2026-05-20T13:50:00+00:00", 10.8, 10.85, 10.7, 10.75, 1_000),
            ]
        }
    }
    trail_result = evaluate_position(position=position, history_payload=trail_payload)
    assert trail_result.status == "adjust_stop"
    assert trail_result.action == "raise_trailing_stop"
    assert trail_result.stop_reference == 10.5
    assert trail_result.trailing_peak_close == 10.8
    assert trail_result.update_hint is not None
    assert trail_result.update_hint["set_current_stop_price"] == 10.5

    trailed_position = ManualPosition(
        symbol="AAPL",
        entry_date=date(2026, 5, 20),
        entry_time_et=datetime(2026, 5, 20, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        entry_price=10.0,
        shares=10,
        remaining_shares=5,
        initial_stop_price=9.7,
        current_stop_price=10.5,
        target_price=10.5,
        target_hit=True,
    )
    trailing_exit_payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:40:00+00:00", 10.1, 10.5, 10.1, 10.45, 1_000),
                candle("2026-05-20T13:45:00+00:00", 10.45, 10.9, 10.4, 10.8, 1_000),
                candle("2026-05-20T13:50:00+00:00", 10.8, 10.85, 10.4, 10.45, 1_000),
            ]
        }
    }
    trailing_exit = evaluate_position(position=trailed_position, history_payload=trailing_exit_payload)
    assert trailing_exit.status == "exit"
    assert trailing_exit.reason == "latest_close_below_post_target_trailing_stop_reference"

    exit_payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:40:00+00:00", 10.1, 10.2, 9.8, 9.95, 1_000),
            ]
        }
    }
    exit_result = evaluate_position(position=position, history_payload=exit_payload)
    assert exit_result.status == "exit"
    assert exit_result.action == "exit_remaining_breakeven_or_stop"
    assert exit_result.reason == "latest_close_below_post_target_stop_reference"


def test_position_monitor_ignores_pre_entry_stop() -> None:
    position = ManualPosition(
        symbol="AAPL",
        entry_date=date(2026, 5, 20),
        entry_time_et=datetime(2026, 5, 20, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        entry_price=10.0,
        shares=10,
        remaining_shares=10,
        initial_stop_price=9.7,
        current_stop_price=9.7,
        target_price=10.5,
        target_hit=False,
    )
    payload = {
        "payload": {
            "candles": [
                candle("2026-05-20T13:30:00+00:00", 10.0, 10.1, 9.5, 10.0, 1_000),
                candle("2026-05-20T13:35:00+00:00", 10.0, 10.2, 9.6, 10.1, 1_000),
                candle("2026-05-20T13:40:00+00:00", 10.1, 10.2, 9.8, 10.1, 1_000),
            ]
        }
    }
    result = evaluate_position(position=position, history_payload=payload)
    assert result.status == "hold"
    assert result.action == "hold"
    assert result.reason == "no_exit_or_partial_condition_met"
    assert result.floating_r == 0.333
    assert result.unrealized_pnl == 1.0


def test_dashboard_model_and_html() -> None:
    root = Path("data/cache/dashboard_smoke")
    shutil.rmtree(root, ignore_errors=True)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    generated = "2026-05-20T13:40:00+00:00"
    (exports / "workflow_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "status": "ok",
                "steps": [{"name": "triggers", "status": "ok", "reason": "done"}],
            }
        ),
        encoding="utf-8",
    )
    (exports / "signals_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "market_regime": {"date": "2026-05-19", "regime": "neutral"},
                "watchlist_count": 1,
                "watchlist": [
                    {
                        "symbol": "AAPL",
                        "total_score": 8.0,
                        "planned_entry_date": "2026-05-20",
                        "pullback_pct": 0.02,
                        "close": 100.0,
                        "source": "fixed_core",
                        "trade_plan": {"suggested_shares": 10},
                        "options_context": {
                            "weekly_gex": {
                                "regime": "positive",
                                "net_gex": 1200000.0,
                                "call_wall": 105.0,
                                "put_wall": 95.0,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (exports / "triggers_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "observation_only": True,
                "watchlist_count": 1,
                "triggered_count": 0,
                "actionable_triggered_count": 0,
                "evaluations": [
                    {
                        "symbol": "AAPL",
                        "status": "waiting",
                        "triggered": False,
                        "last_bar_time_et": "2026-05-20T09:40:00-04:00",
                        "last_close": 101.0,
                        "running_vwap": 100.5,
                        "trigger_price_reference": 102.0,
                        "stop_reference": 97.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (exports / "positions_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "position_count": 1,
                "attention_count": 0,
                "evaluations": [
                    {
                        "symbol": "AAPL",
                        "status": "hold",
                        "action": "hold",
                        "latest_close": 101.0,
                        "stop_reference": 100.7,
                        "trailing_stop_reference": 100.7,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    paper_positions = root / "paper_positions.json"
    paper_positions.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "symbol": "AAPL",
                        "entry_date": "2026-05-20",
                        "entry_time_et": "2026-05-20T09:40:00-04:00",
                        "entry_price": 100.0,
                        "shares": 10,
                        "remaining_shares": 10,
                        "initial_stop_price": 97.0,
                        "current_stop_price": 100.5,
                        "target_price": 103.0,
                        "target_hit": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    paper_journal = root / "paper_trade_journal.csv"
    paper_journal.write_text(
        "trade_id,symbol,side,entry_date,exit_date,entry_price,exit_price,shares,initial_stop_price,fees,setup,source,notes\n",
        encoding="utf-8",
    )
    model = build_dashboard_model(
        exports_dir=exports,
        paper_positions_path=paper_positions,
        paper_journal_path=paper_journal,
        now=datetime(2026, 5, 20, 13, 45, tzinfo=UTC),
    )
    assert model["paper_positions"]["open_count"] == 1
    assert model["signals"]["freshness"]["status"] == "current"
    assert len(model["workflow_history"]) == 1
    assert model["triggers"]["status_counts"]["waiting"] == 1
    assert model["triggers"]["reason_counts"]["waiting"] == 1
    assert model["watchlist_market"]["with_market_data"] == 1
    assert model["watchlist_market"]["rows"][0]["intraday_change_pct"] == 0.01
    assert model["watchlist_market"]["rows"][0]["weekly_gex_regime"] == "positive"
    assert model["watchlist_market"]["rows"][0]["weekly_call_wall"] == 105.0
    assert model["recent_orders"]["order_count"] == 1
    assert model["recent_orders"]["orders"][0]["side"] == "BUY"
    html_text = render_dashboard_html(model, refresh_seconds=10)
    assert "Liubang Monitor" in html_text
    assert "当前操作" in html_text
    assert "最近订单" in html_text
    assert "BUY" in html_text
    assert "Watchlist 今日行情" in html_text
    assert "最近 10 次 workflow/tick" in html_text
    assert "今日有效" in html_text
    assert "AAPL" in html_text
    assert "+1.00%" in html_text
    assert "Call Wall" in html_text
    assert "$1.20M" in html_text
    assert "Trail Ref" in html_text
    assert "trailing" in html_text
    assert "100.70" in html_text
    assert "+0.33R" in html_text
    assert 'http-equiv="refresh" content="10"' in html_text


def test_daily_review_model_and_html() -> None:
    root = Path("data/cache/review_smoke")
    shutil.rmtree(root, ignore_errors=True)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    generated = "2026-05-20T14:00:00+00:00"
    (exports / "workflow_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "status": "skipped",
                "steps": [{"name": "triggers", "status": "skipped", "reason": "outside ET market window"}],
            }
        ),
        encoding="utf-8",
    )
    (exports / "signals_test.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-05-20T12:00:00+00:00",
                "market_regime": {"date": "2026-05-19", "regime": "neutral"},
                "watchlist_count": 2,
                "watchlist": [
                    {
                        "symbol": "AAPL",
                        "total_score": 8.0,
                        "planned_entry_date": "2026-05-20",
                        "pullback_pct": 0.02,
                        "close": 100.0,
                        "source": "fixed_core",
                        "trade_plan": {"suggested_shares": 10},
                        "news_risk": {"level": "high"},
                        "options_context": {
                            "weekly_gex": {
                                "regime": "positive",
                                "net_gex": 1200000.0,
                                "call_wall": 105.0,
                                "put_wall": 95.0,
                            }
                        },
                    },
                    {
                        "symbol": "MSFT",
                        "total_score": 7.5,
                        "planned_entry_date": "2026-05-20",
                        "pullback_pct": 0.03,
                        "close": 200.0,
                        "source": "fixed_core",
                        "trade_plan": {"suggested_shares": 5},
                        "options_context": {
                            "weekly_gex": {
                                "regime": "neutral",
                                "net_gex": 0.0,
                                "call_wall": 205.0,
                                "put_wall": 195.0,
                            }
                        },
                    },
                ],
                "history_sources": {"by_source": {"schwab": 2}, "fallback_count": 0},
                "portfolio_guard": {
                    "current_positions": 3,
                    "max_positions": 2,
                    "available_slots": 0,
                    "current_gross_exposure_value": 50000.0,
                    "max_gross_exposure_value": 40000.0,
                    "current_gross_exposure_pct": 0.5,
                    "allow_new_entries": False,
                },
            }
        ),
        encoding="utf-8",
    )
    (exports / "triggers_first_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "history_sources": {"by_source": {"schwab": 2}, "fallback_count": 0},
                "evaluations": [
                    {
                        "symbol": "AAPL",
                        "status": "triggered",
                        "triggered": True,
                        "action": "observe_only_no_manual_entry",
                        "planned_entry_date": "2026-05-20",
                        "last_bar_time_et": "2026-05-20T10:00:00-04:00",
                        "last_close": 101.0,
                        "trigger_price_reference": 100.8,
                        "stop_reference": 97.0,
                        "risk_per_share_reference": 4.0,
                        "weekly_gex_regime": "positive",
                        "weekly_net_gex": 1200000.0,
                        "trade_plan": {"suggested_shares": 10},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (exports / "triggers_final_test.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-05-20T14:05:00+00:00",
                "history_sources": {"by_source": {"schwab": 2}, "fallback_count": 0},
                "evaluations": [
                    {
                        "symbol": "AAPL",
                        "status": "invalidated",
                        "triggered": False,
                        "action": "keep_observing",
                        "planned_entry_date": "2026-05-20",
                        "last_bar_time_et": "2026-05-20T10:05:00-04:00",
                        "last_close": 99.0,
                        "trigger_price_reference": 100.8,
                        "stop_reference": 97.0,
                        "risk_per_share_reference": 4.0,
                        "reason": "lost_trigger_confirmation",
                    },
                    {
                        "symbol": "NVDA",
                        "status": "waiting_for_market_data",
                        "triggered": False,
                        "reason": "no_regular_session_candles_for_planned_entry_date",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (exports / "paper_positions_update_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "new_positions": [
                    {
                        "symbol": "AAPL",
                        "entry_date": "2026-05-20",
                        "paper_candidate_rank": 1,
                        "paper_slot_selected": True,
                        "paper_fill_status": "filled",
                    }
                ],
                "missed_triggers": [
                    {
                        "symbol": "MSFT",
                        "entry_date": "2026-05-20",
                        "paper_candidate_rank": 2,
                        "paper_slot_selected": False,
                        "paper_fill_status": "triggered_but_not_filled",
                        "paper_skip_reason": "no_opening_slots_available",
                    },
                    {
                        "symbol": "MSFT",
                        "entry_date": "2026-05-20",
                        "paper_candidate_rank": 2,
                        "paper_slot_selected": False,
                        "paper_fill_status": "triggered_but_not_filled",
                        "paper_skip_reason": "no_opening_slots_available",
                    }
                ],
                "candidate_results": [
                    {
                        "symbol": "AAPL",
                        "entry_date": "2026-05-20",
                        "paper_candidate_rank": 1,
                        "paper_slot_selected": True,
                        "paper_fill_status": "filled",
                    },
                    {
                        "symbol": "MSFT",
                        "entry_date": "2026-05-20",
                        "paper_candidate_rank": 2,
                        "paper_slot_selected": False,
                        "paper_fill_status": "triggered_but_not_filled",
                        "paper_skip_reason": "no_opening_slots_available",
                    },
                ],
                "skipped_duplicates": [],
                "skipped_invalid": [],
            }
        ),
        encoding="utf-8",
    )
    (exports / "paper_positions_update_old_bug.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-05-20T13:40:00+00:00",
                "new_positions": [{"symbol": "BUG", "entry_date": "2026-05-20"}],
                "skipped_duplicates": [],
                "skipped_invalid": [],
            }
        ),
        encoding="utf-8",
    )
    (exports / "paper_actions_test.json").write_text(
        json.dumps(
            {
                "generated_at": generated,
                "applied_actions": [{"symbol": "AAPL", "action": "sell_half_at_first_target"}],
                "journal_rows": [{"symbol": "AAPL"}],
                "skipped": [],
            }
        ),
        encoding="utf-8",
    )
    paper_positions = root / "paper_positions.json"
    paper_positions.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "symbol": "NVDA",
                        "entry_date": "2026-05-20",
                        "entry_price": 200.0,
                        "shares": 5,
                        "remaining_shares": 5,
                        "entry_time_et": "2026-05-20T10:20:00-04:00",
                        "initial_stop_price": 194.0,
                        "current_stop_price": 194.0,
                        "target_price": 206.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    paper_journal = root / "paper_trade_journal.csv"
    paper_journal.write_text(
        "\n".join(
            [
                "trade_id,symbol,side,entry_date,exit_date,entry_price,exit_price,shares,initial_stop_price,fees,setup,source,notes",
                "AAPL-1,AAPL,long,2026-05-20,2026-05-20,100,103,10,97,0,strong_pullback,paper,target_1 smoke",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    model = build_review_model(
        review_date=date(2026, 5, 20),
        exports_dir=exports,
        paper_positions_path=paper_positions,
        paper_journal_path=paper_journal,
        now=datetime(2026, 5, 20, 21, 0, tzinfo=UTC),
    )
    assert model["metrics"]["watchlist_count"] == 2
    assert model["metrics"]["triggered_today_count"] == 1
    assert model["metrics"]["open_paper_positions"] == 1
    assert model["metrics"]["today_realized_pnl"] == 30.0
    assert model["metrics"]["data_issue_count"] == 2
    assert model["metrics"]["paper_exposure_breach"]
    assert model["metrics"]["paper_update_filtered_reports"] == 1
    assert model["metrics"]["missed_paper_triggers"] == 1
    assert model["signals"]["is_review_date_signal"]
    assert model["signals"]["watchlist"][0]["weekly_gex_regime"] == "positive"
    assert model["triggers"]["triggered_symbols"] == ["AAPL"]
    assert model["triggers"]["final_evaluations"][0]["first_trigger_time_et"] == "2026-05-20T10:00:00-04:00"
    assert model["triggers"]["final_evaluations"][0]["status"] == "invalidated"
    assert any(item["name"] == "workflow_skipped" and item["severity"] == "info" for item in model["data_issues"])
    assert any(item["name"] == "paper_update_boundary_filter" and item["severity"] == "info" for item in model["data_issues"])
    assert any(item["name"] == "paper_exposure_breach" for item in model["data_issues"])
    assert model["paper_updates"]["new_positions"][0]["symbol"] == "AAPL"
    assert len(model["paper_updates"]["missed_triggers"]) == 1
    assert model["paper_updates"]["missed_triggers"][0]["symbol"] == "MSFT"
    assert model["paper_actions"]["actions"][0]["bucket"] == "applied_actions"
    sample_rows = build_review_sample_rows(model)
    assert sample_rows[0]["symbol"] == "AAPL"
    assert sample_rows[0]["triggered_today"] is True
    assert sample_rows[0]["first_trigger_time_et"] == "10:00"
    assert sample_rows[0]["trigger_time_bucket"] == "morning"
    assert sample_rows[0]["mfe_r_5m_close"] == 0.0
    assert sample_rows[0]["mae_r_5m_close"] == -0.5
    assert sample_rows[0]["weekly_net_gex"] == 1200000.0
    assert sample_rows[0]["gex_risk_tags"] == "gex_positive"
    assert sample_rows[0]["paper_candidate_rank"] == 1
    assert sample_rows[0]["paper_fill_status"] == "filled"
    assert sample_rows[0]["paper_status"] == "closed"
    assert sample_rows[0]["paper_pnl"] == 30.0
    assert sample_rows[0]["exit_reason"] == "target_1"
    assert sample_rows[1]["paper_fill_status"] == "triggered_but_not_filled"
    assert sample_rows[1]["paper_skip_reason"] == "no_opening_slots_available"
    html_text = render_review_html(model)
    assert "Liubang Daily Review" in html_text
    assert "今日触发" in html_text
    assert "首次触发" in html_text
    assert "Call Wall" in html_text
    assert "$1.20M" in html_text
    assert "paper exposure 超限" in html_text
    assert "missed triggers=1" in html_text
    assert "AAPL" in html_text
    assert "$30.00" in html_text


def candle(timestamp: str, open_: float, high: float, low: float, close: float, volume: int) -> dict:
    parsed = datetime.fromisoformat(timestamp).astimezone(UTC)
    return {
        "datetime": int(parsed.timestamp() * 1000),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


if __name__ == "__main__":
    raise SystemExit(main())
