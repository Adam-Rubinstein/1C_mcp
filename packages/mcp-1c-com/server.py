from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_PKG = Path(__file__).resolve().parent
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PKG))
sys.path.insert(0, str(_ROOT / "packages" / "shared"))
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import json_result, load_env_files, normalize_object_name  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

from v8connect import call, default_target, ib_label, session, wrap  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), _ROOT / ".env", Path.cwd() / ".env")

mcp = make_mcp("1c-com")

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_KIND_RU = {
    "document": "Документ",
    "catalog": "Справочник",
    "документ": "Документ",
    "справочник": "Справочник",
}
_KIND_EN = {
    "document": "Document",
    "catalog": "Catalog",
    "документ": "Document",
    "справочник": "Catalog",
}
_MGR_ATTR = {
    "document": "Documents",
    "catalog": "Catalogs",
    "документ": "Documents",
    "справочник": "Catalogs",
}


def _parse_object(object_name: str) -> tuple[str, str]:
    canon = normalize_object_name(object_name)
    if "." not in canon:
        raise ValueError("object_name must be Document.Name or Catalog.Name")
    kind, name = canon.split(".", 1)
    key = kind.lower()
    if key not in ("document", "catalog"):
        raise ValueError(f"Only Document and Catalog supported, got {kind}")
    return key, name


def _to_jsonable(val: Any, conn: Any | None = None) -> Any:
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    if conn is not None:
        try:
            text = call(conn, "String", val)
            if text is not None:
                s = str(text)
                if s.startswith("<comtypes"):
                    raise TypeError("not a presentation")
                return s
        except Exception:
            pass
        try:
            return str(call(val, "UUID"))
        except Exception:
            pass
    try:
        return str(val)
    except Exception:  # noqa: BLE001
        return repr(val)


def _count(obj: Any) -> int:
    disp = wrap(obj)
    n = getattr(disp, "Count", None)
    if callable(n):
        n = n()
    if n is None:
        return 0
    return int(n)


def _run_query(conn: Any, text: str, limit: int = 500) -> tuple[list[str], list[dict[str, Any]]]:
    q = call(conn, "NewObject", "Query")
    q.Text = text
    result = call(q, "Execute")
    selection = call(result, "Choose")
    columns_obj = wrap(result).Columns
    n_cols = _count(columns_obj)
    columns = [str(call(columns_obj, "Get", i).Name) for i in range(n_cols)]
    rows: list[dict[str, Any]] = []
    while call(selection, "Next") and len(rows) < max(1, limit):
        row: dict[str, Any] = {}
        for i, col in enumerate(columns):
            row[col] = _to_jsonable(call(selection, "Get", i), conn)
        rows.append(row)
    return columns, rows


def _query_one_ref(conn: Any, text: str, param: str, value: Any) -> Any:
    q = call(conn, "NewObject", "Query")
    q.Text = text
    try:
        call(q, "SetParameter", param, value)
    except Exception:
        call(q, "УстановитьПараметр", param, value)
    result = call(q, "Execute")
    sel = call(result, "Choose")
    if not call(sel, "Next"):
        return None
    return wrap(call(sel, "Get", 0))


def _find_ref(conn: Any, kind: str, name: str, *, uuid: str | None, number: str | None, code: str | None):
    ru = _KIND_RU[kind]
    if uuid and _UUID_RE.match(uuid.strip()):
        uid = call(conn, "NewObject", "UUID", uuid.strip())
        mgr = wrap(getattr(wrap(getattr(wrap(conn), _MGR_ATTR[kind])), name))
        try:
            ref = call(mgr, "GetRef", uid)
        except Exception:
            ref = call(mgr, "ПолучитьСсылку", uid)
        found = _query_one_ref(
            conn,
            f"ВЫБРАТЬ ПЕРВЫЕ 1 Т.Ссылка ИЗ {ru}.{name} КАК Т ГДЕ Т.Ссылка = &Ref",
            "Ref",
            wrap(ref),
        )
        if found is None:
            raise ValueError(f"Not found: {kind}.{name} uuid={uuid}")
        return found

    if kind == "document":
        if not number:
            raise ValueError("document requires uuid or number")
        found = _query_one_ref(
            conn,
            f"ВЫБРАТЬ ПЕРВЫЕ 1 Т.Ссылка ИЗ {ru}.{name} КАК Т "
            f"ГДЕ Т.Номер = &Number УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ",
            "Number",
            number,
        )
        if found is None:
            raise ValueError(f"Not found: Document.{name} number={number}")
        return found

    if not code and not number:
        raise ValueError("catalog requires uuid or code")
    key = code or number
    found = _query_one_ref(
        conn,
        f"ВЫБРАТЬ ПЕРВЫЕ 1 Т.Ссылка ИЗ {ru}.{name} КАК Т ГДЕ Т.Код = &Code",
        "Code",
        key,
    )
    if found is None:
        raise ValueError(f"Not found: Catalog.{name} code={key}")
    return found


def _get_object(ref: Any) -> Any:
    try:
        return call(ref, "GetObject")
    except Exception:
        return call(ref, "ПолучитьОбъект")


def _meta_attr_names(conn: Any, kind: str, name: str) -> list[str]:
    full = f"{_KIND_EN[kind]}.{name}"
    meta = call(wrap(conn).Metadata, "FindByFullName", full)
    if meta is None:
        meta = call(wrap(conn).Metadata, "FindByFullName", f"{_KIND_RU[kind]}.{name}")
    names: list[str] = []
    if meta is None:
        return names
    attrs = wrap(meta).Attributes
    for i in range(_count(attrs)):
        names.append(str(call(attrs, "Get", i).Name))
    return names


def _ts_names(conn: Any, kind: str, name: str) -> list[str]:
    full = f"{_KIND_EN[kind]}.{name}"
    meta = call(wrap(conn).Metadata, "FindByFullName", full)
    if meta is None:
        return []
    tss = wrap(meta).TabularSections
    out: list[str] = []
    for i in range(_count(tss)):
        out.append(str(call(tss, "Get", i).Name))
    return out


def _read_header(obj: Any, attr_names: list[str], conn: Any) -> dict[str, Any]:
    header: dict[str, Any] = {}
    for std in ("Number", "Date", "Posted", "DeletionMark", "Code", "Description"):
        try:
            header[std] = _to_jsonable(getattr(wrap(obj), std), conn)
        except Exception:
            pass
    for ru in ("Номер", "Дата", "Проведен", "ПометкаУдаления", "Код", "Наименование"):
        try:
            header[ru] = _to_jsonable(getattr(wrap(obj), ru), conn)
        except Exception:
            pass
    for attr in attr_names:
        try:
            header[attr] = _to_jsonable(getattr(wrap(obj), attr), conn)
        except Exception:
            header[attr] = None
    return header


def _read_tabular(obj: Any, ts_name: str, conn: Any, kind: str, name: str) -> list[dict[str, Any]]:
    ru = _KIND_RU[kind]
    qtext = f"ВЫБРАТЬ ПЕРВЫЕ 500 * ИЗ {ru}.{name}.{ts_name} КАК Т ГДЕ Т.Ссылка = &Ref"
    q = call(conn, "NewObject", "Query")
    q.Text = qtext
    call(q, "SetParameter", "Ref", getattr(wrap(obj), "Ref") if hasattr(wrap(obj), "Ref") else wrap(obj).Ссылка)
    try:
        result = call(q, "Execute")
    except Exception:
        return []
    sel = call(result, "Choose")
    columns_obj = wrap(result).Columns
    n_cols = _count(columns_obj)
    columns = [str(call(columns_obj, "Get", i).Name) for i in range(n_cols)]
    rows: list[dict[str, Any]] = []
    while call(sel, "Next"):
        row = {col: _to_jsonable(call(sel, "Get", i), conn) for i, col in enumerate(columns)}
        rows.append(row)
    return rows


def _resolve_value(conn: Any, raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if not s:
        return raw
    if _UUID_RE.match(s):
        return s
    if "." in s:
        # Enum.ВариантыПриемкиТоваров.РазделенаТолькоПоНакладным
        parts = s.split(".")
        if len(parts) >= 3 and parts[0].lower() in ("enum", "перечисление"):
            enum_name, val_name = parts[1], parts[-1]
            enums = wrap(wrap(conn).Enums)
            emgr = getattr(enums, enum_name)
            return getattr(wrap(emgr), val_name)
        if len(parts) == 2:
            # ВариантыПриемкиТоваров.РазделенаТолькоПоНакладным
            enums = wrap(wrap(conn).Enums)
            try:
                emgr = getattr(enums, parts[0])
                return getattr(wrap(emgr), parts[1])
            except Exception:
                pass
    return raw


def _set_attr(obj: Any, name: str, value: Any) -> None:
    setattr(wrap(obj), name, value)


def _write_object(obj: Any) -> None:
    try:
        call(obj, "Write")
        return
    except Exception:
        pass
    call(obj, "Записать")


def _post_object(conn: Any, obj: Any) -> None:
    try:
        mode = wrap(wrap(conn).DocumentWriteMode).Posting
        call(obj, "Write", mode)
        return
    except Exception:
        pass
    try:
        mode = wrap(wrap(conn).РежимЗаписиДокумента).Проведение
        call(obj, "Записать", mode)
        return
    except Exception:
        pass
    call(obj, "Post")


def _unpost_object(conn: Any, obj: Any) -> None:
    try:
        mode = wrap(wrap(conn).DocumentWriteMode).UndoPosting
        call(obj, "Write", mode)
        return
    except Exception:
        pass
    try:
        mode = wrap(wrap(conn).РежимЗаписиДокумента).ОтменаПроведения
        call(obj, "Записать", mode)
        return
    except Exception:
        pass
    call(obj, "UndoPosting")


@mcp.tool()
def com_status() -> str:
    """Health: COM IB target (default WORK). Does not open IB."""
    t = default_target()
    info = ib_label(t)
    has_comtypes = False
    try:
        import comtypes.client  # noqa: F401

        has_comtypes = True
    except ImportError:
        pass
    info.update(
        {
            "ok": has_comtypes and bool(info.get("ib")),
            "comtypes": has_comtypes,
            "platform": "comtypes IV8COMConnector3",
        }
    )
    return json_result(info)


@mcp.tool()
def com_ping(target: str = "") -> str:
    """Connect and read configuration name."""
    t = (target or default_target()).strip() or default_target()
    try:
        with session(target=t) as conn:
            info: dict[str, Any] = {
                "ok": True,
                "connected": True,
                **ib_label(t),
            }
            try:
                meta = wrap(conn).Metadata
                info["configurationName"] = str(meta.Name)
                info["configurationVersion"] = str(getattr(meta, "Version", "") or "")
            except Exception as exc:  # noqa: BLE001
                info["metaError"] = str(exc)
            return json_result(info)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), **ib_label(t)})


@mcp.tool()
def com_query(query_text: str, limit: int = 100, target: str = "") -> str:
    """Run 1C query. Returns columns+rows JSON."""
    if not query_text.strip():
        return json_result({"ok": False, "error": "query_text is required"})
    t = (target or default_target()).strip() or default_target()
    try:
        with session(target=t) as conn:
            columns, rows = _run_query(conn, query_text, limit=limit)
            return json_result({"ok": True, "columns": columns, "rows": rows, "count": len(rows), "target": t})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_metadata_find(object_name: str, target: str = "") -> str:
    """Find metadata by full name, e.g. Document.MyDoc."""
    name = normalize_object_name(object_name)
    t = (target or default_target()).strip() or default_target()
    try:
        with session(target=t) as conn:
            meta = wrap(conn).Metadata
            meta_obj = call(meta, "FindByFullName", name)
            if meta_obj is None:
                meta_obj = call(meta, "FindByFullName", object_name)
            if meta_obj is None:
                return json_result({"ok": False, "error": f"Not found: {name}"})
            return json_result(
                {
                    "ok": True,
                    "fullName": str(getattr(wrap(meta_obj), "FullName", name)),
                    "name": str(wrap(meta_obj).Name),
                    "synonym": str(getattr(wrap(meta_obj), "Synonym", "") or ""),
                }
            )
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_get(
    object_name: str,
    number: str = "",
    uuid: str = "",
    code: str = "",
    include_tabular: bool = True,
    target: str = "",
) -> str:
    """Read Document/Catalog header (+ tabular sections) as JSON."""
    t = (target or default_target()).strip() or default_target()
    try:
        kind, name = _parse_object(object_name)
        with session(target=t) as conn:
            ref = _find_ref(conn, kind, name, uuid=uuid or None, number=number or None, code=code or None)
            obj = _get_object(ref)
            attrs = _meta_attr_names(conn, kind, name)
            header = _read_header(obj, attrs, conn)
            tabular: dict[str, Any] = {}
            if include_tabular:
                for ts in _ts_names(conn, kind, name):
                    tabular[ts] = _read_tabular(obj, ts, conn, kind, name)
            return json_result(
                {
                    "ok": True,
                    "object": f"{_KIND_EN[kind]}.{name}",
                    "header": header,
                    "tabular": tabular,
                    "target": t,
                }
            )
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_write(
    object_name: str,
    values: str,
    number: str = "",
    uuid: str = "",
    code: str = "",
    confirm: bool = False,
    target: str = "",
) -> str:
    """Set attributes (JSON object) and Write(). Requires confirm=true."""
    if not confirm:
        return json_result({"ok": False, "error": "confirm=true required to write", "step": "confirm_write"})
    t = (target or default_target()).strip() or default_target()
    try:
        payload = json.loads(values) if isinstance(values, str) else values
        if not isinstance(payload, dict) or not payload:
            return json_result({"ok": False, "error": "values must be a JSON object of attributes"})
        kind, name = _parse_object(object_name)
        with session(target=t) as conn:
            ref = _find_ref(conn, kind, name, uuid=uuid or None, number=number or None, code=code or None)
            obj = _get_object(ref)
            applied: dict[str, Any] = {}
            for key, raw in payload.items():
                val = _resolve_value(conn, raw)
                _set_attr(obj, str(key), val)
                applied[str(key)] = _to_jsonable(val, conn)
            _write_object(obj)
            return json_result({"ok": True, "written": True, "applied": applied, "target": t})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_post(
    object_name: str,
    number: str = "",
    uuid: str = "",
    confirm: bool = False,
    target: str = "",
) -> str:
    """Post a document. Requires confirm=true."""
    if not confirm:
        return json_result({"ok": False, "error": "confirm=true required to post", "step": "confirm_post"})
    t = (target or default_target()).strip() or default_target()
    try:
        kind, name = _parse_object(object_name)
        if kind != "document":
            return json_result({"ok": False, "error": "com_post is for Document only"})
        with session(target=t) as conn:
            ref = _find_ref(conn, kind, name, uuid=uuid or None, number=number or None, code=None)
            obj = _get_object(ref)
            _post_object(conn, obj)
            posted = _to_jsonable(getattr(wrap(obj), "Posted", getattr(wrap(obj), "Проведен", None)), conn)
            return json_result({"ok": True, "posted": posted, "target": t})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_unpost(
    object_name: str,
    number: str = "",
    uuid: str = "",
    confirm: bool = False,
    target: str = "",
) -> str:
    """Undo posting. Requires confirm=true."""
    if not confirm:
        return json_result({"ok": False, "error": "confirm=true required to unpost", "step": "confirm_unpost"})
    t = (target or default_target()).strip() or default_target()
    try:
        kind, name = _parse_object(object_name)
        if kind != "document":
            return json_result({"ok": False, "error": "com_unpost is for Document only"})
        with session(target=t) as conn:
            ref = _find_ref(conn, kind, name, uuid=uuid or None, number=number or None, code=None)
            obj = _get_object(ref)
            _unpost_object(conn, obj)
            posted = _to_jsonable(getattr(wrap(obj), "Posted", getattr(wrap(obj), "Проведен", None)), conn)
            return json_result({"ok": True, "posted": posted, "target": t})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


def main() -> None:
    run_mcp(mcp, default_port=18763)


if __name__ == "__main__":
    main()
