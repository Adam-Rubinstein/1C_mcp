"""Launch legacy platform JAR (stdio or SSE). Optional Bearer via MCP_TOKEN for SSE only through proxy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "packages" / "mcp-1c-platform" / "runtime" / "1C_mcp_bsl.jar"
if not JAR.is_file():
    JAR = ROOT / "dist" / "1C_mcp_bsl.jar"


def main() -> int:
    platform = os.environ.get("ONEC_PLATFORM_PATH")
    if not platform:
        print("ONEC_PLATFORM_PATH is required", file=sys.stderr)
        return 2
    if not JAR.is_file():
        print(f"JAR not found: {JAR}", file=sys.stderr)
        return 2
    java = os.environ.get("JAVA_BIN", "java")
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    args = [java, "-Dfile.encoding=UTF-8", "-jar", str(JAR), "--platform-path", platform]
    if transport in ("sse", "http"):
        port = os.environ.get("MCP_PORT", "8760")
        args.extend(["--mode", "sse", "--port", port])
    # Note: JAR has no built-in Bearer; put nginx/Caddy or use scripts/auth_proxy.py
    os.execv(java if Path(java).is_file() else java, args)
    return 0


if __name__ == "__main__":
    # execv replaces process; fallback:
    platform = os.environ.get("ONEC_PLATFORM_PATH")
    java = os.environ.get("JAVA_BIN", "java")
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    args = [java, "-Dfile.encoding=UTF-8", "-jar", str(JAR), "--platform-path", platform or ""]
    if transport in ("sse", "http"):
        args.extend(["--mode", "sse", "--port", os.environ.get("MCP_PORT", "8760")])
    raise SystemExit(subprocess.call(args))
