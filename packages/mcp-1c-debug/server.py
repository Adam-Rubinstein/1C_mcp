"""
1C HTTP debug MCP (dbgs protocol).

Orient on PavRedAlex/1c-debug-mcp. Requires:
  - dbgs.exe running / debug server URL
  - DEBUG_SERVER_URL (e.g. http://127.0.0.1:1550)
  - optionally DEBUG_INFOBASE_ALIAS

Tools are stubs that talk HTTP JSON to the debug server when available.
Live attach needs IB + configured debug.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-debug")

# In-memory session state for this process
_STATE: dict[str, Any] = {
    "attached": False,
    "sessionId": None,
    "breakpoints": {},
}


def _base() -> str:
    url = env("DEBUG_SERVER_URL", "http://127.0.0.1:1550")
    return (url or "http://127.0.0.1:1550").rstrip("/")


def _http(method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict[str, Any]:
    url = f"{_base()}{path}"
    data = None
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = env("DEBUG_TOKEN") or env("MCP_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {"ok": True, "status": resp.status}
            try:
                return {"ok": True, "status": resp.status, "data": json.loads(raw)}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "raw": raw[:4000]}
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "error": err_body[:2000]}
    except URLError as exc:
        return {"ok": False, "error": str(exc.reason if hasattr(exc, "reason") else exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def debug_status() -> str:
    ping = _http("GET", "/")
    # try common health paths
    if not ping.get("ok"):
        ping = _http("GET", "/health")
    return json_result(
        {
            "debugServerUrl": _base(),
            "serverReachable": bool(ping.get("ok")),
            "serverResponse": ping,
            "attached": _STATE["attached"],
            "sessionId": _STATE["sessionId"],
            "breakpoints": list(_STATE["breakpoints"].values()),
            "note": "Start dbgs.exe / platform debug server before attach. See docs/GUIDE.md.",
        }
    )


@mcp.tool()
def debug_attach(infobase_alias: str | None = None) -> str:
    """Attach to debug session (HTTP API may vary by dbgs build)."""
    alias = infobase_alias or env("DEBUG_INFOBASE_ALIAS") or env("ONEC_REF") or "default"
    session_id = str(uuid.uuid4())
    # Try several known shapes
    for path, body in (
        ("/attach", {"infobase": alias, "sessionId": session_id}),
        ("/api/attach", {"infobase": alias, "sessionId": session_id}),
        ("/debugger/attach", {"target": alias}),
    ):
        resp = _http("POST", path, body)
        if resp.get("ok"):
            _STATE["attached"] = True
            _STATE["sessionId"] = session_id
            return json_result({"ok": True, "sessionId": session_id, "response": resp, "path": path})
    _STATE["attached"] = False
    return json_result(
        {
            "ok": False,
            "error": "Could not attach — debug HTTP API not reachable or incompatible.",
            "hint": "Set DEBUG_SERVER_URL to your dbgs HTTP endpoint. Protocol inspired by PavRedAlex/1c-debug-mcp.",
            "last": resp,
        }
    )


@mcp.tool()
def debug_detach() -> str:
    sid = _STATE.get("sessionId")
    resp = _http("POST", "/detach", {"sessionId": sid})
    _STATE["attached"] = False
    _STATE["sessionId"] = None
    return json_result({"ok": True, "response": resp})


@mcp.tool()
def debug_set_breakpoint(module: str, line: int) -> str:
    """Set breakpoint at module:line (BSL module path as known to debugger)."""
    bp_id = f"{module}:{line}"
    body = {"module": module, "line": line, "sessionId": _STATE.get("sessionId")}
    resp = _http("POST", "/breakpoint", body)
    if not resp.get("ok"):
        resp = _http("POST", "/api/breakpoint", body)
    _STATE["breakpoints"][bp_id] = {"module": module, "line": line, "id": bp_id}
    return json_result({"ok": bool(resp.get("ok")), "breakpoint": _STATE["breakpoints"][bp_id], "response": resp})


@mcp.tool()
def debug_remove_breakpoint(module: str, line: int) -> str:
    bp_id = f"{module}:{line}"
    body = {"module": module, "line": line, "sessionId": _STATE.get("sessionId")}
    resp = _http("POST", "/breakpoint/remove", body)
    _STATE["breakpoints"].pop(bp_id, None)
    return json_result({"ok": True, "response": resp})


@mcp.tool()
def debug_continue() -> str:
    return json_result(_http("POST", "/continue", {"sessionId": _STATE.get("sessionId")}))


@mcp.tool()
def debug_step_over() -> str:
    return json_result(_http("POST", "/stepOver", {"sessionId": _STATE.get("sessionId")}))


@mcp.tool()
def debug_step_into() -> str:
    return json_result(_http("POST", "/stepInto", {"sessionId": _STATE.get("sessionId")}))


@mcp.tool()
def debug_step_out() -> str:
    return json_result(_http("POST", "/stepOut", {"sessionId": _STATE.get("sessionId")}))


@mcp.tool()
def debug_stack() -> str:
    return json_result(_http("GET", f"/stack?sessionId={_STATE.get('sessionId') or ''}"))


@mcp.tool()
def debug_locals() -> str:
    return json_result(_http("GET", f"/locals?sessionId={_STATE.get('sessionId') or ''}"))


@mcp.tool()
def debug_eval(expression: str) -> str:
    """Evaluate expression in current debug frame."""
    return json_result(
        _http(
            "POST",
            "/eval",
            {"expression": expression, "sessionId": _STATE.get("sessionId")},
        )
    )


def main() -> None:
    run_mcp(mcp, default_port=18767)


if __name__ == "__main__":
    main()
