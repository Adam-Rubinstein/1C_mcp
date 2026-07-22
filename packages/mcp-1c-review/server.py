from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import env, json_result, load_env_files  # noqa: E402
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-review")


def _rules_path() -> Path:
    p = env("REVIEW_RULES_PATH")
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "rules" / "default.yaml"


def _load_rules() -> list[dict[str, Any]]:
    path = _rules_path()
    if not path.is_file():
        return []
    try:
        import yaml  # type: ignore
    except ImportError:
        # minimal YAML-ish: not available — return empty with error later
        return [{"_error": "PyYAML not installed"}]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = data.get("rules") or data
    if isinstance(rules, list):
        return rules
    return []


@mcp.tool()
def review_status() -> str:
    rules = _load_rules()
    err = None
    if rules and isinstance(rules[0], dict) and rules[0].get("_error"):
        err = rules[0]["_error"]
        rules = []
    return json_result(
        {
            "ok": err is None and bool(rules),
            "rulesPath": str(_rules_path()),
            "rulesCount": len(rules),
            "error": err,
        }
    )


@mcp.tool()
def review_list_rules() -> str:
    rules = _load_rules()
    if rules and rules[0].get("_error"):
        return json_result({"ok": False, "error": rules[0]["_error"]})
    summary = [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "severity": r.get("severity", "warning"),
            "scope": r.get("scope"),
        }
        for r in rules
        if isinstance(r, dict)
    ]
    return json_result({"ok": True, "rules": summary})


@mcp.tool()
def review_check(paths: list[str] | None = None, text: str | None = None) -> str:
    """
    Run checklist rules against files or pasted text.
    Rules support: pattern (regex), severity, message, file_glob.
    """
    import re

    rules = _load_rules()
    if rules and rules[0].get("_error"):
        return json_result({"ok": False, "error": rules[0]["_error"]})

    files: list[tuple[str, str]] = []
    if text is not None:
        # Use .bsl suffix so file_glob filters (e.g. *.bsl) still apply to snippets
        files.append(("<snippet>.bsl", text))
    for p in paths or []:
        path = Path(p)
        if path.is_file():
            files.append((str(path), path.read_text(encoding="utf-8", errors="replace")))

    if not files:
        # default: scan CONFIG_DUMP_DIR sample? refuse
        return json_result({"ok": False, "error": "Pass paths=[] or text= for review_check"})

    findings: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        pat = rule.get("pattern")
        if not pat:
            continue
        try:
            rx = re.compile(pat, re.MULTILINE)
        except re.error as exc:
            findings.append({"rule": rule.get("id"), "error": str(exc)})
            continue
        file_glob = (rule.get("file_glob") or "*").lower()
        for path, content in files:
            if file_glob != "*" and not _path_matches_glob(path, file_glob):
                continue
            for m in rx.finditer(content):
                line = content.count("\n", 0, m.start()) + 1
                findings.append(
                    {
                        "rule": rule.get("id"),
                        "title": rule.get("title"),
                        "severity": rule.get("severity", "warning"),
                        "message": rule.get("message") or rule.get("title"),
                        "path": path,
                        "line": line,
                        "match": m.group(0)[:200],
                    }
                )
    return json_result({"ok": True, "findings": findings, "count": len(findings)})


def _glob_suffixes(g: str) -> list[str]:
    if g.startswith("*."):
        return [g[1:]]
    return [g]


def _path_matches_glob(path: str, file_glob: str) -> bool:
    g = file_glob.lower()
    p = path.lower().replace("\\", "/")
    if g == "*":
        return True
    if g.startswith("*.") and p.endswith(g[1:]):
        return True
    if g in p:
        return True
    try:
        return Path(path).match(file_glob)
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    run_mcp(mcp, default_port=18765)


if __name__ == "__main__":
    main()
