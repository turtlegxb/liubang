from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from liubang.backtest import Candle, EASTERN, parse_schwab_candles


DEFAULT_MANUAL_POSITIONS_PATH = Path("data/manual_positions.json")


@dataclass(frozen=True)
class ManualPosition:
    symbol: str
    entry_date: date
    entry_price: float
    shares: int
    remaining_shares: int
    initial_stop_price: float
    current_stop_price: float
    target_price: float
    target_hit: bool = False
    entry_time_et: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PositionEvaluation:
    symbol: str
    status: str
    action: str
    needs_attention: bool
    entry_date: str
    entry_price: float
    remaining_shares: int
    latest_session_date: str | None = None
    latest_close: float | None = None
    stop_reference: float | None = None
    target_price: float | None = None
    holding_sessions: int | None = None
    reason: str | None = None
    update_hint: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


def load_manual_positions(path: Path = DEFAULT_MANUAL_POSITIONS_PATH) -> tuple[ManualPosition, ...]:
    if not path.exists():
        raise FileNotFoundError(f"Manual positions file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = []
    for raw in payload.get("positions", []):
        if not isinstance(raw, dict):
            continue
        positions.append(parse_manual_position(raw))
    return tuple(positions)


def parse_manual_position(raw: dict[str, Any]) -> ManualPosition:
    symbol = str(raw["symbol"]).strip().upper()
    entry_price = float(raw["entry_price"])
    initial_stop = float(raw.get("initial_stop_price", raw.get("current_stop_price")))
    current_stop = float(raw.get("current_stop_price", initial_stop))
    remaining = int(raw.get("remaining_shares", raw.get("shares", 0)))
    return ManualPosition(
        symbol=symbol,
        entry_date=date.fromisoformat(str(raw["entry_date"])),
        entry_price=entry_price,
        shares=int(raw.get("shares", remaining)),
        remaining_shares=remaining,
        initial_stop_price=initial_stop,
        current_stop_price=current_stop,
        target_price=float(raw["target_price"]),
        target_hit=bool(raw.get("target_hit", False)),
        entry_time_et=parse_entry_time_et(raw.get("entry_time_et") or raw.get("entry_bar_time_et")),
        notes=raw.get("notes"),
    )


def monitor_positions(
    *,
    positions: tuple[ManualPosition, ...],
    history_by_symbol: dict[str, dict[str, Any]],
    max_hold_days: int = 5,
) -> dict[str, Any]:
    evaluations = [
        evaluate_position(
            position=position,
            history_payload=history_by_symbol.get(position.symbol, {}),
            max_hold_days=max_hold_days,
        ).to_dict()
        for position in positions
    ]
    attention = [item for item in evaluations if item.get("needs_attention")]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "market-data-only/manual-orders",
        "position_count": len(positions),
        "attention_count": len(attention),
        "evaluations": evaluations,
    }


def evaluate_position(
    *,
    position: ManualPosition,
    history_payload: dict[str, Any],
    max_hold_days: int = 5,
) -> PositionEvaluation:
    candles = candles_after_entry(position, parse_schwab_candles(history_payload, regular_hours_only=True))
    if not candles:
        return base_position_evaluation(
            position,
            status="no_data",
            action="hold",
            needs_attention=False,
            reason="no_regular_session_candles_since_entry",
        )

    sessions = sorted({candle.session_date for candle in candles})
    latest = candles[-1]
    holding_sessions = len(sessions)
    stop_price = max(position.current_stop_price, position.entry_price if position.target_hit else position.current_stop_price)
    current_session_candles = [candle for candle in candles if candle.session_date == latest.session_date]
    session_low = min(candle.low for candle in current_session_candles)
    session_high = max(candle.high for candle in current_session_candles)

    if session_low <= stop_price:
        action = "exit_remaining_stop" if not position.target_hit else "exit_remaining_breakeven_or_stop"
        return base_position_evaluation(
            position,
            status="exit",
            action=action,
            needs_attention=True,
            latest=latest,
            stop_reference=stop_price,
            holding_sessions=holding_sessions,
            reason="latest_session_low_touched_stop_reference",
        )

    if not position.target_hit and session_high >= position.target_price:
        sell_shares = max(1, position.remaining_shares // 2)
        return base_position_evaluation(
            position,
            status="take_partial",
            action="sell_half_at_first_target",
            needs_attention=True,
            latest=latest,
            stop_reference=position.current_stop_price,
            holding_sessions=holding_sessions,
            reason="latest_session_high_touched_first_target",
            update_hint={
                "sell_shares": sell_shares,
                "remaining_shares_after_action": position.remaining_shares - sell_shares,
                "set_target_hit": True,
                "move_stop_to_at_least": round(position.entry_price, 4),
            },
        )

    if holding_sessions >= max_hold_days:
        return base_position_evaluation(
            position,
            status="exit",
            action="exit_remaining_time_stop",
            needs_attention=True,
            latest=latest,
            stop_reference=stop_price,
            holding_sessions=holding_sessions,
            reason=f"max_hold_days_reached_{max_hold_days}",
        )

    return base_position_evaluation(
        position,
        status="hold",
        action="hold",
        needs_attention=False,
        latest=latest,
        stop_reference=stop_price,
        holding_sessions=holding_sessions,
        reason="no_exit_or_partial_condition_met",
    )


def candles_after_entry(position: ManualPosition, candles: list[Candle]) -> list[Candle]:
    output = []
    for candle in candles:
        if candle.session_date < position.entry_date:
            continue
        if (
            position.entry_time_et is not None
            and candle.session_date == position.entry_date
            and candle.dt_et <= position.entry_time_et
        ):
            continue
        output.append(candle)
    return output


def parse_entry_time_et(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def base_position_evaluation(
    position: ManualPosition,
    *,
    status: str,
    action: str,
    needs_attention: bool,
    latest: Candle | None = None,
    stop_reference: float | None = None,
    holding_sessions: int | None = None,
    reason: str | None = None,
    update_hint: dict[str, Any] | None = None,
) -> PositionEvaluation:
    return PositionEvaluation(
        symbol=position.symbol,
        status=status,
        action=action,
        needs_attention=needs_attention,
        entry_date=position.entry_date.isoformat(),
        entry_price=round(position.entry_price, 4),
        remaining_shares=position.remaining_shares,
        latest_session_date=latest.session_date.isoformat() if latest else None,
        latest_close=round(latest.close, 4) if latest else None,
        stop_reference=round(stop_reference, 4) if stop_reference is not None else None,
        target_price=round(position.target_price, 4),
        holding_sessions=holding_sessions,
        reason=reason,
        update_hint=update_hint,
    )


def write_position_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_position_summary(report: dict[str, Any], report_path: Path, positions_path: Path) -> str:
    lines = [
        "Position monitor summary",
        f"Generated: {report['generated_at']}",
        f"Positions: {report['position_count']}",
        f"Needs attention: {report['attention_count']}",
        f"Positions file: {positions_path}",
    ]
    history_sources = report.get("history_sources") or {}
    if history_sources:
        lines.append(f"History sources: {format_history_sources(history_sources)}")
    for idx, item in enumerate(report.get("evaluations", [])[:12], start=1):
        lines.append(
            f"  {idx}. {item['symbol']} status={item['status']} action={item['action']} "
            f"close={item.get('latest_close')} stop={item.get('stop_reference')} "
            f"held={item.get('holding_sessions')}"
        )
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)


def format_history_sources(summary: dict[str, Any]) -> str:
    by_source = summary.get("by_source") or {}
    counts = ", ".join(f"{source}={count}" for source, count in sorted(by_source.items()))
    fallback_count = summary.get("fallback_count") or 0
    return f"{counts or 'none'} fallbacks={fallback_count}"


def format_position_discord_message(report: dict[str, Any]) -> str:
    attention = [item for item in report.get("evaluations", []) if item.get("needs_attention")]
    lines = [
        f"Liubang position monitor | attention={len(attention)}",
    ]
    for idx, item in enumerate(attention[:8], start=1):
        lines.append(
            f"{idx}. {item['symbol']} {item['action']} close={item.get('latest_close')} "
            f"stop={item.get('stop_reference')} reason={item.get('reason')}"
        )
    if not attention:
        lines.append("No position actions currently required.")
    return "\n".join(lines)[:1900]
