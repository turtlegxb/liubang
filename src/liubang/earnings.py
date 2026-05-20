from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_EARNINGS_CACHE_PATH = Path("data/cache/earnings_calendar_yfinance.json")
VALID_TIMINGS = {"bmo", "amc", "dmh", "unknown"}
NON_EARNINGS_SYMBOLS = {"SPY", "QQQ", "XLK", "SMH"}


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    report_date: date
    timing: str = "unknown"
    status: str = "unknown"
    source: str = "unknown"


@dataclass(frozen=True)
class EarningsCalendar:
    events: tuple[EarningsEvent, ...]
    source: str = "none"
    loaded_from: str | None = None

    @property
    def event_count(self) -> int:
        return len(self.events)

    def events_for(self, symbol: str) -> tuple[EarningsEvent, ...]:
        normalized = symbol.upper()
        return tuple(event for event in self.events if event.symbol == normalized)

    def blocked_entry_dates(self, symbol: str, trading_dates: list[date]) -> set[date]:
        blocked: set[date] = set()
        for event in self.events_for(symbol):
            previous = previous_trading_date(trading_dates, event.report_date)
            if previous is not None:
                blocked.add(previous)
            blocked.add(event.report_date)
        return blocked

    def exit_dates(self, symbol: str, trading_dates: list[date]) -> set[date]:
        exits: set[date] = set()
        for event in self.events_for(symbol):
            previous = previous_trading_date(trading_dates, event.report_date)
            if event.timing == "amc":
                exits.add(event.report_date)
            elif previous is not None:
                exits.add(previous)
            else:
                exits.add(event.report_date)
        return exits

    def next_event_on_or_after(self, symbol: str, session_date: date) -> EarningsEvent | None:
        candidates = [
            event
            for event in self.events_for(symbol)
            if event.report_date >= session_date
        ]
        return min(candidates, key=lambda event: event.report_date) if candidates else None

    def summary(self) -> dict[str, Any]:
        by_symbol: dict[str, int] = {}
        for event in self.events:
            by_symbol[event.symbol] = by_symbol.get(event.symbol, 0) + 1
        return {
            "source": self.source,
            "loaded_from": self.loaded_from,
            "event_count": len(self.events),
            "symbols": sorted(by_symbol),
            "events_by_symbol": by_symbol,
        }


def load_earnings_calendar(path: Path) -> EarningsCalendar:
    if not path.exists():
        return EarningsCalendar(events=(), source="missing_file", loaded_from=str(path))

    payload = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for raw in payload.get("events", []):
        if not isinstance(raw, dict):
            continue
        event = parse_event(raw)
        if event is not None:
            events.append(event)
    return EarningsCalendar(
        events=tuple(sorted(events, key=lambda item: (item.report_date, item.symbol))),
        source=str(payload.get("source") or payload.get("name") or "file"),
        loaded_from=str(path),
    )


def load_or_fetch_yfinance_earnings(
    *,
    symbols: tuple[str, ...],
    cache_path: Path = DEFAULT_EARNINGS_CACHE_PATH,
    refresh: bool = False,
    limit: int = 24,
) -> EarningsCalendar:
    if cache_path.exists() and not refresh:
        calendar = load_earnings_calendar(cache_path)
        cached_symbols = load_cached_earnings_symbols(cache_path)
        missing_symbols = tuple(
            symbol.upper()
            for symbol in symbols
            if symbol.upper() not in cached_symbols and symbol.upper() not in NON_EARNINGS_SYMBOLS
        )
        if not missing_symbols:
            return calendar
        events = tuple(calendar.events) + fetch_yfinance_earnings(missing_symbols, limit=limit)
        write_yfinance_earnings_cache(cache_path, events, symbols_requested=tuple(sorted(cached_symbols | set(missing_symbols))))
        return load_earnings_calendar(cache_path)
    events = fetch_yfinance_earnings(symbols, limit=limit)
    write_yfinance_earnings_cache(cache_path, events, symbols_requested=symbols)
    return load_earnings_calendar(cache_path)


def write_yfinance_earnings_cache(
    cache_path: Path,
    events: tuple[EarningsEvent, ...],
    *,
    symbols_requested: tuple[str, ...],
) -> None:
    payload = {
        "name": "yfinance_earnings_calendar",
        "source": "yfinance",
        "updated_at": datetime.now(UTC).isoformat(),
        "symbols_requested": sorted(
            {
                symbol.upper()
                for symbol in symbols_requested
                if symbol.upper() not in NON_EARNINGS_SYMBOLS
            }
        ),
        "events": [
            serialize_event(event)
            for event in sorted(
                deduplicate_events(list(events)),
                key=lambda item: (item.report_date, item.symbol),
            )
        ],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_cached_earnings_symbols(cache_path: Path) -> set[str]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(symbol).upper()
        for symbol in payload.get("symbols_requested", [])
        if symbol
    }


def fetch_yfinance_earnings(symbols: tuple[str, ...], *, limit: int) -> tuple[EarningsEvent, ...]:
    import pandas as pd
    import yfinance as yf

    events: list[EarningsEvent] = []
    for symbol in symbols:
        if symbol.upper() in NON_EARNINGS_SYMBOLS:
            continue
        try:
            frame = yf.Ticker(symbol).get_earnings_dates(limit=limit)
        except Exception:
            frame = None
        if frame is None or getattr(frame, "empty", True):
            events.extend(fetch_yfinance_calendar_fallback(symbol))
            continue

        for index, row in frame.iterrows():
            timestamp = pd.Timestamp(index)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("America/New_York")
            report_date = timestamp.date()
            status = "reported" if not pd.isna(row.get("Reported EPS")) else "estimated"
            events.append(
                EarningsEvent(
                    symbol=symbol.upper(),
                    report_date=report_date,
                    timing=infer_timing(timestamp.hour),
                    status=status,
                    source="yfinance.earnings_dates",
                )
            )
    return tuple(sorted(deduplicate_events(events), key=lambda item: (item.report_date, item.symbol)))


def fetch_yfinance_calendar_fallback(symbol: str) -> tuple[EarningsEvent, ...]:
    import yfinance as yf

    try:
        calendar = yf.Ticker(symbol).calendar
    except Exception:
        return ()

    raw_dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
    if raw_dates is None:
        return ()
    if not isinstance(raw_dates, list):
        raw_dates = [raw_dates]

    events = []
    for raw_date in raw_dates:
        try:
            report_date = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        events.append(
            EarningsEvent(
                symbol=symbol.upper(),
                report_date=report_date,
                timing="unknown",
                status="estimated",
                source="yfinance.calendar",
            )
        )
    return tuple(events)


def parse_event(raw: dict[str, Any]) -> EarningsEvent | None:
    symbol = str(raw.get("symbol", "")).strip().upper()
    raw_date = raw.get("date") or raw.get("report_date")
    if not symbol or not raw_date:
        return None
    timing = str(raw.get("timing", "unknown")).strip().lower()
    if timing not in VALID_TIMINGS:
        timing = "unknown"
    return EarningsEvent(
        symbol=symbol,
        report_date=date.fromisoformat(str(raw_date)),
        timing=timing,
        status=str(raw.get("status", "unknown")),
        source=str(raw.get("source", "file")),
    )


def serialize_event(event: EarningsEvent) -> dict[str, Any]:
    return {
        "symbol": event.symbol,
        "date": event.report_date.isoformat(),
        "timing": event.timing,
        "status": event.status,
        "source": event.source,
    }


def deduplicate_events(events: list[EarningsEvent]) -> list[EarningsEvent]:
    by_key: dict[tuple[str, date], EarningsEvent] = {}
    for event in events:
        key = (event.symbol, event.report_date)
        current = by_key.get(key)
        if current is None or (current.status != "reported" and event.status == "reported"):
            by_key[key] = event
    return list(by_key.values())


def infer_timing(hour: int) -> str:
    if hour <= 9:
        return "bmo"
    if hour >= 16:
        return "amc"
    return "dmh"


def previous_trading_date(trading_dates: list[date], target: date) -> date | None:
    previous = [item for item in trading_dates if item < target]
    if previous:
        return max(previous)
    return previous_weekday(target)


def previous_weekday(target: date) -> date:
    cursor = target - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def next_weekday(target: date) -> date:
    cursor = target + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor
