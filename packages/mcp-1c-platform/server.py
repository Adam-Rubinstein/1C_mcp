from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

from platform_ctx.index import (  # noqa: E402
    find_one,
    load_index,
    read_help,
    search as search_api,
    type_members,
)

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env", Path(_ROOT).parent / ".env")

mcp = make_mcp("1c-platform")

_INDEX = None
_LOAD_ERROR: str | None = None


def _index():
    global _INDEX, _LOAD_ERROR
    if _INDEX is not None:
        return _INDEX
    if _LOAD_ERROR:
        raise RuntimeError(_LOAD_ERROR)
    path = env("ONEC_PLATFORM_PATH")
    if not path:
        _LOAD_ERROR = "Set ONEC_PLATFORM_PATH to 1cv8 version directory"
        raise RuntimeError(_LOAD_ERROR)
    try:
        _INDEX = load_index(Path(path))
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = str(exc)
        raise
    return _INDEX


@mcp.tool()
def platform_status() -> str:
    """Health: platform path and index sizes."""
    path = env("ONEC_PLATFORM_PATH")
    try:
        idx = _index()
        return json_result(
            {
                "ok": True,
                "platformPath": path,
                "methods": len(idx.methods),
                "properties": len(idx.properties),
                "types": len(idx.types),
                "engine": "python-hbk",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "platformPath": path, "error": str(exc)})


@mcp.tool()
def search(query: str, type: str | None = None, limit: int = 10) -> str:
    """
    Search platform API (methods, properties, types).
    type: method | property | type | null for all.
    """
    try:
        idx = _index()
        lim = max(1, min(int(limit or 10), 50))
        hits = search_api(idx, query, type, lim)
        lines = [f"**Search:** `{query}`" + (f" (type={type})" if type else ""), ""]
        for i, d in enumerate(hits, 1):
            lines.append(f"{i}. **{d.name}** (`{d.kind}`)" + (f" — {d.name_en}" if d.name_en and d.name_en != d.name else ""))
        if not hits:
            lines.append("_Nothing found_")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def info(name: str, type: str) -> str:
    """Details for API element. type: method | property | type."""
    try:
        idx = _index()
        d = find_one(idx, name, type)
        if not d:
            return f"Not found: {type} `{name}`"
        help_text = read_help(idx, d.html_path)
        d.snippet = help_text
        return d.to_markdown()
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def getMember(typeName: str, memberName: str) -> str:
    """Member (method/property) of a platform type."""
    try:
        idx = _index()
        members = type_members(idx, typeName)
        for m in members:
            if m.name.casefold() == memberName.casefold() or m.name_en.casefold() == memberName.casefold():
                m.snippet = read_help(idx, m.html_path)
                return m.to_markdown()
        # global fallback
        for kind in ("method", "property"):
            d = find_one(idx, memberName, kind)
            if d:
                d.snippet = read_help(idx, d.html_path)
                return d.to_markdown()
        return f"Member `{memberName}` not found on type `{typeName}`"
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def getMembers(typeName: str) -> str:
    """All methods and properties of a type."""
    try:
        idx = _index()
        members = type_members(idx, typeName)
        if not members:
            # if type exists, still report
            t = find_one(idx, typeName, "type")
            if not t:
                return f"Type not found: `{typeName}`"
            return f"Type `{typeName}` found, but no members indexed in TOC."
        lines = [f"## Members of `{typeName}`", ""]
        for m in members:
            lines.append(f"- **{m.name}** (`{m.kind}`)")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


@mcp.tool()
def getConstructors(typeName: str) -> str:
    """Constructors for a type (from indexed help pages)."""
    try:
        idx = _index()
        # constructors stored as methods under type folder with ctor path
        members = [
            m
            for m in type_members(idx, typeName)
            if "ctor" in m.html_path.lower() or "constructor" in m.html_path.lower()
        ]
        if not members:
            # try search
            members = [d for d in search_api(idx, typeName, "method", 20) if "ctor" in d.html_path.lower()]
        if not members:
            return f"No constructors indexed for `{typeName}`"
        lines = [f"## Constructors of `{typeName}`", ""]
        for m in members:
            lines.append(m.to_markdown())
            lines.append("")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc)})


def main() -> None:
    # warm index at startup for faster first tool call
    try:
        _index()
    except Exception:
        pass
    run_mcp(mcp, default_port=18760)


if __name__ == "__main__":
    main()
