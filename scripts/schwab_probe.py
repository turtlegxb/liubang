#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.config import load_config
from liubang.probe import format_console_summary, run_probe


def main() -> int:
    try:
        config = load_config()
        artifacts = run_probe(config)
    except httpx.HTTPStatusError as exc:
        print(format_http_error(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"schwab_probe failed: {exc}", file=sys.stderr)
        return 1

    print(
        format_console_summary(
            artifacts.report,
            artifacts.public_report_path,
            artifacts.debug_report_path,
        )
    )
    return 0


def format_http_error(exc: httpx.HTTPStatusError) -> str:
    status_code = exc.response.status_code
    if status_code == 401:
        return (
            "schwab_probe failed: Schwab returned 401 Unauthorized.\n"
            "Check that the token file was generated for the same SCHWAB_APP_KEY, "
            "the app is Ready for Use, the account has Trader API access, and the "
            "token has not been revoked or expired."
        )
    if status_code == 429:
        return (
            "schwab_probe failed: Schwab rate limit returned 429 after retries. "
            "Increase --request-sleep-seconds or try again later."
        )
    return f"schwab_probe failed: Schwab HTTP {status_code}: {exc.request.url}"


if __name__ == "__main__":
    raise SystemExit(main())
