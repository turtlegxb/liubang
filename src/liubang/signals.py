from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liubang.backtest import (
    BacktestParams,
    apply_watchlist_scoring_mode,
    aggregate_daily,
    build_scoring_factor_data,
    consecutive_down_days,
    filter_watchlist_for_regime_policy,
    generate_market_regimes,
    latest_context_regimes,
    NON_TRADABLE_CONTEXT_SYMBOLS,
    parse_schwab_candles,
    regime_policy_summary,
    return_by_date,
    rolling_mean,
    summarize_data,
)
from liubang.data_quality import evaluate_data_quality
from liubang.earnings import EarningsCalendar, next_weekday
from liubang.news import NewsBook
from liubang.risk_context import evaluate_news_risk, evaluate_options_risk
from liubang.trade_plan import SizingInputs, build_trade_plan


def generate_signal_report(
    history_by_symbol: dict[str, dict[str, Any]],
    *,
    symbols: tuple[str, ...],
    params: BacktestParams,
    earnings_calendar: EarningsCalendar | None = None,
    news_book: NewsBook | None = None,
    news_max_age_hours: int = 72,
    symbol_sources: dict[str, str] | None = None,
    symbol_metadata: dict[str, dict[str, Any]] | None = None,
    dynamic_universe: dict[str, Any] | None = None,
    sizing: SizingInputs | None = None,
) -> dict[str, Any]:
    symbol_sources = symbol_sources or {}
    symbol_metadata = symbol_metadata or {}
    sizing = sizing or SizingInputs(
        account_equity=params.initial_equity,
        risk_per_trade_pct=params.risk_per_trade_pct,
        max_position_pct=params.max_position_pct,
        hard_stop_pct=params.hard_stop_pct,
        first_target_r=params.first_target_r,
    )
    candles_by_symbol = {
        symbol: parse_schwab_candles(history_by_symbol[symbol], regular_hours_only=True)
        for symbol in symbols
        if symbol in history_by_symbol
    }
    daily_by_symbol = {
        symbol: aggregate_daily(candles)
        for symbol, candles in candles_by_symbol.items()
    }
    if "SPY" not in daily_by_symbol or "QQQ" not in daily_by_symbol:
        raise ValueError("Signal generation requires SPY and QQQ for market regime labels.")

    regimes = generate_market_regimes(daily_by_symbol["SPY"], daily_by_symbol["QQQ"])
    latest_regime_date = max(regimes) if regimes else None
    latest_regime = regimes.get(latest_regime_date, "neutral") if latest_regime_date else "neutral"

    watchlist = []
    for symbol in symbols:
        if symbol in NON_TRADABLE_CONTEXT_SYMBOLS:
            continue
        signal = score_latest_symbol(
            symbol,
            daily_by_symbol.get(symbol, []),
            daily_by_symbol["QQQ"],
            regimes,
            params,
            earnings_calendar,
            news_book,
            news_max_age_hours,
            symbol_source=symbol_sources.get(symbol, "fixed_core"),
            source_metadata=symbol_metadata.get(symbol),
            sizing=sizing,
        )
        if signal is not None:
            watchlist.append(signal)

    watchlist = apply_watchlist_scoring_mode(watchlist, params.scoring_mode)
    pre_policy_watchlist_count = len(watchlist)
    watchlist = filter_watchlist_for_regime_policy(watchlist, params)
    watchlist = sorted(watchlist, key=lambda item: item["total_score"], reverse=True)

    data_summary = summarize_data(daily_by_symbol)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "market-data-only/manual-orders",
        "symbols": list(symbols),
        "dynamic_universe": dynamic_universe,
        "sizing": {
            "account_equity": sizing.account_equity,
            "risk_per_trade_pct": sizing.risk_per_trade_pct,
            "max_position_pct": sizing.max_position_pct,
            "hard_stop_pct": sizing.hard_stop_pct,
            "first_target_r": sizing.first_target_r,
            "weak_regime_size_multiplier": sizing.weak_regime_size_multiplier,
            "note": "Signal trade_plan uses latest close as planning reference; trigger scan recalculates from live trigger price.",
        },
        "market_regime": {
            "date": latest_regime_date.isoformat() if latest_regime_date else None,
            "regime": latest_regime,
        },
        "scoring_mode": params.scoring_mode,
        "regime_policy": regime_policy_summary(params),
        "earnings_filter": earnings_calendar.summary() if earnings_calendar else None,
        "news": news_book.summary() if news_book else None,
        "context_regimes": latest_context_regimes(daily_by_symbol, ("XLK", "SMH")),
        "data": data_summary,
        "data_quality": evaluate_data_quality(data_summary, expected_symbols=symbols),
        "watchlist_count_before_regime_policy": pre_policy_watchlist_count,
        "watchlist_skipped_regime_policy": pre_policy_watchlist_count - len(watchlist),
        "watchlist_count": len(watchlist),
        "watchlist_concentration": build_watchlist_concentration(watchlist),
        "watchlist": watchlist,
    }


def score_latest_symbol(
    symbol: str,
    daily: list[Any],
    qqq_daily: list[Any],
    regimes: dict[Any, str],
    params: BacktestParams,
    earnings_calendar: EarningsCalendar | None = None,
    news_book: NewsBook | None = None,
    news_max_age_hours: int = 72,
    symbol_source: str = "fixed_core",
    source_metadata: dict[str, Any] | None = None,
    sizing: SizingInputs | None = None,
) -> dict[str, Any] | None:
    if len(daily) < 25:
        return None

    idx = len(daily) - 1
    bar = daily[idx]
    trading_dates = [item.session_date for item in daily]
    planned_entry_date = next_weekday(bar.session_date)
    if earnings_calendar is not None:
        blocked_entry_dates = earnings_calendar.blocked_entry_dates(symbol, trading_dates)
        if planned_entry_date in blocked_entry_dates:
            return None
    closes = [item.close for item in daily]
    highs = [item.high for item in daily]
    volumes = [item.volume for item in daily]
    sma5 = rolling_mean(closes, 5)
    sma10 = rolling_mean(closes, 10)
    sma20 = rolling_mean(closes, 20)
    volume5 = rolling_mean(volumes, 5)
    qqq_return_by_date = return_by_date(qqq_daily, 10)
    qqq_return20_by_date = return_by_date(qqq_daily, 20)
    qqq_return60_by_date = return_by_date(qqq_daily, 60)

    if sma5[idx] is None or sma10[idx] is None or sma20[idx] is None:
        return None

    recent_high = max(highs[max(0, idx - 4) : idx + 1])
    if recent_high <= 0:
        return None
    pullback_pct = (recent_high - bar.close) / recent_high
    if not (params.min_pullback_pct <= pullback_pct <= params.max_pullback_pct):
        return None
    down_days = consecutive_down_days(daily, idx)
    symbol_ret10 = closes[idx] / closes[idx - 10] - 1.0 if idx >= 10 else 0.0
    qqq_ret10 = qqq_return_by_date.get(bar.session_date, 0.0)

    strength_score = 0.0
    strength_notes = []
    if bar.close > float(sma20[idx]):
        strength_score += 1
        strength_notes.append("close_above_sma20")
    if float(sma5[idx]) > float(sma10[idx]) > float(sma20[idx]):
        strength_score += 1
        strength_notes.append("sma_stack_positive")
    if symbol_ret10 > 0:
        strength_score += 1
        strength_notes.append("positive_10d_return")
    if symbol_ret10 > qqq_ret10:
        strength_score += 1
        strength_notes.append("outperforming_qqq_10d")
    if bar.close > closes[idx - 20]:
        strength_score += 1
        strength_notes.append("above_20d_prior_close")

    pullback_score = 0.0
    pullback_notes = []
    pullback_score += 1.5
    pullback_notes.append("pullback_depth_in_range")
    if 1 <= down_days <= 3:
        pullback_score += 1
        pullback_notes.append("one_to_three_down_days")
    if bar.close >= float(sma10[idx]) * 0.985:
        pullback_score += 1
        pullback_notes.append("near_or_above_sma10")
    if volume5[idx] is not None and bar.volume <= float(volume5[idx]) * 1.10:
        pullback_score += 1
        pullback_notes.append("volume_not_expanded")
    if bar.close > bar.low + (bar.high - bar.low) * 0.35:
        pullback_score += 0.5
        pullback_notes.append("closed_off_lows")

    total_score = strength_score + pullback_score
    regime = regimes.get(bar.session_date, "neutral")
    risk_notes = []
    if regime == "weak":
        total_score -= 0.75
        risk_notes.append("weak_market_regime_position_should_be_half_size")
    next_earnings = earnings_calendar.next_event_on_or_after(symbol, bar.session_date) if earnings_calendar else None
    if next_earnings is not None:
        risk_notes.append(
            f"next_earnings={next_earnings.report_date.isoformat()} timing={next_earnings.timing}"
        )
    recent_news = (
        [
            {
                "title": item.title,
                "provider": item.provider,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "url": item.url,
            }
            for item in news_book.recent_for(symbol, max_age_hours=news_max_age_hours, limit=3)
        ]
        if news_book is not None
        else []
    )
    if recent_news:
        risk_notes.append(f"recent_news_count={len(recent_news)}")
    news_risk = evaluate_news_risk(recent_news)
    if news_risk["level"] != "low":
        risk_notes.append(
            f"news_risk={news_risk['level']} categories={','.join(news_risk['categories'])}"
        )

    if total_score < params.min_score:
        return None

    factor_data = build_scoring_factor_data(
        daily=daily,
        idx=idx,
        qqq_return20_by_date=qqq_return20_by_date,
        qqq_return60_by_date=qqq_return60_by_date,
        recent_high=recent_high,
        volume5_value=volume5[idx],
        sma20_value=sma20[idx],
        classic_score=total_score,
    )

    sizing = sizing or SizingInputs(
        account_equity=params.initial_equity,
        risk_per_trade_pct=params.risk_per_trade_pct,
        max_position_pct=params.max_position_pct,
        hard_stop_pct=params.hard_stop_pct,
        first_target_r=params.first_target_r,
    )
    trade_plan = build_trade_plan(
        entry_price=bar.close,
        technical_stop=bar.low,
        market_regime=regime,
        sizing=sizing,
    )

    signal = {
        "symbol": symbol,
        "source": symbol_source,
        "action": "watch_for_intraday_confirmation",
        "as_of_date": bar.session_date.isoformat(),
        "planned_entry_date": planned_entry_date.isoformat(),
        "market_regime": regime,
        "total_score": round(total_score, 3),
        "classic_total_score": round(total_score, 3),
        "scoring_mode": params.scoring_mode,
        "strength_score": round(strength_score, 3),
        "pullback_score": round(pullback_score, 3),
        "pullback_pct": round(pullback_pct, 5),
        "factor_data": factor_data,
        "close": round(bar.close, 4),
        "recent_5d_high": round(recent_high, 4),
        "technical_stop_reference": round(bar.low, 4),
        "entry_price_reference": round(bar.close, 4),
        "stop_price_reference": trade_plan.stop_price,
        "first_target_price_reference": trade_plan.first_target_price,
        "trade_plan": trade_plan.to_dict(),
        "hard_stop_rule": f"entry_price * {1.0 - sizing.hard_stop_pct:.4f}",
        "first_target_rule": f"entry_price + {sizing.first_target_r:g}R; sell 50%",
        "remaining_exit_rule": "move stop to breakeven after target_1; force exit by day five",
        "position_rule": (
            f"risk {sizing.risk_per_trade_pct:.2%} of equity, "
            f"capped at {sizing.max_position_pct:.2%} of equity"
        ),
        "entry_trigger": {
            "timeframe": "5m",
            "conditions": [
                "skip first 5-minute candle",
                "5-minute close above running VWAP",
                "5-minute close above prior 5-minute candle high",
            ],
            "order_style": "manual limit or stop-limit",
        },
        "strength_notes": strength_notes,
        "pullback_notes": pullback_notes,
        "risk_notes": risk_notes,
        "recent_news": recent_news,
        "news_risk": news_risk,
        "context_not_scored": [
            "news",
            "options",
        ],
    }
    if source_metadata:
        signal["source_metadata"] = source_metadata
    return signal


def build_watchlist_concentration(watchlist: list[dict[str, Any]]) -> dict[str, Any]:
    by_theme: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for item in watchlist:
        source = str(item.get("source") or "unknown")
        metadata = item.get("source_metadata") or {}
        theme = str(metadata.get("theme") or source)
        by_theme[theme] = by_theme.get(theme, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
    total = len(watchlist)
    top_theme = None
    top_theme_count = 0
    if by_theme:
        top_theme, top_theme_count = max(by_theme.items(), key=lambda item: item[1])
    top_theme_share = top_theme_count / total if total else 0.0
    warnings = []
    if total >= 3 and top_theme_share >= 0.5:
        warnings.append("watchlist_theme_concentration")
    return {
        "watchlist_count": total,
        "by_theme": dict(sorted(by_theme.items())),
        "by_source": dict(sorted(by_source.items())),
        "top_theme": top_theme,
        "top_theme_count": top_theme_count,
        "top_theme_share": round(top_theme_share, 4),
        "warnings": warnings,
    }


def build_signal_selection_payload(
    report: dict[str, Any],
    *,
    signal_report_path: Path | None = None,
) -> dict[str, Any]:
    watchlist = [
        compact_signal_selection_item(rank, item)
        for rank, item in enumerate(report.get("watchlist", []), start=1)
        if isinstance(item, dict)
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_signal_report": str(signal_report_path) if signal_report_path else None,
        "signal_generated_at": report.get("generated_at"),
        "mode": report.get("mode"),
        "scoring_mode": report.get("scoring_mode"),
        "market_regime": report.get("market_regime") or {},
        "context_regimes": report.get("context_regimes") or {},
        "data_quality": report.get("data_quality") or {},
        "history_sources": report.get("history_sources") or {},
        "filters": {
            "regime_policy": report.get("regime_policy") or {},
            "watchlist_count_before_regime_policy": report.get("watchlist_count_before_regime_policy"),
            "watchlist_skipped_regime_policy": report.get("watchlist_skipped_regime_policy"),
            "symbol_cooldown": report.get("symbol_cooldown") or {},
            "risk_throttle": report.get("risk_throttle") or {},
            "portfolio_guard": report.get("portfolio_guard") or {},
        },
        "watchlist_concentration": report.get("watchlist_concentration") or {},
        "watchlist_count": len(watchlist),
        "symbols": [item["symbol"] for item in watchlist if item.get("symbol")],
        "watchlist": watchlist,
    }


def compact_signal_selection_item(rank: int, item: dict[str, Any]) -> dict[str, Any]:
    plan = item.get("trade_plan") or {}
    metadata = item.get("source_metadata") or {}
    news = item.get("recent_news") or []
    news_risk = item.get("news_risk") or {}
    options = item.get("options_context") or {}
    options_risk = item.get("options_risk") or {}
    weekly_gex = options.get("weekly_gex") or {}
    factor_data = item.get("factor_data") or {}
    return {
        "rank": rank,
        "symbol": item.get("symbol"),
        "source": item.get("source"),
        "theme": metadata.get("theme"),
        "source_reason": metadata.get("reason"),
        "action": item.get("action"),
        "as_of_date": item.get("as_of_date"),
        "planned_entry_date": item.get("planned_entry_date"),
        "market_regime": item.get("market_regime"),
        "scoring_mode": item.get("scoring_mode"),
        "total_score": item.get("total_score"),
        "classic_total_score": item.get("classic_total_score"),
        "ranked_total_score": item.get("ranked_total_score"),
        "overlay_score": factor_data.get("overlay_score"),
        "rs20_rank": factor_data.get("rs20_rank"),
        "atr20_pct": factor_data.get("atr20_pct"),
        "pullback_pct": item.get("pullback_pct"),
        "close": item.get("close"),
        "entry_price_reference": item.get("entry_price_reference"),
        "stop_price_reference": item.get("stop_price_reference"),
        "first_target_price_reference": item.get("first_target_price_reference"),
        "suggested_shares": plan.get("suggested_shares"),
        "suggested_position_value": plan.get("suggested_position_value"),
        "suggested_dollar_risk": plan.get("suggested_dollar_risk"),
        "binding_constraint": plan.get("binding_constraint"),
        "news_risk": news_risk.get("level"),
        "options_risk": options_risk.get("level"),
        "weekly_gex_regime": weekly_gex.get("regime"),
        "weekly_net_gex": weekly_gex.get("net_gex"),
        "weekly_call_wall": weekly_gex.get("call_wall"),
        "weekly_put_wall": weekly_gex.get("put_wall"),
        "risk_notes": item.get("risk_notes") or [],
        "recent_news_count": len(news),
        "top_news_headline": news[0].get("title") if news else None,
    }


def write_signal_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_signal_summary(report: dict[str, Any], report_path: Path) -> str:
    regime = report["market_regime"]
    lines = [
        "Signal summary",
        f"Generated: {report['generated_at']}",
        f"Market regime: {regime['regime']} ({regime['date']})",
        f"Scoring mode: {report.get('scoring_mode')}",
        f"Watchlist count: {report['watchlist_count']}",
    ]
    regime_policy = report.get("regime_policy") or {}
    if regime_policy.get("enabled"):
        skipped = report.get("watchlist_skipped_regime_policy")
        lines.append(f"Regime policy: {regime_policy.get('mode')} skipped={skipped}")
    context_regimes = report.get("context_regimes") or {}
    if context_regimes:
        context = ", ".join(
            f"{symbol}={item.get('regime')}"
            for symbol, item in sorted(context_regimes.items())
        )
        lines.append(f"Context regimes: {context}")
    portfolio_guard = report.get("portfolio_guard") or {}
    if portfolio_guard:
        lines.append(
            "Portfolio guard: "
            f"{portfolio_guard.get('current_positions')}/{portfolio_guard.get('max_positions')} "
            f"positions, slots={portfolio_guard.get('available_slots')}, "
            f"exposure={portfolio_guard.get('current_gross_exposure_pct')} "
            f"allow_new={portfolio_guard.get('allow_new_entries')}"
        )
    data_quality = report.get("data_quality") or {}
    if data_quality:
        lines.append(
            f"Data quality: {data_quality.get('status')} "
            f"latest={data_quality.get('latest_end_date')} "
            f"warnings={','.join(data_quality.get('warnings') or []) or 'none'}"
        )
    concentration = report.get("watchlist_concentration") or {}
    if concentration:
        lines.append(
            "Watchlist concentration: "
            f"top_theme={concentration.get('top_theme')} "
            f"share={concentration.get('top_theme_share')} "
            f"warnings={','.join(concentration.get('warnings') or []) or 'none'}"
        )
    risk_throttle = report.get("risk_throttle") or {}
    if risk_throttle:
        lines.append(
            "Risk throttle: "
            f"{risk_throttle.get('status')} "
            f"allow_new={risk_throttle.get('allow_new_entries')} "
            f"flags={','.join(risk_throttle.get('flags') or []) or 'none'}"
        )
    history_sources = report.get("history_sources") or {}
    if history_sources:
        lines.append(f"History sources: {format_history_sources(history_sources)}")
    for idx, item in enumerate(report["watchlist"][:10], start=1):
        news = item.get("recent_news") or []
        news_note = f" news={len(news)}" if news else ""
        source_note = f" source={item.get('source')}" if item.get("source") != "fixed_core" else ""
        options_note = compact_options_note(item.get("options_context") or {})
        risk_note = compact_context_risk_note(item)
        plan = item.get("trade_plan") or {}
        shares_note = (
            f" shares={plan['suggested_shares']}"
            if plan.get("suggested_shares") is not None
            else ""
        )
        lines.append(
            f"  {idx}. {item['symbol']} score={item['total_score']} "
            f"pullback={item['pullback_pct']:.2%} close={item['close']} "
            f"regime={item['market_regime']}{source_note}{shares_note}{news_note}{options_note}{risk_note}"
        )
    signal_selection = report.get("signal_selection") or {}
    if signal_selection.get("path"):
        lines.append(f"Selection: {signal_selection.get('path')}")
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)


def format_discord_message(report: dict[str, Any]) -> str:
    regime = report["market_regime"]
    lines = [
        f"Liubang watchlist | regime={regime['regime']} | date={regime['date']}",
        f"Candidates: {report['watchlist_count']}",
    ]
    regime_policy = report.get("regime_policy") or {}
    if regime_policy.get("enabled") and report.get("watchlist_skipped_regime_policy"):
        lines.append(
            f"Regime policy: {regime_policy.get('mode')} skipped={report.get('watchlist_skipped_regime_policy')}"
        )
    context_regimes = report.get("context_regimes") or {}
    if context_regimes:
        context = ", ".join(
            f"{symbol}={item.get('regime')}"
            for symbol, item in sorted(context_regimes.items())
        )
        lines.append(f"Context: {context}")
    portfolio_guard = report.get("portfolio_guard") or {}
    if portfolio_guard:
        lines.append(
            f"Slots: {portfolio_guard.get('available_slots')}/"
            f"{portfolio_guard.get('max_positions')} "
            f"exposure={portfolio_guard.get('current_gross_exposure_pct')} "
            f"allow_new={portfolio_guard.get('allow_new_entries')}"
        )
    data_quality = report.get("data_quality") or {}
    if data_quality and data_quality.get("status") != "ok":
        lines.append(f"Data warnings: {','.join(data_quality.get('warnings') or [])}")
    concentration = report.get("watchlist_concentration") or {}
    if concentration and concentration.get("warnings"):
        lines.append(
            f"Concentration: {concentration.get('top_theme')} share={concentration.get('top_theme_share')}"
        )
    risk_throttle = report.get("risk_throttle") or {}
    if risk_throttle and not risk_throttle.get("allow_new_entries", True):
        lines.append(f"Risk throttle: blocked flags={','.join(risk_throttle.get('flags') or [])}")
    history_sources = report.get("history_sources") or {}
    if history_sources and history_sources.get("fallback_count"):
        lines.append(f"History fallbacks: {history_sources.get('fallback_count')}")
    for idx, item in enumerate(report["watchlist"][:8], start=1):
        news = item.get("recent_news") or []
        headline = f" | {truncate(news[0]['title'], 80)}" if news else ""
        source_note = " dyn" if item.get("source") == "dynamic_yfinance" else ""
        options_note = compact_options_note(item.get("options_context") or {})
        risk_note = compact_context_risk_note(item)
        plan = item.get("trade_plan") or {}
        shares_note = (
            f" shares={plan['suggested_shares']}"
            if plan.get("suggested_shares") is not None
            else ""
        )
        lines.append(
            f"{idx}. {item['symbol']}{source_note} score={item['total_score']} "
            f"pullback={item['pullback_pct']:.2%} close={item['close']}{shares_note}{options_note}{risk_note}{headline}"
        )
    if not report["watchlist"]:
        lines.append("No candidates passed the current threshold.")
    message = "\n".join(lines)
    return message[:1900]


def attach_options_context(
    report: dict[str, Any],
    *,
    summaries_by_symbol: dict[str, dict[str, Any]],
    errors_by_symbol: dict[str, str] | None = None,
) -> dict[str, Any]:
    errors_by_symbol = errors_by_symbol or {}
    for item in report.get("watchlist", []):
        symbol = item.get("symbol")
        if symbol in summaries_by_symbol:
            summary = summaries_by_symbol[symbol]
            item["options_context"] = summary
            options_risk = evaluate_options_risk(summary)
            item["options_risk"] = options_risk
            pc_oi = summary.get("put_call_oi_ratio")
            pc_vol = summary.get("put_call_volume_ratio")
            note_parts = []
            if pc_oi is not None:
                note_parts.append(f"pc_oi={pc_oi}")
            if pc_vol is not None:
                note_parts.append(f"pc_vol={pc_vol}")
            if note_parts:
                item.setdefault("risk_notes", []).append("options_" + "_".join(note_parts))
            if options_risk["level"] != "low":
                flag_names = ",".join(flag["name"] for flag in options_risk["flags"])
                item.setdefault("risk_notes", []).append(f"options_risk={options_risk['level']} flags={flag_names}")
            weekly_gex = summary.get("weekly_gex") or {}
            if weekly_gex:
                regime = weekly_gex.get("regime")
                if regime and regime != "neutral":
                    item.setdefault("risk_notes", []).append(f"weekly_gex={regime}")
                for gex_note in weekly_gex.get("risk_notes") or []:
                    item.setdefault("risk_notes", []).append(f"weekly_gex_{gex_note}")
        elif symbol in errors_by_symbol:
            item.setdefault("risk_notes", []).append("options_error")
            item["options_error"] = errors_by_symbol[symbol]

    report["options"] = {
        "source": "schwab",
        "symbols": sorted(summaries_by_symbol),
        "errors": errors_by_symbol,
    }
    return report


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def format_history_sources(summary: dict[str, Any]) -> str:
    by_source = summary.get("by_source") or {}
    counts = ", ".join(f"{source}={count}" for source, count in sorted(by_source.items()))
    fallback_count = summary.get("fallback_count") or 0
    return f"{counts or 'none'} fallbacks={fallback_count}"


def compact_context_risk_note(item: dict[str, Any]) -> str:
    parts = []
    news_risk = item.get("news_risk") or {}
    options_risk = item.get("options_risk") or {}
    weekly_gex = ((item.get("options_context") or {}).get("weekly_gex") or {})
    if news_risk.get("level") in {"elevated", "high"}:
        parts.append(f"news_risk={news_risk.get('level')}")
    if options_risk.get("level") in {"elevated", "high"}:
        parts.append(f"options_risk={options_risk.get('level')}")
    if weekly_gex.get("regime") in {"positive", "negative"}:
        parts.append(f"gex={weekly_gex.get('regime')}")
    return (" " + " ".join(parts)) if parts else ""


def compact_options_note(options: dict[str, Any]) -> str:
    parts = []
    pc_oi = options.get("put_call_oi_ratio")
    if pc_oi is not None:
        parts.append(f"pc_oi={pc_oi}")
    weekly_gex = options.get("weekly_gex") or {}
    if weekly_gex.get("regime"):
        parts.append(f"gex={weekly_gex.get('regime')}")
    return (" " + " ".join(parts)) if parts else ""
