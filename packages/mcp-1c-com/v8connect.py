"""COM connect for 1C 8.3.x: comtypes + IV8COMConnector3, no win32com Connect.

win32com Dispatch('V83.COMConnector').Connect fails with TYPE_E_LIBNOTREGISTERED
on 8.3.27. GetObject wrapping via ITypeComp also fails (E_NOTIMPL) — always use
comtypes.client.dynamic._Dispatch.

Always close the application object after a tool call: leftover COM sessions
block Configurator exclusive lock on a file IB.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import gc
from pathlib import Path
from typing import Any, Iterator

from onec_mcp_shared import env, is_work_target, resolve_ib, resolve_ib_auth

# Live COM apps in this process (session() + atexit if the MCP process dies).
_OPEN: list[tuple[Any, Any]] = []
_ATEXIT_DONE = False


def _patch_dynamic_dispatch() -> None:
    import comtypes.client.dynamic as dyn
    from comtypes import automation

    if getattr(dyn, "_v8_patched", False):
        return

    def dispatch(obj):  # noqa: ANN001
        if isinstance(obj, dyn._Dispatch):
            return obj
        if isinstance(obj, ctypes.POINTER(automation.IDispatch)):
            return dyn._Dispatch(obj)
        return obj

    dyn.Dispatch = dispatch
    dyn._v8_patched = True  # type: ignore[attr-defined]


def _comcntr_path() -> Path:
    platform = (env("ONEC_PLATFORM_PATH") or "").strip()
    if platform:
        p = Path(platform) / "bin" / "comcntr.dll"
        if p.is_file():
            return p
    bin_exe = (env("ONEC_BIN") or "").strip()
    if bin_exe:
        p = Path(bin_exe).parent / "comcntr.dll"
        if p.is_file():
            return p
    fallback = Path(r"C:\Program Files\1cv8\8.3.27.1719\bin\comcntr.dll")
    if fallback.is_file():
        return fallback
    raise RuntimeError("comcntr.dll not found. Set ONEC_PLATFORM_PATH or ONEC_BIN.")


def wrap(obj: Any) -> Any:
    """Force _Dispatch so 1C objects without ITypeComp still work."""
    _patch_dynamic_dispatch()
    import comtypes.client.dynamic as dyn
    from comtypes import automation

    if obj is None:
        return None
    if isinstance(obj, dyn._Dispatch):
        return obj
    if isinstance(obj, ctypes.POINTER(automation.IDispatch)):
        return dyn._Dispatch(obj)
    comobj = getattr(obj, "_comobj", None)
    if comobj is not None:
        return dyn._Dispatch(comobj)
    return obj


def call(obj: Any, name: str, *args: Any) -> Any:
    """Call a 1C method that COM may expose as property or method."""
    disp = wrap(obj)
    disp._FlagAsMethod(name)
    fn = getattr(disp, name)
    if callable(fn):
        return wrap(fn(*args))
    if args:
        raise TypeError(f"{name} is not callable and args were passed")
    return wrap(fn)


def _release_dispatch(obj: Any) -> None:
    if obj is None:
        return
    comobj = getattr(obj, "_comobj", obj)
    try:
        comobj.Release()
    except Exception:
        pass


def close(conn: Any, connector: Any = None) -> None:
    """Drop V83 application so the IB COM session ends."""
    pair = (conn, connector)
    try:
        _OPEN.remove(pair)
    except ValueError:
        pass
    _release_dispatch(conn)
    _release_dispatch(connector)
    gc.collect()


def _close_all() -> None:
    for conn, connector in list(_OPEN):
        close(conn, connector)


def _ensure_atexit() -> None:
    global _ATEXIT_DONE
    if _ATEXIT_DONE:
        return
    atexit.register(_close_all)
    _ATEXIT_DONE = True


def _is_server_target(target: str) -> bool:
    t = (target or "").strip().lower()
    return t in ("talanceva", "server", "srv", "prod_server")


def _server_conn_parts(target: str) -> tuple[str, str, str, str]:
    t = (target or "").strip().lower()
    if t in ("talanceva", "server", "srv"):
        server = env("ONEC_SERVER_TALANCEVA") or "srv1021:1841"
        ref = env("ONEC_REF_TALANCEVA") or "Talanceva"
        user = env("ONEC_USER_TALANCEVA") or env("ONEC_USER_WORK") or env("ONEC_USER", "") or ""
        password = env("ONEC_PASSWORD_TALANCEVA")
        if password is None:
            password = env("ONEC_PASSWORD_WORK")
        if password is None:
            password = env("ONEC_PASSWORD", "") or ""
        return server, ref, user, password or ""
    raise ValueError(f"Unknown server IB target: {target!r}")


def connect(*, target: str = "work") -> tuple[Any, Any]:
    """Open V83 COM connection. Returns (app, connector). Default target=work."""
    _patch_dynamic_dispatch()
    import comtypes.client
    from comtypes.gen.V83 import COMConnector, IV8COMConnector3

    t = (target or "work").strip().lower()
    if _is_server_target(t):
        server, ref, user, password = _server_conn_parts(t)
        conn_str = f'Srvr="{server}";Ref="{ref}";'
    elif t in ("dev", "develop", "sandbox", "base2"):
        ib = resolve_ib("dev")
        user, password = resolve_ib_auth("dev")
        conn_str = f'File="{ib}";'
    else:
        ib = resolve_ib("work")
        user, password = resolve_ib_auth("work")
        conn_str = f'File="{ib}";'

    comtypes.client.GetModule(str(_comcntr_path()))
    connector = comtypes.client.CreateObject(COMConnector, interface=IV8COMConnector3)
    try:
        connector.MaxConnections = 1
    except Exception:
        pass
    if user:
        conn_str += f'Usr="{user}";'
    conn_str += f'Pwd="{password}";'
    raw = connector.Connect(conn_str)
    return wrap(raw), connector


@contextlib.contextmanager
def session(*, target: str = "work") -> Iterator[Any]:
    _ensure_atexit()
    conn, connector = connect(target=target)
    _OPEN.append((conn, connector))
    try:
        yield conn
    finally:
        close(conn, connector)


def default_target() -> str:
    raw = (env("ONEC_COM_TARGET") or "work").strip().lower()
    return "dev" if raw in ("dev", "develop", "sandbox", "base2") else "work"


def ib_label(target: str) -> dict[str, Any]:
    t = (target or default_target()).strip().lower()
    if _is_server_target(t):
        server, ref, user, _ = _server_conn_parts(t)
        return {"target": "talanceva", "ib": f'Srvr="{server}";Ref="{ref}";', "user": user}
    work = is_work_target(t)
    try:
        ib = resolve_ib("work" if work else "dev")
    except Exception:
        ib = None
    user, _ = resolve_ib_auth("work" if work else "dev")
    return {"target": "work" if work else "dev", "ib": ib, "user": user}
