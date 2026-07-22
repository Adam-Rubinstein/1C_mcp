#!/usr/bin/env python3
"""Smoke checks that do not require live IB."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "packages" / "shared"
sys.path.insert(0, str(SHARED))

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    failures.append(msg)


def main() -> int:
    # imports
    try:
        from onec_mcp_shared import normalize_object_name, write_list_file

        assert normalize_object_name("Документ.X") == "Document.X"
        ok("shared import + normalize")
    except Exception as exc:
        fail(f"shared: {exc}")

    # pytest
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"],
        cwd=str(ROOT),
    )
    if r.returncode == 0:
        ok("pytest")
    else:
        fail("pytest failed")

    # platform jar help
    jar = ROOT / "packages" / "mcp-1c-platform" / "runtime" / "1C_mcp_bsl.jar"
    if not jar.is_file():
        jar = ROOT / "dist" / "1C_mcp_bsl.jar"
    if jar.is_file():
        jr = subprocess.run(
            ["java", "-Dfile.encoding=UTF-8", "-jar", str(jar), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if jr.returncode == 0 or "platform" in (jr.stdout + jr.stderr).lower() or "Usage" in (jr.stdout + jr.stderr):
            ok("platform JAR --help")
        else:
            fail(f"platform JAR help exit={jr.returncode}")
    else:
        fail(f"JAR missing: {jar}")

    # files search against Estet repo if present
    estet_cf = Path(r"C:\Users\rubinshtein\Desktop\Projects\1C ERP\src\cf")
    if estet_cf.is_dir():
        os.environ["REPO_CF"] = str(estet_cf)
        os.environ["CONFIG_DUMP_DIR"] = str(estet_cf)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "files_server", ROOT / "packages" / "mcp-1c-files" / "server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        try:
            spec.loader.exec_module(mod)
            data = json.loads(mod.files_search(r"Эст_КвитанцияФПП", max_results=5))
            if data.get("ok") and data.get("count", 0) >= 1:
                ok(f"files_search on Estet cf (hits={data['count']})")
            else:
                fail(f"files_search unexpected: {data}")
            st = json.loads(mod.files_status())
            if st.get("ok"):
                ok("files_status")
            else:
                fail(f"files_status: {st}")
        except Exception as exc:
            fail(f"files package: {exc}")
    else:
        print("[SKIP] Estet src/cf not found")

    # review
    import importlib.util

    os.environ["REVIEW_RULES_PATH"] = str(ROOT / "packages" / "mcp-1c-review" / "rules" / "default.yaml")
    spec = importlib.util.spec_from_file_location(
        "review_server", ROOT / "packages" / "mcp-1c-review" / "server.py"
    )
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

    # dump_status without IB should still return json
    try:
        spec = importlib.util.spec_from_file_location(
            "dump_server", ROOT / "packages" / "mcp-1c-dump" / "server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        data = json.loads(mod.dump_status())
        assert "onecBin" in data
        ok("dump_status")
        # refuse full without objects
        data = json.loads(mod.dump_objects(objects=[], force_full=True))
        assert data.get("ok") is False
        ok("dump_objects refuses empty/full")
    except Exception as exc:
        fail(f"dump: {exc}")

    # load refuses without confirm
    try:
        spec = importlib.util.spec_from_file_location(
            "load_server", ROOT / "packages" / "mcp-1c-load" / "server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        data = json.loads(mod.load_objects(objects=["Document.X"], confirm=False))
        assert data.get("ok") is False
        ok("load_objects requires confirm")
    except Exception as exc:
        fail(f"load: {exc}")

    # debug status (no server)
    try:
        spec = importlib.util.spec_from_file_location(
            "debug_server", ROOT / "packages" / "mcp-1c-debug" / "server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        data = json.loads(mod.debug_status())
        assert "debugServerUrl" in data
        ok("debug_status")
    except Exception as exc:
        fail(f"debug: {exc}")

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
