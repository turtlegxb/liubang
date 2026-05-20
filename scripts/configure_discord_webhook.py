#!/usr/bin/env python3
from __future__ import annotations

import getpass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
KEY = "DISCORD_WEBHOOK_URL"


def main() -> int:
    webhook_url = getpass.getpass("Discord webhook URL: ").strip()
    if not webhook_url:
        print("No webhook URL entered.")
        return 1
    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        print("Input does not look like a Discord webhook URL.")
        return 1

    lines = read_env_lines(ENV_PATH)
    updated = upsert_env_value(lines, KEY, webhook_url)
    ENV_PATH.write_text("".join(updated), encoding="utf-8")
    print(f"Updated {ENV_PATH} with {KEY}.")
    return 0


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def upsert_env_value(lines: list[str], key: str, value: str) -> list[str]:
    output = []
    found = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
            output.append(f"{key}={quote_env_value(value)}\n")
            found = True
        else:
            output.append(line)
    if not found:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append(f"{key}={quote_env_value(value)}\n")
    return output


def quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


if __name__ == "__main__":
    raise SystemExit(main())
