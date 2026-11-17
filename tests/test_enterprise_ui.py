from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from onec_mcp_shared.enterprise_ui import click_allowed, key_events, normalize_name, pick_window


def test_pick_ignores_planner_and_designer():
    windows = [
        {"hwnd": 1, "pid": 10, "title": "Планировщик"},
        {"hwnd": 2, "pid": 11, "title": "Конфигуратор - ERP КОПИЯ"},
        {"hwnd": 3, "pid": 12, "title": "[КОПИЯ] Talanceva / 1С:ERP"},
    ]
    chosen = pick_window(windows)
    assert chosen["pid"] == 12


def test_pick_refuses_two_enterprise_windows():
    windows = [
        {"hwnd": 1, "pid": 1, "title": "ERP КОПИЯ"},
        {"hwnd": 2, "pid": 2, "title": "ERP КОПИЯ запасная"},
    ]
    assert pick_window(windows) is None


def test_save_yes_and_post_are_blocked():
    assert click_allowed("  Нет  ")[0] is True
    assert click_allowed("OK")[0] is True
    assert click_allowed("Да") == (False, "save yes blocked")
    assert click_allowed("Провести и закрыть")[0] is False
    assert click_allowed("Да", confirm_write=True)[0] is True


def test_digits_are_virtual_keys():
    events = key_events("70125000080001")
    assert all(item["kind"] == "vk" for item in events)
    assert events[0]["vk"] == ord("7")


def test_cyrillic_stays_unicode():
    events = key_events("Товары")
    assert events[0]["kind"] == "unicode"
    assert normalize_name("  Да \u00a0 ") == "да"
