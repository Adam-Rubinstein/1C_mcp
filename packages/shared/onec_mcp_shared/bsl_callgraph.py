"""Lightweight BSL call graph for vendor/legacy prefixes (portable)."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .bsl_units import list_units

_IDENT = r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*"
_CALL = re.compile(rf"(?<![\wА-Яа-яЁё])({_IDENT})(?:\.({_IDENT}))?\s*\(")
_STRING_OR_COMMENT = re.compile(
    r"//.*?$|\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'",
    re.MULTILINE,
)


@dataclass
class CallEdge:
    caller_module: str
    caller_unit: str
    callee_module: str | None  # None = same-module / unresolved module
    callee_name: str
    line: int
    kind: str  # direct


def _strip_noise(line: str) -> str:
    return _STRING_OR_COMMENT.sub(" ", line)


def _prefixes_from_env() -> list[str]:
    raw = os.environ.get("ONEC_VENDOR_PREFIXES", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _module_key(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _name_matches_prefix(name: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any(name.startswith(p) for p in prefixes)


def collect_edges_in_file(
    path: Path,
    root: Path,
    text: str,
    prefixes: list[str],
    known_units: set[str],
) -> list[CallEdge]:
    """Collect direct calls; keep edges where callee name or module matches prefixes,
    or callee is a known unit name in the indexed set."""
    mod = _module_key(path, root)
    units = list_units(text)
    lines = text.splitlines()
    edges: list[CallEdge] = []
    for u in units:
        # skip units that are not "ours" unless file path suggests own object
        own_unit = _name_matches_prefix(u.name, prefixes) or _path_looks_owned(mod, prefixes)
        if prefixes and not own_unit:
            # still index calls FROM any unit inside owned path
            if not _path_looks_owned(mod, prefixes):
                continue
        for ln in range(u.signature_line, u.end_line):
            raw = lines[ln - 1]
            cleaned = _strip_noise(raw)
            for m in _CALL.finditer(cleaned):
                left, right = m.group(1), m.group(2)
                # skip language keywords / constructors lightly
                if left.casefold() in _SKIP_CALLEES:
                    continue
                if right:
                    callee_mod, callee_name = left, right
                else:
                    callee_mod, callee_name = None, left
                if callee_name.casefold() == u.name.casefold():
                    continue
                # filter: keep if prefix match on callee, or known local unit, or module prefix
                keep = False
                if _name_matches_prefix(callee_name, prefixes):
                    keep = True
                elif callee_mod and _name_matches_prefix(callee_mod, prefixes):
                    keep = True
                elif callee_mod is None and callee_name in known_units:
                    keep = True
                elif not prefixes:
                    keep = True
                if not keep:
                    continue
                edges.append(
                    CallEdge(
                        caller_module=mod,
                        caller_unit=u.name,
                        callee_module=callee_mod,
                        callee_name=callee_name,
                        line=ln,
                        kind="direct",
                    )
                )
    return edges


def _path_looks_owned(rel: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    base = Path(rel).name
    parts = rel.replace("\\", "/").split("/")
    return any(any(p.startswith(pref) for p in parts) or base.startswith(pref) for pref in prefixes)


_SKIP_CALLEES = {
    "если",
    "для",
    "пока",
    "новый",
    "new",
    "возврати",
    "возврат",
    "перем",
    "попытка",
    "исключение",
    "конецпопытки",
    "и",
    "или",
    "не",
    "процедура",
    "функция",
}


def rebuild_graph(roots: list[Path], prefixes: list[str] | None = None) -> dict:
    prefs = prefixes if prefixes is not None else _prefixes_from_env()
    bsl_files: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.bsl"):
            if path.is_file():
                bsl_files.append((root, path))

    # first pass: unit names in owned files
    known_units: set[str] = set()
    texts: dict[str, tuple[Path, Path, str]] = {}
    newest_mtime = 0.0
    for root, path in bsl_files:
        rel = _module_key(path, root)
        if prefs and not _path_looks_owned(rel, prefs):
            continue
        try:
            st = path.stat()
            newest_mtime = max(newest_mtime, st.st_mtime)
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts[rel] = (root, path, text)
        for u in list_units(text):
            known_units.add(u.name)

    edges: list[CallEdge] = []
    for rel, (root, path, text) in texts.items():
        edges.extend(collect_edges_in_file(path, root, text, prefs, known_units))

    return {
        "ok": True,
        "builtAt": time.time(),
        "builtAtIso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prefixes": prefs,
        "moduleCount": len(texts),
        "edgeCount": len(edges),
        "sourceNewestMtime": newest_mtime,
        "edges": [asdict(e) for e in edges],
    }


def graph_is_stale(index: dict, roots: list[Path], prefixes: list[str] | None = None) -> bool:
    prefs = prefixes if prefixes is not None else index.get("prefixes") or _prefixes_from_env()
    newest = 0.0
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.bsl"):
            rel = _module_key(path, root)
            if prefs and not _path_looks_owned(rel, list(prefs)):
                continue
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest > float(index.get("sourceNewestMtime") or 0) + 0.01


def save_graph(index: dict, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


def load_graph(cache_path: Path) -> dict | None:
    if not cache_path.is_file():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def query_edges(
    index: dict,
    *,
    unit: str | None = None,
    direction: str = "callers",
    limit: int = 50,
) -> list[dict]:
    """direction: callers = who calls unit; callees = who unit calls."""
    if not unit:
        return []
    want = unit.casefold()
    out: list[dict] = []
    for e in index.get("edges") or []:
        if direction == "callees":
            if str(e.get("caller_unit", "")).casefold() == want:
                out.append(e)
        else:
            if str(e.get("callee_name", "")).casefold() == want:
                out.append(e)
        if len(out) >= limit:
            break
    return out
