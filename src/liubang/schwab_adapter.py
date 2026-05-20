from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from schwab.auth import client_from_token_file
from schwab.client import Client


@dataclass(frozen=True)
class RetrySettings:
    request_sleep_seconds: float = 0.5
    max_retries: int = 3
    backoff_seconds: float = 2.0


class SchwabAdapter:
    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        token_path: Path,
        retry_settings: RetrySettings,
    ) -> None:
        self._client = client_from_token_file(
            str(token_path),
            app_key,
            app_secret,
            enforce_enums=True,
        )
        self._retry = retry_settings

    def token_age_seconds(self) -> float | None:
        token_age = getattr(self._client, "token_age", None)
        if not callable(token_age):
            return None
        try:
            return float(token_age())
        except Exception:
            return None

    def get_account_numbers(self) -> list[dict[str, Any]]:
        response = self._request(lambda: self._client.get_account_numbers(), "account_numbers")
        data = response.json()
        return data if isinstance(data, list) else []

    def get_accounts(self) -> list[dict[str, Any]]:
        response = self._request(
            lambda: self._client.get_accounts(fields=[Client.Account.Fields.POSITIONS]),
            "accounts",
        )
        data = response.json()
        return data if isinstance(data, list) else []

    def get_quotes(self, symbols: tuple[str, ...]) -> dict[str, Any]:
        response = self._request(lambda: self._client.get_quotes(symbols), "quotes")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def get_five_minute_history(
        self,
        symbol: str,
        *,
        extended_hours: bool,
    ) -> dict[str, Any]:
        response = self._request(
            lambda: self._client.get_price_history_every_five_minutes(
                symbol,
                need_extended_hours_data=extended_hours,
            ),
            f"five_minute_history:{symbol}",
        )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def get_option_chain(
        self,
        symbol: str,
        *,
        strike_count: int = 10,
        to_date: object | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            lambda: self._client.get_option_chain(
                symbol,
                contract_type=Client.Options.ContractType.ALL,
                strike_count=strike_count,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
                strike_range=Client.Options.StrikeRange.NEAR_THE_MONEY,
                to_date=to_date,
            ),
            f"option_chain:{symbol}",
        )
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _request(self, call: Callable[[], httpx.Response], label: str) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._retry.max_retries + 1):
            response = call()
            last_response = response
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                self._sleep_between_requests()
                return response

            if attempt >= self._retry.max_retries:
                break

            retry_after = _retry_after_seconds(response)
            sleep_seconds = retry_after or self._retry.backoff_seconds * (2**attempt)
            time.sleep(sleep_seconds)

        if last_response is None:
            raise RuntimeError(f"Schwab request failed before response: {label}")
        last_response.raise_for_status()
        return last_response

    def _sleep_between_requests(self) -> None:
        if self._retry.request_sleep_seconds > 0:
            time.sleep(self._retry.request_sleep_seconds)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = response.headers.get("retry-after")
    if not raw_value:
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return None
