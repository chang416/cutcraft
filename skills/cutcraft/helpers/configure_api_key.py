#!/usr/bin/env python3
"""Interactively save one or more ElevenLabs keys without echoing them."""

from __future__ import annotations

import getpass
import os
from pathlib import Path


def main() -> int:
    print("Create a restricted speech-to-text key at:")
    print("https://elevenlabs.io/docs/overview/administration/workspaces/api-keys")
    raw = getpass.getpass("Paste one key, or several comma-separated keys (input hidden): ").strip()
    keys = [item.strip() for item in raw.split(",") if item.strip()]
    if not keys:
        raise SystemExit("No key received; nothing was written.")
    if any("\n" in key or "\r" in key or "=" in key for key in keys):
        raise SystemExit("Invalid key format; nothing was written.")
    config_dir = Path.home() / ".config" / "cutcraft"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config_dir, 0o700)
    config_path = config_dir / ".env"
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"ELEVENLABS_API_KEYS={','.join(keys)}\n")
    os.chmod(config_path, 0o600)
    print(f"Saved {len(keys)} key(s) to {config_path}; values were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
