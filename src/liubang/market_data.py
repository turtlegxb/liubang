from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liubang.schwab_adapter import SchwabAdapter


class SchwabHistoryCache:
    def __init__(self, *, adapter: SchwabAdapter, cache_dir: Path, extended_hours: bool) -> None:
        self._adapter = adapter
        self._cache_dir = cache_dir
        self._extended_hours = extended_hours

    def get_five_minute_history(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        cache_path = self.cache_path(symbol)
        if not refresh and cache_path.exists():
            cached = read_json(cache_path)
            cached.setdefault("source", "schwab")
            return cached

        payload = self._adapter.get_five_minute_history(
            symbol,
            extended_hours=self._extended_hours,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "symbol": symbol.upper(),
            "source": "schwab",
            "extended_hours": self._extended_hours,
            "payload": payload,
        }
        write_json(cache_path, wrapper)
        return wrapper

    def cache_path(self, symbol: str) -> Path:
        session = "ext" if self._extended_hours else "rth"
        return self._cache_dir / f"schwab_5m_{symbol.upper()}_{session}.json"


class YFinanceHistoryCache:
    def __init__(
        self,
        *,
        cache_dir: Path,
        period: str = "60d",
        interval: str = "5m",
        max_retries: int = 2,
        retry_sleep_seconds: float = 1.0,
    ) -> None:
        self._cache_dir = cache_dir
        self._period = period
        self._interval = interval
        self._max_retries = max_retries
        self._retry_sleep_seconds = retry_sleep_seconds

    def get_history(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        cache_path = self.cache_path(symbol)
        if not refresh and cache_path.exists():
            return read_json(cache_path)

        payload = {"symbol": symbol.upper(), "empty": True, "candles": []}
        for attempt in range(self._max_retries + 1):
            try:
                payload = fetch_yfinance_history(symbol, period=self._period, interval=self._interval)
            except Exception as exc:
                payload = {
                    "symbol": symbol.upper(),
                    "empty": True,
                    "candles": [],
                    "error": "yfinance_fetch_failed",
                    "error_detail": str(exc)[:300],
                }
            if payload_has_candles(payload):
                break
            if attempt < self._max_retries:
                time.sleep(self._retry_sleep_seconds * (attempt + 1))

        if not payload_has_candles(payload) and cache_path.exists():
            cached = read_json(cache_path)
            if payload_has_candles(cached):
                cached["stale_due_to_empty_refresh"] = True
                if payload.get("error"):
                    cached["stale_refresh_error"] = payload.get("error")
                    cached["stale_refresh_error_detail"] = payload.get("error_detail")
                return cached

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "symbol": symbol.upper(),
            "source": "yfinance",
            "period": self._period,
            "interval": self._interval,
            "payload": payload,
        }
        write_json(cache_path, wrapper)
        return wrapper

    def cache_path(self, symbol: str) -> Path:
        safe_period = self._period.replace("/", "_")
        safe_interval = self._interval.replace("/", "_")
        return self._cache_dir / f"yfinance_{safe_interval}_{safe_period}_{symbol.upper()}.json"


def fetch_yfinance_history(symbol: str, *, period: str, interval: str) -> dict[str, Any]:
    import pandas as pd
    import yfinance as yf

    frame = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return {"symbol": symbol.upper(), "empty": True, "candles": []}

    frame = normalize_yfinance_frame(frame, symbol)
    candles = []
    for index, row in frame.iterrows():
        timestamp = pd.Timestamp(index)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        timestamp = timestamp.tz_convert("UTC")
        try:
            candles.append(
                {
                    "datetime": int(timestamp.timestamp() * 1000),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row.get("Volume") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "symbol": symbol.upper(),
        "empty": not candles,
        "candles": candles,
        "source": "yfinance",
    }


def normalize_yfinance_frame(frame: Any, symbol: str) -> Any:
    try:
        import pandas as pd
    except Exception:
        return frame

    if isinstance(frame.columns, pd.MultiIndex):
        levels = frame.columns.names
        ticker = symbol.upper()
        if ticker in frame.columns.get_level_values(-1):
            frame = frame.xs(ticker, axis=1, level=-1)
        elif ticker in frame.columns.get_level_values(0):
            frame = frame.xs(ticker, axis=1, level=0)
        elif len(frame.columns.levels) > 1:
            frame.columns = frame.columns.get_level_values(0)
    return frame


def payload_has_candles(payload: dict[str, Any]) -> bool:
    raw_payload = payload.get("payload", payload)
    candles = raw_payload.get("candles") if isinstance(raw_payload, dict) else None
    return bool(candles)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
