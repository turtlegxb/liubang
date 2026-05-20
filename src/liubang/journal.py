from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from liubang.defaults import DEFAULT_INITIAL_EQUITY


DEFAULT_TRADE_JOURNAL_PATH = Path("data/trade_journal.csv")


@dataclass(frozen=True)
class JournalLot:
    trade_id: str
    symbol: str
    side: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: int
    initial_stop_price: float | None
    fees: float
    setup: str | None = None
    source: str | None = None
    notes: str | None = None

    @property
    def pnl(self) -> float:
        direction = 1.0 if self.side == "long" else -1.0
        return (self.exit_price - self.entry_price) * self.shares * direction - self.fees

    @property
    def initial_risk(self) -> float | None:
        if self.initial_stop_price is None:
            return None
        if self.side == "long":
            risk_per_share = self.entry_price - self.initial_stop_price
        else:
            risk_per_share = self.initial_stop_price - self.entry_price
        if risk_per_share <= 0:
            return None
        return risk_per_share * self.shares

    @property
    def r_multiple(self) -> float | None:
        initial_risk = self.initial_risk
        if not initial_risk:
            return None
        return self.pnl / initial_risk


@dataclass(frozen=True)
class JournalTrade:
    trade_id: str
    symbol: str
    side: str
    entry_date: date
    exit_date: date
    entry_price: float
    shares: int
    pnl: float
    initial_risk: float | None
    r_multiple: float | None
    holding_weekdays: int
    setup: str | None
    source: str | None
    lot_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_date"] = self.entry_date.isoformat()
        payload["exit_date"] = self.exit_date.isoformat()
        payload["pnl"] = round(self.pnl, 2)
        payload["initial_risk"] = round(self.initial_risk, 2) if self.initial_risk is not None else None
        payload["r_multiple"] = round(self.r_multiple, 4) if self.r_multiple is not None else None
        return payload


def load_journal_lots(path: Path = DEFAULT_TRADE_JOURNAL_PATH) -> tuple[JournalLot, ...]:
    if not path.exists():
        raise FileNotFoundError(f"Trade journal file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    lots = []
    for index, row in enumerate(rows, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue
        lots.append(parse_journal_lot(row, index=index))
    return tuple(lots)


def parse_journal_lot(row: dict[str, str], *, index: int) -> JournalLot:
    symbol = required(row, "symbol").upper()
    entry_date = date.fromisoformat(required(row, "entry_date"))
    exit_date = date.fromisoformat(required(row, "exit_date"))
    entry_price = as_float(required(row, "entry_price"))
    trade_id = (row.get("trade_id") or "").strip() or f"{symbol}-{entry_date.isoformat()}-{index}"
    return JournalLot(
        trade_id=trade_id,
        symbol=symbol,
        side=(row.get("side") or "long").strip().lower(),
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=as_float(required(row, "exit_price")),
        shares=as_int(required(row, "shares")),
        initial_stop_price=optional_float(row.get("initial_stop_price")),
        fees=optional_float(row.get("fees")) or 0.0,
        setup=optional_text(row.get("setup")),
        source=optional_text(row.get("source")),
        notes=optional_text(row.get("notes")),
    )


def aggregate_trades(lots: tuple[JournalLot, ...]) -> tuple[JournalTrade, ...]:
    by_id: dict[str, list[JournalLot]] = {}
    for lot in lots:
        by_id.setdefault(lot.trade_id, []).append(lot)

    trades = []
    for trade_id, group in by_id.items():
        group = sorted(group, key=lambda lot: (lot.exit_date, lot.symbol))
        first = group[0]
        shares = sum(lot.shares for lot in group)
        pnl = sum(lot.pnl for lot in group)
        risks = [lot.initial_risk for lot in group if lot.initial_risk is not None]
        total_risk = sum(risks) if risks else None
        r_multiple = pnl / total_risk if total_risk else None
        trades.append(
            JournalTrade(
                trade_id=trade_id,
                symbol=first.symbol,
                side=first.side,
                entry_date=min(lot.entry_date for lot in group),
                exit_date=max(lot.exit_date for lot in group),
                entry_price=first.entry_price,
                shares=shares,
                pnl=pnl,
                initial_risk=total_risk,
                r_multiple=r_multiple,
                holding_weekdays=count_weekdays(min(lot.entry_date for lot in group), max(lot.exit_date for lot in group)),
                setup=first.setup,
                source=first.source,
                lot_count=len(group),
            )
        )
    return tuple(sorted(trades, key=lambda trade: (trade.exit_date, trade.trade_id)))


def summarize_journal(
    *,
    lots: tuple[JournalLot, ...],
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    path: Path | None = None,
) -> dict[str, Any]:
    trades = aggregate_trades(lots)
    realized_pnl = sum(trade.pnl for trade in trades)
    winners = [trade for trade in trades if trade.pnl > 0]
    losers = [trade for trade in trades if trade.pnl < 0]
    r_values = [trade.r_multiple for trade in trades if trade.r_multiple is not None]
    equity_curve = build_equity_curve(trades, initial_equity=initial_equity)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_path": str(path) if path else None,
        "initial_equity": round(initial_equity, 2),
        "lot_count": len(lots),
        "trade_count": len(trades),
        "closed_trade_count": len(trades),
        "win_rate": round(len(winners) / len(trades), 4) if trades else None,
        "realized_pnl": round(realized_pnl, 2),
        "return_pct": round(realized_pnl / initial_equity, 6) if initial_equity else None,
        "average_pnl": round(realized_pnl / len(trades), 2) if trades else None,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "profit_factor": profit_factor(winners, losers),
        "average_holding_weekdays": round(sum(trade.holding_weekdays for trade in trades) / len(trades), 2) if trades else None,
        "max_drawdown_pct": max_drawdown_pct(equity_curve, initial_equity=initial_equity),
        "by_setup": summarize_groups(trades, "setup"),
        "by_symbol": summarize_groups(trades, "symbol"),
        "trades": [trade.to_dict() for trade in trades],
    }


def build_equity_curve(trades: tuple[JournalTrade, ...], *, initial_equity: float) -> list[dict[str, Any]]:
    equity = initial_equity
    curve = []
    for trade in trades:
        equity += trade.pnl
        curve.append(
            {
                "date": trade.exit_date.isoformat(),
                "trade_id": trade.trade_id,
                "equity": round(equity, 2),
                "pnl": round(trade.pnl, 2),
            }
        )
    return curve


def max_drawdown_pct(equity_curve: list[dict[str, Any]], *, initial_equity: float) -> float:
    peak = initial_equity
    max_dd = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return round(max_dd, 6)


def summarize_groups(trades: tuple[JournalTrade, ...], field: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for trade in trades:
        key = str(getattr(trade, field) or "unknown")
        bucket = output.setdefault(key, {"trade_count": 0, "wins": 0, "pnl": 0.0, "r_total": 0.0, "r_count": 0})
        bucket["trade_count"] += 1
        bucket["wins"] += 1 if trade.pnl > 0 else 0
        bucket["pnl"] += trade.pnl
        if trade.r_multiple is not None:
            bucket["r_total"] += trade.r_multiple
            bucket["r_count"] += 1
    for bucket in output.values():
        bucket["win_rate"] = round(bucket["wins"] / bucket["trade_count"], 4) if bucket["trade_count"] else None
        bucket["pnl"] = round(bucket["pnl"], 2)
        bucket["average_r"] = round(bucket["r_total"] / bucket["r_count"], 4) if bucket["r_count"] else None
        del bucket["wins"]
        del bucket["r_total"]
        del bucket["r_count"]
    return dict(sorted(output.items(), key=lambda item: item[1]["pnl"], reverse=True))


def profit_factor(winners: list[JournalTrade], losers: list[JournalTrade]) -> float | None:
    gross_profit = sum(trade.pnl for trade in winners)
    gross_loss = abs(sum(trade.pnl for trade in losers))
    if gross_loss == 0:
        return None if gross_profit == 0 else float("inf")
    return round(gross_profit / gross_loss, 4)


def write_journal_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_journal_summary(report: dict[str, Any], report_path: Path) -> str:
    win_rate = report.get("win_rate")
    win_rate_text = f"{win_rate:.2%}" if win_rate is not None else "n/a"
    return "\n".join(
        [
            "Journal summary",
            f"Generated: {report['generated_at']}",
            f"Trades: {report['trade_count']} lots={report['lot_count']}",
            f"Win rate: {win_rate_text}",
            f"Realized PnL: {report['realized_pnl']}",
            f"Average R: {report.get('average_r')}",
            f"Max drawdown: {report['max_drawdown_pct']:.2%}",
            f"Report: {report_path}",
        ]
    )


def required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required journal field: {key}")
    return value


def optional_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def optional_float(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    return as_float(value)


def as_float(value: str) -> float:
    return float(value.replace(",", ""))


def as_int(value: str) -> int:
    return int(float(value.replace(",", "")))


def count_weekdays(start: date, end: date) -> int:
    if end < start:
        return 0
    days = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days
