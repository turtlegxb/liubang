from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from liubang.journal import JournalTrade, aggregate_trades, load_journal_lots


@dataclass(frozen=True)
class RiskThrottleInputs:
    recent_trade_lookback: int = 5
    max_recent_r_loss: float = 3.0
    max_month_r_loss: float = 4.0
    max_consecutive_losses: int = 3


def risk_throttle_from_journal(
    path: Path,
    *,
    inputs: RiskThrottleInputs = RiskThrottleInputs(),
    as_of_date: date | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "source": "missing_file",
            "path": str(path),
            "allow_new_entries": True,
            "status": "not_loaded",
            "reason": "journal_file_not_found",
        }
    try:
        trades = aggregate_trades(load_journal_lots(path))
    except Exception as exc:
        return {
            "source": "trade_journal",
            "path": str(path),
            "allow_new_entries": False,
            "status": "error",
            "reason": "journal_parse_failed",
            "error": str(exc)[:300],
        }
    return evaluate_risk_throttle(
        trades=trades,
        inputs=inputs,
        as_of_date=as_of_date,
        source_path=path,
    )


def evaluate_risk_throttle(
    *,
    trades: tuple[JournalTrade, ...],
    inputs: RiskThrottleInputs = RiskThrottleInputs(),
    as_of_date: date | None = None,
    source_path: Path | None = None,
) -> dict[str, Any]:
    as_of_date = as_of_date or datetime.now(UTC).date()
    closed = tuple(sorted(trades, key=lambda trade: (trade.exit_date, trade.trade_id)))
    recent = closed[-max(0, inputs.recent_trade_lookback) :] if inputs.recent_trade_lookback else ()
    month_trades = tuple(
        trade
        for trade in closed
        if trade.exit_date.year == as_of_date.year and trade.exit_date.month == as_of_date.month
    )
    recent_r_total = sum_r(recent)
    month_r_total = sum_r(month_trades)
    consecutive_losses = count_consecutive_losses(closed)

    flags = []
    if recent and recent_r_total <= -abs(inputs.max_recent_r_loss):
        flags.append("recent_r_loss_limit")
    if month_trades and month_r_total <= -abs(inputs.max_month_r_loss):
        flags.append("month_r_loss_limit")
    if consecutive_losses >= inputs.max_consecutive_losses:
        flags.append("consecutive_loss_limit")

    return {
        "source": "trade_journal",
        "path": str(source_path) if source_path else None,
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "allow_new_entries": not flags,
        "status": "blocked" if flags else "ok",
        "flags": flags,
        "closed_trade_count": len(closed),
        "recent_trade_lookback": inputs.recent_trade_lookback,
        "recent_trade_count": len(recent),
        "recent_r_total": round(recent_r_total, 4),
        "max_recent_r_loss": inputs.max_recent_r_loss,
        "current_month_trade_count": len(month_trades),
        "current_month_r_total": round(month_r_total, 4),
        "max_month_r_loss": inputs.max_month_r_loss,
        "consecutive_losses": consecutive_losses,
        "max_consecutive_losses": inputs.max_consecutive_losses,
    }


def sum_r(trades: tuple[JournalTrade, ...]) -> float:
    return sum(float(trade.r_multiple or 0.0) for trade in trades)


def count_consecutive_losses(trades: tuple[JournalTrade, ...]) -> int:
    count = 0
    for trade in reversed(trades):
        if trade.pnl < 0:
            count += 1
        else:
            break
    return count
