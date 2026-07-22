from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import (  # noqa: E402
    env,
    json_result,
    list_dumped_paths,
    load_env_files,
    merge_copy,
    normalize_object_name,
    now_stamp,
    resolve_ib,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env", Path(_ROOT).parent / ".env")

mcp = make_mcp("1c-dump")


def _tmp_root() -> Path:
    return Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-dump")) or ".tmp/1c-dump")


@mcp.tool()
def dump_status() -> str:
    """Health: ONEC_BIN, DEV/WORK IB, repo paths."""
    dev = env("ONEC_IB_DEV") or env("ONEC_IB")
    work = env("ONEC_IB_WORK")
    data = {
        "onecBin": env("ONEC_BIN"),
        "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
        "ibDev": dev,
        "ibDevExists": Path(dev or ".").is_dir(),
        "ibWork": work,
        "ibWorkExists": Path(work or ".").is_dir() if work else False,
        "extension": env("ONEC_EXTENSION"),
        "repoCf": env("REPO_CF"),
        "repoCfe": env("REPO_CFE"),
        "note": "dump always uses target=dev (ONEC_IB_DEV) unless target=work is passed",
    }
    data["ok"] = bool(data["onecBinExists"] and data["ibDevExists"])
    return json_result(data)


@mcp.tool()
def dump_objects(
    objects: list[str],
    target_dir: str | None = None,
    extension: str | bool | None = None,
    merge_into_repo: bool = True,
    force_full: bool = False,
    target: str = "dev",
) -> str:
    """
    Partial dump via Designer -listFile (default from DEV / InfoBase2).
    target: 'dev' (default) or 'work'. Prefer dev — no storage.
    """
    if force_full and not objects:
        return json_result({"ok": False, "error": "Full dump into repo is disabled. Pass objects."})
    if not objects:
        return json_result({"ok": False, "error": "objects is required (non-empty list)"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    dump_dir = Path(target_dir) if target_dir else _tmp_root() / now_stamp()
    dump_dir.mkdir(parents=True, exist_ok=True)
    list_file = dump_dir / "objects.txt"
    canon = [normalize_object_name(o) for o in objects]
    write_list_file(canon, list_file)

    args = ["/DumpConfigToFiles", str(dump_dir)]
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-listFile", str(list_file), "-Format", "Hierarchical"])

    result = run_designer(args, work_dir=dump_dir, objects=canon, target=target)
    result.dump_dir = str(dump_dir)
    result.dumped_paths = list_dumped_paths(dump_dir)
    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    real_files = [
        p
        for p in result.dumped_paths
        if not p.endswith(("objects.txt", "designer.out", "ConfigDumpInfo.xml"))
    ]
    # Designer may exit 1 with only "хранилище не установлено" while files are written
    if real_files and not result.storage_error:
        payload["ok"] = True
        if result.exit_code != 0:
            payload["warning"] = "Designer non-zero exit, but object files were written"
    if result.storage_error:
        payload["message"] = (
            "Designer reported configuration storage / lock issue. Capture: "
            + ", ".join(result.objects_to_capture)
        )
        payload["ok"] = False
    if merge_into_repo and payload.get("ok") and real_files:
        repo = env("REPO_CFE") if ext_name else env("REPO_CF")
        if not repo:
            payload["mergeError"] = "REPO_CF / REPO_CFE not set"
        else:
            report = merge_copy(dump_dir, Path(repo))
            junk = Path(repo) / "objects.txt"
            if junk.is_file():
                junk.unlink()
            designer_log = Path(repo) / "designer.out"
            if designer_log.is_file():
                designer_log.unlink()
            payload["mergeReport"] = report
    return json_result(payload)


@mcp.tool()
def dump_changes(
    target_dir: str | None = None,
    config_dump_info_path: str | None = None,
    extension: str | bool | None = None,
    merge_into_repo: bool = True,
    target: str = "dev",
) -> str:
    """Incremental dump vs ConfigDumpInfo.xml from DEV by default."""
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    default_info = env("REPO_CFE" if ext_name else "REPO_CF", "")
    info = Path(config_dump_info_path or (str(Path(default_info) / "ConfigDumpInfo.xml") if default_info else ""))
    if not info.is_file():
        return json_result({"ok": False, "error": f"ConfigDumpInfo.xml not found: {info}"})
    dump_dir = Path(target_dir) if target_dir else _tmp_root() / f"changes-{now_stamp()}"
    dump_dir.mkdir(parents=True, exist_ok=True)
    args = ["/DumpConfigToFiles", str(dump_dir)]
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-update", "-configDumpInfoForChanges", str(info), "-Format", "Hierarchical"])
    result = run_designer(args, work_dir=dump_dir, objects=[], target=target)
    result.dump_dir = str(dump_dir)
    result.dumped_paths = list_dumped_paths(dump_dir)
    payload = result.to_dict()
    payload["target"] = target
    if merge_into_repo and result.exit_code == 0:
        repo = env("REPO_CFE") if ext_name else env("REPO_CF")
        if repo:
            payload["mergeReport"] = merge_copy(dump_dir, Path(repo))
    return json_result(payload)


def main() -> None:
    run_mcp(mcp, default_port=18761)


if __name__ == "__main__":
    main()
