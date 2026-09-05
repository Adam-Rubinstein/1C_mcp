from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import (  # noqa: E402
    env,
    is_work_target,
    json_result,
    list_dumped_paths,
    load_env_files,
    merge_copy,
    normalize_object_name,
    now_stamp,
    require_storage_path,
    resolve_ib,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.work_gates import (  # noqa: E402
    DesignerBusy,
    acquire_object_locks,
    forms_incomplete_after_dump,
    refuse_dirty_repo,
    require_work_task,
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
    from onec_mcp_shared.work_gates import _gates_root

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
        "gatesRoot": str(_gates_root()),
        "mcpGatesRootEnv": (env("MCP_GATES_ROOT") or "").strip() or None,
        "storagePathSet": bool((env("ONEC_STORAGE_PATH") or "").strip()),
        "note": (
            "Default target=dev only for sandbox smoke. "
            "For 'from Configurator' use target=work. "
            "WORK dump requires manage_session+force_close. "
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
    confirm_merge_dev: bool = False,
    confirm_overwrite_dirty: bool = False,
    confirm_discard_local_edits: bool = False,
    force_full: bool = False,
    target: str = "dev",
    manage_session: bool = False,
    force_close: bool = False,
    reopen_designer: bool | None = None,
    task: str | None = None,
) -> str:
    """Partial dump via Designer -listFile. WORK merge: task=; dirty → stash+reapply (not overwrite)."""
    if force_full and not objects:
        return json_result({"ok": False, "error": "Full dump into repo is disabled. Pass objects."})
    if not objects:
        return json_result({"ok": False, "error": "objects is required (non-empty list)"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    t = (target or "dev").strip().lower()
    if merge_into_repo and t in ("dev", "develop", "sandbox", "base2") and not confirm_merge_dev:
        return json_result(
            {
                "ok": False,
                "error": "Refusing merge_into_repo=true from DEV without confirm_merge_dev=true.",
                "step": "refuse_dev_merge",
                "hint": "Use target=work for Configurator truth, or set confirm_merge_dev=true deliberately.",
                "stop": True,
            }
        )
    if reopen_designer is None:
        reopen_designer = False
    if t in ("dev", "develop", "sandbox", "base2"):
        reopen_designer = False

    if is_work_target(t):
        if not manage_session:
            return json_result(
                {
                    "ok": False,
                    "error": "Refusing WORK dump without manage_session=true.",
                    "step": "require_manage_session",
                    "hint": (
                        "Pass manage_session=true and force_close=true. "
                        "Agent must close/reopen Designer, not ask the user. "
                        "Do not python -c / shell Designer on InfoBase3."
                    ),
                    "stop": True,
                }
            )
        manage_session = True
        force_close = True

    ext_name_preview = None
    if extension is True:
        ext_name_preview = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name_preview = extension

    canon = [normalize_object_name(o) for o in objects]
    if merge_into_repo and is_work_target(t):
        task_err = require_work_task(task, target=t)
        if task_err:
            return json_result(task_err)
        lock_err = acquire_object_locks(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
            tool="dump_objects",
        )
        if lock_err:
            return json_result(lock_err)

    if is_work_target(t):
        try:
            require_storage_path()
        except ValueError as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "require_storage_for_work",
                    "message": (
                        "WORK dump requires ONEC_STORAGE_PATH so Designer attaches the repository. "
                        "Without it local files desync from storage (Get loop)."
                    ),
                    "stop": True,
                }
            )

    dump_dir = Path(target_dir) if target_dir else _tmp_root() / now_stamp()
    dump_dir.mkdir(parents=True, exist_ok=True)
    list_file = dump_dir / "objects.txt"
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

    attach = True if is_work_target(t) else False

    def _do_dump():
        return run_designer(
            args,
            work_dir=dump_dir,
            objects=canon,
            target=target,
            attach_storage=attach,
            extension_storage=bool(ext_name),
        )

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do_dump,
                force_close=force_close,
                reopen=reopen_designer,
                attach_storage=attach or None,
            )
        else:
            result = _do_dump()
    except DesignerBusy as exc:
        return json_result({**exc.payload, "session": session_meta})
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
    # DEV: offline storage message OK if files written. WORK: offline = fail.
    if real_files and not result.storage_error and not result.storage_access_error:
        if is_work_target(t) and (result.storage_offline or result.objects_to_get):
            payload["ok"] = False
            payload["message"] = (
                "WORK dump with storage offline or get-required. "
                "Fix ONEC_STORAGE_* / Get objects, then retry. "
                "Do not treat this as a successful sync."
            )
            if result.objects_to_get:
                payload["objectsToGet"] = result.objects_to_get
        else:
            payload["ok"] = True
            if result.exit_code != 0 and not is_work_target(t):
                payload["warning"] = "Designer non-zero exit, but object files were written"
            if is_work_target(t):
                payload["warning"] = (
                    "WORK dump finished with storage attached. "
                    "Before load back: patch only your diff; lock; do not Put blindly."
                )
    if any(normalize_object_name(o).lower() in ("configuration", "конфигурация") for o in canon):
        from onec_mcp_shared.config_root import configuration_ext_missing

        missing_ext = configuration_ext_missing(dump_dir)
        if missing_ext:
            payload["ok"] = False
            payload["step"] = "fix_configuration_ext_incomplete"
            payload["missingExt"] = missing_ext
            payload["message"] = (
                "Configuration dump without Ext/ (UI files). "
                "WORK batch /F dump often omits Ext when storage is disconnected. "
                "Open IB from 1C list (storage connected) or set ONEC_STORAGE_*, "
                "then re-dump. Never fill Ext from git REPO_CF."
            )
    missing_forms = forms_incomplete_after_dump(canon, dump_dir, result.dumped_paths or [])
    if missing_forms and payload.get("ok"):
        payload["ok"] = False
        payload["step"] = "fix_forms_incomplete"
        payload["missingForms"] = missing_forms
        payload["message"] = (
            "Dump missing Forms/.../Ext/Form.xml. Do not patch/load form from stale git. "
            "Re-dump with storage attached; include Document.X.Form.Y in objects list."
        )
        payload["stop"] = True
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
            dirty_err = refuse_dirty_repo(
                dump_dir,
                Path(repo),
                confirm_overwrite_dirty=confirm_overwrite_dirty,
                confirm_discard_local_edits=confirm_discard_local_edits,
                auto_stash=True,
            )
            if dirty_err:
                if dirty_err.get("stop"):
                    payload.update(dirty_err)
                    return json_result(payload)
                # step reapply_stash: dirty stashed, repo cleaned — proceed merge, keep note
                payload["dirtyStash"] = {
                    "step": dirty_err.get("step"),
                    "stashDir": dirty_err.get("stashDir"),
                    "dirtyPaths": dirty_err.get("dirtyPaths"),
                    "hint": dirty_err.get("hint"),
                }
                payload["step"] = dirty_err.get("step") or payload.get("step")
            report = merge_copy(dump_dir, Path(repo))
            junk = Path(repo) / "objects.txt"
            if junk.is_file():
                junk.unlink()
            designer_log = Path(repo) / "designer.out"
            if designer_log.is_file():
                designer_log.unlink()
            payload["mergeReport"] = report
            if payload.get("dirtyStash"):
                payload["message"] = (
                    "Dump merged after stashing dirty git files. "
                    "Re-apply patch from dirtyStash.stashDir onto dumped files, then lock/load."
                )
    return json_result(payload)


@mcp.tool()
def dump_changes(
    target_dir: str | None = None,
    config_dump_info_path: str | None = None,
    extension: str | bool | None = None,
    merge_into_repo: bool = True,
    confirm_merge_dev: bool = False,
    target: str = "dev",
) -> str:
    """Incremental dump vs ConfigDumpInfo.xml from DEV by default."""
    t = (target or "dev").strip().lower()
    if merge_into_repo and t in ("dev", "develop", "sandbox", "base2") and not confirm_merge_dev:
        return json_result(
            {
                "ok": False,
                "error": "Refusing merge_into_repo=true from DEV without confirm_merge_dev=true.",
                "step": "refuse_dev_merge",
                "stop": True,
            }
        )
    if is_work_target(t):
        try:
            require_storage_path()
        except ValueError as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "require_storage_for_work",
                    "stop": True,
                }
            )
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
    result = run_designer(
        args,
        work_dir=dump_dir,
        objects=[],
        target=target,
        attach_storage=is_work_target(t),
        extension_storage=bool(ext_name) and is_work_target(t),
    )
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
