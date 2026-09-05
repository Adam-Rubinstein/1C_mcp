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


def _strip_cmd_quotes(cmd: str) -> str:
    return (
        (cmd or "")
        .replace('"', "")
        .replace("'", "")
        .replace("«", "")
        .replace("»", "")
        .replace("\u00ab", "")
        .replace("\u00bb", "")
    )


def _ibname_from_cmdline(cmd_unquoted: str) -> str | None:
    """Parse /IBName Title or /IBName\"Title\" (single argv token from 1C starter)."""
    m = re.search(r'/IBName\s*"([^"]+)"', cmd_unquoted, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"/IBName\s+(.+?)(?=\s+/|$)", cmd_unquoted, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
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
    # Parse /IBName before _strip_cmd_quotes — strip removes inner quotes and
    # turns /IBName"ERP КОПИЯ" into /IBNameERP КОПИЯ (unparseable).
    name = _ibname_from_cmdline(cmd)
    if name:
        mapping = _ibases_v8i_paths()
        file_path = mapping.get(name)
        if file_path and _norm(file_path) == needle:
            return True
    cmd_unquoted = _strip_cmd_quotes(cmd)
    cmd_n = _norm(cmd_unquoted)
    if _path_token_in_cmdline(cmd_n, needle):
        return True
    name = _ibname_from_cmdline(cmd_unquoted)
    if name:
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
        if "designer" in low or "конфигуратор" in low:
            kind = "designer"
        elif "enterprise" in low or "предприятие" in low:
            kind = "enterprise"
        else:
            kind = "unknown"
        found.append(IbProcess(pid=pid, kind=kind, cmdline=cmd))
    return found


def _iter_named_processes(names: tuple[str, ...]) -> list[tuple[int, str]]:
    want = {n.lower() for n in names}
    out: list[tuple[int, str]] = []
    try:
        import win32com.client  # type: ignore

        wmi = win32com.client.GetObject("winmgmts:")
        for proc in wmi.InstancesOf("Win32_Process"):
            name = (proc.Name or "").lower()
            if name not in want:
                continue
            out.append((int(proc.ProcessId), proc.CommandLine or ""))
        return out
    except Exception:
        pass
    try:
        filt = " OR ".join(f"Name='{n}'" for n in names)
        ps = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-CimInstance Win32_Process -Filter \"{filt}\" "
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


def kill_dbgs_orphans(owner_pids: list[int]) -> list[dict[str, Any]]:
    """Kill dbgs.exe whose --ownerPID is a Designer we just closed. Never 1cv8.exe by image name."""
    report: list[dict[str, Any]] = []
    if not owner_pids:
        return report
    owners = {int(p) for p in owner_pids if int(p) > 0}
    for pid, cmd in _iter_named_processes(("dbgs.exe",)):
        m = re.search(r"--ownerPID[=:\s]+(\d+)", cmd or "", flags=re.IGNORECASE)
        if not m:
            continue
        owner = int(m.group(1))
        if owner not in owners:
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
            report.append({"pid": pid, "kind": "dbgs", "ownerPID": owner, "closed": True})
        except Exception as exc:  # noqa: BLE001
            report.append({"pid": pid, "kind": "dbgs", "ownerPID": owner, "closed": False, "error": str(exc)})
    return report


def clear_stale_ib_lock_files(ib_path: str | Path) -> list[str]:
    """Remove .cfl lock files when no IB process holds the base (after force_close)."""
    removed: list[str] = []
    if find_ib_processes(ib_path):
        return removed
    root = Path(ib_path)
    if not root.is_dir():
        return removed
    for cfl in root.glob("*.cfl"):
        try:
            cfl.unlink()
            removed.append(str(cfl.name))
        except OSError:
            pass
    return removed


def close_ib_sessions(ib_path: str | Path, *, force: bool = False, timeout_sec: float = 30.0) -> list[dict[str, Any]]:
    procs = find_ib_processes(ib_path)
    report: list[dict[str, Any]] = []
    designer_pids = [p.pid for p in procs if p.kind == "designer"]
    for p in procs:
        try:
            args = ["taskkill", "/PID", str(p.pid)]
            if force:
                args.append("/F")
            subprocess.run(args, capture_output=True, check=False)
            report.append({"pid": p.pid, "kind": p.kind, "closed": True})
        except Exception as exc:  # noqa: BLE001
            report.append({"pid": p.pid, "kind": p.kind, "closed": False, "error": str(exc)})
    if designer_pids:
        report.extend(kill_dbgs_orphans(designer_pids))
    deadline = time.time() + timeout_sec
    while time.time() < deadline and find_ib_processes(ib_path):
        time.sleep(0.5)
    still = find_ib_processes(ib_path)
    if still and force:
        still_designers = [p.pid for p in still if p.kind == "designer"]
        for p in still:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True, check=False)
        if still_designers:
            report.extend(kill_dbgs_orphans(still_designers))
    if force and not find_ib_processes(ib_path):
        cleared = clear_stale_ib_lock_files(ib_path)
        if cleared:
            report.append({"clearedCfl": cleared})
    return report


def storage_cli_args(*, extension: bool = False) -> list[str]:
    """Optional /ConfigurationRepository* args from env (never invent path).

    Storage login is independent of IB login. Empty ONEC_STORAGE_PASSWORD is
    valid — do not fall back to ONEC_PASSWORD_WORK (env() treats '' as unset).

    Prefer IB-bound storage via /IBName in run_designer for WORK — only use this
    for /F batch when list title is unavailable.
    extension=True → ONEC_STORAGE_PATH_CFE (fallback ONEC_STORAGE_PATH).
    """
    import os

    if extension:
        path = (os.environ.get("ONEC_STORAGE_PATH_CFE") or os.environ.get("ONEC_STORAGE_PATH") or "").strip()
    else:
        path = (os.environ.get("ONEC_STORAGE_PATH") or "").strip()
    if not path:
        return []
    args = ["/ConfigurationRepositoryF", path]
    user = (
        (os.environ.get("ONEC_STORAGE_USER") or "").strip()
        or (env("ONEC_USER_WORK") or "").strip()
        or (env("ONEC_USER") or "").strip()
    )
    # Never fall back to IB password — storage auth is separate; empty is valid.
    # PowerShell `$env:ONEC_STORAGE_PASSWORD=''` often unsets the key; treat missing
    # as empty, not as ONEC_PASSWORD_WORK (that caused «Ошибка аутентификации»).
    if "ONEC_STORAGE_PASSWORD" in os.environ:
        password = os.environ.get("ONEC_STORAGE_PASSWORD") or ""
    else:
        password = ""
    if user:
        args.extend(["/ConfigurationRepositoryN", user])
    # Omit -P when empty: some Designer builds treat empty /ConfigurationRepositoryP
    # as failed auth; interactive IB binding uses empty password without the flag.
    if password:
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

    Interactive reopen (like_starter=True): launch Configurator/Enterprise via
    /IBName + title (two argv) + IB user — restores configuration
    repository binding already stored on the IB. Prefer list argv (Unicode-safe);
    do not use a bare /F path for interactive WORK reopen (drops storage).
    Do NOT pass /AppAutoCheckMode — with explicit DESIGNER/ENTERPRISE it opens
    the "Запуск 1С:Предприятия" list instead of Configurator.

    Interactive /IBName reopen restores repository binding from the IB —
    never pass /ConfigurationRepository* again (re-auth while already bound
    → «Ошибка аутентификации в хранилище»). Explicit attach only for headless
    /F batch (like_starter=False).
    """
    onec_bin = require_env("ONEC_BIN")
    user, password = auth_for_ib_path(ib_path)
    mode_arg = "DESIGNER" if mode == "designer" else "ENTERPRISE"
    ib_name = ib_name_for_path(ib_path) if like_starter else None
    argv = [onec_bin, mode_arg]
    launch = "F"
    if like_starter and ib_name:
        # Two argv tokens: /IBName + title. Do NOT use /IBName"Title" as one
        # token — CreateProcessW re-quotes it to "/IBName\"Title\"" and 1C
        # fails to parse → opens «Запуск 1С:Предприятия» instead of DESIGNER.
        # No /AppAutoCheckMode for the same reason (mode list / launcher).
        argv.extend(["/IBName", ib_name])
        launch = "IBName"
    else:
        argv.extend(["/F", str(ib_path)])
        if not like_starter:
            argv.append("/DisableStartupDialogs")
    if user:
        argv.extend(["/N", user])
    argv.extend(["/P", password])
    storage_args: list[str] = []
    # /IBName already reconnects storage from IB — do not re-auth via CLI
    if like_starter and ib_name:
        do_attach = False
    else:
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


def _ib_is_work(ib_path: str | Path) -> bool:
    work = (env("ONEC_IB_WORK") or "").strip()
    if not work:
        return False
    try:
        return Path(ib_path).resolve() == Path(work).resolve()
    except OSError:
        return os.path.normcase(os.path.normpath(str(ib_path))) == os.path.normcase(
            os.path.normpath(work)
        )


def _wait_designer_ready(ib_path: str | Path, *, timeout_sec: float = 90.0) -> dict[str, Any]:
    """Poll until a Designer process for this IB is visible (or timeout)."""
    deadline = time.time() + max(5.0, float(timeout_sec))
    while time.time() < deadline:
        procs = find_ib_processes(ib_path)
        designers = [p for p in procs if p.kind == "designer"]
        if designers:
            return {
                "ok": True,
                "pids": [p.pid for p in designers],
                "waitedSec": round(timeout_sec - (deadline - time.time()), 1),
            }
        time.sleep(0.5)
    return {
        "ok": False,
        "error": "Designer not visible after reopen wait",
        "waitedSec": timeout_sec,
    }


def with_managed_session(
    ib_path: str | Path,
    fn: Callable[[], T],
    *,
    force_close: bool = False,
    reopen: bool = False,
    restart_even_on_fail: bool = False,
    attach_storage: bool | None = None,
) -> tuple[T | None, dict[str, Any]]:
    """
    Close sessions on ib, run fn, optionally reopen what was open.

    Reopen uses /IBName + WORK user (starter-like) so IB-bound configuration
    repository comes back — **without** CLI /ConfigurationRepository* re-auth.
    Explicit ONEC_STORAGE_* attach is only for headless /F batch inside fn.
    WORK: restart_even_on_fail default False (do not pop Configurator after fail).
    """
    from onec_mcp_shared.work_gates import DesignerBusy, designer_mutex

    meta: dict[str, Any] = {"ib": str(ib_path), "reopen": reopen}
    work = (env("ONEC_IB_WORK") or "").strip()
    target_guess = "dev"
    if work:
        try:
            if Path(ib_path).resolve() == Path(work).resolve():
                target_guess = "work"
        except OSError:
            if os.path.normcase(os.path.normpath(str(ib_path))) == os.path.normcase(os.path.normpath(work)):
                target_guess = "work"
    try:
        with designer_mutex(target_guess, tool="with_managed_session"):
            return _with_managed_session_body(
                ib_path,
                fn,
                force_close=force_close,
                reopen=reopen,
                restart_even_on_fail=restart_even_on_fail,
                attach_storage=attach_storage,
                meta=meta,
            )
    except DesignerBusy as exc:
        meta.update(exc.payload)
        raise


def _with_managed_session_body(
    ib_path: str | Path,
    fn: Callable[[], T],
    *,
    force_close: bool,
    reopen: bool,
    restart_even_on_fail: bool,
    attach_storage: bool | None,
    meta: dict[str, Any],
) -> tuple[T | None, dict[str, Any]]:
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
        from onec_mcp_shared.work_gates import clear_reopen_lease, write_reopen_lease

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
        # Hold mutex peers via reopen lease until Designer is observable
        # (Popen returns before IB exclusive lock — «Ожидание запуска» race).
        wait_ready_sec = 90.0
        try:
            wait_ready_sec = float(
                (env("MCP_REOPEN_READY_WAIT_SEC") or "90").strip() or "90"
            )
        except ValueError:
            wait_ready_sec = 90.0
        lease_pids = [int(s.get("pid") or 0) for s in started if s.get("pid")]
        for pid in lease_pids:
            write_reopen_lease("work" if _ib_is_work(ib_path) else "dev", pid=pid)
        ready = _wait_designer_ready(ib_path, timeout_sec=wait_ready_sec)
        meta["reopenReady"] = ready
        if ready.get("ok"):
            # Brief settle so exclusive IB lock is taken before peers dump.
            time.sleep(1.5)
            clear_reopen_lease("work" if _ib_is_work(ib_path) else "dev")
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
                "Reopened with /F + /ConfigurationRepository* from ONEC_STORAGE_* "
                "(no IBName match — explicit storage attach)."
            )
        elif used_ibname:
            meta["note"] = (
                "Reopened with /IBName + IB user; storage from IB binding "
                "(no CLI /ConfigurationRepository* re-auth)."
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
