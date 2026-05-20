from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from liubang.schwab_adapter import SchwabAdapter


DEFAULT_OPTIONS_CACHE_DIR = Path("data/cache/options")


class SchwabOptionChainCache:
    def __init__(
        self,
        *,
        adapter: SchwabAdapter,
        cache_dir: Path = DEFAULT_OPTIONS_CACHE_DIR,
        strike_count: int = 10,
        max_dte: int = 45,
    ) -> None:
        self._adapter = adapter
        self._cache_dir = cache_dir
        self._strike_count = strike_count
        self._max_dte = max_dte

    def get_chain(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        cache_path = self.cache_path(symbol)
        if cache_path.exists() and not refresh:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        to_date = date.today() + timedelta(days=self._max_dte)
        payload = self._adapter.get_option_chain(
            symbol,
            strike_count=self._strike_count,
            to_date=to_date,
        )
        wrapper = {
            "symbol": symbol.upper(),
            "source": "schwab",
            "fetched_at": datetime.now(UTC).isoformat(),
            "strike_count": self._strike_count,
            "max_dte": self._max_dte,
            "payload": payload,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(wrapper, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return wrapper

    def cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"schwab_options_{symbol.upper()}_{self._strike_count}_{self._max_dte}.json"


def summarize_option_chain(chain_wrapper: dict[str, Any]) -> dict[str, Any]:
    payload = chain_wrapper.get("payload", chain_wrapper)
    symbol = str(chain_wrapper.get("symbol") or payload.get("symbol") or "").upper()
    call_rows = collect_option_rows(payload.get("callExpDateMap") or {}, "call")
    put_rows = collect_option_rows(payload.get("putExpDateMap") or {}, "put")
    call_volume = sum(row["volume"] for row in call_rows)
    put_volume = sum(row["volume"] for row in put_rows)
    call_oi = sum(row["open_interest"] for row in call_rows)
    put_oi = sum(row["open_interest"] for row in put_rows)
    rows = call_rows + put_rows
    liquid_rows = [row for row in rows if row["bid"] > 0 and row["ask"] > 0]
    avg_spread_pct = (
        sum(row["spread_pct"] for row in liquid_rows) / len(liquid_rows)
        if liquid_rows
        else None
    )
    return {
        "symbol": symbol,
        "source": chain_wrapper.get("source", "schwab"),
        "fetched_at": chain_wrapper.get("fetched_at"),
        "contracts": len(rows),
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_volume_ratio": round(put_volume / call_volume, 3) if call_volume else None,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_oi_ratio": round(put_oi / call_oi, 3) if call_oi else None,
        "avg_spread_pct": round(avg_spread_pct, 3) if avg_spread_pct is not None else None,
        "nearest_expirations": sorted({row["expiration"] for row in rows})[:3],
    }


def collect_option_rows(expiration_map: dict[str, Any], side: str) -> list[dict[str, Any]]:
    rows = []
    for expiration_key, strikes in expiration_map.items():
        if not isinstance(strikes, dict):
            continue
        expiration = str(expiration_key).split(":", 1)[0]
        for strike, contracts in strikes.items():
            if not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                bid = number(contract.get("bid"))
                ask = number(contract.get("ask"))
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
                rows.append(
                    {
                        "symbol": contract.get("symbol"),
                        "side": side,
                        "put_call": contract.get("putCall"),
                        "expiration": expiration,
                        "expiration_date": contract.get("expirationDate"),
                        "days_to_expiration": int(number(contract.get("daysToExpiration"))),
                        "strike": float(strike),
                        "bid": bid,
                        "ask": ask,
                        "last": number(contract.get("last")),
                        "mark": number(contract.get("mark")),
                        "delta": number(contract.get("delta")),
                        "gamma": number(contract.get("gamma")),
                        "theta": number(contract.get("theta")),
                        "vega": number(contract.get("vega")),
                        "volatility": number(contract.get("volatility")),
                        "multiplier": number(contract.get("multiplier")),
                        "spread_pct": ((ask - bid) / mid * 100.0) if mid else 0.0,
                        "volume": int(number(contract.get("totalVolume"))),
                        "open_interest": int(number(contract.get("openInterest"))),
                    }
                )
    return rows


def number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
