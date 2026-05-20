from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_NEWS_CACHE_DIR = Path("data/cache/news")
NON_NEWS_SYMBOLS = {"SPY", "QQQ", "XLK", "SMH"}


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    title: str
    published_at: datetime | None
    provider: str
    url: str | None
    source: str = "unknown"


@dataclass(frozen=True)
class NewsBook:
    items_by_symbol: dict[str, tuple[NewsItem, ...]]
    source: str

    def recent_for(self, symbol: str, *, max_age_hours: int, limit: int) -> tuple[NewsItem, ...]:
        items = self.items_by_symbol.get(symbol.upper(), ())
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        recent = [
            item
            for item in items
            if item.published_at is None or item.published_at >= cutoff
        ]
        return tuple(recent[:limit])

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "symbols": sorted(self.items_by_symbol),
            "item_count": sum(len(items) for items in self.items_by_symbol.values()),
        }


def load_or_fetch_yfinance_news(
    *,
    symbols: tuple[str, ...],
    cache_dir: Path = DEFAULT_NEWS_CACHE_DIR,
    refresh: bool = False,
    limit: int = 10,
) -> NewsBook:
    items_by_symbol = {}
    for symbol in symbols:
        if symbol.upper() in NON_NEWS_SYMBOLS:
            continue
        items_by_symbol[symbol] = load_or_fetch_symbol_news(
            symbol,
            cache_dir=cache_dir,
            refresh=refresh,
            limit=limit,
        )
    return NewsBook(items_by_symbol=items_by_symbol, source="yfinance")


def load_or_fetch_symbol_news(
    symbol: str,
    *,
    cache_dir: Path,
    refresh: bool,
    limit: int,
) -> tuple[NewsItem, ...]:
    cache_path = cache_dir / f"{symbol.upper()}.json"
    if cache_path.exists() and not refresh:
        return parse_news_payload(cache_path)

    payload = fetch_yfinance_news(symbol, limit=limit)
    if payload.get("error") and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["stale_due_to_fetch_error"] = str(payload["error"])[:300]
        cache_path.write_text(json.dumps(cached, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return parse_news_payload(cache_path)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return parse_news_payload(cache_path)


def fetch_yfinance_news(symbol: str, *, limit: int) -> dict[str, Any]:
    import yfinance as yf

    try:
        raw_items = yf.Ticker(symbol).news or []
    except Exception as exc:
        return {
            "symbol": symbol.upper(),
            "source": "yfinance",
            "fetched_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
            "items": [],
        }

    items = []
    for raw in raw_items[:limit]:
        content = raw.get("content", raw) if isinstance(raw, dict) else {}
        if not isinstance(content, dict):
            continue
        items.append(normalize_yfinance_news_item(symbol, content))
    return {
        "symbol": symbol.upper(),
        "source": "yfinance",
        "fetched_at": datetime.now(UTC).isoformat(),
        "items": items,
    }


def normalize_yfinance_news_item(symbol: str, content: dict[str, Any]) -> dict[str, Any]:
    provider = content.get("provider") or {}
    canonical_url = content.get("canonicalUrl") or {}
    click_url = content.get("clickThroughUrl") or {}
    return {
        "symbol": symbol.upper(),
        "title": str(content.get("title") or "").strip(),
        "published_at": content.get("pubDate") or content.get("displayTime"),
        "provider": str(provider.get("displayName") or provider.get("sourceId") or "unknown"),
        "url": canonical_url.get("url") or click_url.get("url"),
        "source": "yfinance.news",
    }


def parse_news_payload(path: Path) -> tuple[NewsItem, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for raw in payload.get("items", []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        items.append(
            NewsItem(
                symbol=str(raw.get("symbol") or payload.get("symbol") or "").upper(),
                title=title,
                published_at=parse_datetime(raw.get("published_at")),
                provider=str(raw.get("provider") or "unknown"),
                url=raw.get("url"),
                source=str(raw.get("source") or payload.get("source") or "unknown"),
            )
        )
    return tuple(sorted(items, key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC), reverse=True))


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
