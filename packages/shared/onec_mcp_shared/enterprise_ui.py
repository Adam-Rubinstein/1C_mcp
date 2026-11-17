"""Pure rules for driving the Enterprise window. No Win32 calls."""

from __future__ import annotations


def normalize_name(name: str) -> str:
    return " ".join((name or "").replace("\u00a0", " ").split()).casefold()


def click_allowed(name: str, *, confirm_write: bool = False) -> tuple[bool, str]:
    """Block buttons that write or post a document unless the task asked to save."""
    folded = normalize_name(name)
    if folded in {"провести и закрыть", "провести"} and not confirm_write:
        return False, "post blocked"
    if folded == "да" and not confirm_write:
        return False, "save yes blocked"
    return True, ""


def pick_window(
    windows: list[dict],
    *,
    must_contain: str,
    deny: tuple[str, ...] = ("Конфигуратор",),
) -> dict | None:
    """One Enterprise window. Designer and other infobases are not a match."""
    must = (must_contain or "").casefold().strip()
    if not must:
        return None
    deny_folded = tuple(item.casefold() for item in deny)
    hits = []
    for window in windows:
        title = str(window.get("title") or "")
        folded = title.casefold()
        if any(item in folded for item in deny_folded):
            continue
        if must in folded:
            hits.append(window)
    if len(hits) != 1:
        return None
    return hits[0]


def key_events(text: str) -> list[dict]:
    """Digits go as virtual keys. Other characters go as Unicode input.

    The barcode dialog ignored Unicode digits and accepted VK 0-9.
    The global search accepted Unicode for Cyrillic.
    """
    events: list[dict] = []
    for ch in text:
        if "0" <= ch <= "9":
            events.append({"kind": "vk", "vk": ord(ch)})
        else:
            events.append({"kind": "unicode", "code": ord(ch)})
    return events
