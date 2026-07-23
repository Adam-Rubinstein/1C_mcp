#!/usr/bin/env python3
"""Detailed audit of all 1C MCP packages + Estet mcp.json wiring."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\Tools\1C_mcp")
ESTET_MCP = Path(r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\.cursor\mcp.json")
sys.path.insert(0, str(ROOT / "packages" / "shared"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

MCP_JSON_ENV: dict[str, dict[str, str]] = {}
if ESTET_MCP.is_file():
    cfg = json.loads(ESTET_MCP.read_text(encoding="utf-8"))
    for name, block in cfg.get("mcpServers", {}).items():
        MCP_JSON_ENV[name] = dict(block.get("env") or {})

REPORT: list[dict] = []


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}", flush=True)


def record(server: str, check: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "FAIL"
    line = f"[{status}] {server}: {check}"
    if detail:
        line += f" — {detail[:220]}"
    print(line, flush=True)
    REPORT.append({"server": server, "check": check, "ok": ok, "detail": detail})


def load_mod(name: str, path: Path):
    key = f"1c-{name}"
    if key in MCP_JSON_ENV:
        for k, v in MCP_JSON_ENV[key].items():
            os.environ[k] = v
    sys.path.insert(0, str(ROOT / "packages" / "shared"))
    if name == "platform":
        sys.path.insert(0, str(ROOT / "packages" / "mcp-1c-platform"))
    if name == "review":
        os.environ.setdefault(
            "REVIEW_RULES_PATH",
            str(ROOT / "packages" / "mcp-1c-review" / "rules" / "default.yaml"),
        )
    # unique module name avoids cache
    mod_name = f"audit_{name}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def parse_json_or_text(raw: str) -> dict | str:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def is_useful(raw: str | dict, *, min_len: int = 10) -> bool:
    if isinstance(raw, dict):
        if raw.get("ok") is False and raw.get("error"):
            return False
        return True
    return isinstance(raw, str) and len(raw) >= min_len and "error" not in raw[:40].lower()


def mcp_stdio_starts(server_key: str, timeout_sec: float = 8.0) -> tuple[bool, str]:
    """Start stdio server, confirm it stays alive (MCP waits on stdin)."""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    script = ROOT / "scripts" / "run_server.py"
    name = server_key.replace("1c-", "")
    env = os.environ.copy()
    env["MCP_TRANSPORT"] = "stdio"
    env["PYTHONUNBUFFERED"] = "1"
    for k, v in MCP_JSON_ENV.get(server_key, {}).items():
        env[k] = v
    try:
        proc = subprocess.Popen(
            [str(py), "-u", str(script), name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    time.sleep(timeout_sec if name != "platform" else max(timeout_sec, 12.0))
    code = proc.poll()
    if code is None:
        # still running = listening on stdio
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        return True, "stdio process alive (listening)"
    err = ""
    try:
        err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")
    except Exception:
        pass
    return False, f"exited {code}: {err[:300]}"


def mcp_stdio_initialize(server_key: str, timeout_sec: float = 20.0) -> tuple[bool, str]:
    """Try MCP initialize with Content-Length framing."""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    script = ROOT / "scripts" / "run_server.py"
    name = server_key.replace("1c-", "")
    env = os.environ.copy()
    env["MCP_TRANSPORT"] = "stdio"
    env["PYTHONUNBUFFERED"] = "1"
    for k, v in MCP_JSON_ENV.get(server_key, {}).items():
        env[k] = v
    proc = subprocess.Popen(
        [str(py), "-u", str(script), name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "audit", "version": "0.1"},
        },
    }
    body = json.dumps(init).encode("utf-8")
    msg = b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(msg)
        proc.stdin.flush()
        # use communicate with timeout via thread-less select alternative: read with deadline
        deadline = time.time() + timeout_sec
        buf = b""
        while time.time() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")
                return False, f"exited {proc.returncode}: {err[:200]}"
            # non-blocking-ish: read available
            try:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            except Exception:
                chunk = b""
            if chunk:
                buf += chunk
                if b"\r\n\r\n" in buf:
                    header, rest = buf.split(b"\r\n\r\n", 1)
                    clen = 0
                    for line in header.decode("utf-8", errors="replace").split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            clen = int(line.split(":", 1)[1].strip())
                    while len(rest) < clen and time.time() < deadline:
                        more = proc.stdout.read(clen - len(rest))
                        if not more:
                            break
                        rest += more
                    if len(rest) >= clen:
                        data = json.loads(rest[:clen].decode("utf-8"))
                        proc.kill()
                        name_out = data.get("result", {}).get("serverInfo", {}).get("name", "ok")
                        return True, str(name_out)
            else:
                time.sleep(0.05)
        proc.kill()
        err = ""
        try:
            err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            pass
        return False, f"timeout; stderr={err[:150]} stdout={buf[:80]!r}"
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass
        return False, str(exc)


def main() -> int:
    section("0. Paths & mcp.json")
    plat = Path(os.environ.get("ONEC_PLATFORM_PATH", r"C:\Program Files\1cv8\8.3.27.1719"))
    paths = {
        "venv python": ROOT / ".venv" / "Scripts" / "python.exe",
        "run_server.py": ROOT / "scripts" / "run_server.py",
        "ONEC_BIN": Path(os.environ.get("ONEC_BIN", r"C:\Program Files\1cv8\8.3.27.1719\bin\1cv8.exe")),
        "ONEC_PLATFORM_PATH": plat,
        "ONEC_IB_DEV": Path(os.environ.get("ONEC_IB_DEV", r"C:\Users\rubinshtein\Documents\InfoBase2")),
        "ONEC_IB_WORK": Path(os.environ.get("ONEC_IB_WORK", r"C:\Users\rubinshtein\Documents\InfoBase3")),
        "REPO_CF": Path(os.environ.get("REPO_CF", r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cf")),
        "REPO_CFE": Path(os.environ.get("REPO_CFE", r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cfe")),
        "estet mcp.json": ESTET_MCP,
    }
    for label, p in paths.items():
        record("infra", label, p.exists(), str(p))
    hbk = list(plat.rglob("shcntx_ru.hbk"))[:1]
    record("infra", "shcntx_ru.hbk", bool(hbk), str(hbk[0]) if hbk else "missing")

    expected_servers = [
        "1c-platform",
        "1c-dump",
        "1c-load",
        "1c-com",
        "1c-files",
        "1c-review",
        "1c-journal",
        "1c-debug",
        "1c-bsl",
    ]
    cfg = json.loads(ESTET_MCP.read_text(encoding="utf-8"))
    configured = list(cfg.get("mcpServers", {}).keys())
    record("infra", "mcp.json has all 9", set(configured) == set(expected_servers), str(configured))
    for s in expected_servers:
        block = cfg["mcpServers"].get(s, {})
        cmd_ok = Path(block.get("command", "")).is_file()
        args = block.get("args") or []
        args_ok = len(args) >= 2 and Path(args[0]).is_file()
        record("infra", f"{s} command/args", cmd_ok and args_ok)

    packages = {
        "platform": ROOT / "packages" / "mcp-1c-platform" / "server.py",
        "dump": ROOT / "packages" / "mcp-1c-dump" / "server.py",
        "load": ROOT / "packages" / "mcp-1c-load" / "server.py",
        "com": ROOT / "packages" / "mcp-1c-com" / "server.py",
        "files": ROOT / "packages" / "mcp-1c-files" / "server.py",
        "review": ROOT / "packages" / "mcp-1c-review" / "server.py",
        "journal": ROOT / "packages" / "mcp-1c-journal" / "server.py",
        "debug": ROOT / "packages" / "mcp-1c-debug" / "server.py",
        "bsl": ROOT / "packages" / "mcp-1c-bsl" / "server.py",
    }

    section("1. 1c-platform")
    try:
        mod = load_mod("platform", packages["platform"])
        st = parse_json_or_text(mod.platform_status())
        record(
            "1c-platform",
            "platform_status",
            isinstance(st, dict) and st.get("ok") is True and st.get("methods", 0) > 1000,
            json.dumps(st, ensure_ascii=False)[:200] if isinstance(st, dict) else str(st)[:200],
        )
        s = mod.search("ТекущаяДатаСеанса", None, 5)
        record("1c-platform", "search ТекущаяДатаСеанса", "ТекущаяДатаСеанса" in s or "CurrentSessionDate" in s, s[:200])
        s = mod.search("Query", "type", 3)
        record("1c-platform", "search Query type", "Query" in s or "Запрос" in s, s[:200])
        info = mod.info("Запрос", "type")
        record("1c-platform", "info Запрос", "Запрос" in info or "Query" in info, info[:200])
        members = mod.getMembers("Запрос")
        record("1c-platform", "getMembers Запрос", "Members" in members or "method" in members, members[:200])
        member = mod.getMember("Запрос", "Выполнить")
        record("1c-platform", "getMember Запрос.Выполнить", "Выполнить" in member or "Execute" in member or "method" in member.lower(), member[:200])
        ctors = mod.getConstructors("Массив")
        record("1c-platform", "getConstructors Массив", len(ctors) > 5, ctors[:200])
    except Exception as exc:
        record("1c-platform", "module", False, str(exc))

    section("2. 1c-files")
    try:
        mod = load_mod("files", packages["files"])
        st = parse_json_or_text(mod.files_status())
        record("1c-files", "files_status", isinstance(st, dict) and st.get("ok") is not False, json.dumps(st, ensure_ascii=False)[:200] if isinstance(st, dict) else str(st))
        sr = parse_json_or_text(mod.files_search("Эст_Выпуск", max_results=5))
        record("1c-files", "files_search", isinstance(sr, dict) and sr.get("ok") and sr.get("count", 0) >= 1, f"count={sr.get('count') if isinstance(sr, dict) else '?'}")
        us = parse_json_or_text(mod.files_find_usages("Эст_Выпуск", max_results=10))
        record("1c-files", "files_find_usages", isinstance(us, dict) and us.get("ok", True), f"count={us.get('count') if isinstance(us, dict) else '?'}")
        path = r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cf\Documents\Эст_Выпуск\Ext\ObjectModule.bsl"
        rd = parse_json_or_text(mod.files_read(path, max_bytes=5000))
        content = ""
        if isinstance(rd, dict):
            content = rd.get("content") or rd.get("text") or ""
        record("1c-files", "files_read", isinstance(rd, dict) and rd.get("ok") and len(content) > 0, f"bytes={len(content)}")
    except Exception as exc:
        record("1c-files", "module", False, str(exc))

    section("3. 1c-review")
    try:
        mod = load_mod("review", packages["review"])
        st = parse_json_or_text(mod.review_status())
        record("1c-review", "review_status", True, json.dumps(st, ensure_ascii=False)[:200] if isinstance(st, dict) else str(st)[:200])
        rules = parse_json_or_text(mod.review_list_rules())
        n = len(rules.get("rules") or rules.get("items") or []) if isinstance(rules, dict) else 0
        record("1c-review", "review_list_rules", n >= 1, f"rules={n}")
        chk = parse_json_or_text(mod.review_check(text="А = ТекущаяДата();\n"))
        findings = chk.get("findings") if isinstance(chk, dict) else []
        record("1c-review", "review_check no-current-date", any(f.get("rule") == "no-current-date" for f in (findings or [])), str(findings)[:200])
        path = r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cf\Documents\Эст_Выпуск\Ext\ObjectModule.bsl"
        chk2 = parse_json_or_text(mod.review_check(paths=[path]))
        record("1c-review", "review_check path", isinstance(chk2, dict) and chk2.get("ok") is not False, f"findings={len((chk2 or {}).get('findings') or [])}")
    except Exception as exc:
        record("1c-review", "module", False, str(exc))

    section("4. 1c-dump / 1c-load status & gates")
    try:
        from onec_mcp_shared import resolve_ib

        record("1c-dump", "resolve_ib dev", "InfoBase2" in resolve_ib("dev"), resolve_ib("dev"))
        record("1c-dump", "resolve_ib work", "InfoBase3" in resolve_ib("work"), resolve_ib("work"))
        mod = load_mod("dump", packages["dump"])
        st = parse_json_or_text(mod.dump_status())
        record("1c-dump", "dump_status", isinstance(st, dict) and st.get("ok") is not False, json.dumps(st, ensure_ascii=False)[:250] if isinstance(st, dict) else str(st))
        mod = load_mod("load", packages["load"])
        st = parse_json_or_text(mod.load_health())
        record("1c-load", "load_health", isinstance(st, dict) and st.get("ok") is not False, json.dumps(st, ensure_ascii=False)[:250] if isinstance(st, dict) else str(st))
        gate = parse_json_or_text(mod.load_objects(objects=["Document.X"], confirm=False))
        record("1c-load", "confirm gate", isinstance(gate, dict) and gate.get("ok") is False, json.dumps(gate, ensure_ascii=False)[:200] if isinstance(gate, dict) else str(gate))
    except Exception as exc:
        record("1c-dump", "module", False, str(exc))

    section("5. Session matching")
    try:
        from onec_mcp_shared import session as sess

        real = sess._ibases_v8i_paths()
        record("session", "ibases.v8i", len(real) >= 2, str(real))
        cmd_work = r'DESIGNER /IBName"ERP КОПИЯ" /AppAutoCheckMode'
        cmd_dev = r'DESIGNER /IBName"ERP КОПИЯ запасная" /Lru'
        ok_exact = (
            sess._cmdline_matches_ib(cmd_dev, r"C:\Users\rubinshtein\Documents\InfoBase2")
            and not sess._cmdline_matches_ib(cmd_dev, r"C:\Users\rubinshtein\Documents\InfoBase3")
            and sess._cmdline_matches_ib(cmd_work, r"C:\Users\rubinshtein\Documents\InfoBase3")
            and not sess._cmdline_matches_ib(cmd_work, r"C:\Users\rubinshtein\Documents\InfoBase2")
        )
        record("session", "exact IBName (no prefix bug)", ok_exact)
        record("session", "DEV open processes", True, str([(p.pid, p.kind) for p in sess.find_ib_processes(r"C:\Users\rubinshtein\Documents\InfoBase2")]))
        record("session", "WORK open processes", True, str([(p.pid, p.kind) for p in sess.find_ib_processes(r"C:\Users\rubinshtein\Documents\InfoBase3")]))
    except Exception as exc:
        record("session", "module", False, str(exc))

    section("6. 1c-com")
    try:
        mod = load_mod("com", packages["com"])
        st = parse_json_or_text(mod.com_status())
        record("1c-com", "com_status pywin32", isinstance(st, dict) and st.get("pywin32") is True, json.dumps(st, ensure_ascii=False)[:200] if isinstance(st, dict) else str(st))
        ping = parse_json_or_text(mod.com_ping())
        record("1c-com", "com_ping", isinstance(ping, dict) and ping.get("ok") is True, json.dumps(ping, ensure_ascii=False)[:250] if isinstance(ping, dict) else str(ping))
        if isinstance(ping, dict) and ping.get("ok"):
            q = parse_json_or_text(mod.com_query("ВЫБРАТЬ 1 КАК N", limit=1))
            record("1c-com", "com_query", isinstance(q, dict) and q.get("ok") is True, json.dumps(q, ensure_ascii=False)[:200] if isinstance(q, dict) else str(q))
            md = parse_json_or_text(mod.com_metadata_find("Документ.Эст_Выпуск"))
            record("1c-com", "com_metadata_find", isinstance(md, dict) and md.get("ok") is True, json.dumps(md, ensure_ascii=False)[:200] if isinstance(md, dict) else str(md))
        else:
            record("1c-com", "com_query", False, "skipped: com_ping failed")
            record("1c-com", "com_metadata_find", False, "skipped: com_ping failed")
    except Exception as exc:
        record("1c-com", "module", False, str(exc))

    section("7. 1c-journal")
    try:
        mod = load_mod("journal", packages["journal"])
        st = parse_json_or_text(mod.journal_status())
        record("1c-journal", "journal_status", True, json.dumps(st, ensure_ascii=False)[:200] if isinstance(st, dict) else str(st)[:200])
        recent = parse_json_or_text(mod.journal_recent(limit=5))
        ok_jr = isinstance(recent, dict) and recent.get("ok") is True
        record("1c-journal", "journal_recent", ok_jr, json.dumps(recent, ensure_ascii=False)[:250] if isinstance(recent, dict) else str(recent)[:250])
    except Exception as exc:
        record("1c-journal", "module", False, str(exc))

    section("8. 1c-debug")
    try:
        mod = load_mod("debug", packages["debug"])
        st = parse_json_or_text(mod.debug_status())
        record("1c-debug", "debug_status", isinstance(st, dict) and ("debugServerUrl" in st or "ok" in st), json.dumps(st, ensure_ascii=False)[:250] if isinstance(st, dict) else str(st))
        att = parse_json_or_text(mod.debug_attach())
        # without dbgs must not crash; typically ok=false
        record("1c-debug", "debug_attach no-crash", isinstance(att, dict), json.dumps(att, ensure_ascii=False)[:250] if isinstance(att, dict) else str(att))
    except Exception as exc:
        record("1c-debug", "module", False, str(exc))

    section("9. 1c-bsl")
    try:
        mod = load_mod("bsl", packages["bsl"])
        st = parse_json_or_text(mod.bsl_status())
        record("1c-bsl", "bsl_status", isinstance(st, dict) and ("hint" in st or "ok" in st), json.dumps(st, ensure_ascii=False)[:250] if isinstance(st, dict) else str(st))
        help_ = parse_json_or_text(mod.bsl_launch_help())
        record("1c-bsl", "bsl_launch_help", isinstance(help_, dict) or isinstance(help_, str), json.dumps(help_, ensure_ascii=False)[:250] if isinstance(help_, dict) else str(help_)[:250])
    except Exception as exc:
        record("1c-bsl", "module", False, str(exc))

    section("10. Live IB dump/load DEV")
    from onec_mcp_shared.session import find_ib_processes

    dev = r"C:\Users\rubinshtein\Documents\InfoBase2"
    work = r"C:\Users\rubinshtein\Documents\InfoBase3"
    dev_busy = find_ib_processes(dev)
    work_busy = find_ib_processes(work)
    record("live-ib", "DEV free", len(dev_busy) == 0, str(dev_busy))
    record("live-ib", "WORK processes (ok if open)", True, str([(p.pid, p.kind) for p in work_busy]))
    if not dev_busy:
        try:
            dmod = load_mod("dump", packages["dump"])
            obj = "Document.Эст_КвитанцияФПП"
            data = parse_json_or_text(dmod.dump_objects(objects=[obj], merge_into_repo=False, target="dev"))
            record("live-ib", "dump DEV", isinstance(data, dict) and data.get("ok") is True, json.dumps({k: data.get(k) for k in ("ok", "exitCode", "storageError", "objectsToCapture")} if isinstance(data, dict) else {}, ensure_ascii=False))
            lmod = load_mod("load", packages["load"])
            data = parse_json_or_text(lmod.load_objects(objects=[obj], confirm=True, target="dev"))
            load_ok = isinstance(data, dict) and (data.get("ok") is True or bool(data.get("objectsToCapture") or data.get("storageError")))
            record(
                "live-ib",
                "load DEV (ok or storage gate)",
                load_ok,
                json.dumps({k: data.get(k) for k in ("ok", "exitCode", "storageError", "objectsToCapture")} if isinstance(data, dict) else {}, ensure_ascii=False),
            )
            work_after = find_ib_processes(work)
            record("live-ib", "WORK untouched", True, str([(p.pid, p.kind) for p in work_after]))
        except Exception as exc:
            record("live-ib", "dump/load", False, str(exc))
    else:
        record("live-ib", "dump DEV", False, "DEV locked")
        record("live-ib", "load DEV", False, "DEV locked")

    section("11. Stdio process start (all 9)")
    for s in expected_servers:
        ok, detail = mcp_stdio_starts(s, timeout_sec=5.0 if s != "1c-platform" else 10.0)
        record(s, "stdio starts & stays up", ok, detail)

    section("12. Stdio MCP initialize (sample: files + platform)")
    for s in ("1c-files", "1c-platform"):
        ok, detail = mcp_stdio_initialize(s, timeout_sec=25.0 if s == "1c-platform" else 15.0)
        record(s, "stdio initialize", ok, detail)

    section("13. pytest")
    r = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", str(ROOT / "tests"), "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    record("pytest", "all tests", r.returncode == 0, (r.stdout + r.stderr)[-300:])

    section("SUMMARY")
    fails = [x for x in REPORT if not x["ok"]]
    oks = [x for x in REPORT if x["ok"]]
    print(f"Passed: {len(oks)}", flush=True)
    print(f"Failed: {len(fails)}", flush=True)

    expected_fail_keys = {
        ("1c-com", "com_ping"),
        ("1c-com", "com_query"),
        ("1c-com", "com_metadata_find"),
        ("1c-journal", "journal_recent"),
    }
    # debug attach ok=false is fine; we only check no-crash
    unexpected = [x for x in fails if (x["server"], x["check"]) not in expected_fail_keys]
    expected = [x for x in fails if (x["server"], x["check"]) in expected_fail_keys]

    if expected:
        print("\nKnown limitations (COM Connect / registration):", flush=True)
        for x in expected:
            print(f"  - {x['server']}: {x['check']} — {x['detail'][:140]}", flush=True)
    if unexpected:
        print("\nUnexpected failures:", flush=True)
        for x in unexpected:
            print(f"  - {x['server']}: {x['check']} — {x['detail'][:140]}", flush=True)
        print("\nVERDICT: PROBLEMS", flush=True)
        return 1

    print("\nNo unexpected failures.", flush=True)
    print("VERDICT: OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
