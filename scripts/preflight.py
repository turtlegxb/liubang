#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from liubang.cli_utils import load_env


REQUIRED_ENV = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_TOKEN_PATH")
REQUIRED_MODULES = ("schwab", "dotenv", "yfinance", "httpx", "lxml")
REQUIRED_PATHS = ("config/core_universe.json", "requirements.txt", "scripts/run_workflow.py")


def main() -> int:
    load_env()
    checks = []
    checks.extend(check_modules())
    checks.extend(check_env())
    checks.extend(check_paths())
    checks.extend(check_output_dirs())
    failed = [check for check in checks if check["status"] == "fail"]
    print("Preflight summary")
    print(f"Generated: {datetime.now(UTC).isoformat()}")
    for check in checks:
        detail = f" - {check['detail']}" if check.get("detail") else ""
        print(f"  {check['status'].upper()} {check['name']}{detail}")
    return 1 if failed else 0


def check_modules() -> list[dict]:
    checks = []
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
            checks.append({"name": f"module:{module}", "status": "ok"})
        except Exception as exc:
            checks.append({"name": f"module:{module}", "status": "fail", "detail": str(exc)})
    return checks


def check_env() -> list[dict]:
    checks = []
    for name in REQUIRED_ENV:
        value = os.getenv(name)
        checks.append(
            {
                "name": f"env:{name}",
                "status": "ok" if value else "fail",
                "detail": "set" if value else "missing",
            }
        )
    token_path = os.getenv("SCHWAB_TOKEN_PATH")
    if token_path:
        path = Path(token_path).expanduser()
        checks.append(
            {
                "name": "file:SCHWAB_TOKEN_PATH",
                "status": "ok" if path.exists() else "fail",
                "detail": str(path),
            }
        )
    discord = os.getenv("DISCORD_WEBHOOK_URL")
    checks.append(
        {
            "name": "env:DISCORD_WEBHOOK_URL",
            "status": "ok" if discord else "warn",
            "detail": "set" if discord else "missing optional webhook",
        }
    )
    return checks


def check_paths() -> list[dict]:
    checks = []
    for raw_path in REQUIRED_PATHS:
        path = PROJECT_ROOT / raw_path
        checks.append(
            {
                "name": f"path:{raw_path}",
                "status": "ok" if path.exists() else "fail",
                "detail": str(path),
            }
        )
    return checks


def check_output_dirs() -> list[dict]:
    checks = []
    for raw_path in ("data/cache", "data/exports", "logs"):
        path = PROJECT_ROOT / raw_path
        try:
            path.mkdir(parents=True, exist_ok=True)
            status = "ok"
            detail = str(path)
        except Exception as exc:
            status = "fail"
            detail = str(exc)
        checks.append({"name": f"dir:{raw_path}", "status": status, "detail": detail})
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
