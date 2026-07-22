"""
BSL Language Server MCP — launcher / status.

Official BSL LS is a separate distribution. This package documents how to wire it
and exposes status tools. Prefer the upstream BSL LS MCP binary when installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-bsl")


@mcp.tool()
def bsl_status() -> str:
    jar = env("BSL_LS_JAR") or env("BSL_LS_PATH")
    cmd = env("BSL_LS_COMMAND")
    java = env("JAVA_BIN", "java")
    return json_result(
        {
            "ok": bool((jar and Path(jar).is_file()) or (cmd and shutil.which(cmd))),
            "bslLsJar": jar,
            "bslLsCommand": cmd,
            "java": java,
            "hint": "Install 1c-syntax/bsl-language-server and set BSL_LS_JAR or BSL_LS_COMMAND. See docs/GUIDE.md.",
        }
    )


@mcp.tool()
def bsl_launch_help() -> str:
    return json_result(
        {
            "stdio_example": {
                "command": "java",
                "args": ["-jar", "<BSL_LS_JAR>", "--mcp"],
                "env": {"CONFIG_DUMP_DIR": "<path to src/cf>"},
            },
            "note": "This MCP package is a thin companion; use upstream BSL LS MCP entrypoint for diagnostics.",
        }
    )


def main() -> None:
    # If BSL_LS_COMMAND set and MCP_TRANSPORT=stdio, optionally exec it
    if os.environ.get("MCP_TRANSPORT", "stdio") == "stdio" and env("BSL_LS_COMMAND"):
        cmd = env("BSL_LS_COMMAND")
        if cmd and shutil.which(cmd):
            os.execvp(cmd, [cmd, *os.environ.get("BSL_LS_ARGS", "").split()])
    run_mcp(mcp, default_port=18768)


if __name__ == "__main__":
    main()
