"""Configuration repository batch tools: get / lock / unlock / commit / report."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import (  # noqa: E402
    env,
    json_result,
    load_env_files,
    normalize_object_name,
    now_stamp,
    require_storage_path,
    resolve_ib,
    run_designer,
    write_list_file,
    write_storage_objects_file,
)
from onec_mcp_shared.work_gates import (  # noqa: E402
    DesignerBusy,
    acquire_object_locks,
    refuse_entire_without_env,
    refuse_get_captured,
    refuse_parent_object_without_confirm,
    release_object_locks,
    clear_lock_receipts,
    require_work_task,
    write_aligned_marker,
    write_lock_receipt,
)
from onec_mcp_shared.work_integrity import (  # noqa: E402
    IntegrityError,
    check_load_receipt_for_commit,
    check_pending_stash_receipt,
    clear_integrity_receipts,
    compare_current_snapshot,
    hash_object_files,
    read_load_receipt,
    write_lock_receipt as write_integrity_lock_receipt,
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


def _refuse_entire_work(*, target: str, entire_config: bool) -> str | None:
    if (target or "").strip().lower() in ("work", "prod", "base3") and entire_config:
        return json_result(
            {
                "ok": False,
                "error": "entire_config is forbidden in the automated WORK pipeline.",
                "step": "require_exact_storage_objects",
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
    env_err = refuse_entire_without_env(entire_config=entire_config)
    if env_err:
        return None, json_result(env_err)
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
    extension_storage: bool = False,
) -> dict:
    try:
        require_storage_path()
        ib = resolve_ib(target)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "stop": True}

    t = (target or "work").strip().lower()
    if reopen_designer is None:
        reopen_designer = False
    if t in ("dev", "develop", "sandbox", "base2"):
        reopen_designer = False

    def _do():
        return run_designer(
            designer_args,
            work_dir=work,
            objects=objects,
            target=target,
            attach_storage=True,
            extension_storage=extension_storage,
        )

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do,
                force_close=force_close,
                reopen=reopen_designer,
                attach_storage=False,
            )
        else:
            result = _do()
    except DesignerBusy as exc:
        payload = dict(exc.payload)
        payload["session"] = session_meta
        return payload
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "session": session_meta}

    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    payload["storagePath"] = env("ONEC_STORAGE_PATH_CFE" if extension_storage else "ONEC_STORAGE_PATH")
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
        payload["message"] = (
            "Storage not connected. Check ONEC_STORAGE_* / UNC; "
            "interactive reopen uses /IBName without CLI re-auth."
        )
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
        if result.designer_busy:
            payload["step"] = "work_designer_busy"
            payload["hint"] = "Wait for work_designer.lock; do not taskkill /IM 1cv8.exe."
    else:
        payload["ok"] = True
        payload["message"] = "Storage operation finished."
    return payload


def _finish_storage(
    payload: dict,
    *,
    kind: str,
    objects: list[str],
    target: str,
    extension: str | None,
    task: str | None,
) -> str:
    if payload.get("ok"):
        if kind == "get":
            path = write_aligned_marker(
                objects, target=target, extension=extension, task=task
            )
            payload["alignedMarker"] = str(path)
        elif kind == "lock":
            path = write_lock_receipt(
                objects, target=target, extension=extension, task=task
            )
            payload["lockReceipt"] = str(path)
            if (task or "").strip():
                integrity_path = write_integrity_lock_receipt(
                    objects,
                    task=task or "",
                    target=target,
                    extension=extension,
                )
                payload["integrityLockReceipt"] = str(integrity_path)
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
                "storage_dump_version",
                "storage_status",
            ],
            "note": (
                "WORK storage_get is forbidden; exact lock is the first mutating step. "
                "storage_commit requires task-bound verified load, "
                "confirm=true and user-approved comment."
            ),
        }
    )


def _extension_name(extension: str | bool | None) -> str | None:
    if extension is True:
        return env("ONEC_EXTENSION")
    if isinstance(extension, str) and extension:
        return extension
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_local_onec(argv: list[str], log_path: Path, *, timeout_sec: int = 3600) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        text = ""
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8-sig", errors="replace")
        if proc.stdout:
            text += "\n" + proc.stdout
        if proc.stderr:
            text += "\n" + proc.stderr
        return proc.returncode, text
    except subprocess.TimeoutExpired as exc:
        return 1, f"Local 1C command timed out after {timeout_sec}s: {exc}"


@mcp.tool(name="storage_get")
def storage_get(
    objects: list[str] | None = None,
    target: str = "work",
    revised: bool = False,
    confirm_revised: bool = False,
    force: bool = False,
    confirm_force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    include_child_objects: bool = True,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
    task: str | None = None,
    confirm_get_captured: bool = False,
) -> str:
    """Get from storage. WORK: task=; auto force_close. On busy retry; never ask user to close Designer."""
    if (target or "").strip().lower() in ("work", "prod", "base3"):
        return json_result(
            {
                "ok": False,
                "error": (
                    "storage_get is forbidden in the automated WORK edit pipeline. "
                    "Use exact storage_lock first; it aligns an uncaptured object before the locked dump."
                ),
                "step": "use_storage_lock",
                "stop": True,
            }
        )
    entire_work_error = _refuse_entire_work(
        target=target,
        entire_config=entire_config,
    )
    if entire_work_error:
        return entire_work_error
    if revised and not confirm_revised:
        return json_result(
            {
                "ok": False,
                "error": "revised=true overwrites local changes on locked objects; set confirm_revised=true.",
                "stop": True,
            }
        )
    if force and not confirm_force:
        return json_result(
            {
                "ok": False,
                "error": "force=true on storage_get requires confirm_force=true.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None
    ext_name = _extension_name(extension)
    task_err = require_work_task(task, target=target)
    if task_err:
        return json_result(task_err)
    if (target or "").strip().lower() in ("work", "prod", "base3"):
        pending_err = check_pending_stash_receipt(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if pending_err:
            pending_err["step"] = "refuse_pending_reapply"
            return json_result(pending_err)
    cap_err = refuse_get_captured(
        canon,
        target=target,
        extension=ext_name,
        confirm_get_captured=confirm_get_captured,
    )
    if cap_err:
        return json_result(cap_err)
    lock_err = acquire_object_locks(
        canon, task=task or "", target=target, extension=ext_name, tool="storage_get"
    )
    if lock_err:
        return json_result(lock_err)

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryUpdateCfg"]
    if revised:
        args.append("-revised")
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_storage_objects_file(
            canon, list_file, include_child_objects=include_child_objects
        )
        args.extend(["-Objects", str(list_file)])
    ext_name = _extension_name(extension)
    if ext_name:
        args.extend(["-Extension", ext_name])

    payload = _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
        extension_storage=bool(ext_name),
    )
    return _finish_storage(
        payload,
        kind="get",
        objects=canon,
        target=target,
        extension=ext_name,
        task=task,
    )


@mcp.tool(name="storage_lock")
def storage_lock(
    objects: list[str] | None = None,
    target: str = "work",
    revised: bool = False,
    confirm_revised: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    include_child_objects: bool = True,
    confirm_parent_object: bool = False,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
    task: str | None = None,
) -> str:
    """Capture in storage. WORK: task=; auto force_close. busy→retry; never ask user to close Designer."""
    entire_work_error = _refuse_entire_work(
        target=target,
        entire_config=entire_config,
    )
    if entire_work_error:
        return entire_work_error
    if (target or "").strip().lower() in ("work", "prod", "base3") and revised:
        return json_result(
            {
                "ok": False,
                "error": "revised storage_lock is forbidden in the automated WORK pipeline.",
                "step": "refuse_work_destructive_storage",
                "stop": True,
            }
        )
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
    if not entire_config:
        parent_err = refuse_parent_object_without_confirm(
            canon, confirm_parent_object=confirm_parent_object
        )
        if parent_err:
            return json_result(parent_err)
    ext_name = _extension_name(extension)
    task_err = require_work_task(task, target=target)
    if task_err:
        return json_result(task_err)
    if (target or "").strip().lower() in ("work", "prod", "base3"):
        pending_err = check_pending_stash_receipt(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if pending_err:
            pending_err["step"] = "refuse_pending_reapply"
            return json_result(pending_err)
    lock_err = acquire_object_locks(
        canon, task=task or "", target=target, extension=ext_name, tool="storage_lock"
    )
    if lock_err:
        return json_result(lock_err)

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryLock"]
    if revised:
        args.append("-revised")
    if not entire_config:
        list_file = work / "objects.txt"
        write_storage_objects_file(
            canon, list_file, include_child_objects=include_child_objects
        )
        args.extend(["-Objects", str(list_file)])
    if ext_name:
        args.extend(["-Extension", ext_name])

    payload = _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
        extension_storage=bool(ext_name),
    )
    return _finish_storage(
        payload,
        kind="lock",
        objects=canon,
        target=target,
        extension=ext_name,
        task=task,
    )


@mcp.tool(name="storage_unlock")
def storage_unlock(
    objects: list[str] | None = None,
    target: str = "work",
    force: bool = False,
    confirm_force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    include_child_objects: bool = True,
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
    task: str | None = None,
) -> str:
    """Release capture. force discards local changes — needs confirm_force."""
    entire_work_error = _refuse_entire_work(
        target=target,
        entire_config=entire_config,
    )
    if entire_work_error:
        return entire_work_error
    if (target or "").strip().lower() in ("work", "prod", "base3") and force:
        return json_result(
            {
                "ok": False,
                "error": "force storage_unlock is forbidden in the automated WORK pipeline.",
                "step": "refuse_work_destructive_storage",
                "stop": True,
            }
        )
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
    task_err = require_work_task(task, target=target)
    if task_err:
        return json_result(task_err)
    ext_name = _extension_name(extension)
    if (target or "").strip().lower() in ("work", "prod", "base3"):
        pending_err = check_pending_stash_receipt(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if pending_err:
            pending_err["step"] = "refuse_pending_reapply"
            return json_result(pending_err)

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryUnLock"]
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_storage_objects_file(
            canon, list_file, include_child_objects=include_child_objects
        )
        args.extend(["-Objects", str(list_file)])
    if ext_name:
        args.extend(["-Extension", ext_name])

    payload = _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
        extension_storage=bool(ext_name),
    )
    if payload.get("ok"):
        release_object_locks(canon, task=task, target=target, extension=ext_name)
        clear_lock_receipts(canon, target=target, extension=ext_name)
        if canon and (task or "").strip():
            payload["clearedIntegrityReceipts"] = clear_integrity_receipts(
                canon,
                task=task or "",
                target=target,
                extension=ext_name,
            )
    return json_result(payload)


@mcp.tool(name="storage_commit")
def storage_commit(
    objects: list[str] | None = None,
    comment: str = "",
    confirm: bool = False,
    keep_locked: bool = False,
    force: bool = False,
    confirm_force: bool = False,
    entire_config: bool = False,
    confirm_entire: bool = False,
    include_child_objects: bool = True,
    extension: str | bool | None = None,
    target: str = "work",
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
    task: str | None = None,
) -> str:
    """Put objects to storage. confirm=true + non-empty comment required. Agent: only on explicit user ask."""
    entire_work_error = _refuse_entire_work(
        target=target,
        entire_config=entire_config,
    )
    if entire_work_error:
        return entire_work_error
    if (target or "").strip().lower() in ("work", "prod", "base3") and force:
        return json_result(
            {
                "ok": False,
                "error": "force storage_commit is forbidden in the automated WORK pipeline.",
                "step": "refuse_work_destructive_storage",
                "stop": True,
            }
        )
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
    if force and not confirm_force:
        return json_result(
            {
                "ok": False,
                "error": "force=true on storage_commit requires confirm_force=true.",
                "stop": True,
            }
        )
    canon, err = _gate_objects(
        objects, entire_config=entire_config, confirm_entire=confirm_entire
    )
    if err:
        return err
    assert canon is not None
    task_err = require_work_task(task, target=target)
    if task_err:
        return json_result(task_err)
    if entire_config:
        return json_result(
            {
                "ok": False,
                "error": "Verified integrity commit currently requires an exact object list.",
                "step": "require_exact_commit_objects",
                "stop": True,
            }
        )
    if keep_locked and (target or "").strip().lower() in ("work", "prod", "base3"):
        return json_result(
            {
                "ok": False,
                "error": "keep_locked=true is incompatible with verified WORK tip checks.",
                "step": "require_verified_commit",
                "stop": True,
            }
        )

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryCommit", "-comment", comment.strip()]
    if keep_locked:
        args.append("-keepLocked")
    if force:
        args.append("-force")
    if not entire_config:
        list_file = work / "objects.txt"
        write_storage_objects_file(
            canon, list_file, include_child_objects=include_child_objects
        )
        args.extend(["-Objects", str(list_file)])
    ext_name = _extension_name(extension)
    if (target or "").strip().lower() in ("work", "prod", "base3"):
        integrity_error = check_load_receipt_for_commit(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if integrity_error:
            integrity_error["message"] = (
                "Refusing storage_commit until the exact task/object load has passed post-load verification."
            )
            return json_result(integrity_error)
        load_receipt_before = read_load_receipt(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if load_receipt_before is None:
            return json_result(
                {
                    "ok": False,
                    "error": "Verified load receipt disappeared before commit.",
                    "step": "require_verified_load_receipt",
                    "stop": True,
                }
            )
        precommit_dir = work / "pre-commit-snapshot"
        precommit_dir.mkdir(parents=True, exist_ok=True)
        precommit_list = precommit_dir / "objects.txt"
        write_list_file(canon, precommit_list)
        precommit_args = ["/DumpConfigToFiles", str(precommit_dir)]
        if ext_name:
            precommit_args.extend(["-Extension", ext_name])
        precommit_args.extend(
            ["-listFile", str(precommit_list), "-Format", "Hierarchical"]
        )
        precommit_payload = _run_storage_op(
            precommit_args,
            objects=canon,
            target=target,
            manage_session=manage_session,
            force_close=force_close,
            reopen_designer=False,
            work=precommit_dir,
            extension_storage=bool(ext_name),
        )
        if not precommit_payload.get("ok"):
            precommit_payload["step"] = "pre_commit_snapshot_failed"
            precommit_payload["stop"] = True
            return json_result(precommit_payload)
        try:
            precommit_hashes = hash_object_files(precommit_dir, canon)
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "pre_commit_snapshot_failed",
                    "stop": True,
                }
            )
        precommit_compare = compare_current_snapshot(
            load_receipt_before["verifiedHashes"],
            precommit_hashes,
        )
        if not precommit_compare.get("ok"):
            return json_result(
                {
                    "ok": False,
                    "error": "WORK changed after verified load; refusing storage_commit.",
                    "step": "work_changed_after_load",
                    "preCommitCompare": precommit_compare,
                    "preCommitSnapshotDir": str(precommit_dir),
                    "stop": True,
                }
            )
        load_receipt_after = read_load_receipt(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
        if load_receipt_after != load_receipt_before:
            return json_result(
                {
                    "ok": False,
                    "error": "Load receipt changed during pre-commit verification.",
                    "step": "work_changed_after_load",
                    "stop": True,
                }
            )
    if ext_name:
        args.extend(["-Extension", ext_name])

    payload = _run_storage_op(
        args,
        objects=canon,
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=False,
        work=work,
        extension_storage=bool(ext_name),
    )
    if payload.get("ok") and (target or "").strip().lower() in ("work", "prod", "base3"):
        tip_get_dir = work / "tip-get"
        tip_get_dir.mkdir(parents=True, exist_ok=True)
        tip_list = tip_get_dir / "objects.txt"
        write_storage_objects_file(
            canon,
            tip_list,
            include_child_objects=include_child_objects,
        )
        tip_get_args = [
            "/ConfigurationRepositoryUpdateCfg",
            "-Objects",
            str(tip_list),
        ]
        if ext_name:
            tip_get_args.extend(["-Extension", ext_name])
        tip_get_payload = _run_storage_op(
            tip_get_args,
            objects=canon,
            target=target,
            manage_session=manage_session,
            force_close=force_close,
            reopen_designer=False,
            work=tip_get_dir,
            extension_storage=bool(ext_name),
        )
        payload["tipGet"] = tip_get_payload
        if not tip_get_payload.get("ok"):
            payload["ok"] = False
            payload["error"] = "Commit succeeded but storage tip Get failed."
            payload["step"] = "post_commit_tip_verify_failed"
            payload["stop"] = True
        else:
            tip_dump_dir = work / "tip-snapshot"
            tip_dump_dir.mkdir(parents=True, exist_ok=True)
            tip_dump_list = tip_dump_dir / "objects.txt"
            write_list_file(canon, tip_dump_list)
            tip_dump_args = ["/DumpConfigToFiles", str(tip_dump_dir)]
            if ext_name:
                tip_dump_args.extend(["-Extension", ext_name])
            tip_dump_args.extend(
                ["-listFile", str(tip_dump_list), "-Format", "Hierarchical"]
            )
            tip_dump_payload = _run_storage_op(
                tip_dump_args,
                objects=canon,
                target=target,
                manage_session=manage_session,
                force_close=force_close,
                reopen_designer=reopen_designer,
                work=tip_dump_dir,
                extension_storage=bool(ext_name),
            )
            payload["tipDump"] = tip_dump_payload
            if not tip_dump_payload.get("ok"):
                payload["ok"] = False
                payload["error"] = "Commit succeeded but storage tip dump failed."
                payload["step"] = "post_commit_tip_verify_failed"
                payload["stop"] = True
            else:
                try:
                    tip_hashes = hash_object_files(tip_dump_dir, canon)
                except (IntegrityError, OSError) as exc:
                    payload["ok"] = False
                    payload["error"] = str(exc)
                    payload["step"] = "post_commit_tip_verify_failed"
                    payload["stop"] = True
                else:
                    tip_compare = compare_current_snapshot(
                        load_receipt_before["verifiedHashes"],
                        tip_hashes,
                    )
                    payload["storageTipSnapshotDir"] = str(tip_dump_dir)
                    payload["storageTipCompare"] = tip_compare
                    if not tip_compare.get("ok"):
                        payload["ok"] = False
                        payload["error"] = (
                            "Committed storage tip does not match the verified WORK source."
                        )
                        payload["step"] = "post_commit_tip_mismatch"
                        payload["stop"] = True
                    else:
                        payload["message"] = (
                            "Storage commit finished; storage tip matches verified WORK/source."
                        )
    if payload.get("ok") and not keep_locked:
        release_object_locks(
            canon,
            task=task,
            target=target,
            extension=ext_name,
        )
        clear_lock_receipts(canon, target=target, extension=ext_name)
        payload["clearedIntegrityReceipts"] = clear_integrity_receipts(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
    elif (
        not payload.get("ok")
        and payload.get("step", "").startswith("post_commit_")
        and not keep_locked
    ):
        release_object_locks(
            canon,
            task=task,
            target=target,
            extension=ext_name,
        )
        clear_lock_receipts(canon, target=target, extension=ext_name)
        payload["clearedIntegrityReceipts"] = clear_integrity_receipts(
            canon,
            task=task or "",
            target=target,
            extension=ext_name,
        )
    return json_result(payload)


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

    payload = _run_storage_op(
        args,
        objects=[],
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
        extension_storage=bool(ext_name),
    )
    payload["reportPath"] = str(out)
    payload["reportExists"] = out.is_file()
    return json_result(payload)


@mcp.tool(name="storage_dump_version")
def storage_dump_version(
    version: int,
    output_path: str | None = None,
    objects: list[str] | None = None,
    extract_dir: str | None = None,
    keep_configuration: bool = False,
    target: str = "work",
    extension: str | bool | None = None,
    manage_session: bool = True,
    force_close: bool = True,
    reopen_designer: bool | None = None,
) -> str:
    """Read-only export of an exact repository version, optionally extracting objects."""
    if version == 0 or version < -1:
        return json_result(
            {
                "ok": False,
                "error": "version must be a positive repository version or -1 for latest.",
                "stop": True,
            }
        )

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    ext_name = _extension_name(extension)
    suffix = ".cfe" if ext_name else ".cf"
    out = Path(output_path) if output_path else work / f"storage-v{version}{suffix}"
    if out.suffix.lower() != suffix:
        return json_result(
            {
                "ok": False,
                "error": f"output_path must have {suffix} extension.",
                "stop": True,
            }
        )

    tmp_root = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-storage"))).resolve().parent
    try:
        out.resolve().relative_to(tmp_root)
    except ValueError:
        return json_result(
            {
                "ok": False,
                "error": f"output_path must be under {tmp_root}.",
                "stop": True,
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["/ConfigurationRepositoryDumpCfg", str(out), "-v", str(version)]
    if ext_name:
        args.extend(["-Extension", ext_name])

    payload = _run_storage_op(
        args,
        objects=[],
        target=target,
        manage_session=manage_session,
        force_close=force_close,
        reopen_designer=reopen_designer,
        work=work,
        extension_storage=bool(ext_name),
    )
    payload["version"] = version
    payload["configurationPath"] = str(out)
    payload["configurationExists"] = out.is_file()
    if out.is_file():
        payload["configurationSize"] = out.stat().st_size
        payload["configurationSha256"] = _sha256_file(out)
    elif payload.get("ok"):
        payload["ok"] = False
        payload["error"] = "Designer reported success but the exported configuration file is missing."

    canon = _canon(objects)
    if not payload.get("ok") or not canon:
        return json_result(payload)
    if ext_name:
        payload["ok"] = False
        payload["error"] = "Object extraction from historical CFE requires a matching main configuration and is not supported yet."
        payload["stop"] = True
        return json_result(payload)

    extracted = Path(extract_dir) if extract_dir else work / f"objects-v{version}"
    try:
        extracted.resolve().relative_to(tmp_root)
    except ValueError:
        payload["ok"] = False
        payload["error"] = f"extract_dir must be under {tmp_root}."
        payload["stop"] = True
        return json_result(payload)
    extracted.mkdir(parents=True, exist_ok=True)

    onec_bin = env("ONEC_BIN", "") or ""
    if not Path(onec_bin).is_file():
        payload["ok"] = False
        payload["error"] = f"ONEC_BIN not found: {onec_bin}"
        return json_result(payload)

    ib_dir = work / f"extract-ib-v{version}"
    if ib_dir.exists():
        shutil.rmtree(ib_dir, ignore_errors=True)
    create_log = work / "extract-create.out"
    connection = f'File="{ib_dir}";'
    create_args = [
        onec_bin,
        "CREATEINFOBASE",
        connection,
        "/UseTemplate",
        str(out),
        "/AddInListN",
        "/DisableStartupDialogs",
        "/Out",
        str(create_log),
    ]
    create_code, create_text = _run_local_onec(create_args, create_log)
    if create_code != 0:
        shutil.rmtree(ib_dir, ignore_errors=True)
        empty_args = [
            onec_bin,
            "CREATEINFOBASE",
            connection,
            "/AddInListN",
            "/DisableStartupDialogs",
            "/Out",
            str(create_log),
        ]
        empty_code, empty_text = _run_local_onec(empty_args, create_log)
        load_log = work / "extract-load.out"
        load_args = [
            onec_bin,
            "DESIGNER",
            "/F",
            str(ib_dir),
            "/DisableStartupDialogs",
            "/Out",
            str(load_log),
            "/LoadCfg",
            str(out),
        ]
        load_code, load_text = _run_local_onec(load_args, load_log)
        create_text = "\n".join((create_text, empty_text, load_text))
        create_code = empty_code or load_code

    payload["extractCreateLogTail"] = "\n".join(create_text.splitlines()[-40:])
    if create_code != 0:
        payload["ok"] = False
        payload["error"] = "Failed to create a temporary infobase from the historical configuration."
        shutil.rmtree(ib_dir, ignore_errors=True)
        return json_result(payload)

    list_file = work / "extract-objects.txt"
    write_list_file(canon, list_file)
    dump_log = work / "extract-dump.out"
    dump_args = [
        onec_bin,
        "DESIGNER",
        "/F",
        str(ib_dir),
        "/DisableStartupDialogs",
        "/Out",
        str(dump_log),
        "/DumpConfigToFiles",
        str(extracted),
        "-listFile",
        str(list_file),
        "-Format",
        "Hierarchical",
    ]
    dump_code, dump_text = _run_local_onec(dump_args, dump_log)
    payload["extractLogTail"] = "\n".join(dump_text.splitlines()[-40:])
    payload["extractDir"] = str(extracted)
    payload["extractedPaths"] = sorted(
        str(path.relative_to(extracted)).replace("\\", "/")
        for path in extracted.rglob("*")
        if path.is_file()
    )
    payload["objects"] = canon
    if dump_code != 0 or not payload["extractedPaths"]:
        payload["ok"] = False
        payload["error"] = "Historical configuration exported, but object extraction failed."
    shutil.rmtree(ib_dir, ignore_errors=True)
    if not keep_configuration and payload.get("ok"):
        out.unlink(missing_ok=True)
        payload["configurationExists"] = False
        payload["configurationKept"] = False
    else:
        payload["configurationKept"] = out.is_file()
    return json_result(payload)


if __name__ == "__main__":
    run_mcp(mcp, default_port=8769)
