"""Local Enterprise window driver. Stdio on the machine where 1C is open."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "packages" / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.enterprise_ui import pick_window  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

import win_ui  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-ui")

_KEYS = {
    "f7": 0x76,
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
}


def _window() -> dict:
    must = (env("UI_TITLE_MUST") or "").strip()
    if not must:
        raise RuntimeError("Set UI_TITLE_MUST to the WORK window title")
    extra = tuple(part.strip() for part in (env("UI_TITLE_DENY") or "").split(",") if part.strip())
    chosen = pick_window(win_ui.list_windows(), must_contain=must, deny=("Конфигуратор", *extra))
    if chosen is None:
        raise RuntimeError("Enterprise window not found or not unique")
    return chosen


@mcp.tool()
def ui_windows() -> str:
    """List visible top-level windows. Titles only, no command lines."""
    return json_result({"ok": True, "windows": win_ui.list_windows()})


@mcp.tool()
def ui_find(name_contains: str = "") -> str:
    """Find UI Automation elements in the WORK Enterprise window. 1C tab Select() is not implemented; click the bounds."""
    try:
        window = _window()
        return json_result({"ok": True, "window": window, "elements": win_ui.uia_find(window, name_contains)})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def ui_shot(path: str) -> str:
    """Screenshot the WORK Enterprise window to a png. A pid is not a screenshot."""
    try:
        window = _window()
        saved = win_ui.screenshot(window, path)
        return json_result({"ok": True, "window": window, "path": saved})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def ui_click(x: int, y: int, name: str = "", confirm_write: bool = False) -> str:
    """Click inside the WORK Enterprise window. Refuses Провести и закрыть and Да unless confirm_write. If focus is blocked and name is set, uses UIA Invoke."""
    try:
        window = _window()
        win_ui.click_at(window, x, y, name=name, confirm_write=confirm_write)
        return json_result({"ok": True, "window": window, "x": x, "y": y, "name": name})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def ui_invoke(name_contains: str) -> str:
    """Invoke a named control in WORK Enterprise via UI Automation (no mouse focus required)."""
    try:
        window = _window()
        win_ui.invoke_by_name(window, name_contains)
        return json_result({"ok": True, "window": window, "name": name_contains})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def ui_type(text: str, x: int = 0, y: int = 0) -> str:
    """Type into the ERP field. If x,y > 0, focuses that UIA element via SendKeys (no process foreground). Else needs focus."""
    try:
        window = _window()
        if int(x) > 0 and int(y) > 0:
            win_ui.type_at(window, int(x), int(y), text)
        else:
            win_ui.type_text(window, text)
        return json_result({"ok": True, "window": window, "length": len(text), "x": x, "y": y})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def ui_key(key: str) -> str:
    """Press f7, enter, esc, or tab in the ERP window. F7 runs only on the page that owns the command."""
    try:
        vk = _KEYS.get(key.strip().lower())
        if vk is None:
            return json_result({"ok": False, "error": "key must be f7, enter, esc, or tab"})
        window = _window()
        win_ui.tap_key(window, vk)
        return json_result({"ok": True, "window": window, "key": key})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    run_mcp(mcp, default_port=18770)
