#!/usr/bin/env python3
"""Run a package MCP server: python scripts/run_server.py dump|load|com|files|review|journal|debug|bsl|platform"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "platform": ROOT / "packages" / "mcp-1c-platform" / "server.py",
    "dump": ROOT / "packages" / "mcp-1c-dump" / "server.py",
    "load": ROOT / "packages" / "mcp-1c-load" / "server.py",
    "com": ROOT / "packages" / "mcp-1c-com" / "server.py",
    "files": ROOT / "packages" / "mcp-1c-files" / "server.py",
    "review": ROOT / "packages" / "mcp-1c-review" / "server.py",
    "journal": ROOT / "packages" / "mcp-1c-journal" / "server.py",
    "debug": ROOT / "packages" / "mcp-1c-debug" / "server.py",
    "bsl": ROOT / "packages" / "mcp-1c-bsl" / "server.py",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in PACKAGES:
        print("Usage: run_server.py <" + "|".join(PACKAGES) + ">", file=sys.stderr)
        return 2
    path = PACKAGES[sys.argv[1]]
    sys.path.insert(0, str(ROOT / "packages" / "shared"))
    runpy.run_path(str(path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
