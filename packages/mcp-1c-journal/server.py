from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.work_gates import refuse_work_ib_path  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-journal")


def _connect():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for journal (pip install pywin32)") from exc

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
def journal_status() -> str:
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
            "note": "Reads event log via COM when IB is available.",
        }
    )


@mcp.tool()
def journal_recent(
    hours: int = 24,
    limit: int = 50,
    event: str | None = None,
    level: str | None = None,
) -> str:
    """
    Read recent event log entries via COM EventLog.
    level: Error / Warning / Information / Note (platform-dependent).
    """
    try:
        conn = _connect()
        # Filter: DateStart / DateEnd
        end = datetime.now()
        start = end - timedelta(hours=max(1, hours))
        # Use UnloadEventLog to temp or iterate EventLog
        # Platform COM: EventLog.Unload / GetEventLog
        # Fallback approach: privileged call via Execute if available is unsafe; use EventLogFilter
        filter_obj = conn.NewObject("EventLogFilter")
        filter_obj.StartDate = start
        filter_obj.EndDate = end
        if event:
            try:
                filter_obj.Event = event
            except Exception:  # noqa: BLE001
                pass
        if level:
            try:
                filter_obj.Level = level
            except Exception:  # noqa: BLE001
                pass

        # UnloadEventLog(FileName, Filter) — write to temp XML/table
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="1c-journal-")) / "events.xml"
        try:
            conn.UnloadEventLog(str(tmp), filter_obj)
        except Exception:
            # alternate: GlobalContext method name may be ВыгрузитьЖурналРегистрации
            try:
                conn.ВыгрузитьЖурналРегистрации(str(tmp), filter_obj)
            except Exception as exc2:  # noqa: BLE001
                return json_result(
                    {
                        "ok": False,
                        "error": f"UnloadEventLog failed: {exc2}",
                        "hint": "Requires privileges and COM support for event log unload.",
                    }
                )

        text = tmp.read_text(encoding="utf-8", errors="replace") if tmp.is_file() else ""
        # Return truncated raw + parse simple Event elements if present
        events = _parse_events_xml(text, limit)
        return json_result(
            {
                "ok": True,
                "file": str(tmp),
                "count": len(events),
                "events": events,
                "rawTail": text[-3000:] if not events else "",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


def _parse_events_xml(text: str, limit: int) -> list[dict[str, Any]]:
    import re

    events: list[dict[str, Any]] = []
    # very loose: <Event>...</Event> blocks
    for block in re.findall(r"<Event[\s\S]*?</Event>", text)[:limit]:
        item: dict[str, Any] = {"raw": block[:500]}
        for key in ("Level", "Date", "User", "ApplicationName", "Event", "Comment", "Metadata"):
            m = re.search(rf"<{key}>([^<]*)</{key}>", block)
            if m:
                item[key.lower()] = m.group(1)
        events.append(item)
    return events


def main() -> None:
    run_mcp(mcp, default_port=18766)


if __name__ == "__main__":
    main()
