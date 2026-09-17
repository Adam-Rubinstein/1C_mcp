#!/usr/bin/env python3
"""Extract exact objects from an existing CF into hierarchical XML files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from onec_mcp_shared import env, normalize_object_name, write_list_file


def _read_log(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _run_onec(args: list[str], log: Path) -> tuple[int, str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(args, check=False)
    return completed.returncode, _read_log(log)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--extract-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--object", action="append", dest="objects", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    configuration = Path(args.configuration).resolve()
    extract_dir = Path(args.extract_dir).resolve()
    work_dir = Path(args.work_dir).resolve()
    objects = [normalize_object_name(value) for value in args.objects]
    onec_bin = env("ONEC_BIN", "") or ""

    if not configuration.is_file():
        raise SystemExit(f"Configuration file not found: {configuration}")
    if not Path(onec_bin).is_file():
        raise SystemExit(f"ONEC_BIN not found: {onec_bin}")
    if extract_dir.exists() and any(extract_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"Extract directory is not empty: {extract_dir}")

    work_dir.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    ib_dir = work_dir / "extract-ib"
    shutil.rmtree(ib_dir, ignore_errors=True)

    create_log = work_dir / "create.out"
    connection = f"File={ib_dir};"
    create_args = [
        onec_bin,
        "CREATEINFOBASE",
        connection,
        "/UseTemplate",
        str(configuration),
        "/DisableStartupDialogs",
        "/Out",
        str(create_log),
    ]
    create_code, create_text = _run_onec(create_args, create_log)
    if create_code != 0:
        shutil.rmtree(ib_dir, ignore_errors=True)
        empty_code, empty_text = _run_onec(
            [
                onec_bin,
                "CREATEINFOBASE",
                connection,
                "/DisableStartupDialogs",
                "/Out",
                str(create_log),
            ],
            create_log,
        )
        load_log = work_dir / "load.out"
        load_code, load_text = _run_onec(
            [
                onec_bin,
                "DESIGNER",
                "/F",
                str(ib_dir),
                "/DisableStartupDialogs",
                "/Out",
                str(load_log),
                "/LoadCfg",
                str(configuration),
            ],
            load_log,
        )
        create_code = empty_code or load_code
        create_text = "\n".join((create_text, empty_text, load_text))
    if create_code != 0:
        shutil.rmtree(ib_dir, ignore_errors=True)
        raise SystemExit(
            "Failed to create temporary infobase:\n"
            + "\n".join(create_text.splitlines()[-40:])
        )

    list_file = write_list_file(objects, work_dir / "objects.txt")
    dump_log = work_dir / "dump.out"
    dump_code, dump_text = _run_onec(
        [
            onec_bin,
            "DESIGNER",
            "/F",
            str(ib_dir),
            "/DisableStartupDialogs",
            "/Out",
            str(dump_log),
            "/DumpConfigToFiles",
            str(extract_dir),
            "-listFile",
            str(list_file),
            "-Format",
            "Hierarchical",
        ],
        dump_log,
    )
    shutil.rmtree(ib_dir, ignore_errors=True)
    extracted_paths = sorted(
        str(path.relative_to(extract_dir)).replace("\\", "/")
        for path in extract_dir.rglob("*")
        if path.is_file()
    )
    payload = {
        "ok": dump_code == 0 and bool(extracted_paths),
        "configuration": str(configuration),
        "configurationSize": configuration.stat().st_size,
        "configurationSha256": _sha256(configuration),
        "extractDir": str(extract_dir),
        "objects": objects,
        "extractedPaths": extracted_paths,
        "logTail": "\n".join(dump_text.splitlines()[-40:]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
