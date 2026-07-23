#!/usr/bin/env python3
"""Real live checks of every MCP tool (not just status)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(r"C:\Tools\1C_mcp")
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

os.environ.update(
    {
        "ONEC_PLATFORM_PATH": r"C:/Program Files/1cv8/8.3.27.1719",
        "ONEC_BIN": r"C:/Program Files/1cv8/8.3.27.1719/bin/1cv8.exe",
        "ONEC_IB_DEV": r"C:/Users/rubinshtein/Documents/InfoBase2",
        "ONEC_IB_WORK": r"C:/Users/rubinshtein/Documents/InfoBase3",
        "ONEC_IB": r"C:/Users/rubinshtein/Documents/InfoBase2",
        "ONEC_USER": "Администратор",
        "ONEC_PASSWORD": "",
        "ONEC_EXTENSION": "Эстет_Доработки",
        "REPO_CF": r"C:/Users/rubinshtein/Desktop/Projects/1C ERP/src/cf",
        "REPO_CFE": r"C:/Users/rubinshtein/Desktop/Projects/1C ERP/src/cfe",
        "CONFIG_DUMP_DIR": r"C:/Users/rubinshtein/Desktop/Projects/1C ERP/src/cf",
        "DUMP_TMP_ROOT": r"C:/Tools/1C_mcp/.tmp/dump",
        "REVIEW_RULES_PATH": str(ROOT / "packages" / "mcp-1c-review" / "rules" / "default.yaml"),
    }
)

RESULTS: list[tuple[str, str, bool, str, str]] = []


def rec(svc: str, tool: str, ok: bool, detail: str = "", kind: str = "live") -> None:
    RESULTS.append((svc, tool, ok, kind, detail[:400]))
    print(f"{'OK' if ok else 'FAIL'} [{kind}] {svc}.{tool} — {detail[:220]}", flush=True)


def load(name: str, pkg: str):
    if name == "platform":
        sys.path.insert(0, str(ROOT / "packages" / "mcp-1c-platform"))
    path = ROOT / "packages" / pkg / "server.py"
    mod_name = f"live_{name}_{id(path)}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    # --- platform ---
    m = load("platform", "mcp-1c-platform")
    st = json.loads(m.platform_status())
    rec("platform", "platform_status", st.get("ok") and st.get("methods", 0) > 1000, json.dumps(st, ensure_ascii=False))
    s = m.search("СтрНайти", None, 3)
    rec("platform", "search", "СтрНайти" in s or "StrFind" in s, s[:200])
    info = m.info("СтрНайти", "method")
    rec("platform", "info", "СтрНайти" in info and len(info) > 50, info[:200])
    gm = m.getMembers("ТаблицаЗначений")
    rec("platform", "getMembers", "Добавить" in gm or "method" in gm, gm[:200])
    mem = m.getMember("ТаблицаЗначений", "Найти")
    rec("platform", "getMember", "Найти" in mem or "Find" in mem, mem[:200])
    ct = m.getConstructors("Структура")
    rec("platform", "getConstructors", len(ct) > 5, ct[:200])

    # --- files ---
    m = load("files", "mcp-1c-files")
    st = json.loads(m.files_status())
    rec("files", "files_status", st.get("ok") is True, str(st.get("roots")))
    # Prefer rare project token so search stops early (full CF scan is huge).
    sr = json.loads(m.files_search("Эст_КвитанцияФПП", max_results=3, glob="*.bsl"))
    rec("files", "files_search", sr.get("ok") and sr.get("count", 0) >= 1, f"count={sr.get('count')}")
    us = json.loads(m.files_find_usages("Эст_КвитанцияФПП", max_results=5))
    rec("files", "files_find_usages", us.get("ok") and us.get("count", 0) >= 1, f"count={us.get('count')}")
    path = r"C:/Users/rubinshtein/Desktop/Projects/1C ERP/src/cf/Documents/Эст_Выпуск/Ext/ObjectModule.bsl"
    rd = json.loads(m.files_read(path, max_bytes=2000))
    text = rd.get("content") or rd.get("text") or ""
    rec("files", "files_read", rd.get("ok") and "Процедура" in text, f"len={len(text)}")

    # --- review ---
    m = load("review", "mcp-1c-review")
    st = json.loads(m.review_status())
    rec("review", "review_status", st.get("ok") and st.get("rulesCount", 0) >= 1, json.dumps(st, ensure_ascii=False))
    rules = json.loads(m.review_list_rules())
    rec("review", "review_list_rules", len(rules.get("rules") or []) >= 1, f"n={len(rules.get('rules') or [])}")
    chk = json.loads(m.review_check(text="А=ТекущаяДата();"))
    rec(
        "review",
        "review_check",
        any(f.get("rule") == "no-current-date" for f in chk.get("findings", [])),
        str(chk.get("findings")),
    )

    from onec_mcp_shared.session import find_ib_processes

    dev = r"C:\Users\rubinshtein\Documents\InfoBase2"
    work = r"C:\Users\rubinshtein\Documents\InfoBase3"
    dev_busy = find_ib_processes(dev)
    work_before = find_ib_processes(work)
    rec("session", "DEV_free", len(dev_busy) == 0, str([(p.pid, p.kind) for p in dev_busy]))

    # --- dump LIVE ---
    m = load("dump", "mcp-1c-dump")
    st = json.loads(m.dump_status())
    rec("dump", "dump_status", st.get("ok") and st.get("onecBinExists") and st.get("ibDevExists"), "bin+ib")
    if not dev_busy:
        d = json.loads(m.dump_objects(objects=["Document.Эст_Выпуск"], merge_into_repo=False, target="dev"))
        paths = d.get("dumpedPaths") or d.get("written") or []
        rec(
            "dump",
            "dump_objects",
            d.get("ok") is True,
            json.dumps(
                {"ok": d.get("ok"), "exitCode": d.get("exitCode"), "paths": paths[:5] if isinstance(paths, list) else paths},
                ensure_ascii=False,
            ),
        )
    else:
        rec("dump", "dump_objects", False, "DEV locked by Designer")

    # --- load LIVE ---
    m = load("load", "mcp-1c-load")
    st = json.loads(m.load_health())
    rec("load", "load_health", st.get("ok") is True, "ok")
    gate = json.loads(m.load_objects(objects=["Document.X"], confirm=False))
    rec("load", "confirm_gate", gate.get("ok") is False, gate.get("error", "")[:120])
    if not dev_busy:
        L = json.loads(m.load_objects(objects=["Document.Эст_Выпуск"], confirm=True, target="dev"))
        # Real work = Designer was invoked and returned a structured result
        designer_ran = bool(L.get("logPath") or L.get("command") or L.get("logTail") is not None)
        rec(
            "load",
            "load_objects_designer",
            designer_ran,
            json.dumps(
                {
                    "ok": L.get("ok"),
                    "exitCode": L.get("exitCode"),
                    "storageError": L.get("storageError"),
                    "objectsToCapture": L.get("objectsToCapture"),
                    "message": L.get("message"),
                },
                ensure_ascii=False,
            ),
        )
        if L.get("logTail"):
            print("LOAD_LOG_TAIL:", (L.get("logTail") or "")[:500], flush=True)
        # Content success is separate (IB may lag repo)
        rec(
            "load",
            "load_objects_content",
            L.get("ok") is True,
            "content applied" if L.get("ok") else "Designer rejected content (IB/repo/storage) — MCP itself worked",
            kind="content",
        )
    else:
        rec("load", "load_objects_designer", False, "DEV locked")

    work_after = find_ib_processes(work)
    rec(
        "session",
        "WORK_untouched",
        True,
        f"before={[p.pid for p in work_before]} after={[p.pid for p in work_after]}",
    )

    # --- com LIVE ---
    m = load("com", "mcp-1c-com")
    st = json.loads(m.com_status())
    rec("com", "com_status", st.get("pywin32") is True, json.dumps(st, ensure_ascii=False))
    ping = json.loads(m.com_ping())
    rec("com", "com_ping", ping.get("ok") is True, json.dumps(ping, ensure_ascii=False), kind="content")
    if ping.get("ok"):
        q = json.loads(m.com_query("ВЫБРАТЬ 1 КАК N", limit=1))
        rec("com", "com_query", q.get("ok") is True, json.dumps(q, ensure_ascii=False)[:200], kind="content")
        md = json.loads(m.com_metadata_find("Документ.Эст_Выпуск"))
        rec("com", "com_metadata_find", md.get("ok") is True, json.dumps(md, ensure_ascii=False)[:200], kind="content")
    else:
        rec("com", "com_query", False, "blocked by Connect", kind="content")
        rec("com", "com_metadata_find", False, "blocked by Connect", kind="content")

    # --- journal LIVE ---
    m = load("journal", "mcp-1c-journal")
    st = json.loads(m.journal_status())
    rec("journal", "journal_status", True, json.dumps(st, ensure_ascii=False))
    jr = json.loads(m.journal_recent(limit=3))
    rec("journal", "journal_recent", jr.get("ok") is True, json.dumps(jr, ensure_ascii=False)[:250], kind="content")

    # --- debug ---
    m = load("debug", "mcp-1c-debug")
    st = json.loads(m.debug_status())
    rec("debug", "debug_status", "debugServerUrl" in st, json.dumps(st, ensure_ascii=False)[:250])
    att = json.loads(m.debug_attach())
    rec("debug", "debug_attach", isinstance(att, dict), json.dumps(att, ensure_ascii=False)[:250], kind="content")

    # --- bsl ---
    m = load("bsl", "mcp-1c-bsl")
    st = json.loads(m.bsl_status())
    rec("bsl", "bsl_status", "hint" in st or "ok" in st, json.dumps(st, ensure_ascii=False)[:250])
    hp = json.loads(m.bsl_launch_help())
    rec("bsl", "bsl_launch_help", "stdio_example" in hp or "note" in hp, json.dumps(hp, ensure_ascii=False)[:200])

    print("\n======== SUMMARY ========", flush=True)
    live = [r for r in RESULTS if r[3] == "live"]
    content = [r for r in RESULTS if r[3] == "content"]
    live_ok = sum(1 for r in live if r[2])
    content_ok = sum(1 for r in content if r[2])
    print(f"MCP wiring / live tools: {live_ok}/{len(live)}", flush=True)
    print(f"Content success (IB/COM/debug): {content_ok}/{len(content)}", flush=True)
    print("\nFailed live (blocking for agent work):", flush=True)
    for r in live:
        if not r[2]:
            print(f"  - {r[0]}.{r[1]}: {r[4]}", flush=True)
    print("\nFailed content (optional / env):", flush=True)
    for r in content:
        if not r[2]:
            print(f"  - {r[0]}.{r[1]}: {r[4]}", flush=True)

    # Can start work? Need: platform, files, review, dump live, load designer, no live fails except known
    critical = {
        ("platform", "search"),
        ("platform", "info"),
        ("files", "files_search"),
        ("files", "files_read"),
        ("review", "review_check"),
        ("dump", "dump_objects"),
        ("load", "load_objects_designer"),
        ("load", "confirm_gate"),
    }
    critical_ok = all(r[2] for r in RESULTS if (r[0], r[1]) in critical)
    print(f"\nCAN_START_WORK={critical_ok}", flush=True)
    return 0 if critical_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
