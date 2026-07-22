from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
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
    p = Path(path)
    if not p.is_file():
        return json_result({"ok": False, "error": f"Not found: {path}"})
    roots = _roots()
    resolved = p.resolve()
    if roots and not any(str(resolved).startswith(str(r.resolve())) for r in roots):
        return json_result({"ok": False, "error": "Path is outside allowed CONFIG_DUMP_DIR / REPO_* roots"})
    data = p.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return json_result({"ok": True, "path": str(p), "size": p.stat().st_size, "content": text})


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
