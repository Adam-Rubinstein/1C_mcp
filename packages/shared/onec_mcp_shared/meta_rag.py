"""Metadata FTS index over 1C dump XML (portable RAG-lite)."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

_NAME = re.compile(r"<Name>([^<]+)</Name>")
_SYN = re.compile(r"<Synonym>.*?<v8:content>([^<]*)</v8:content>", re.DOTALL)
_COMMENT = re.compile(r"<Comment>([^<]*)</Comment>")

# Top-level object folders in CF dump
_OBJECT_DIRS = (
    "Catalogs",
    "Documents",
    "Enums",
    "Reports",
    "DataProcessors",
    "CommonModules",
    "InformationRegisters",
    "AccumulationRegisters",
    "AccountingRegisters",
    "CalculationRegisters",
    "BusinessProcesses",
    "Tasks",
    "ChartsOfCharacteristicTypes",
    "ChartsOfAccounts",
    "ChartsOfCalculationTypes",
    "ExchangePlans",
    "FilterCriteria",
    "SettingsStorages",
    "Constants",
    "CommonForms",
    "CommonCommands",
    "CommandGroups",
    "CommonTemplates",
    "DefinedTypes",
    "EventSubscriptions",
    "ScheduledJobs",
    "FunctionalOptions",
    "FunctionalOptionsParameters",
    "DefinedTypes",
    "HTTPServices",
    "WebServices",
    "XDTOPackages",
    "StyleItems",
    "Styles",
    "Languages",
    "Roles",
    "Subsystems",
    "SessionParameters",
)


def _cache_dir(dump_tmp: str | None) -> Path:
    base = Path(dump_tmp) if dump_tmp else Path.cwd() / ".tmp"
    return base / "meta-rag"


def db_path(dump_tmp: str | None) -> Path:
    return _cache_dir(dump_tmp) / "meta.sqlite"


def dirty_flag_path(dump_tmp: str | None) -> Path:
    return _cache_dir(dump_tmp) / "dirty.flag"


def mark_dirty(dump_tmp: str | None) -> None:
    p = dirty_flag_path(dump_tmp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(time.time()), encoding="utf-8")


def clear_dirty(dump_tmp: str | None) -> None:
    p = dirty_flag_path(dump_tmp)
    if p.is_file():
        p.unlink()


def is_dirty(dump_tmp: str | None) -> bool:
    return dirty_flag_path(dump_tmp).is_file()


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    # Synonym often has nested v8:content
    parts: list[str] = []
    if el.text and el.text.strip():
        parts.append(el.text.strip())
    for child in el:
        if _local_name(child.tag) == "content" and child.text:
            parts.append(child.text.strip())
        else:
            t = _text_of(child)
            if t:
                parts.append(t)
    return " ".join(parts)


def iter_metadata_files(roots: list[Path]) -> list[Path]:
    """Collect object metadata XML.

    Hier dump layout (ERP): ``Catalogs/Номенклатура.xml`` next to ``Catalogs/Номенклатура/``.
    Nested layout: ``Catalogs/Foo/Foo.xml``.
    """
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for folder in _OBJECT_DIRS:
            base = root / folder
            if not base.is_dir():
                continue
            # Flat: Catalogs/ObjectName.xml
            for cand in base.glob("*.xml"):
                if cand.is_file():
                    key = str(cand.resolve())
                    if key not in seen:
                        seen.add(key)
                        out.append(cand)
            # Nested: Catalogs/ObjectName/ObjectName.xml
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                cand = child / f"{child.name}.xml"
                if cand.is_file():
                    key = str(cand.resolve())
                    if key not in seen:
                        seen.add(key)
                        out.append(cand)
    return out


def _parse_object_xml(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name_m = _NAME.search(raw)
    if not name_m:
        return None
    name = name_m.group(1).strip()
    syn_m = _SYN.search(raw)
    synonym = syn_m.group(1).strip() if syn_m else ""
    com_m = _COMMENT.search(raw)
    comment = com_m.group(1).strip() if com_m else ""
    # Meta type = parent folder (Catalogs, Documents, …)
    meta_type = path.parent.name
    if meta_type not in _OBJECT_DIRS and path.parent.parent.name in _OBJECT_DIRS:
        meta_type = path.parent.parent.name
    return {
        "name": name,
        "synonym": synonym,
        "comment": comment,
        "metaType": meta_type,
        "path": str(path).replace("\\", "/"),
        "blob": f"{name} {synonym} {comment} {meta_type}",
    }

def reindex(roots: list[Path], dump_tmp: str | None = None) -> dict:
    files = iter_metadata_files(roots)
    newest = 0.0
    rows: list[dict] = []
    for f in files:
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
        doc = _parse_object_xml(f)
        if doc:
            rows.append(doc)

    path = db_path(dump_tmp)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE meta USING fts5(
                name, synonym, comment, metaType, path, blob,
                tokenize='unicode61'
            )
            """
        )
        conn.executemany(
            "INSERT INTO meta(name, synonym, comment, metaType, path, blob) VALUES (?,?,?,?,?,?)",
            [
                (r["name"], r["synonym"], r["comment"], r["metaType"], r["path"], r["blob"])
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()

    meta = {
        "ok": True,
        "builtAt": time.time(),
        "builtAtIso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "docCount": len(rows),
        "fileCount": len(files),
        "sourceNewestMtime": newest,
        "db": str(path),
    }
    (path.parent / "meta-status.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    clear_dirty(dump_tmp)
    return meta


def load_status(dump_tmp: str | None = None) -> dict | None:
    p = _cache_dir(dump_tmp) / "meta-status.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def status(roots: list[Path], dump_tmp: str | None = None) -> dict:
    st = load_status(dump_tmp)
    dirty = is_dirty(dump_tmp)
    if st is None:
        return {"ok": True, "exists": False, "stale": True, "dirty": dirty}
    newest = 0.0
    for f in iter_metadata_files(roots):
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    stale = dirty or newest > float(st.get("sourceNewestMtime") or 0) + 0.01
    return {
        "ok": True,
        "exists": True,
        "stale": stale,
        "dirty": dirty,
        "builtAtIso": st.get("builtAtIso"),
        "docCount": st.get("docCount"),
        "db": st.get("db"),
    }


def search(query: str, dump_tmp: str | None = None, limit: int = 20) -> dict:
    st = load_status(dump_tmp)
    if st is None:
        return {"ok": False, "error": "No RAG index — call rag_reindex first"}
    path = Path(st.get("db") or db_path(dump_tmp))
    if not path.is_file():
        return {"ok": False, "error": "SQLite index missing — call rag_reindex"}
    q = query.strip()
    if not q:
        return {"ok": False, "error": "query is required"}
    # FTS: quote tokens safely
    tokens = re.findall(r"[\wА-Яа-яЁё]+", q, flags=re.UNICODE)
    if not tokens:
        return {"ok": False, "error": "query has no searchable tokens"}
    fts = " AND ".join(f'"{t}"' for t in tokens[:12])
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(
            """
            SELECT name, synonym, comment, metaType, path,
                   bm25(meta) AS score
            FROM meta
            WHERE meta MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (fts, limit),
        )
        hits = [
            {
                "name": r[0],
                "synonym": r[1],
                "comment": r[2],
                "metaType": r[3],
                "path": r[4],
                "score": r[5],
            }
            for r in cur.fetchall()
        ]
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": str(exc), "fts": fts}
    finally:
        conn.close()
    return {
        "ok": True,
        "query": query,
        "count": len(hits),
        "hits": hits,
        "staleHint": is_dirty(dump_tmp),
    }
