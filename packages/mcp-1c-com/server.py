from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.work_gates import refuse_work_ib_path  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-com")


def _connect():
    """Connect via V83.COMConnector. Requires Windows + 1C COM registration."""
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for COM (pip install pywin32)") from exc

    connector = win32com.client.Dispatch("V83.COMConnector")
    ib = env("ONEC_IB_DEV") or env("ONEC_IB")
    refuse = refuse_work_ib_path(ib)
    if refuse:
        raise RuntimeError(refuse["error"])
    server = env("ONEC_SERVER")
    ref = env("ONEC_REF")
    user = env("ONEC_USER", "") or ""
    password = env("ONEC_PASSWORD", "") or ""
    if ib:
        conn_str = f'File="{ib}";'
    elif server and ref:
        conn_str = f'Srvr="{server}";Ref="{ref}";'
    else:
        raise ValueError("Set ONEC_IB_DEV / ONEC_IB or ONEC_SERVER+ONEC_REF")
    if user:
        conn_str += f'Usr="{user}";'
    conn_str += f'Pwd="{password}";'
    return connector.Connect(conn_str)


@mcp.tool()
def com_status() -> str:
    """Health: COM availability and IB settings (does not open IB unless ping=true)."""
    has_pywin32 = False
    try:
        import win32com.client  # noqa: F401

        has_pywin32 = True
    except ImportError:
        pass
    return json_result(
        {
            "ok": has_pywin32 and bool(env("ONEC_IB") or (env("ONEC_SERVER") and env("ONEC_REF"))),
            "pywin32": has_pywin32,
            "onecIb": env("ONEC_IB"),
            "onecServer": env("ONEC_SERVER"),
            "onecRef": env("ONEC_REF"),
            "platform": "Windows COM (V83.COMConnector)",
        }
    )


@mcp.tool()
def com_ping() -> str:
    """Open COM connection and read Infobase version string if available."""
    try:
        conn = _connect()
        info: dict[str, Any] = {"ok": True, "connected": True}
        try:
            # Metadata.Name / Configuration properties vary by release
            meta = conn.Metadata
            info["configurationName"] = str(meta.Name)
            info["configurationVersion"] = str(getattr(meta, "Version", ""))
        except Exception as exc:  # noqa: BLE001
            info["metaError"] = str(exc)
        return json_result(info)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_query(query_text: str, limit: int = 100) -> str:
    """
    Execute a 1C query (read-only recommended). Returns rows as list of dicts.
    Requires live IB. limit caps rows returned.
    """
    if not query_text.strip():
        return json_result({"ok": False, "error": "query_text is required"})
    try:
        conn = _connect()
        q = conn.NewObject("Query")
        q.Text = query_text
        result = q.Execute()
        selection = result.Choose()
        columns = [str(c.Name) for c in result.Columns]
        rows: list[dict[str, Any]] = []
        while selection.Next() and len(rows) < max(1, limit):
            row: dict[str, Any] = {}
            for col in columns:
                val = selection.Get(result.Columns.IndexOf(col))
                row[col] = _to_jsonable(val)
            rows.append(row)
        return json_result({"ok": True, "columns": columns, "rows": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def com_metadata_find(object_name: str) -> str:
    """Find metadata object by full name, e.g. Document.MyDoc or Документ.MyDoc."""
    from onec_mcp_shared import normalize_object_name

    name = normalize_object_name(object_name)
    try:
        conn = _connect()
        # FindByFullName is available on Metadata
        meta_obj = conn.Metadata.FindByFullName(name)
        if meta_obj is None:
            # try Russian path via original
            meta_obj = conn.Metadata.FindByFullName(object_name)
        if meta_obj is None:
            return json_result({"ok": False, "error": f"Not found: {name}"})
        return json_result(
            {
                "ok": True,
                "fullName": str(getattr(meta_obj, "FullName", name)),
                "name": str(meta_obj.Name),
                "synonym": str(getattr(meta_obj, "Synonym", "")),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


def _to_jsonable(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    try:
        return str(val)
    except Exception:  # noqa: BLE001
        return repr(val)


def main() -> None:
    run_mcp(mcp, default_port=18763)


if __name__ == "__main__":
    main()
