"""Close / reopen 1C Designer or Enterprise for a file infobase."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import env, require_env

T = TypeVar("T")


@dataclass
class IbProcess:
    pid: int
    kind: str  # designer | enterprise | unknown
    cmdline: str


def _norm(path: str | Path) -> str:
    return str(Path(path).resolve()).lower().replace("/", "\\")


def _ibases_v8i_paths() -> dict[str, str]:
    """Map IB display name -> File= path from ibases.v8i."""
    appdata = os.environ.get("APPDATA", "")
    v8i = Path(appdata) / "1C" / "1CEStart" / "ibases.v8i"
    if not v8i.is_file():
        return {}
    text = v8i.read_text(encoding="utf-8", errors="replace")
    mapping: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current and line.lower().startswith("connect="):
            m = re.search(r'File="([^"]+)"', line, flags=re.IGNORECASE)
            if m:
                mapping[current] = m.group(1)
    return mapping


def _cmdline_matches_ib(cmdline: str, ib_path: str | Path) -> bool:
    needle = _norm(ib_path)
    cmd = cmdline or ""
    cmd_n = _norm(cmd.replace('"', ""))
    if needle in cmd_n:
        return True
    # /F path variants
    if Path(ib_path).name.lower() in cmd.lower() and "infobase" in cmd.lower():
        # weak match — also check full parent
        if _norm(Path(ib_path).parent) in cmd_n:
            return True
    # /IBName"Title" — exact title only (prefix names like "ERP КОПИЯ" vs "ERP КОПИЯ запасная")
    m = re.search(r'/IBName"?([^"/]+)"?', cmd, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        mapping = _ibases_v8i_paths()
        file_path = mapping.get(name)
        if file_path and _norm(file_path) == needle:
            return True
    return False


def _iter_onec_processes() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        import win32com.client  # type: ignore

        wmi = win32com.client.GetObject("winmgmts:")
        for proc in wmi.InstancesOf("Win32_Process"):
            name = (proc.Name or "").lower()
            if name not in ("1cv8.exe", "1cv8c.exe", "1cv8s.exe"):
                continue
            out.append((int(proc.ProcessId), proc.CommandLine or ""))
        return out
    except Exception:
        pass
    try:
        ps = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='1cv8.exe' OR Name='1cv8c.exe'\" "
                "| ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in ps.splitlines():
            if "|" not in line:
                continue
            pid_s, cmd = line.split("|", 1)
            out.append((int(pid_s.strip()), cmd.strip()))
    except Exception:
        pass
    return out


def find_ib_processes(ib_path: str | Path) -> list[IbProcess]:
    found: list[IbProcess] = []
    for pid, cmd in _iter_onec_processes():
        if not _cmdline_matches_ib(cmd, ib_path):
            continue
        low = cmd.lower()
        if "designer" in low:
            kind = "designer"
        elif "enterprise" in low:
            kind = "enterprise"
        else:
            kind = "unknown"
        found.append(IbProcess(pid=pid, kind=kind, cmdline=cmd))
    return found


def close_ib_sessions(ib_path: str | Path, *, force: bool = False, timeout_sec: float = 30.0) -> list[dict[str, Any]]:
    procs = find_ib_processes(ib_path)
    report: list[dict[str, Any]] = []
    for p in procs:
        try:
            args = ["taskkill", "/PID", str(p.pid)]
            if force:
                args.append("/F")
            subprocess.run(args, capture_output=True, check=False)
            report.append({"pid": p.pid, "kind": p.kind, "closed": True})
        except Exception as exc:  # noqa: BLE001
            report.append({"pid": p.pid, "kind": p.kind, "closed": False, "error": str(exc)})
    deadline = time.time() + timeout_sec
    while time.time() < deadline and find_ib_processes(ib_path):
        time.sleep(0.5)
    still = find_ib_processes(ib_path)
    if still and force:
        for p in still:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True, check=False)
    return report


def start_ib_session(ib_path: str | Path, mode: str = "designer") -> dict[str, Any]:
    onec_bin = require_env("ONEC_BIN")
    user = env("ONEC_USER", "") or ""
    password = env("ONEC_PASSWORD", "") or ""
    mode_arg = "DESIGNER" if mode == "designer" else "ENTERPRISE"
    argv = [onec_bin, mode_arg, "/F", str(ib_path), "/DisableStartupDialogs"]
    if user:
        argv.extend(["/N", user])
    argv.extend(["/P", password])
    proc = subprocess.Popen(argv, cwd=str(Path(ib_path).parent))
    return {"pid": proc.pid, "mode": mode_arg, "ib": str(ib_path)}


def with_managed_session(
    ib_path: str | Path,
    fn: Callable[[], T],
    *,
    force_close: bool = False,
    restart_even_on_fail: bool = True,
) -> tuple[T | None, dict[str, Any]]:
    """Close sessions on ib, run fn, restart designer/enterprise that were open."""
    meta: dict[str, Any] = {"ib": str(ib_path)}
    before = find_ib_processes(ib_path)
    modes = sorted({p.kind for p in before if p.kind in ("designer", "enterprise")})
    if not modes:
        modes = ["designer"]
    meta["closed"] = close_ib_sessions(ib_path, force=force_close)
    meta["hadModes"] = modes
    error: Exception | None = None
    result: T | None = None
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        error = exc
        meta["error"] = str(exc)
    if error is None or restart_even_on_fail:
        started = []
        for mode in modes:
            if mode == "unknown":
                continue
            started.append(start_ib_session(ib_path, mode=mode))
        meta["started"] = started
    if error is not None:
        raise error
    return result, meta
