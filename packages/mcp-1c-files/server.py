from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.bsl_callgraph import (  # noqa: E402
    graph_is_stale,
    load_graph,
    query_edges,
    rebuild_graph,
    save_graph,
)
from onec_mcp_shared.bsl_units import extract_unit, outline  # noqa: E402
from onec_mcp_shared.work_gates import is_forbidden_secret_path, path_is_under  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-files")


def _roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("CONFIG_DUMP_DIR", "REPO_CF", "REPO_CFE"):
        v = env(key)
        if v:
            p = Path(v)
            if p.is_dir():
                roots.append(p)
    # unique
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        s = str(r.resolve())
        if s not in seen:
            seen.add(s)
            out.append(r)
    return out


def _resolve_under_roots(path: str) -> tuple[Path | None, str | None]:
    p = Path(path)
    if not p.is_file():
        return None, f"Not found: {path}"
    if is_forbidden_secret_path(p):
        return None, f"Refusing to read secret path: {p.name}"
    roots = _roots()
    if not roots:
        return None, "Set CONFIG_DUMP_DIR and/or REPO_CF / REPO_CFE"
    resolved = p.resolve()
    if not any(path_is_under(resolved, r) for r in roots):
        return None, "Path is outside allowed CONFIG_DUMP_DIR / REPO_* roots"
    return resolved, None


def _graph_cache_path() -> Path:
    raw = env("DUMP_TMP_ROOT") or env("ONEC_GRAPH_CACHE") or ""
    if raw:
        return Path(raw) / "bsl-callgraph.json"
    return Path.cwd() / ".tmp" / "bsl-callgraph.json"


def _parse_prefixes(prefixes: str = "") -> list[str] | None:
    if prefixes.strip():
        return [p.strip() for p in prefixes.split(",") if p.strip()]
    return None


@mcp.tool()
def files_status() -> str:
    roots = _roots()
    return json_result(
        {
            "ok": bool(roots),
            "roots": [str(r) for r in roots],
            "configDumpDir": env("CONFIG_DUMP_DIR"),
            "repoCf": env("REPO_CF"),
            "repoCfe": env("REPO_CFE"),
        }
    )


@mcp.tool()
def files_search(
    pattern: str,
    glob: str = "*.{bsl,xml,mdo,txt,md}",
    max_results: int = 50,
    case_insensitive: bool = True,
) -> str:
    """Search text in config dump roots (regex)."""
    roots = _roots()
    if not roots:
        return json_result({"ok": False, "error": "Set CONFIG_DUMP_DIR and/or REPO_CF / REPO_CFE"})
    try:
        flags = re.IGNORECASE if case_insensitive else 0
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return json_result({"ok": False, "error": f"Invalid regex: {exc}"})

    # expand simple brace globs minimally: *.{bsl,xml} -> multiple suffixes
    suffixes = _parse_glob_suffixes(glob)
    hits: list[dict] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if suffixes and path.suffix.lower() not in suffixes and not _match_name_glob(path, glob):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    hits.append(
                        {
                            "path": str(path),
                            "rel": str(path.relative_to(root)).replace("\\", "/"),
                            "root": str(root),
                            "line": i,
                            "text": line[:400],
                        }
                    )
                    if len(hits) >= max_results:
                        return json_result({"ok": True, "count": len(hits), "hits": hits, "truncated": True})
    return json_result({"ok": True, "count": len(hits), "hits": hits, "truncated": False})


@mcp.tool()
def files_find_usages(symbol: str, max_results: int = 50) -> str:
    """Find references to a symbol (identifier) in BSL/XML dump."""
    if not symbol.strip():
        return json_result({"ok": False, "error": "symbol is required"})
    # word-ish boundary for BSL Cyrillic/Latin
    pat = rf"(?<![\wА-Яа-яЁё]){re.escape(symbol)}(?![\wА-Яа-яЁё])"
    return files_search(pat, glob="*.{bsl,xml}", max_results=max_results, case_insensitive=False)


@mcp.tool()
def files_read(path: str, max_bytes: int = 200_000) -> str:
    """Read a file under allowed dump roots."""
    resolved, err = _resolve_under_roots(path)
    if err:
        return json_result({"ok": False, "error": err, "stop": True})
    assert resolved is not None
    data = resolved.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return json_result({"ok": True, "path": str(resolved), "size": resolved.stat().st_size, "content": text})


@mcp.tool()
def files_list_procedures(path: str) -> str:
    """List procedures/functions in a BSL module (outline without bodies). Prefer over full files_read for large modules."""
    resolved, err = _resolve_under_roots(path)
    if err:
        return json_result({"ok": False, "error": err, "stop": True})
    assert resolved is not None
    if resolved.suffix.lower() != ".bsl":
        return json_result({"ok": False, "error": "Only .bsl modules are supported"})
    text = resolved.read_text(encoding="utf-8", errors="replace")
    units = outline(text)
    return json_result(
        {
            "ok": True,
            "path": str(resolved),
            "count": len(units),
            "units": units,
            "hint": "Use files_read_procedure(path, name) for one unit body",
        }
    )


@mcp.tool()
def files_read_procedure(path: str, name: str) -> str:
    """Read one procedure/function body from a BSL module by name."""
    if not name.strip():
        return json_result({"ok": False, "error": "name is required"})
    resolved, err = _resolve_under_roots(path)
    if err:
        return json_result({"ok": False, "error": err, "stop": True})
    assert resolved is not None
    if resolved.suffix.lower() != ".bsl":
        return json_result({"ok": False, "error": "Only .bsl modules are supported"})
    text = resolved.read_text(encoding="utf-8", errors="replace")
    found = extract_unit(text, name)
    if found is None:
        names = [u["name"] for u in outline(text)[:40]]
        return json_result(
            {
                "ok": False,
                "error": f"Unit not found: {name}",
                "availableSample": names,
            }
        )
    unit, body = found
    return json_result(
        {
            "ok": True,
            "path": str(resolved),
            "unit": {
                "kind": unit.kind,
                "name": unit.name,
                "start_line": unit.start_line,
                "end_line": unit.end_line,
                "signature_line": unit.signature_line,
            },
            "content": body,
        }
    )


@mcp.tool()
def files_outline(path: str) -> str:
    """Alias of files_list_procedures — compact module outline."""
    return files_list_procedures(path)


@mcp.tool()
def graph_rebuild(prefixes: str = "") -> str:
    """Rebuild BSL call graph for modules matching ONEC_VENDOR_PREFIXES or prefixes arg (comma-separated)."""
    roots = _roots()
    if not roots:
        return json_result({"ok": False, "error": "Set CONFIG_DUMP_DIR and/or REPO_CF / REPO_CFE"})
    prefs = _parse_prefixes(prefixes)
    index = rebuild_graph(roots, prefixes=prefs)
    cache = _graph_cache_path()
    save_graph(index, cache)
    return json_result(
        {
            "ok": True,
            "cache": str(cache),
            "builtAtIso": index.get("builtAtIso"),
            "prefixes": index.get("prefixes"),
            "moduleCount": index.get("moduleCount"),
            "edgeCount": index.get("edgeCount"),
        }
    )


@mcp.tool()
def graph_status() -> str:
    """Call-graph cache age and stale flag vs BSL mtimes."""
    roots = _roots()
    cache = _graph_cache_path()
    index = load_graph(cache)
    if index is None:
        return json_result({"ok": True, "exists": False, "cache": str(cache), "stale": True})
    stale = graph_is_stale(index, roots) if roots else True
    return json_result(
        {
            "ok": True,
            "exists": True,
            "cache": str(cache),
            "stale": stale,
            "builtAtIso": index.get("builtAtIso"),
            "prefixes": index.get("prefixes"),
            "moduleCount": index.get("moduleCount"),
            "edgeCount": index.get("edgeCount"),
        }
    )


@mcp.tool()
def graph_callers(unit: str, limit: int = 50) -> str:
    """Who calls this procedure/function name (from cached graph). Rebuild if stale."""
    cache = _graph_cache_path()
    index = load_graph(cache)
    if index is None:
        return json_result({"ok": False, "error": "No graph cache — call graph_rebuild first", "cache": str(cache)})
    roots = _roots()
    stale = graph_is_stale(index, roots) if roots else False
    edges = query_edges(index, unit=unit, direction="callers", limit=limit)
    return json_result({"ok": True, "unit": unit, "stale": stale, "count": len(edges), "edges": edges})


@mcp.tool()
def graph_callees(unit: str, limit: int = 50) -> str:
    """What this procedure/function calls (from cached graph)."""
    cache = _graph_cache_path()
    index = load_graph(cache)
    if index is None:
        return json_result({"ok": False, "error": "No graph cache — call graph_rebuild first", "cache": str(cache)})
    roots = _roots()
    stale = graph_is_stale(index, roots) if roots else False
    edges = query_edges(index, unit=unit, direction="callees", limit=limit)
    return json_result({"ok": True, "unit": unit, "stale": stale, "count": len(edges), "edges": edges})


def _parse_glob_suffixes(glob_pat: str) -> set[str] | None:
    # *.{bsl,xml,mdo} or *.bsl
    m = re.search(r"\.\{([^}]+)\}", glob_pat)
    if m:
        return {"." + x.strip().lower() for x in m.group(1).split(",")}
    if glob_pat.startswith("*.") and "{" not in glob_pat:
        return {glob_pat[1:].lower()}
    return None


def _match_name_glob(path: Path, glob_pat: str) -> bool:
    # fallback: always allow if complex glob
    return True


def main() -> None:
    run_mcp(mcp, default_port=18764)


if __name__ == "__main__":
    main()
