from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liubang.market_data import read_json, write_json
from liubang.universe import unique_symbols


DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH = Path("data/cache/dynamic_universe_yfinance.json")
DEFAULT_DYNAMIC_SCREENERS = ("most_actives", "day_gainers", "growth_technology_stocks")
US_EQUITY_EXCHANGES = {"NMS", "NYQ", "ASE", "NAS", "NCM", "NGM", "PCX"}
QUOTE_FIELDS = (
    "symbol",
    "shortName",
    "longName",
    "quoteType",
    "market",
    "region",
    "exchange",
    "regularMarketPrice",
    "regularMarketVolume",
    "averageDailyVolume10Day",
    "averageDailyVolume3Month",
    "averageDailyVolume",
    "marketCap",
    "regularMarketChangePercent",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "sector",
    "industry",
)


@dataclass(frozen=True)
class DynamicUniverseItem:
    symbol: str
    source: str
    screeners: tuple[str, ...]
    short_name: str | None
    price: float
    regular_market_volume: int
    average_volume_proxy: int
    dollar_volume: float
    average_dollar_volume_proxy: float
    market_cap: int | None
    change_percent: float | None
    rank_score: float

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "screeners": list(self.screeners),
            "short_name": self.short_name,
            "price": round(self.price, 4),
            "regular_market_volume": self.regular_market_volume,
            "average_volume_proxy": self.average_volume_proxy,
            "dollar_volume": round(self.dollar_volume, 2),
            "average_dollar_volume_proxy": round(self.average_dollar_volume_proxy, 2),
            "market_cap": self.market_cap,
            "change_percent": round(self.change_percent, 4) if self.change_percent is not None else None,
            "rank_score": round(self.rank_score, 4),
            "reason": (
                "yfinance screener liquidity supplement; "
                f"screeners={','.join(self.screeners)}"
            ),
        }


@dataclass(frozen=True)
class DynamicUniverseSelection:
    items: tuple[DynamicUniverseItem, ...]
    source: str
    cache_path: str | None
    fetched_at: str | None
    screeners: tuple[str, ...]
    raw_quote_count: int
    filters: dict[str, Any]
    excluded_symbols: tuple[str, ...]
    errors: dict[str, str]
    stale_due_to_fetch_error: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.items)

    def metadata_by_symbol(self) -> dict[str, dict[str, Any]]:
        return {item.symbol: item.to_metadata() for item in self.items}

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "cache_path": self.cache_path,
            "fetched_at": self.fetched_at,
            "screeners": list(self.screeners),
            "raw_quote_count": self.raw_quote_count,
            "selected_count": len(self.items),
            "symbols": list(self.symbols),
            "filters": self.filters,
            "excluded_symbols": list(self.excluded_symbols),
            "errors": self.errors,
            "stale_due_to_fetch_error": self.stale_due_to_fetch_error,
        }


def parse_screeners(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_DYNAMIC_SCREENERS
    return tuple(
        item.strip()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    )


def load_or_select_yfinance_dynamic_universe(
    *,
    cache_path: Path = DEFAULT_DYNAMIC_UNIVERSE_CACHE_PATH,
    refresh: bool = False,
    screeners: tuple[str, ...] = DEFAULT_DYNAMIC_SCREENERS,
    screener_count: int = 100,
    excluded_symbols: tuple[str, ...] = (),
    limit: int = 8,
    min_price: float = 5.0,
    min_market_cap: float = 2_000_000_000.0,
    min_dollar_volume: float = 1_000_000_000.0,
    min_average_dollar_volume: float = 300_000_000.0,
) -> DynamicUniverseSelection:
    payload = load_or_fetch_yfinance_screener_payload(
        cache_path=cache_path,
        refresh=refresh,
        screeners=screeners,
        screener_count=screener_count,
    )
    return select_dynamic_universe(
        payload,
        cache_path=cache_path,
        excluded_symbols=excluded_symbols,
        limit=limit,
        min_price=min_price,
        min_market_cap=min_market_cap,
        min_dollar_volume=min_dollar_volume,
        min_average_dollar_volume=min_average_dollar_volume,
    )


def load_or_fetch_yfinance_screener_payload(
    *,
    cache_path: Path,
    refresh: bool,
    screeners: tuple[str, ...],
    screener_count: int,
) -> dict[str, Any]:
    if cache_path.exists() and not refresh:
        return read_json(cache_path)

    try:
        payload = fetch_yfinance_screener_payload(screeners=screeners, count=screener_count)
    except Exception as exc:
        if cache_path.exists():
            payload = read_json(cache_path)
            payload["stale_due_to_fetch_error"] = str(exc)[:300]
            return payload
        return {
            "source": "yfinance.screen",
            "fetched_at": datetime.now(UTC).isoformat(),
            "screeners": list(screeners),
            "quotes": [],
            "errors": {"fetch": str(exc)[:300]},
        }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(cache_path, payload)
    return payload


def fetch_yfinance_screener_payload(*, screeners: tuple[str, ...], count: int) -> dict[str, Any]:
    import yfinance as yf

    quotes_by_symbol: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    for screener in screeners:
        try:
            result = yf.screen(screener, count=count)
        except Exception as exc:
            errors[screener] = str(exc)[:300]
            continue

        raw_quotes = result.get("quotes", []) if isinstance(result, dict) else []
        for raw_quote in raw_quotes:
            if not isinstance(raw_quote, dict):
                continue
            quote = normalize_yfinance_quote(raw_quote)
            symbol = str(quote.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            current = quotes_by_symbol.setdefault(symbol, {"symbol": symbol, "screeners": []})
            current["screeners"] = unique_strings((*tuple(current.get("screeners", [])), screener))
            for field, value in quote.items():
                if field not in current or current[field] in (None, ""):
                    current[field] = value

    return {
        "source": "yfinance.screen",
        "fetched_at": datetime.now(UTC).isoformat(),
        "screeners": list(screeners),
        "quotes": sorted(quotes_by_symbol.values(), key=lambda item: item["symbol"]),
        "errors": errors,
    }


def normalize_yfinance_quote(raw: dict[str, Any]) -> dict[str, Any]:
    quote = {}
    for field in QUOTE_FIELDS:
        if field in raw:
            quote[field] = normalize_json_scalar(raw[field])
    if "symbol" in quote:
        quote["symbol"] = str(quote["symbol"]).strip().upper()
    return quote


def select_dynamic_universe(
    payload: dict[str, Any],
    *,
    cache_path: Path | None,
    excluded_symbols: tuple[str, ...],
    limit: int,
    min_price: float,
    min_market_cap: float,
    min_dollar_volume: float,
    min_average_dollar_volume: float,
) -> DynamicUniverseSelection:
    excluded = set(unique_symbols(excluded_symbols))
    filters = {
        "limit": limit,
        "min_price": min_price,
        "min_market_cap": min_market_cap,
        "min_dollar_volume": min_dollar_volume,
        "min_average_dollar_volume_proxy": min_average_dollar_volume,
        "average_volume_proxy": "averageDailyVolume10Day, falling back to 3-month or generic average volume",
    }
    items = []
    quotes = payload.get("quotes", [])
    for quote in quotes if isinstance(quotes, list) else []:
        item = item_from_quote(
            quote,
            excluded_symbols=excluded,
            min_price=min_price,
            min_market_cap=min_market_cap,
            min_dollar_volume=min_dollar_volume,
            min_average_dollar_volume=min_average_dollar_volume,
        )
        if item is not None:
            items.append(item)

    items = sorted(
        items,
        key=lambda item: (
            item.rank_score,
            item.dollar_volume,
            item.average_dollar_volume_proxy,
        ),
        reverse=True,
    )[: max(0, limit)]

    return DynamicUniverseSelection(
        items=tuple(items),
        source=str(payload.get("source") or "yfinance.screen"),
        cache_path=str(cache_path) if cache_path else None,
        fetched_at=payload.get("fetched_at"),
        screeners=tuple(payload.get("screeners") or ()),
        raw_quote_count=len(quotes) if isinstance(quotes, list) else 0,
        filters=filters,
        excluded_symbols=tuple(sorted(excluded)),
        errors={str(key): str(value) for key, value in (payload.get("errors") or {}).items()},
        stale_due_to_fetch_error=payload.get("stale_due_to_fetch_error"),
    )


def item_from_quote(
    quote: dict[str, Any],
    *,
    excluded_symbols: set[str],
    min_price: float,
    min_market_cap: float,
    min_dollar_volume: float,
    min_average_dollar_volume: float,
) -> DynamicUniverseItem | None:
    symbol = str(quote.get("symbol") or "").strip().upper()
    if not symbol or symbol in excluded_symbols or not looks_like_tradeable_symbol(symbol):
        return None
    if not is_us_equity_quote(quote):
        return None

    price = as_float(quote.get("regularMarketPrice"))
    volume = as_int(quote.get("regularMarketVolume"))
    average_volume = first_int(
        quote,
        "averageDailyVolume10Day",
        "averageDailyVolume3Month",
        "averageDailyVolume",
    )
    if price is None or price < min_price or volume is None or average_volume is None:
        return None

    market_cap = as_int(quote.get("marketCap"))
    if market_cap is not None and market_cap < min_market_cap:
        return None

    dollar_volume = price * volume
    average_dollar_volume = price * average_volume
    if dollar_volume < min_dollar_volume or average_dollar_volume < min_average_dollar_volume:
        return None

    screeners = tuple(str(item) for item in quote.get("screeners", []) if item)
    change_percent = as_float(quote.get("regularMarketChangePercent"))
    rank_score = (
        min(dollar_volume / min_dollar_volume, 8.0)
        + min(average_dollar_volume / min_average_dollar_volume, 8.0)
        + max(0, len(screeners) - 1) * 0.75
    )
    if change_percent is not None:
        rank_score += max(-2.0, min(2.0, change_percent / 5.0))

    return DynamicUniverseItem(
        symbol=symbol,
        source="dynamic_yfinance",
        screeners=screeners,
        short_name=str(quote.get("shortName") or quote.get("longName") or "").strip() or None,
        price=price,
        regular_market_volume=volume,
        average_volume_proxy=average_volume,
        dollar_volume=dollar_volume,
        average_dollar_volume_proxy=average_dollar_volume,
        market_cap=market_cap,
        change_percent=change_percent,
        rank_score=rank_score,
    )


def is_us_equity_quote(quote: dict[str, Any]) -> bool:
    quote_type = str(quote.get("quoteType") or "").upper()
    if quote_type and quote_type != "EQUITY":
        return False
    market = str(quote.get("market") or "").lower()
    if market and market != "us_market":
        return False
    region = str(quote.get("region") or "").upper()
    if region and region != "US":
        return False
    exchange = str(quote.get("exchange") or "").upper()
    return not exchange or exchange in US_EQUITY_EXCHANGES


def looks_like_tradeable_symbol(symbol: str) -> bool:
    if not re.match(r"^[A-Z][A-Z0-9.-]{0,11}$", symbol):
        return False
    bad_fragments = ("-W", "-WS", "-WT", "-U", "-UN", "-R", ".W", ".WS", ".WT")
    return not any(fragment in symbol for fragment in bad_fragments)


def unique_strings(values: tuple[str, ...]) -> list[str]:
    output = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in output:
            output.append(normalized)
    return output


def first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = as_int(source.get(key))
        if value is not None:
            return value
    return None


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def normalize_json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return value.item()
    except AttributeError:
        return str(value)
