from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# packages/shared on path
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-platform")

_JAR = Path(__file__).resolve().parent / "runtime" / "1C_mcp_bsl.jar"
# also check monorepo dist
if not _JAR.is_file():
    _JAR = _ROOT.parent / "dist" / "1C_mcp_bsl.jar"


def _java_bin() -> str:
    return env("JAVA_BIN", "java") or "java"


def _platform_path() -> str:
    p = env("ONEC_PLATFORM_PATH")
    if not p:
        raise ValueError("Set ONEC_PLATFORM_PATH to 1cv8 version directory (e.g. .../8.3.27.1719)")
    return p


@mcp.tool()
def platform_status() -> str:
    """Health: jar, java, platform path."""
    return json_result(
        {
            "ok": _JAR.is_file() and Path(_platform_path()).is_dir(),
            "jar": str(_JAR),
            "jarExists": _JAR.is_file(),
            "platformPath": env("ONEC_PLATFORM_PATH"),
            "platformExists": Path(env("ONEC_PLATFORM_PATH", "") or ".").is_dir(),
            "java": _java_bin(),
            "note": "Tools search/info/getMember proxy via legacy JAR (SSE/stdio). Prefer MCP_TRANSPORT=stdio with jar direct, or use run_jar_sse.",
        }
    )


@mcp.tool()
def run_jar_help() -> str:
    """Show legacy JAR CLI help (validates Java + jar)."""
    if not _JAR.is_file():
        return json_result({"ok": False, "error": f"JAR not found: {_JAR}"})
    proc = subprocess.run(
        [_java_bin(), "-Dfile.encoding=UTF-8", "-jar", str(_JAR), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return json_result(
        {
            "ok": proc.returncode == 0,
            "exitCode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }
    )


@mcp.tool()
def search(query: str, type: str | None = None, limit: int = 10) -> str:
    """
    Search platform API via legacy JAR invoked per-call (stdio one-shot is not supported by JAR).
    For full tools use Cursor stdio entrypoint scripts/run_platform_jar.py or HTTP SSE jar.
    This tool returns setup instructions if proxy unavailable.
    """
    return json_result(
        {
            "ok": False,
            "error": "Use platform JAR in stdio/SSE mode for search/info/getMember.",
            "hint": "Set MCP entrypoint to: java -Dfile.encoding=UTF-8 -jar packages/mcp-1c-platform/runtime/1C_mcp_bsl.jar --platform-path <ONEC_PLATFORM_PATH>",
            "query": query,
            "type": type,
            "limit": limit,
            "status": platform_status(),
        }
    )


def main() -> None:
    # Default: if stdio and JAR exists, prefer documenting jar-direct.
    # Python wrapper still useful for status and HTTP health checks.
    run_mcp(mcp, default_port=18760)


if __name__ == "__main__":
    main()
