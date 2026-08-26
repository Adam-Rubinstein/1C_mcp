"""COM connect for 1C 8.3.x: comtypes + IV8COMConnector3, no win32com Connect.

win32com Dispatch('V83.COMConnector').Connect fails with TYPE_E_LIBNOTREGISTERED
on 8.3.27. GetObject wrapping via ITypeComp also fails (E_NOTIMPL) — always use
comtypes.client.dynamic._Dispatch.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

from onec_mcp_shared import env, is_work_target, resolve_ib, resolve_ib_auth


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


def connect(*, target: str = "work") -> Any:
    """Open V83 COM connection. Default target=work (ERP КОПИЯ)."""
    _patch_dynamic_dispatch()
    import comtypes.client
    from comtypes.gen.V83 import COMConnector, IV8COMConnector3

    t = (target or "work").strip().lower()
    if t in ("dev", "develop", "sandbox", "base2"):
        ib = resolve_ib("dev")
        user, password = resolve_ib_auth("dev")
    else:
        ib = resolve_ib("work")
        user, password = resolve_ib_auth("work")
        t = "work"

    comtypes.client.GetModule(str(_comcntr_path()))
    connector = comtypes.client.CreateObject(COMConnector, interface=IV8COMConnector3)
    conn_str = f'File="{ib}";'
    if user:
        conn_str += f'Usr="{user}";'
    conn_str += f'Pwd="{password}";'
    raw = connector.Connect(conn_str)
    return wrap(raw)


def default_target() -> str:
    raw = (env("ONEC_COM_TARGET") or "work").strip().lower()
    return "dev" if raw in ("dev", "develop", "sandbox", "base2") else "work"


def ib_label(target: str) -> dict[str, Any]:
    t = (target or default_target()).strip().lower()
    work = is_work_target(t)
    try:
        ib = resolve_ib("work" if work else "dev")
    except Exception:
        ib = None
    user, _ = resolve_ib_auth("work" if work else "dev")
    return {"target": "work" if work else "dev", "ib": ib, "user": user}
