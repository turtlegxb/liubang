from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from liubang.config import SchwabProbeConfig
from liubang.schwab_adapter import RetrySettings, SchwabAdapter


EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ProbeArtifacts:
    public_report_path: Path
    debug_report_path: Path | None
    report: dict[str, Any]


def run_probe(config: SchwabProbeConfig) -> ProbeArtifacts:
    if not config.token_path.exists():
        raise FileNotFoundError(f"Schwab token file not found: {config.token_path}")

    adapter = SchwabAdapter(
        app_key=config.app_key,
        app_secret=config.app_secret,
        token_path=config.token_path,
        retry_settings=RetrySettings(
            request_sleep_seconds=config.request_sleep_seconds,
            max_retries=config.max_retries,
            backoff_seconds=config.backoff_seconds,
        ),
    )

    generated_at = datetime.now(UTC)
    account_probe = fetch_accounts_safely(adapter) if config.include_accounts else skipped_account_probe()
    quotes_raw = adapter.get_quotes(config.symbols)

    histories_raw: dict[str, dict[str, Any]] = {}
    history_summaries: dict[str, dict[str, Any]] = {}
    for symbol in config.symbols:
        history = adapter.get_five_minute_history(
            symbol,
            extended_hours=config.extended_hours,
        )
        histories_raw[symbol] = history
        history_summaries[symbol] = summarize_history(history)

    report = {
        "generated_at": generated_at.isoformat(),
        "config": redacted_config(config),
        "token": summarize_token(adapter.token_age_seconds()),
        "accounts": account_probe["summary"],
        "quotes": summarize_quotes(quotes_raw, config.symbols),
        "five_minute_history": history_summaries,
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
    public_report_path = config.output_dir / f"schwab_probe_{timestamp}.json"
    write_json(public_report_path, report)

    debug_report_path = None
    if config.include_debug_report:
        debug_report = {
            "generated_at": generated_at.isoformat(),
            "raw_account_numbers": account_probe["raw_account_numbers"],
            "raw_accounts": account_probe["raw_accounts"],
            "account_error": account_probe["error"],
            "raw_quotes": quotes_raw,
            "raw_five_minute_history": histories_raw,
        }
        debug_report_path = config.output_dir / f"schwab_probe_debug_{timestamp}.json"
        write_json(debug_report_path, debug_report)

    return ProbeArtifacts(
        public_report_path=public_report_path,
        debug_report_path=debug_report_path,
        report=report,
    )


def fetch_accounts_safely(adapter: SchwabAdapter) -> dict[str, Any]:
    raw_account_numbers: list[dict[str, Any]] = []
    raw_accounts: list[dict[str, Any]] = []
    try:
        raw_account_numbers = adapter.get_account_numbers()
        raw_accounts = adapter.get_accounts()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        return {
            "raw_account_numbers": raw_account_numbers,
            "raw_accounts": raw_accounts,
            "error": {
                "status_code": status_code,
                "message": _account_error_message(status_code),
            },
            "summary": {
                "status": "unavailable",
                "status_code": status_code,
                "message": _account_error_message(status_code),
                "account_count": 0,
                "accounts": [],
            },
        }

    return {
        "raw_account_numbers": raw_account_numbers,
        "raw_accounts": raw_accounts,
        "error": None,
        "summary": summarize_accounts(raw_account_numbers, raw_accounts),
    }


def skipped_account_probe() -> dict[str, Any]:
    message = "Account probe skipped. Strategy mode is market-data only; orders are manual."
    return {
        "raw_account_numbers": [],
        "raw_accounts": [],
        "error": None,
        "summary": {
            "status": "skipped",
            "message": message,
            "account_count": 0,
            "accounts": [],
        },
    }


def _account_error_message(status_code: int) -> str:
    if status_code == 401:
        return (
            "Schwab account endpoint is unauthorized for this token/app. "
            "Market-data endpoints may still work."
        )
    return f"Schwab account endpoint failed with HTTP {status_code}."


def redacted_config(config: SchwabProbeConfig) -> dict[str, Any]:
    config_dict = asdict(config)
    config_dict["app_key"] = mask_value(config.app_key)
    config_dict["app_secret"] = "<redacted>"
    config_dict["token_path"] = compact_path(config.token_path)
    config_dict["output_dir"] = compact_path(config.output_dir)
    return config_dict


def summarize_token(token_age_seconds: float | None) -> dict[str, Any]:
    if token_age_seconds is None:
        return {"age_seconds": None, "age_days": None}
    return {
        "age_seconds": round(token_age_seconds, 1),
        "age_days": round(token_age_seconds / 86400, 2),
    }


def summarize_accounts(
    account_numbers_raw: list[dict[str, Any]],
    accounts_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    hash_by_account = {
        str(item.get("accountNumber")): str(item.get("hashValue"))
        for item in account_numbers_raw
        if item.get("accountNumber") and item.get("hashValue")
    }
    summaries = []
    for account in accounts_raw:
        securities_account = account.get("securitiesAccount", account)
        account_number = str(securities_account.get("accountNumber", ""))
        positions = securities_account.get("positions") or []
        balances = (
            securities_account.get("currentBalances")
            or securities_account.get("projectedBalances")
            or securities_account.get("initialBalances")
            or {}
        )
        summaries.append(
            {
                "account_type": securities_account.get("type"),
                "account_hash": mask_value(hash_by_account.get(account_number, "")),
                "cash_balance": first_number(
                    balances,
                    "cashBalance",
                    "cashAvailableForTrading",
                    "availableFunds",
                    "moneyMarketFund",
                ),
                "liquidation_value": first_number(
                    balances,
                    "liquidationValue",
                    "liquidationValueLong",
                    "accountValue",
                ),
                "long_market_value": first_number(balances, "longMarketValue"),
                "short_market_value": first_number(balances, "shortMarketValue"),
                "positions": summarize_positions(positions),
            }
        )
    return {
        "status": "ok",
        "account_count": len(summaries),
        "accounts": summaries,
    }


def summarize_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for position in positions:
        instrument = position.get("instrument") or {}
        symbol = instrument.get("symbol")
        if not symbol:
            continue
        summaries.append(
            {
                "symbol": symbol,
                "quantity": first_number(position, "longQuantity", "shortQuantity"),
                "market_value": first_number(position, "marketValue"),
            }
        )
    return summaries


def summarize_quotes(
    quotes_raw: dict[str, Any],
    symbols: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    return {
        symbol: extract_quote_summary(quotes_raw.get(symbol) or quotes_raw.get(symbol.upper()) or {})
        for symbol in symbols
    }


def extract_quote_summary(raw_quote: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_main_type": nested_find(raw_quote, "assetMainType"),
        "description": nested_find(raw_quote, "description"),
        "exchange": nested_find(raw_quote, "exchange"),
        "last_price": nested_find(
            raw_quote,
            "lastPrice",
            "regularMarketLastPrice",
            "mark",
            "markPrice",
        ),
        "bid_price": nested_find(raw_quote, "bidPrice"),
        "ask_price": nested_find(raw_quote, "askPrice"),
        "total_volume": nested_find(raw_quote, "totalVolume", "regularMarketTradeVolume"),
        "quote_time": normalize_epoch(nested_find(raw_quote, "quoteTime", "quoteTimeInLong")),
        "trade_time": normalize_epoch(nested_find(raw_quote, "tradeTime", "tradeTimeInLong")),
        "raw_available": bool(raw_quote),
    }


def summarize_history(history_raw: dict[str, Any]) -> dict[str, Any]:
    candles = history_raw.get("candles") or []
    candles = [candle for candle in candles if isinstance(candle, dict)]
    if not candles:
        return {
            "symbol": history_raw.get("symbol"),
            "empty": True,
            "candle_count": 0,
            "start": None,
            "end": None,
            "returned_calendar_days": None,
            "latest_session_vwap": None,
        }

    sorted_candles = sorted(candles, key=lambda candle: candle.get("datetime", 0))
    start_dt = parse_epoch(sorted_candles[0].get("datetime"))
    end_dt = parse_epoch(sorted_candles[-1].get("datetime"))
    latest_session_vwap = calculate_latest_session_vwap(sorted_candles)

    return {
        "symbol": history_raw.get("symbol"),
        "empty": bool(history_raw.get("empty", False)),
        "candle_count": len(sorted_candles),
        "start": start_dt.isoformat() if start_dt else None,
        "end": end_dt.isoformat() if end_dt else None,
        "returned_calendar_days": (end_dt.date() - start_dt.date()).days if start_dt and end_dt else None,
        "latest_session_vwap": latest_session_vwap,
    }


def calculate_latest_session_vwap(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated_candles: list[tuple[datetime, dict[str, Any]]] = []
    for candle in candles:
        dt = parse_epoch(candle.get("datetime"))
        if dt is not None:
            dated_candles.append((dt.astimezone(EASTERN), candle))
    if not dated_candles:
        return None

    latest_date = max(dt.date() for dt, _ in dated_candles)
    session_candles = [candle for dt, candle in dated_candles if dt.date() == latest_date]
    numerator = 0.0
    denominator = 0.0
    for candle in session_candles:
        volume = float(candle.get("volume") or 0)
        high = candle.get("high")
        low = candle.get("low")
        close = candle.get("close")
        if volume <= 0 or high is None or low is None or close is None:
            continue
        typical_price = (float(high) + float(low) + float(close)) / 3.0
        numerator += typical_price * volume
        denominator += volume

    if denominator <= 0:
        return {
            "date": latest_date.isoformat(),
            "vwap": None,
            "volume": 0,
            "candle_count": len(session_candles),
        }

    return {
        "date": latest_date.isoformat(),
        "vwap": round(numerator / denominator, 4),
        "volume": int(denominator),
        "candle_count": len(session_candles),
    }


def first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def nested_find(source: Any, *keys: str) -> Any:
    if isinstance(source, dict):
        for key in keys:
            if key in source:
                return source[key]
        for value in source.values():
            found = nested_find(value, *keys)
            if found is not None:
                return found
    elif isinstance(source, list):
        for value in source:
            found = nested_find(value, *keys)
            if found is not None:
                return found
    return None


def parse_epoch(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000.0
    return datetime.fromtimestamp(timestamp, tz=UTC)


def normalize_epoch(value: Any) -> str | None:
    parsed = parse_epoch(value)
    return parsed.isoformat() if parsed else None


def mask_value(value: str, *, keep_start: int = 4, keep_end: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return f"{value[:keep_start]}...{value[-keep_end:]}"


def compact_path(path: Path) -> str:
    home = Path.home().resolve()
    try:
        return str(path.resolve()).replace(str(home), "~", 1)
    except OSError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_console_summary(report: dict[str, Any], public_report_path: Path, debug_report_path: Path | None) -> str:
    lines = []
    lines.append("Schwab probe summary")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Symbols: {', '.join(report['config']['symbols'])}")

    token = report.get("token") or {}
    if token.get("age_days") is not None:
        lines.append(f"Token age: {token['age_days']} days")

    accounts = report.get("accounts") or {}
    account_status = accounts.get("status", "unknown")
    account_line = f"Accounts: {accounts.get('account_count', 0)} status={account_status}"
    if accounts.get("message"):
        account_line += f" ({accounts['message']})"
    lines.append(account_line)
    for account in accounts.get("accounts", []):
        lines.append(
            "  - "
            f"type={account.get('account_type')} "
            f"hash={account.get('account_hash')} "
            f"cash={account.get('cash_balance')} "
            f"liq={account.get('liquidation_value')} "
            f"positions={len(account.get('positions', []))}"
        )

    lines.append("Quotes:")
    for symbol, quote in (report.get("quotes") or {}).items():
        lines.append(
            "  - "
            f"{symbol}: last={quote.get('last_price')} "
            f"bid={quote.get('bid_price')} ask={quote.get('ask_price')} "
            f"volume={quote.get('total_volume')}"
        )

    lines.append("5-minute history:")
    for symbol, history in (report.get("five_minute_history") or {}).items():
        vwap = history.get("latest_session_vwap") or {}
        lines.append(
            "  - "
            f"{symbol}: candles={history.get('candle_count')} "
            f"start={history.get('start')} end={history.get('end')} "
            f"days={history.get('returned_calendar_days')} "
            f"latest_vwap={vwap.get('vwap')}"
        )

    lines.append(f"Public report: {public_report_path}")
    if debug_report_path:
        lines.append(f"Private debug report: {debug_report_path}")
    return "\n".join(lines)
