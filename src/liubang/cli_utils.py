from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from liubang.earnings import EarningsCalendar, load_earnings_calendar, load_or_fetch_yfinance_earnings
from liubang.market_data import SchwabHistoryCache, YFinanceHistoryCache, payload_has_candles
from liubang.news import DEFAULT_NEWS_CACHE_DIR, NewsBook, load_or_fetch_yfinance_news
from liubang.schwab_adapter import RetrySettings, SchwabAdapter


SHELL_ENV_NAMES = (
    "SCHWAB_APP_KEY",
    "SCHWAB_APP_SECRET",
    "SCHWAB_TOKEN_PATH",
    "SCHWAB_PROBE_SYMBOLS",
    "SCHWAB_REQUEST_SLEEP_SECONDS",
    "SCHWAB_MAX_RETRIES",
    "SCHWAB_BACKOFF_SECONDS",
    "SCHWAB_INCLUDE_EXTENDED_HOURS",
    "SCHWAB_INCLUDE_ACCOUNTS",
    "DISCORD_WEBHOOK_URL",
)


def load_env(
    *,
    shell_env_path: Path | None = None,
    shell_env_names: tuple[str, ...] = SHELL_ENV_NAMES,
) -> None:
    load_dotenv(override=False)
    missing = tuple(name for name in shell_env_names if not os.getenv(name))
    if missing:
        load_zshrc_env_vars(missing, path=shell_env_path)


def load_zshrc_env_vars(
    names: tuple[str, ...],
    *,
    path: Path | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, str]:
    zshrc_path = (path or Path.home() / ".zshrc").expanduser()
    names = tuple(name for name in names if is_safe_env_name(name))
    if not names or not zshrc_path.exists():
        return {}

    keys = " ".join(names)
    script = (
        f"source {shell_quote(str(zshrc_path))} >/dev/null 2>&1 || true\n"
        f"keys=({keys})\n"
        "for key in $keys; do\n"
        "  if (( ${+parameters[$key]} )); then\n"
        "    value=${(P)key}\n"
        "    if [[ -n \"$value\" ]]; then\n"
        "      printf '%s\\0%s\\0' \"$key\" \"$value\"\n"
        "    fi\n"
        "  fi\n"
        "done\n"
    )
    try:
        result = subprocess.run(
            ["/bin/zsh", "-lc", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    values = parse_null_delimited_env(result.stdout)
    loaded = {}
    for name, value in values.items():
        if name in names and value and not os.getenv(name):
            os.environ[name] = value
            loaded[name] = value
    return loaded


def parse_null_delimited_env(raw: bytes) -> dict[str, str]:
    parts = raw.split(b"\0")
    values = {}
    for index in range(0, len(parts) - 1, 2):
        if not parts[index]:
            continue
        try:
            name = parts[index].decode("utf-8")
            value = parts[index + 1].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_safe_env_name(name):
            values[name] = value
    return values


def is_safe_env_name(name: str) -> bool:
    return bool(name) and all(char.isalnum() or char == "_" for char in name)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_schwab_history_cache(*, cache_dir: Path, extended_hours: bool) -> SchwabHistoryCache:
    adapter = SchwabAdapter(
        app_key=required_env("SCHWAB_APP_KEY"),
        app_secret=required_env("SCHWAB_APP_SECRET"),
        token_path=Path(required_env("SCHWAB_TOKEN_PATH")).expanduser().resolve(),
        retry_settings=RetrySettings(),
    )
    return SchwabHistoryCache(
        adapter=adapter,
        cache_dir=cache_dir,
        extended_hours=extended_hours,
    )


def load_histories(
    *,
    symbols: tuple[str, ...],
    cache_dir: Path,
    extended_hours: bool,
    refresh: bool,
    history_provider: str = "schwab",
    fallback_history_provider: str = "yfinance",
    yfinance_period: str = "60d",
) -> dict[str, dict]:
    schwab_cache = (
        build_schwab_history_cache(cache_dir=cache_dir, extended_hours=extended_hours)
        if history_provider == "schwab"
        else None
    )
    yfinance_cache = (
        YFinanceHistoryCache(cache_dir=cache_dir, period=yfinance_period, interval="5m")
        if history_provider == "yfinance" or fallback_history_provider == "yfinance"
        else None
    )

    histories = {}
    for symbol in symbols:
        if history_provider == "schwab":
            try:
                if schwab_cache is None:
                    raise RuntimeError("Schwab cache is unavailable")
                payload = schwab_cache.get_five_minute_history(symbol, refresh=refresh)
            except Exception as exc:
                payload = {
                    "symbol": symbol.upper(),
                    "source": "schwab",
                    "payload": {"candles": []},
                    "error": "schwab_fetch_failed",
                    "error_detail": str(exc)[:300],
                }
            if not payload_has_candles(payload) and fallback_history_provider == "yfinance":
                if yfinance_cache is None:
                    raise RuntimeError("yfinance fallback cache is unavailable")
                fallback_payload = dict(yfinance_cache.get_history(symbol, refresh=refresh))
                fallback_payload["fallback_from"] = history_provider
                fallback_payload["fallback_reason"] = payload.get("error") or "primary_returned_no_candles"
                if payload.get("error_detail"):
                    fallback_payload["primary_error_detail"] = payload["error_detail"]
                payload = fallback_payload
        elif history_provider == "yfinance":
            if yfinance_cache is None:
                raise RuntimeError("yfinance cache is unavailable")
            payload = yfinance_cache.get_history(symbol, refresh=refresh)
        else:
            raise ValueError(f"Unsupported history provider: {history_provider}")
        histories[symbol] = payload
    return histories


def summarize_history_sources(history_by_symbol: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    fallback_symbols = {}
    error_symbols = {}
    stale_symbols = []
    for symbol, payload in sorted(history_by_symbol.items()):
        source = history_source(payload)
        by_source[source] = by_source.get(source, 0) + 1
        if payload.get("fallback_from"):
            fallback_symbols[symbol] = {
                "from": payload.get("fallback_from"),
                "to": source,
                "reason": payload.get("fallback_reason"),
            }
        if payload.get("stale_due_to_empty_refresh"):
            stale_symbols.append(symbol)
        error = payload.get("error") or payload.get("primary_error_detail")
        if error:
            error_symbols[symbol] = str(error)
    return {
        "by_source": by_source,
        "fallback_count": len(fallback_symbols),
        "fallback_symbols": fallback_symbols,
        "stale_due_to_empty_refresh": stale_symbols,
        "error_symbols": error_symbols,
    }


def history_source(payload: dict[str, Any]) -> str:
    source = payload.get("source")
    if source:
        return str(source)
    if "extended_hours" in payload:
        return "schwab"
    raw_payload = payload.get("payload")
    if isinstance(raw_payload, dict) and raw_payload.get("source"):
        return str(raw_payload["source"])
    return "unknown"


def parse_float_grid(raw: str) -> tuple[float, ...]:
    values = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item:
            values.append(float(item))
    return tuple(values)


def load_earnings_for_symbols(
    *,
    symbols: tuple[str, ...],
    source: str,
    calendar_path: Path,
    refresh: bool,
    limit: int,
) -> EarningsCalendar | None:
    if source == "none":
        return None
    if source == "file":
        return load_earnings_calendar(calendar_path)
    if source == "yfinance":
        return load_or_fetch_yfinance_earnings(
            symbols=symbols,
            cache_path=calendar_path,
            refresh=refresh,
            limit=limit,
        )
    raise ValueError(f"Unsupported earnings source: {source}")


def load_news_for_symbols(
    *,
    symbols: tuple[str, ...],
    source: str,
    cache_dir: Path = DEFAULT_NEWS_CACHE_DIR,
    refresh: bool = False,
    limit: int = 10,
) -> NewsBook | None:
    if source == "none":
        return None
    if source == "yfinance":
        return load_or_fetch_yfinance_news(
            symbols=symbols,
            cache_dir=cache_dir,
            refresh=refresh,
            limit=limit,
        )
    raise ValueError(f"Unsupported news source: {source}")


def send_discord_message(webhook_url: str, content: str, *, max_retries: int = 3) -> None:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(webhook_url, json={"content": content}, timeout=15.0)
            response.raise_for_status()
            return
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(1.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
