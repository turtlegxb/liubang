from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from liubang.backtest import Candle, EASTERN, parse_schwab_candles
from liubang.defaults import (
    DEFAULT_FIRST_TARGET_R,
    DEFAULT_HARD_STOP_PCT,
    DEFAULT_INITIAL_EQUITY,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_RISK_PER_TRADE_PCT,
    DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER,
)
from liubang.trade_plan import SizingInputs, build_trade_plan


@dataclass(frozen=True)
class TriggerEvaluation:
    symbol: str
    status: str
    triggered: bool
    planned_entry_date: str | None
    signal_score: float | None = None
    source: str | None = None
    last_bar_time_et: str | None = None
    last_close: float | None = None
    running_vwap: float | None = None
    prior_candle_high: float | None = None
    trigger_price_reference: float | None = None
    stop_reference: float | None = None
    risk_per_share_reference: float | None = None
    trade_plan: dict[str, Any] | None = None
    manual_position_template: dict[str, Any] | None = None
    conditions: dict[str, bool] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


def load_latest_signal_report(exports_dir: Path = Path("data/exports")) -> tuple[Path, dict[str, Any]]:
    reports = sorted(exports_dir.glob("signals_*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No signal reports found in {exports_dir}")
    path = reports[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def scan_intraday_triggers(
    *,
    signal_report: dict[str, Any],
    history_by_symbol: dict[str, dict[str, Any]],
    current_session_date: date | None = None,
    sizing: SizingInputs | None = None,
    observation_only: bool = False,
) -> dict[str, Any]:
    sizing = sizing or sizing_from_signal_report(signal_report)
    portfolio_guard = signal_report.get("portfolio_guard") or {}
    entry_allowed = bool(portfolio_guard.get("allow_new_entries", True))
    open_symbols = {
        str(symbol).upper()
        for symbol in portfolio_guard.get("open_symbols", [])
        if symbol
    }
    evaluations = []
    for signal in signal_report.get("watchlist", []):
        symbol = str(signal.get("symbol") or "").upper()
        if not symbol:
            continue
        evaluation = evaluate_intraday_trigger(
            signal=signal,
            history_payload=history_by_symbol.get(symbol, {}),
            current_session_date=current_session_date,
            sizing=sizing,
        ).to_dict()
        duplicate_symbol = symbol in open_symbols
        evaluation["entry_allowed_by_portfolio_guard"] = entry_allowed
        evaluation["entry_allowed_by_symbol_guard"] = not duplicate_symbol
        if duplicate_symbol:
            evaluation["duplicate_open_position"] = True
        if evaluation.get("triggered"):
            if observation_only:
                evaluation["action"] = "observe_only_no_manual_entry"
                evaluation["observation_only"] = True
                evaluation.pop("manual_position_template", None)
            elif duplicate_symbol:
                evaluation["action"] = "do_not_open_duplicate_symbol_position"
                evaluation.pop("manual_position_template", None)
            elif not entry_allowed:
                evaluation["action"] = "do_not_open_new_position_portfolio_full"
                evaluation.pop("manual_position_template", None)
            else:
                evaluation["action"] = "prepare_manual_entry"
        evaluations.append(evaluation)

    triggered = [item for item in evaluations if item.get("triggered")]
    actionable = [
        item
        for item in triggered
        if item.get("action") == "prepare_manual_entry"
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "market-data-only/manual-orders",
        "observation_only": observation_only,
        "signal_generated_at": signal_report.get("generated_at"),
        "sizing": {
            "account_equity": sizing.account_equity,
            "risk_per_trade_pct": sizing.risk_per_trade_pct,
            "max_position_pct": sizing.max_position_pct,
            "hard_stop_pct": sizing.hard_stop_pct,
            "first_target_r": sizing.first_target_r,
            "weak_regime_size_multiplier": sizing.weak_regime_size_multiplier,
        },
        "portfolio_guard": portfolio_guard or None,
        "market_regime": signal_report.get("market_regime"),
        "watchlist_count": len(signal_report.get("watchlist", [])),
        "triggered_count": len(triggered),
        "actionable_triggered_count": len(actionable),
        "blocked_triggered_count": len(triggered) - len(actionable),
        "evaluations": evaluations,
    }


def evaluate_intraday_trigger(
    *,
    signal: dict[str, Any],
    history_payload: dict[str, Any],
    current_session_date: date | None = None,
    sizing: SizingInputs | None = None,
) -> TriggerEvaluation:
    symbol = str(signal.get("symbol") or "").upper()
    planned_entry_date = parse_date(signal.get("planned_entry_date"))
    signal_score = as_float(signal.get("total_score"))
    source = signal.get("source")
    sizing = sizing or SizingInputs()
    if planned_entry_date is None:
        return TriggerEvaluation(
            symbol=symbol,
            status="invalid_signal",
            triggered=False,
            planned_entry_date=None,
            signal_score=signal_score,
            source=source,
            reason="missing_planned_entry_date",
        )

    current_session_date = current_session_date or datetime.now(EASTERN).date()
    if current_session_date < planned_entry_date:
        return TriggerEvaluation(
            symbol=symbol,
            status="pending_entry_date",
            triggered=False,
            planned_entry_date=planned_entry_date.isoformat(),
            signal_score=signal_score,
            source=source,
            reason="planned_entry_date_not_reached",
        )

    candles = parse_schwab_candles(history_payload, regular_hours_only=True)
    session_candles = [candle for candle in candles if candle.session_date == planned_entry_date]
    if not session_candles:
        status = "expired_no_data" if current_session_date > planned_entry_date else "waiting_for_market_data"
        return TriggerEvaluation(
            symbol=symbol,
            status=status,
            triggered=False,
            planned_entry_date=planned_entry_date.isoformat(),
            signal_score=signal_score,
            source=source,
            reason="no_regular_session_candles_for_planned_entry_date",
        )
    if current_session_date > planned_entry_date:
        return TriggerEvaluation(
            symbol=symbol,
            status="expired",
            triggered=False,
            planned_entry_date=planned_entry_date.isoformat(),
            signal_score=signal_score,
            source=source,
            last_bar_time_et=session_candles[-1].dt_et.isoformat(),
            last_close=round(session_candles[-1].close, 4),
            reason="planned_entry_date_has_passed",
        )
    if len(session_candles) < 2:
        return TriggerEvaluation(
            symbol=symbol,
            status="waiting_after_first_candle",
            triggered=False,
            planned_entry_date=planned_entry_date.isoformat(),
            signal_score=signal_score,
            source=source,
            last_bar_time_et=session_candles[-1].dt_et.isoformat(),
            last_close=round(session_candles[-1].close, 4),
            reason="first_regular_5m_candle_is_skipped",
        )

    latest = session_candles[-1]
    prior = session_candles[-2]
    vwap = running_vwap(session_candles)
    technical_stop = as_float(signal.get("technical_stop_reference"))
    invalidated = technical_stop is not None and latest.close <= technical_stop
    close_above_vwap = latest.close > vwap
    close_above_prior_high = latest.close > prior.high
    triggered = (not invalidated) and close_above_vwap and close_above_prior_high
    trigger_price = max(vwap, prior.high)
    trade_plan = build_trade_plan(
        entry_price=trigger_price,
        technical_stop=technical_stop,
        market_regime=str(signal.get("market_regime") or "neutral"),
        sizing=sizing,
    )

    if invalidated:
        status = "invalidated"
        reason = "last_close_below_technical_stop_reference"
    elif triggered:
        status = "triggered"
        reason = "close_above_running_vwap_and_prior_candle_high"
    else:
        status = "waiting"
        reason = "confirmation_conditions_not_met"

    return TriggerEvaluation(
        symbol=symbol,
        status=status,
        triggered=triggered,
        planned_entry_date=planned_entry_date.isoformat(),
        signal_score=signal_score,
        source=source,
        last_bar_time_et=latest.dt_et.isoformat(),
        last_close=round(latest.close, 4),
        running_vwap=round(vwap, 4),
        prior_candle_high=round(prior.high, 4),
        trigger_price_reference=round(trigger_price, 4),
        stop_reference=trade_plan.stop_price,
        risk_per_share_reference=trade_plan.risk_per_share,
        trade_plan=trade_plan.to_dict(),
        manual_position_template=(
            build_manual_position_template(symbol, planned_entry_date, trade_plan)
            if triggered and trade_plan.suggested_shares > 0
            else None
        ),
        conditions={
            "first_5m_candle_skipped": True,
            "close_above_running_vwap": close_above_vwap,
            "close_above_prior_5m_high": close_above_prior_high,
            "not_invalidated_by_technical_stop": not invalidated,
        },
        reason=reason,
    )


def running_vwap(candles: list[Candle]) -> float:
    total_volume = sum(max(0.0, candle.volume) for candle in candles)
    if total_volume <= 0:
        return sum(candle.close for candle in candles) / len(candles)
    weighted = 0.0
    for candle in candles:
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        weighted += typical_price * max(0.0, candle.volume)
    return weighted / total_volume


def sizing_from_signal_report(report: dict[str, Any]) -> SizingInputs:
    raw = report.get("sizing") or {}
    return SizingInputs(
        account_equity=float(raw.get("account_equity", DEFAULT_INITIAL_EQUITY)),
        risk_per_trade_pct=float(raw.get("risk_per_trade_pct", DEFAULT_RISK_PER_TRADE_PCT)),
        max_position_pct=float(raw.get("max_position_pct", DEFAULT_MAX_POSITION_PCT)),
        hard_stop_pct=float(raw.get("hard_stop_pct", DEFAULT_HARD_STOP_PCT)),
        first_target_r=float(raw.get("first_target_r", DEFAULT_FIRST_TARGET_R)),
        weak_regime_size_multiplier=float(
            raw.get("weak_regime_size_multiplier", DEFAULT_WEAK_REGIME_SIZE_MULTIPLIER)
        ),
    )


def write_trigger_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_trigger_summary(report: dict[str, Any], report_path: Path, signal_path: Path | None = None) -> str:
    lines = [
        "Trigger summary",
        f"Generated: {report['generated_at']}",
        f"Triggered count: {report['triggered_count']}",
        f"Actionable triggered: {report.get('actionable_triggered_count', report['triggered_count'])}",
    ]
    portfolio_guard = report.get("portfolio_guard") or {}
    if portfolio_guard:
        lines.append(
            "Portfolio guard: "
            f"slots={portfolio_guard.get('available_slots')} "
            f"exposure={portfolio_guard.get('current_gross_exposure_pct')} "
            f"allow_new={portfolio_guard.get('allow_new_entries')}"
        )
    if signal_path is not None:
        lines.append(f"Signal report: {signal_path}")
    history_sources = report.get("history_sources") or {}
    if history_sources:
        lines.append(f"History sources: {format_history_sources(history_sources)}")
    for idx, item in enumerate(report.get("evaluations", [])[:12], start=1):
        lines.append(
            f"  {idx}. {item['symbol']} status={item['status']} "
            f"close={item.get('last_close')} trigger_ref={item.get('trigger_price_reference')} "
            f"stop_ref={item.get('stop_reference')} shares={shares_from_item(item)}"
            f"{action_note(item)}"
        )
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)


def format_trigger_discord_message(report: dict[str, Any]) -> str:
    triggered = [item for item in report.get("evaluations", []) if item.get("triggered")]
    actionable = [
        item
        for item in triggered
        if item.get("entry_allowed_by_portfolio_guard", True)
        and item.get("entry_allowed_by_symbol_guard", True)
    ]
    lines = [
        f"Liubang trigger scan | triggered={len(triggered)} actionable={len(actionable)} | signal_at={report.get('signal_generated_at')}",
    ]
    if not triggered:
        lines.append("No watchlist symbols have triggered the 5m confirmation yet.")
    portfolio_guard = report.get("portfolio_guard") or {}
    if portfolio_guard:
        lines.append(
            f"Slots: {portfolio_guard.get('available_slots')}/"
            f"{portfolio_guard.get('max_positions')} "
            f"exposure={portfolio_guard.get('current_gross_exposure_pct')} "
            f"allow_new={portfolio_guard.get('allow_new_entries')}"
        )
    for idx, item in enumerate(triggered[:8], start=1):
        source_note = " dyn" if item.get("source") == "dynamic_yfinance" else ""
        lines.append(
            f"{idx}. {item['symbol']}{source_note} close={item.get('last_close')} "
            f"trigger_ref={item.get('trigger_price_reference')} stop_ref={item.get('stop_reference')} "
            f"shares={shares_from_item(item)}{action_note(item)}"
        )
    return "\n".join(lines)[:1900]


def shares_from_item(item: dict[str, Any]) -> Any:
    plan = item.get("trade_plan") or {}
    return plan.get("suggested_shares")


def action_note(item: dict[str, Any]) -> str:
    action = item.get("action")
    if not action:
        return ""
    return f" action={action}"


def format_history_sources(summary: dict[str, Any]) -> str:
    by_source = summary.get("by_source") or {}
    counts = ", ".join(f"{source}={count}" for source, count in sorted(by_source.items()))
    fallback_count = summary.get("fallback_count") or 0
    return f"{counts or 'none'} fallbacks={fallback_count}"


def build_manual_position_template(symbol: str, entry_date: date, trade_plan: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "entry_date": entry_date.isoformat(),
        "entry_price": trade_plan.entry_price_reference,
        "shares": trade_plan.suggested_shares,
        "remaining_shares": trade_plan.suggested_shares,
        "initial_stop_price": trade_plan.stop_price,
        "current_stop_price": trade_plan.stop_price,
        "target_price": trade_plan.first_target_price,
        "target_hit": False,
        "source": "scan_triggers",
        "notes": "Adjust entry_price and shares to actual manual fill before monitoring.",
    }


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
