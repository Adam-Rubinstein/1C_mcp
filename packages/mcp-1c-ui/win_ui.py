"""Win32 driver for the local ERP Enterprise window.

Does not start 1cv8 and does not read passwords.
UI Automation and screenshots go through the same .NET calls that worked on 6486.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

from onec_mcp_shared.enterprise_ui import click_allowed, key_events

user32 = ctypes.windll.user32

ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

_PS_UIA = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][Int64]$env:UI_HWND)
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$want = $env:UI_NAME
$out = @()
foreach ($el in $all) {
  $name = $el.Current.Name
  if ($want -and ($name -notlike ("*" + $want + "*"))) { continue }
  $r = $el.Current.BoundingRectangle
  if ($r.Width -le 0 -or $r.Height -le 0) { continue }
  $out += [pscustomobject]@{
    name = $name
    type = $el.Current.ControlType.ProgrammaticName
    x = [int]$r.X
    y = [int]$r.Y
    w = [int]$r.Width
    h = [int]$r.Height
  }
  if ($out.Count -ge 200) { break }
}
$out | ConvertTo-Json -Compress
"""

_PS_SHOT = r"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class UiShot {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  public struct RECT { public int L; public int T; public int R; public int B; }
}
"@
$hwnd = [IntPtr][Int64]$env:UI_HWND
$rect = New-Object UiShot+RECT
[UiShot]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
$w = $rect.R - $rect.L
$h = $rect.B - $rect.T
if ($w -le 0 -or $h -le 0) { throw "empty window rect" }
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$printed = [UiShot]::PrintWindow($hwnd, $hdc, 2)
$g.ReleaseHdc($hdc)
if (-not $printed) {
  $g.CopyFromScreen($rect.L, $rect.T, 0, 0, $bmp.Size)
}
$bmp.Save($env:UI_SHOT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("SAVED=" + $env:UI_SHOT)
"""

_PS_INVOKE = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][Int64]$env:UI_HWND)
$want = $env:UI_NAME
if (-not $want) { throw "UI_NAME required" }
$cond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::NameProperty, $want)
$el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if ($el -eq $null) {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($cand in $all) {
    if ($cand.Current.Name -like ("*" + $want + "*")) { $el = $cand; break }
  }
}
if ($el -eq $null) { throw ("element not found: " + $want) }
$pattern = $null
if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
  $pattern.Invoke()
  Write-Output "INVOKED"
} else {
  throw ("no InvokePattern: " + $el.Current.Name)
}
"""

_PS_TYPE_AT = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][Int64]$env:UI_HWND)
$x = [int]$env:UI_X
$y = [int]$env:UI_Y
$pt = New-Object System.Windows.Point($x, $y)
$el = [System.Windows.Automation.AutomationElement]::FromPoint($pt)
if ($el -eq $null) { throw "no element at point" }
$el.SetFocus()
Start-Sleep -Milliseconds 150
$text = $env:UI_TEXT
[System.Windows.Forms.SendKeys]::SendWait("^a")
Start-Sleep -Milliseconds 50
[System.Windows.Forms.SendKeys]::SendWait($text)
Write-Output ("TYPED=" + $el.Current.ControlType.ProgrammaticName)
"""


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def list_windows() -> list[dict]:
    found: list[dict] = []

    def _each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": int(hwnd), "pid": int(pid.value), "title": title})
        return True

    user32.EnumWindows(ENUM_PROC(_each), 0)
    return found


def _run_ps(script: str, env: dict[str, str]) -> str:
    flags = 0x08000000 if os.name == "nt" else 0
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**dict(**_base_env()), **env},
        check=False,
        creationflags=flags,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or "powershell failed")
    return proc.stdout


def _base_env() -> dict[str, str]:
    import os

    return dict(os.environ)


def focus(window: dict) -> None:
    hwnd = int(window["hwnd"])
    user32.ShowWindow(hwnd, 9)
    try:
        user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    current = ctypes.windll.kernel32.GetCurrentThreadId()
    target_pid = wintypes.DWORD()
    target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
    for _ in range(6):
        fg = user32.GetForegroundWindow()
        fg_pid = wintypes.DWORD()
        fg_thread = user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
        user32.AttachThreadInput(current, fg_thread, True)
        user32.AttachThreadInput(current, target_thread, True)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.AttachThreadInput(current, target_thread, False)
        user32.AttachThreadInput(current, fg_thread, False)
        now = user32.GetForegroundWindow()
        now_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(now, ctypes.byref(now_pid))
        if int(now_pid.value) == int(window["pid"]):
            return
    raise RuntimeError("ERP window did not take focus")


def invoke_by_name(window: dict, name_contains: str) -> None:
    """Click via UI Automation InvokePattern — works when SetForegroundWindow is blocked."""
    if not (name_contains or "").strip():
        raise RuntimeError("name required for invoke")
    _run_ps(_PS_INVOKE, {"UI_HWND": str(int(window["hwnd"])), "UI_NAME": name_contains.strip()})


def _require_foreground(window: dict) -> None:
    now = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(now, ctypes.byref(pid))
    if int(pid.value) != int(window["pid"]):
        raise RuntimeError("foreground is not the ERP window")


def _send(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise RuntimeError("SendInput did not deliver every event")


def type_text(window: dict, text: str) -> None:
    try:
        focus(window)
        _require_foreground(window)
    except RuntimeError:
        # Fallback: UIA SetFocus + SendKeys into current caret (caller should point field first).
        raise
    batch: list[INPUT] = []
    for event in key_events(text):
        down = INPUT(type=1)
        up = INPUT(type=1)
        if event["kind"] == "vk":
            down.u.ki.wVk = int(event["vk"])
            up.u.ki.wVk = int(event["vk"])
            up.u.ki.dwFlags = KEYEVENTF_KEYUP
        else:
            down.u.ki.wScan = int(event["code"])
            down.u.ki.dwFlags = KEYEVENTF_UNICODE
            up.u.ki.wScan = int(event["code"])
            up.u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        batch.extend((down, up))
    _send(batch)


def type_at(window: dict, x: int, y: int, text: str) -> None:
    """Focus the UIA element under (x,y) and type via SendKeys — works without process foreground."""
    if not text:
        raise RuntimeError("text required")
    # Escape SendKeys specials except we pass raw; brace specials.
    escaped = (
        text.replace("{", "{{")
        .replace("}", "}}")
        .replace("+", "{+}")
        .replace("^", "{^}")
        .replace("%", "{%}")
        .replace("~", "{~}")
        .replace("(", "{(}")
        .replace(")", "{)}")
    )
    _run_ps(
        _PS_TYPE_AT,
        {
            "UI_HWND": str(int(window["hwnd"])),
            "UI_X": str(int(x)),
            "UI_Y": str(int(y)),
            "UI_TEXT": escaped,
        },
    )


def tap_key(window: dict, vk: int) -> None:
    focus(window)
    _require_foreground(window)
    down = INPUT(type=1)
    down.u.ki.wVk = int(vk)
    up = INPUT(type=1)
    up.u.ki.wVk = int(vk)
    up.u.ki.dwFlags = KEYEVENTF_KEYUP
    _send([down, up])


def tap_chord(window: dict, *vks: int) -> None:
    """Hold modifiers then tap the last key (e.g. Alt+A → VK_MENU, ord('A'))."""
    if not vks:
        raise RuntimeError("chord keys required")
    focus(window)
    _require_foreground(window)
    downs: list[INPUT] = []
    ups: list[INPUT] = []
    for vk in vks:
        down = INPUT(type=1)
        down.u.ki.wVk = int(vk)
        downs.append(down)
        up = INPUT(type=1)
        up.u.ki.wVk = int(vk)
        up.u.ki.dwFlags = KEYEVENTF_KEYUP
        ups.append(up)
    ups.reverse()
    _send(downs + ups)


def click_at(window: dict, x: int, y: int, *, name: str = "", confirm_write: bool = False) -> None:
    allowed, reason = click_allowed(name, confirm_write=confirm_write)
    if name and not allowed:
        raise RuntimeError(reason)
    try:
        focus(window)
    except RuntimeError:
        if name:
            invoke_by_name(window, name)
            return
        raise
    point = POINT(int(x), int(y))
    under = user32.WindowFromPoint(point)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(under, ctypes.byref(pid))
    if int(pid.value) != int(window["pid"]):
        if name:
            invoke_by_name(window, name)
            return
        raise RuntimeError("click point is not the ERP window")
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def uia_find(window: dict, name_contains: str = "") -> list[dict]:
    raw = _run_ps(_PS_UIA, {"UI_HWND": str(int(window["hwnd"])), "UI_NAME": name_contains or ""})
    text = raw.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        return [data]
    return list(data)


def screenshot(window: dict, path: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_ps(_PS_SHOT, {"UI_HWND": str(int(window["hwnd"])), "UI_SHOT": str(target)})
    if not target.is_file():
        raise RuntimeError("screenshot was not written")
    return str(target)
