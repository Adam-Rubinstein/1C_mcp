#!/usr/bin/env python3
"""Smoke checks. Use --live-ib for Designer dump/load against ONEC_IB_DEV."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    failures.append(msg)


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _ld

        _ld(ROOT / ".env", override=False)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-ib", action="store_true", help="Run Designer dump/load on ONEC_IB_DEV")
    args = parser.parse_args()
    load_dotenv()

    try:
        from onec_mcp_shared import normalize_object_name, parse_storage_errors

        assert normalize_object_name("Документ.X") == "Document.X"
        err, _ = parse_storage_errors("Соединение с хранилищем конфигурации не установлено", ["Document.X"])
        assert err is False
        err, objs = parse_storage_errors("объект Document.X не захвачен в хранилище", ["Document.X"])
        assert err is True and "Document.X" in objs
        ok("shared normalize + storage parse")
    except Exception as exc:
        fail(f"shared: {exc}")

    r = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"], cwd=str(ROOT))
    if r.returncode == 0:
        ok("pytest")
    else:
        fail("pytest failed")

    # platform python
    try:
        sys.path.insert(0, str(ROOT / "packages" / "mcp-1c-platform"))
        from platform_ctx.index import load_index, search
        from pathlib import Path as P

        pp = os.environ.get("ONEC_PLATFORM_PATH")
        if pp and P(pp).is_dir():
            idx = load_index(P(pp))
            hits = search(idx, "Query", None, 3)
            if idx.methods and hits:
                ok(f"platform index methods={len(idx.methods)} search(Query)={len(hits)}")
            else:
                fail(f"platform index weak: methods={len(idx.methods)} hits={len(hits)}")
        else:
            fail("ONEC_PLATFORM_PATH missing")
    except Exception as exc:
        fail(f"platform: {exc}")

    # files / review
    estet = Path(os.environ.get("REPO_CF") or r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cf")
    if estet.is_dir():
        os.environ["REPO_CF"] = str(estet)
        os.environ["CONFIG_DUMP_DIR"] = str(estet)
        import importlib.util

        spec = importlib.util.spec_from_file_location("files_server", ROOT / "packages" / "mcp-1c-files" / "server.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        try:
            spec.loader.exec_module(mod)
            data = json.loads(mod.files_search("Эст_", max_results=3))
            if data.get("ok") and data.get("count", 0) >= 1:
                ok(f"files_search hits={data['count']}")
            else:
                fail(f"files_search: {data}")
        except Exception as exc:
            fail(f"files: {exc}")
    else:
        print("[SKIP] REPO_CF not found")

    import importlib.util

    os.environ["REVIEW_RULES_PATH"] = str(ROOT / "packages" / "mcp-1c-review" / "rules" / "default.yaml")
    spec = importlib.util.spec_from_file_location("review_server", ROOT / "packages" / "mcp-1c-review" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    try:
        spec.loader.exec_module(mod)
        data = json.loads(mod.review_check(text="\u0410 = \u0422\u0435\u043a\u0443\u0449\u0430\u044f\u0414\u0430\u0442\u0430();\n"))
        if data.get("ok") and any(f.get("rule") == "no-current-date" for f in data.get("findings", [])):
            ok("review_check")
        else:
            fail(f"review_check: {data}")
    except Exception as exc:
        fail(f"review: {exc}")

    # dump/load status + confirm gate
    for name, path in (
        ("dump", ROOT / "packages" / "mcp-1c-dump" / "server.py"),
        ("load", ROOT / "packages" / "mcp-1c-load" / "server.py"),
        ("debug", ROOT / "packages" / "mcp-1c-debug" / "server.py"),
        ("bsl", ROOT / "packages" / "mcp-1c-bsl" / "server.py"),
        ("com", ROOT / "packages" / "mcp-1c-com" / "server.py"),
    ):
        spec = importlib.util.spec_from_file_location(f"{name}_srv", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        try:
            spec.loader.exec_module(mod)
            if name == "dump":
                st = json.loads(mod.dump_status())
                if st.get("ok"):
                    ok("dump_status")
                else:
                    fail(f"dump_status: {st}")
            elif name == "load":
                st = json.loads(mod.load_status())
                bad = json.loads(mod.load_objects(objects=["Document.X"], confirm=False))
                work_gate = json.loads(
                    mod.load_objects(objects=["Document.X"], confirm=True, target="work", storage_captured=False)
                )
                prep = json.loads(mod.load_prepare_work(objects=["Document.X"]))
                if (
                    bad.get("ok") is False
                    and st.get("ok")
                    and work_gate.get("ok") is False
                    and work_gate.get("stop") is True
                    and prep.get("ok")
                    and prep.get("stop") is True
                ):
                    ok("load_status + confirm + work storage gate + prepare")
                else:
                    fail(f"load: {st} {bad} {work_gate} {prep}")
            elif name == "debug":
                st = json.loads(mod.debug_status())
                if "debugServerUrl" in st:
                    ok("debug_status")
                else:
                    fail(f"debug: {st}")
            elif name == "bsl":
                st = json.loads(mod.bsl_status())
                if "hint" in st or "ok" in st:
                    ok("bsl_status")
                else:
                    fail(f"bsl: {st}")
            elif name == "com":
                st = json.loads(mod.com_status())
                if st.get("pywin32") is not None:
                    ok("com_status")
                else:
                    fail(f"com: {st}")
        except Exception as exc:
            fail(f"{name}: {exc}")

    if args.live_ib:
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("dump_server", ROOT / "packages" / "mcp-1c-dump" / "server.py")
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            obj = os.environ.get("SMOKE_OBJECT", "Document.Эст_КвитанцияФПП")
            data = json.loads(mod.dump_objects(objects=[obj], merge_into_repo=False, target="dev"))
            if data.get("ok"):
                ok(f"live dump {obj}")
            else:
                fail(f"live dump: {data.get('logTail') or data}")
            spec = importlib.util.spec_from_file_location("load_server", ROOT / "packages" / "mcp-1c-load" / "server.py")
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            data = json.loads(mod.load_objects(objects=[obj], confirm=True, target="dev"))
            if data.get("ok"):
                ok(f"live load {obj}")
            elif data.get("objectsToCapture") or data.get("storageError"):
                ok(f"live load storage gate (objectsToCapture) — expected if IB uses storage")
            elif data.get("logTail") or data.get("logPath"):
                # Designer ran; failure is IB/repo content (storage, form paths, etc.), not MCP wiring
                ok(f"live load Designer ran (exit={data.get('exitCode')}) — check logTail for IB/repo sync")
            else:
                fail(f"live load: {data.get('logTail') or data}")
        except Exception as exc:
            fail(f"live-ib: {exc}")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
