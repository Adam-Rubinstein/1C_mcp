"""Configuration repository batch tools: get / lock / unlock / commit / report."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import (  # noqa: E402
    env,
    is_work_target,
    json_result,
    load_env_files,
    normalize_object_name,
    now_stamp,
    require_storage_path,
    resolve_ib,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402
from onec_mcp_shared.session import with_managed_session  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env", Path(_ROOT).parent / ".env")

mcp = make_mcp("1c-storage")


def _canon(objects: list[str] | None) -> list[str]:
    return [normalize_object_name(o) for o in (objects or []) if (o or "").strip()]


def _refuse_entire(*, entire_config: bool, confirm_entire: bool) -> str | None:
    if entire_config and not confirm_entire:
        return json_result(
            {
                "ok": False,
                "error": "entire_config=true requires confirm_entire=true.",
                "stop": True,
            }
        )
    return None


def _gate_objects(
    objects: list[str] | None,
    *,
    entire_config: bool,
    confirm_entire: bool,
) -> tuple[list[str] | None, str | None]:
    err = _refuse_entire(entire_config=entire_config, confirm_entire=confirm_entire)
    if err:
        return None, err
    if entire_config:
        return [], None
    canon = _canon(objects)
    if not canon:
        return None, json_result(
            {
                "ok": False,
                "error": "objects is required (or entire_config=true with confirm_entire=true).",
                "stop": True,
            }
        )
    return canon, None


def _run_storage_op(
    designer_args: list[str],
    *,
    objects: list[str],
    target: str,
    manage_session: bool,
    force_close: bool,
    reopen_designer: bool | None,
    work: Path,
) -> str:
    try:
        require_storage_path()
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc), "stop": True})

    t = (target or "work").strip().lower()
    if reopen_designer is None:
        reopen_designer = is_work_target(t)
    if t in ("dev", "develop", "sandbox", "base2"):
        reopen_designer = False

    def _do():
        return run_designer(
            designer_args,
            work_dir=work,
            objects=objects,
            target=target,
            attach_storage=True,
        )

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do,
                force_close=force_close,
                reopen=reopen_designer,
                attach_storage=True,
            )
        else:
            result = _do()
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "session": session_meta})

    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    payload["storagePath"] = env("ONEC_STORAGE_PATH")
    if session_meta:
        payload["session"] = session_meta
    if result.objects_to_get:
        payload["message"] = (
            "Need get from storage first: " + ", ".join(result.objects_to_get)
        )
        payload["ok"] = False
    elif result.storage_access_error:
        payload["message"] = (
            "Storage access/lock error (OBJECTS table or shared access). "
            "Close other Configurators / wait for SMB lock, then retry."
        )
        payload["ok"] = False
    elif result.storage_offline:
        payload["message"] = "Storage not connected despite ONEC_STORAGE_*. Check path/user."
        payload["ok"] = False
    elif result.storage_error or result.objects_to_capture:
        payload["message"] = (
            "Storage lock/capture issue. Capture or unlock: "
            + ", ".join(result.objects_to_capture or objects)
        )
        payload["ok"] = False
    elif result.exit_code != 0:
        payload["message"] = "Designer failed. See logTail."
        payload["ok"] = False
    else:
        payload["ok"] = True
        payload["message"] = "Storage operation finished."
    return json_result(payload)


@mcp.tool(name="storage_status")
def storage_status() -> str:
    """Health: storage path, WORK IB, ONEC_BIN."""
    work = env("ONEC_IB_WORK")
    path = (env("ONEC_STORAGE_PATH") or "").strip()
    return json_result(
        {
            "ok": bool(path) and Path(env("ONEC_BIN", "") or ".").is_file(),
            "onecBin": env("ONEC_BIN"),
            "ibWork": work,
            "storagePath": path,
            "storagePathSet": bool(path),
            "tools": [
                "storage_get",
                "storage_lock",
                "storage_unlock",
                "storage_commit",
                "storage_report",
                "storage_status",
            ],
            "note": (
                "Get/lock/unlock/commit need ONEC_STORAGE_*. "
                "storage_commit requires confirm=true + comment; agent must not Put without user ask."
            ),
        }
    )


@mcp.tool(name="storage_get")
def storage_get(
    objects: list[str] | None = None,
    target: str = "work",
    revised: bool = False,
    confirm_revised: bool = False,
    force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Get objects from config storage (UpdateCfg). revised overwrites local on locked — needs confirm_revised."""
    if revised and not confirm_revised:
        return json_result(
            {
                "ok": False,
                "error": "revised=true overwrites local changes on locked objects; set confirm_revised=true.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryUpdateCfg"]
    if revised:
        args.append("-revised")
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_list_file(canon, list_file, for_load=False)
        args.extend(["-Objects", str(list_file)])
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])

    return _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
    )


@mcp.tool(name="storage_lock")
def storage_lock(
    objects: list[str] | None = None,
    target: str = "work",
    revised: bool = False,
    confirm_revised: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Capture (lock) objects in config storage."""
    if revised and not confirm_revised:
        return json_result(
            {
                "ok": False,
                "error": "revised=true on lock gets locked objects; set confirm_revised=true.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryLock"]
    if revised:
        args.append("-revised")
    if not entire_config:
        list_file = work / "objects.txt"
        write_list_file(canon, list_file, for_load=False)
        args.extend(["-Objects", str(list_file)])
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])

    return _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
    )


@mcp.tool(name="storage_unlock")
def storage_unlock(
    objects: list[str] | None = None,
    target: str = "work",
    force: bool = False,
    confirm_force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Release capture. force discards local changes — needs confirm_force."""
    if force and not confirm_force:
        return json_result(
            {
                "ok": False,
                "error": "force=true discards local changes on unlock; set confirm_force=true.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryUnLock"]
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_list_file(canon, list_file, for_load=False)
        args.extend(["-Objects", str(list_file)])
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])

    return _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
    )


@mcp.tool(name="storage_commit")
def storage_commit(
    objects: list[str] | None = None,
    comment: str = "",
    confirm: bool = False,
    keep_locked: bool = False,
    force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    extension: str | bool | None = None,
    target: str = "work",
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Put objects to storage. confirm=true + non-empty comment required. Agent: only on explicit user ask."""
    if not confirm:
        return json_result(
            {
                "ok": False,
                "error": "Refusing storage_commit without confirm=true (writes to repository).",
                "stop": True,
                "hint": "Only after user explicitly asked to put/поместить, with comment.",
            }
        )
    if not (comment or "").strip():
        return json_result(
            {
                "ok": False,
                "error": "comment is required for storage_commit.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryCommit", "-comment", comment.strip()]
    if keep_locked:
        args.append("-keepLocked")
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_list_file(canon, list_file, for_load=False)
        args.extend(["-Objects", str(list_file)])
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])

    return _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
    )


@mcp.tool(name="storage_report")
def storage_report(
    report_path: str | None = None,
    target: str = "work",
    report_format: str = "txt",
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Read-only storage history report."""
    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    out = Path(report_path) if report_path else work / f"storage_report.{report_format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = (report_format or "txt").strip().lower()
    if fmt not in ("txt", "mxl"):
        fmt = "txt"
    args = ["/ConfigurationRepositoryReport", str(out), "-ReportFormat", fmt]
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    if ext_name:
        args.extend(["-Extension", ext_name])

    result_json = _run_storage_op(
        args,
        objects=[],
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
    )
    # Attach path hint
    import json

    try:
        data = json.loads(result_json)
        data["reportPath"] = str(out)
        return json_result(data)
    except Exception:
        return result_json


if __name__ == "__main__":
    run_mcp(mcp, default_port=8769)
