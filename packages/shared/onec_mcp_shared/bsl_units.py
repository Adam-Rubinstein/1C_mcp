"""Parse BSL module into procedures/functions (portable, no MCP)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_DIRECTIVE = re.compile(r"^\s*&[^\n]*$")
_START = re.compile(
    r"^\s*(?:Асинх\s+)?(Процедура|Функция)\s+"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(",
    re.IGNORECASE,
)
_END_PROC = re.compile(r"^\s*КонецПроцедуры\b", re.IGNORECASE)
_END_FUNC = re.compile(r"^\s*КонецФункции\b", re.IGNORECASE)


@dataclass(frozen=True)
class BslUnit:
    kind: str  # procedure | function
    name: str
    start_line: int  # 1-based, includes leading &directives
    end_line: int  # 1-based, inclusive
    signature_line: int  # line with Процедура/Функция


def list_units(text: str) -> list[BslUnit]:
    lines = text.splitlines()
    units: list[BslUnit] = []
    i = 0
    n = len(lines)
    while i < n:
        m = _START.match(lines[i])
        if not m:
            i += 1
            continue
        kind_raw, name = m.group(1), m.group(2)
        if kind_raw.casefold().startswith("ф") or "func" in kind_raw.casefold():
            kind = "function"
            end_rx = _END_FUNC
        else:
            kind = "procedure"
            end_rx = _END_PROC
        sig = i
        dir_start = sig
        k = sig - 1
        while k >= 0 and _DIRECTIVE.match(lines[k]):
            dir_start = k
            k -= 1
        start_line = dir_start + 1
        signature_line = sig + 1
        end_line = None
        for t in range(sig + 1, n):
            if end_rx.match(lines[t]):
                end_line = t + 1
                break
            if _START.match(lines[t]):
                break
        if end_line is None:
            i = sig + 1
            continue
        units.append(
            BslUnit(
                kind=kind,
                name=name,
                start_line=start_line,
                end_line=end_line,
                signature_line=signature_line,
            )
        )
        i = end_line
    return units


def find_unit(text: str, name: str) -> BslUnit | None:
    want = name.strip().casefold()
    for u in list_units(text):
        if u.name.casefold() == want:
            return u
    return None


def extract_unit(text: str, name: str) -> tuple[BslUnit, str] | None:
    u = find_unit(text, name)
    if u is None:
        return None
    lines = text.splitlines()
    body = "\n".join(lines[u.start_line - 1 : u.end_line])
    if text.endswith("\n"):
        body += "\n"
    return u, body


def outline(text: str) -> list[dict]:
    return [asdict(u) for u in list_units(text)]
