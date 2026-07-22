"""Start all MCP SSE services as background processes (Windows-friendly)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)

# 1876x avoids clashes with other local agents on 876x
SERVICES = [
    ("dump", 18761),
    ("load", 18762),
    ("com", 18763),
    ("files", 18764),
    ("review", 18765),
    ("journal", 18766),
    ("debug", 18767),
]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    load_dotenv(ROOT / ".env")
    logs = ROOT / ".tmp" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    pids: list[int] = []
    for name, port in SERVICES:
        env = os.environ.copy()
        env["MCP_TRANSPORT"] = "sse"
        env["MCP_HOST"] = env.get("MCP_HOST", "0.0.0.0")
        env["MCP_PORT"] = str(port)
        out = open(logs / f"{name}.log", "w", encoding="utf-8")
        err = open(logs / f"{name}.err", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [str(PY), str(ROOT / "scripts" / "run_server.py"), name],
            cwd=str(ROOT),
            env=env,
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            if sys.platform == "win32"
            else 0,
        )
        pids.append(proc.pid)
        print(f"started {name} pid={proc.pid} port={port}")
    (logs / "pids.txt").write_text("\n".join(str(p) for p in pids), encoding="utf-8")
    time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
