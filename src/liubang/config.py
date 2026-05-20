from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from liubang.cli_utils import load_env


DEFAULT_SYMBOLS = ("AAPL", "NVDA", "TSLA", "QQQ", "SPY")


@dataclass(frozen=True)
class SchwabProbeConfig:
    app_key: str
    app_secret: str
    token_path: Path
    symbols: tuple[str, ...]
    output_dir: Path
    request_sleep_seconds: float
    max_retries: int
    backoff_seconds: float
    extended_hours: bool
    include_accounts: bool
    include_debug_report: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Schwab account, quote, and 5-minute candle APIs."
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols. Default: AAPL,NVDA,TSLA,QQQ,SPY.",
    )
    parser.add_argument("--app-key", default=None, help="Schwab app key.")
    parser.add_argument("--app-secret", default=None, help="Schwab app secret.")
    parser.add_argument(
        "--token-path",
        default=None,
        help="Path to an existing Schwab token JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for probe JSON reports. Default: data/exports.",
    )
    parser.add_argument(
        "--request-sleep-seconds",
        type=float,
        default=None,
        help="Sleep between API requests. Default: 0.5.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Retries for rate limits and transient server errors. Default: 3.",
    )
    parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=None,
        help="Initial retry backoff in seconds. Default: 2.0.",
    )
    parser.add_argument(
        "--extended-hours",
        action="store_true",
        help="Include extended-hours candles in 5-minute history requests.",
    )
    parser.add_argument(
        "--include-accounts",
        action="store_true",
        help="Also probe Schwab account and position endpoints. Defaults off.",
    )
    parser.add_argument(
        "--write-debug-report",
        action="store_true",
        help="Write a private full raw API debug report. Defaults off.",
    )
    return parser


def load_config(argv: list[str] | None = None) -> SchwabProbeConfig:
    load_env()
    args = build_parser().parse_args(argv)

    app_key = _first_value(args.app_key, os.getenv("SCHWAB_APP_KEY"))
    app_secret = _first_value(args.app_secret, os.getenv("SCHWAB_APP_SECRET"))
    token_path = _first_value(args.token_path, os.getenv("SCHWAB_TOKEN_PATH"))

    missing = [
        name
        for name, value in (
            ("SCHWAB_APP_KEY", app_key),
            ("SCHWAB_APP_SECRET", app_secret),
            ("SCHWAB_TOKEN_PATH", token_path),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing required Schwab configuration: "
            + ", ".join(missing)
            + ". Provide command-line args, environment variables, .env values, or ~/.zshrc values."
        )

    output_dir = _path_value(args.output_dir, os.getenv("LIUBANG_OUTPUT_DIR"), "data/exports")
    symbols = _parse_symbols(_first_value(args.symbols, os.getenv("SCHWAB_PROBE_SYMBOLS")))

    return SchwabProbeConfig(
        app_key=str(app_key),
        app_secret=str(app_secret),
        token_path=_expand_path(str(token_path)),
        symbols=symbols,
        output_dir=output_dir,
        request_sleep_seconds=float(
            _first_value(args.request_sleep_seconds, os.getenv("SCHWAB_REQUEST_SLEEP_SECONDS"), 0.5)
        ),
        max_retries=int(_first_value(args.max_retries, os.getenv("SCHWAB_MAX_RETRIES"), 3)),
        backoff_seconds=float(_first_value(args.backoff_seconds, os.getenv("SCHWAB_BACKOFF_SECONDS"), 2.0)),
        extended_hours=args.extended_hours
        or _truthy(os.getenv("SCHWAB_INCLUDE_EXTENDED_HOURS")),
        include_accounts=args.include_accounts or _truthy(os.getenv("SCHWAB_INCLUDE_ACCOUNTS")),
        include_debug_report=args.write_debug_report,
    )


def _first_value(*values: object) -> object | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _path_value(cli_value: str | None, env_value: str | None, default: str) -> Path:
    return _expand_path(str(_first_value(cli_value, env_value, default)))


def _expand_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve()


def _parse_symbols(raw: object | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_SYMBOLS
    symbols = tuple(
        symbol.strip().upper()
        for symbol in str(raw).replace(";", ",").split(",")
        if symbol.strip()
    )
    return symbols or DEFAULT_SYMBOLS


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
