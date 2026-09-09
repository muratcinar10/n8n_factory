#!/usr/bin/env python3
"""LaunchAgent entrypoint. Lives in ~/Library/Application Support after install."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

SUPPORT = Path.home() / "Library/Application Support/ai-product-factory-v4"
TOKEN_FILE = SUPPORT / "bridge-token"
WORKSPACES = Path(
    "/Users/muratcinar/Downloads/n8n-self-hosted-ai/ai-product-factory-v4/factory-infrastructure/workspaces"
)


def main() -> None:
    token = TOKEN_FILE.read_text(encoding="utf-8").splitlines()[0].strip()
    if not token:
        raise SystemExit("cursor bridge token is missing")
    os.environ["FACTORY_BRIDGE_TOKEN"] = token
    os.environ["FACTORY_CURSOR_BRIDGE_TOKEN"] = token
    os.environ.setdefault("FACTORY_CURSOR_BRIDGE_HOST", "0.0.0.0")
    os.environ.setdefault("FACTORY_CURSOR_BRIDGE_PORT", "8766")
    os.environ.setdefault("FACTORY_CURSOR_WORKSPACES", str(WORKSPACES))
    os.environ.setdefault("HOME", str(Path.home()))
    runpy.run_path(str(SUPPORT / "cursor_developer_bridge.py"), run_name="__main__")


if __name__ == "__main__":
    main()
