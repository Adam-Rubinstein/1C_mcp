"""Close / reopen 1C Designer or Enterprise for a file infobase."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import auth_for_ib_path, env, require_env

T = TypeVar("T")


@dataclass
class IbProcess:
    pid: int
    kind: str  # designer | enterprise | unknown
    cmdline: str


def _norm(path: str | Path) -> str:
    return str(Path(path).resolve()).lower().replace("/", "\\")


def _path_token_in_cmdline(cmd_n: str, needle: str) -> bool:
    """True if needle appears as a full path token (InfoBase must not match InfoBase2)."""
    if not needle:
        return False
    start = 0
    while True:
        i = cmd_n.find(needle, start)
        if i < 0:
            return False
        end = i + len(needle)
        if end >= len(cmd_n) or cmd_n[end] in '\\/" \t':
            return True
        start = i + 1


def _ibases_v8i_paths() -> dict[str, str]:
    """Map IB display name -> File= path from ibases.v8i."""
    appdata = os.environ.get("APPDATA", "")
    v8i = Path(appdata) / "1C" / "1CEStart" / "ibases.v8i"
    if not v8i.is_file():
        return {}
    # Default ANSI on RU Windows starters; utf-8 fallback
    try:
        text = v8i.read_text(encoding="utf-8-sig")
    except Exception:
        text = v8i.read_text(encoding="cp1251", errors="replace")
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


def ib_name_for_path(ib_path: str | Path) -> str | None:
    """Reverse lookup: file IB path -> list title in ibases.v8i (e.g. ERP КОПИЯ)."""
    needle = _norm(ib_path)
    for name, file_path in _ibases_v8i_paths().items():
        if _norm(file_path) == needle:
            return name
    return None


def _cmdline_matches_ib(cmdline: str, ib_path: str | Path) -> bool:
    """
    Match process to IB strictly.

    - Full /F path equals ib_path (path-token boundary)
    - /IBName maps via ibases.v8i to the same path

    Do NOT match by parent folder or bare "InfoBase" substring — that cross-hits
    InfoBase / InfoBase2 / InfoBase3 under the same Documents folder.
    """
    needle = _norm(ib_path)
    cmd = cmdline or ""
    cmd_n = _norm(cmd.replace('"', ""))
    if _path_token_in_cmdline(cmd_n, needle):
        return True
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


def storage_cli_args() -> list[str]:
    """Optional /ConfigurationRepository* args from env (never invent path)."""
    path = (env("ONEC_STORAGE_PATH") or "").strip()
    if not path:
        return []
    args = ["/ConfigurationRepositoryF", path]
    user = (env("ONEC_STORAGE_USER") or "").strip()
    password = env("ONEC_STORAGE_PASSWORD")
    if password is None:
        password = ""
    if user:
        args.extend(["/ConfigurationRepositoryN", user])
    args.extend(["/ConfigurationRepositoryP", password])
    return args


def start_ib_session(
    ib_path: str | Path,
    mode: str = "designer",
    *,
    attach_storage: bool | None = None,
    like_starter: bool = True,
) -> dict[str, Any]:
    """
    Start Designer/Enterprise for an IB.

    Interactive reopen (like_starter=True): launch like 1C starter via
    /IBName"<title from ibases.v8i>" + IB user — restores configuration
    repository binding already stored on the IB. Prefer list argv (Unicode-safe);
    do not use a bare /F path for interactive WORK reopen (drops storage).

    Optional ONEC_STORAGE_* adds /ConfigurationRepository* when attach_storage
    is True or None and path is set.
    """
    onec_bin = require_env("ONEC_BIN")
    user, password = auth_for_ib_path(ib_path)
    mode_arg = "DESIGNER" if mode == "designer" else "ENTERPRISE"
    ib_name = ib_name_for_path(ib_path) if like_starter else None
    argv = [onec_bin, mode_arg]
    launch = "F"
    if like_starter and ib_name:
        # One argv token keeps Cyrillic IB titles intact under CreateProcessW.
        argv.append(f'/IBName"{ib_name}"')
        argv.append("/AppAutoCheckMode")
        launch = "IBName"
    else:
        argv.extend(["/F", str(ib_path)])
        if not like_starter:
            argv.append("/DisableStartupDialogs")
    if user:
        argv.extend(["/N", user])
    argv.extend(["/P", password])
    storage_args: list[str] = []
    storage_path_set = bool((env("ONEC_STORAGE_PATH") or "").strip())
    do_attach = (
        True
        if attach_storage is True
        else (False if attach_storage is False else storage_path_set)
    )
    if do_attach and mode == "designer":
        storage_args = storage_cli_args()
        argv.extend(storage_args)
    proc = subprocess.Popen(argv, cwd=str(Path(ib_path).parent))
    return {
        "pid": proc.pid,
        "mode": mode_arg,
        "ib": str(ib_path),
        "ibName": ib_name,
        "user": user,
        "likeStarter": like_starter and bool(ib_name),
        "launch": launch,
        "storageAttached": bool(storage_args),
    }


def with_managed_session(
    ib_path: str | Path,
    fn: Callable[[], T],
    *,
    force_close: bool = False,
    reopen: bool = False,
    restart_even_on_fail: bool = True,
    attach_storage: bool | None = None,
) -> tuple[T | None, dict[str, Any]]:
    """
    Close sessions on ib, run fn, optionally reopen what was open.

    Reopen uses /IBName + WORK user (starter-like) so IB-bound configuration
    repository comes back. Optional ONEC_STORAGE_* for explicit CLI attach.
    Never invent a Designer session if none was open (do not pop DEV for the user).
    """
    meta: dict[str, Any] = {"ib": str(ib_path), "reopen": reopen}
    before = find_ib_processes(ib_path)
    modes = sorted({p.kind for p in before if p.kind in ("designer", "enterprise")})
    meta["hadModes"] = modes
    meta["closed"] = close_ib_sessions(ib_path, force=force_close)
    error: Exception | None = None
    result: T | None = None
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        error = exc
        meta["error"] = str(exc)
    if reopen and modes and (error is None or restart_even_on_fail):
        started = []
        for mode in modes:
            started.append(
                start_ib_session(
                    ib_path,
                    mode=mode,
                    attach_storage=attach_storage,
                    like_starter=True,
                )
            )
        meta["started"] = started
        used_ibname = any(s.get("launch") == "IBName" for s in started)
        if used_ibname:
            meta["note"] = (
                "Reopened like 1C starter (/IBName + IB user). "
                "Configuration repository should restore from IB binding."
            )
        else:
            meta["warning"] = (
                "IB title not found in ibases.v8i; fell back to /F. "
                "Storage may not reconnect — open IB from the 1C list or set ONEC_STORAGE_*."
            )
            meta["note"] = "Reopened with /F + IB user (no IBName match)."
        if any(s.get("storageAttached") for s in started):
            meta["note"] = (
                "Reopened with /IBName (or /F) + /ConfigurationRepository* from ONEC_STORAGE_*."
            )
    else:
        meta["started"] = []
        if not reopen:
            meta["userAction"] = (
                f"Конфигуратор на {ib_path} закрыт для batch dump/load. "
                "Откройте нужную ИБ сами (как из списка баз)."
            )
    if error is not None:
        raise error
    return result, meta
