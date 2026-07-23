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
from onec_mcp_shared.session import with_managed_session  # noqa: E402

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
        "storagePathSet": bool((env("ONEC_STORAGE_PATH") or "").strip()),
        "note": (
            "Default target=dev only for sandbox smoke. "
            "For 'from Configurator' use target=work. "
            "manage_session on work reopens like 1C starter (/IBName + WORK user)."
        ),
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
    manage_session: bool = False,
    force_close: bool = False,
    reopen_designer: bool | None = None,
) -> str:
    """
    Partial dump via Designer -listFile.
    target: 'dev' (sandbox) or 'work' (daily Configurator IB).
    manage_session: close only the target IB, dump, then reopen on work like starter.
    reopen_designer: None = auto (True on work, False on dev).
    objects: ONLY metadata for the current task — do not add 'related' catalogs/extensions
    unless the user explicitly asked for them.
    """
    if force_full and not objects:
        return json_result({"ok": False, "error": "Full dump into repo is disabled. Pass objects."})
    if not objects:
        return json_result({"ok": False, "error": "objects is required (non-empty list)"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    t = (target or "dev").strip().lower()
    if reopen_designer is None:
        reopen_designer = t in ("work", "prod", "base3")
    if t in ("dev", "develop", "sandbox", "base2"):
        reopen_designer = False

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

    def _do_dump():
        return run_designer(args, work_dir=dump_dir, objects=canon, target=target)

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do_dump,
                force_close=force_close,
                reopen=reopen_designer,
            )
        else:
            result = _do_dump()
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "session": session_meta})

    result.dump_dir = str(dump_dir)
    result.dumped_paths = list_dumped_paths(dump_dir)
    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    if session_meta:
        payload["session"] = session_meta
        if session_meta.get("userAction"):
            payload["userAction"] = session_meta["userAction"]
        if session_meta.get("warning"):
            payload["sessionWarning"] = session_meta["warning"]
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
