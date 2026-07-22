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
    require_env,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-dump")


def _tmp_root() -> Path:
    root = env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-dump"))
    return Path(root)


@mcp.tool()
def dump_status() -> str:
    """Health: ONEC_BIN, IB settings, last-run hints."""
    data = {
        "onecBin": env("ONEC_BIN"),
        "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
        "onecIb": env("ONEC_IB"),
        "onecServer": env("ONEC_SERVER"),
        "onecRef": env("ONEC_REF"),
        "extension": env("ONEC_EXTENSION"),
        "dumpTmpRoot": str(_tmp_root()),
        "repoCf": env("REPO_CF"),
        "repoCfe": env("REPO_CFE"),
    }
    data["ok"] = bool(data["onecBinExists"] and (data["onecIb"] or (data["onecServer"] and data["onecRef"])))
    return json_result(data)


@mcp.tool()
def dump_objects(
    objects: list[str],
    target_dir: str | None = None,
    extension: str | bool | None = None,
    merge_into_repo: bool = False,
    force_full: bool = False,
) -> str:
    """
    Partial dump via Designer -listFile.
    objects: e.g. ["Document.MyDoc"] or ["Документ.MyDoc"].
    force_full=true without objects is refused unless objects empty and force_full explicitly for full (still refused to full-repo; only tmp).
    """
    if force_full and not objects:
        return json_result(
            {
                "ok": False,
                "error": "Full dump into repo is disabled. Pass objects for partial dump, or force_full to a temp dir only via internal full mode not implemented for safety.",
            }
        )
    if not objects:
        return json_result({"ok": False, "error": "objects is required (non-empty list)"})

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

    result = run_designer(args, work_dir=dump_dir, objects=canon)
    result.dump_dir = str(dump_dir)
    result.dumped_paths = list_dumped_paths(dump_dir)

    payload = result.to_dict()
    if result.storage_error:
        payload["message"] = (
            "Designer reported configuration storage / lock issue. "
            "Capture these objects in repository, then retry: "
            + ", ".join(result.objects_to_capture)
        )

    if merge_into_repo and result.exit_code == 0 and not result.storage_error:
        repo = env("REPO_CFE") if ext_name else env("REPO_CF")
        if not repo:
            payload["mergeError"] = "REPO_CF / REPO_CFE not set"
        else:
            report = merge_copy(dump_dir, Path(repo))
            # do not copy objects.txt into repo root blindly — remove if present
            junk = Path(repo) / "objects.txt"
            if junk.is_file():
                junk.unlink()
            payload["mergeReport"] = report

    return json_result(payload)


@mcp.tool()
def dump_changes(
    target_dir: str | None = None,
    config_dump_info_path: str | None = None,
    extension: str | bool | None = None,
    merge_into_repo: bool = False,
) -> str:
    """Incremental dump vs ConfigDumpInfo.xml (-update -configDumpInfoForChanges)."""
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension

    default_info = env("REPO_CFE" if ext_name else "REPO_CF", "")
    info = Path(config_dump_info_path or (str(Path(default_info) / "ConfigDumpInfo.xml") if default_info else ""))
    if not info.is_file():
        return json_result(
            {
                "ok": False,
                "error": f"ConfigDumpInfo.xml not found: {info}. Run a full dump once to create it, or pass config_dump_info_path.",
            }
        )

    dump_dir = Path(target_dir) if target_dir else _tmp_root() / f"changes-{now_stamp()}"
    dump_dir.mkdir(parents=True, exist_ok=True)

    args = ["/DumpConfigToFiles", str(dump_dir)]
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-update", "-configDumpInfoForChanges", str(info), "-Format", "Hierarchical"])

    result = run_designer(args, work_dir=dump_dir, objects=[])
    result.dump_dir = str(dump_dir)
    result.dumped_paths = list_dumped_paths(dump_dir)
    payload = result.to_dict()
    if merge_into_repo and result.exit_code == 0:
        repo = env("REPO_CFE") if ext_name else env("REPO_CF")
        if repo:
            payload["mergeReport"] = merge_copy(dump_dir, Path(repo))
    return json_result(payload)


def main() -> None:
    run_mcp(mcp, default_port=18761)


if __name__ == "__main__":
    main()
