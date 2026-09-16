from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
    _gates_root,
)
from onec_mcp_shared.work_integrity import (  # noqa: E402
    IntegrityError,
    build_structural_diff,
    check_dump_receipt as check_integrity_dump_receipt,
    check_lock_receipt as check_integrity_lock_receipt,
    check_pending_stash_receipt,
    clear_integrity_receipts,
    clear_pending_stash_receipt,
    copy_object_files,
    create_manifest_token,
    read_dump_receipt,
    read_pending_stash_receipt,
    write_dump_receipt,
    write_pending_stash_receipt,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402
from onec_mcp_shared.session import with_managed_session  # noqa: E402
from onec_mcp_shared import meta_rag  # noqa: E402

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
    snapshot_only: bool = False,
) -> str:
    """Partial dump. WORK: exact lock first; snapshot_only is read-only forensic capture."""
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
        if confirm_discard_local_edits:
            return json_result(
                {
                    "ok": False,
                    "error": (
                        "Automated discard of local edits is forbidden for WORK. "
                        "Preserve and merge every delta or resolve it outside the load pipeline."
                    ),
                    "step": "refuse_work_discard",
                    "stop": True,
                }
            )

    ext_name_preview = None
    if extension is True:
        ext_name_preview = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name_preview = extension

    if is_work_target(t) and target_dir:
        requested_target = Path(target_dir).resolve()
        configured_repo = env("REPO_CFE") if ext_name_preview else env("REPO_CF")
        if configured_repo:
            repo_path = Path(configured_repo).resolve()
            try:
                requested_target.relative_to(repo_path)
            except ValueError:
                pass
            else:
                return json_result(
                    {
                        "ok": False,
                        "error": (
                            "Direct WORK dump into REPO_CF/REPO_CFE is forbidden. "
                            "Use merge_into_repo=true for dirty-stash protection or a temp staging directory."
                        ),
                        "step": "refuse_repo_target_dir",
                        "stop": True,
                    }
                )

    canon = [normalize_object_name(o) for o in objects]
    if snapshot_only and merge_into_repo:
        return json_result(
            {
                "ok": False,
                "error": "snapshot_only=true requires merge_into_repo=false.",
                "stop": True,
            }
        )
    if is_work_target(t) and not snapshot_only:
        task_err = require_work_task(task, target=t)
        if task_err:
            return json_result(task_err)
        integrity_lock_err = check_integrity_lock_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
        )
        if integrity_lock_err:
            integrity_lock_err["step"] = "require_lock_before_dump"
            integrity_lock_err["hint"] = (
                "Run storage_lock for this exact task/object list, then dump again. "
                "A dump made before lock is never a load baseline."
            )
            return json_result(integrity_lock_err)
        pending_err = check_pending_stash_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
        )
        if pending_err:
            pending_err["step"] = "refuse_pending_reapply"
            return json_result(pending_err)
        lock_err = acquire_object_locks(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
            tool="dump_objects",
        )
        if lock_err:
            return json_result(lock_err)
        clear_integrity_receipts(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
            kinds=("dump", "load"),
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
                    "WORK locked baseline dump finished with storage attached. "
                    "Patch only this baseline; pre-load and post-load verification are mandatory."
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

    immutable_baseline_dir: Path | None = None
    baseline_hashes: dict[str, str] | None = None
    if (
        is_work_target(t)
        and not snapshot_only
        and payload.get("ok")
        and real_files
    ):
        safe_task = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in (task or "task")
        )
        immutable_baseline_dir = (
            _gates_root()
            / "work_integrity"
            / "baselines"
            / f"{now_stamp()}_{os.getpid()}_{safe_task}"
        )
        try:
            baseline_hashes = copy_object_files(
                dump_dir,
                canon,
                immutable_baseline_dir,
            )
        except (IntegrityError, OSError) as exc:
            payload["ok"] = False
            payload["step"] = "write_immutable_baseline"
            payload["error"] = str(exc)
            payload["stop"] = True

    if merge_into_repo and payload.get("ok") and real_files:
        repo = env("REPO_CFE") if ext_name else env("REPO_CF")
        if not repo:
            payload["mergeError"] = "REPO_CF / REPO_CFE not set"
            payload["ok"] = False
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
                try:
                    pending_path = write_pending_stash_receipt(
                        canon,
                        task=task or "",
                        target=t,
                        extension=ext_name,
                        stash_dir=payload["dirtyStash"].get("stashDir"),
                    )
                except (IntegrityError, OSError) as exc:
                    payload["ok"] = False
                    payload["error"] = str(exc)
                    payload["step"] = "write_pending_stash_receipt"
                    payload["stop"] = True
                    return json_result(payload)
                payload["pendingStashReceipt"] = str(pending_path)
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
            # Indexes (RAG / call-graph) are stale after successful merge into repo
            try:
                meta_rag.mark_dirty(env("DUMP_TMP_ROOT") or None)
                payload["indexesDirty"] = True
                payload["indexesHint"] = "Call rag_reindex and graph_rebuild (1c-files) after dump merge"
            except Exception as exc:  # noqa: BLE001
                payload["indexesDirtyError"] = str(exc)

    if (
        is_work_target(t)
        and not snapshot_only
        and payload.get("ok")
        and real_files
        and immutable_baseline_dir is not None
        and baseline_hashes is not None
    ):
        repo_source = env("REPO_CFE") if ext_name else env("REPO_CF")
        source_for_load = Path(repo_source or "") if merge_into_repo else dump_dir
        try:
            receipt_path = write_dump_receipt(
                canon,
                task=task or "",
                source_dir=source_for_load,
                immutable_baseline_dir=immutable_baseline_dir,
                baseline_hashes=baseline_hashes,
                target=t,
                extension=ext_name,
            )
            payload["integrityReceiptId"] = receipt_path.name
            payload["integrityReceipt"] = str(receipt_path)
            payload["immutableBaselineDir"] = str(immutable_baseline_dir)
            payload["baselineHashes"] = baseline_hashes
            if payload.get("dirtyStash"):
                payload["step"] = "reapply_stash"
                payload["message"] = (
                    "Dirty files were stashed. WORK load is blocked until all stash deltas "
                    "are merged three-way onto the immutable locked baseline."
                )
        except (IntegrityError, OSError) as exc:
            payload["ok"] = False
            payload["step"] = "write_dump_integrity_receipt"
            payload["error"] = str(exc)
            payload["stop"] = True
    return json_result(payload)


@mcp.tool()
def reapply_stash(
    objects: list[str],
    task: str,
    integrity_receipt_id: str,
    stash_dir: str,
    source_dir: str | None = None,
    confirm: bool = False,
    target: str = "work",
    extension: str | bool | None = None,
) -> str:
    """Three-way reapply of every pending dirty-stash delta onto a locked dump."""
    if not confirm:
        return json_result(
            {
                "ok": False,
                "error": "confirm=true is required to modify the dumped source.",
                "stop": True,
            }
        )
    t = (target or "work").strip().lower()
    if not is_work_target(t):
        return json_result(
            {
                "ok": False,
                "error": "reapply_stash is only valid for a WORK dump receipt.",
                "stop": True,
            }
        )
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    canon = [normalize_object_name(obj) for obj in objects]
    receipt = read_dump_receipt(
        canon,
        task=task,
        target=t,
        extension=ext_name,
    )
    if receipt is None or receipt.get("receiptId") != integrity_receipt_id:
        return json_result(
            {
                "ok": False,
                "error": "Exact locked dump receipt is missing or does not match.",
                "step": "require_locked_dump",
                "stop": True,
            }
        )
    source = Path(source_dir or receipt["sourceDir"]).resolve()
    receipt_error = check_integrity_dump_receipt(
        canon,
        task=task,
        target=t,
        extension=ext_name,
        source_dir=source,
    )
    if receipt_error:
        return json_result(receipt_error)
    pending = read_pending_stash_receipt(
        canon,
        target=t,
        extension=ext_name,
    )
    stash = Path(stash_dir).resolve()
    if (
        pending is None
        or pending.get("task") != task
        or Path(pending.get("stashDir") or "").resolve() != stash
    ):
        return json_result(
            {
                "ok": False,
                "error": "Pending stash receipt does not match this task/object/stashDir.",
                "step": "refuse_pending_reapply",
                "stop": True,
            }
        )
    manifest_path = stash / "manifest.json"
    try:
        stash_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return json_result(
            {
                "ok": False,
                "error": f"Cannot read dirty stash manifest: {exc}",
                "step": "refuse_pending_reapply",
                "stop": True,
            }
        )

    git_root_result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if git_root_result.returncode != 0:
        return json_result(
            {
                "ok": False,
                "error": "sourceDir is not inside a git worktree.",
                "step": "refuse_pending_reapply",
                "stop": True,
            }
        )
    git_root = Path(git_root_result.stdout.strip()).resolve()
    staged_outputs: list[tuple[Path, bytes | None]] = []
    applied: list[str] = []
    conflicts: list[dict[str, str]] = []

    for entry in stash_manifest.get("files") or []:
        rel = str(entry.get("path") or "").replace("\\", "/")
        kind = str(entry.get("kind") or "")
        if not rel or ".." in Path(rel).parts:
            conflicts.append({"path": rel, "error": "unsafe stash path"})
            continue
        destination = (git_root / rel).resolve()
        try:
            destination.relative_to(source)
        except ValueError:
            conflicts.append({"path": rel, "error": "stash path is outside sourceDir"})
            continue
        stashed_file = stash / rel.replace("/", os.sep)
        if kind != "tracked_deleted" and not stashed_file.is_file():
            conflicts.append({"path": rel, "error": "stashed file is missing"})
            continue
        if kind == "untracked":
            if destination.is_file() and destination.read_bytes() != stashed_file.read_bytes():
                conflicts.append(
                    {"path": rel, "error": "untracked file now exists with different content"}
                )
                continue
            staged_outputs.append((destination, stashed_file.read_bytes()))
            applied.append(rel)
            continue
        if kind not in {"tracked", "tracked_deleted"}:
            conflicts.append({"path": rel, "error": f"unsupported stash kind: {kind}"})
            continue

        base_result = subprocess.run(
            ["git", "-C", str(git_root), "show", f"HEAD:{rel}"],
            capture_output=True,
            check=False,
        )
        if base_result.returncode != 0:
            conflicts.append({"path": rel, "error": "cannot read HEAD base"})
            continue
        current_bytes = destination.read_bytes() if destination.is_file() else b""
        stashed_bytes = (
            b"" if kind == "tracked_deleted" else stashed_file.read_bytes()
        )
        use_crlf = b"\r\n" in current_bytes
        current_for_merge = current_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        base_for_merge = base_result.stdout.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        stash_for_merge = stashed_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        with tempfile.TemporaryDirectory(prefix="onec-stash-merge-") as temp_name:
            temp = Path(temp_name)
            current_file = temp / "current"
            base_file = temp / "base"
            stash_file = temp / "stash"
            current_file.write_bytes(current_for_merge)
            base_file.write_bytes(base_for_merge)
            stash_file.write_bytes(stash_for_merge)
            merge_result = subprocess.run(
                [
                    "git",
                    "merge-file",
                    "-p",
                    str(current_file),
                    str(base_file),
                    str(stash_file),
                ],
                capture_output=True,
                check=False,
            )
        if merge_result.returncode != 0:
            conflicts.append(
                {
                    "path": rel,
                    "error": "three-way merge conflict; no files were changed",
                }
            )
            continue
        merged_bytes = merge_result.stdout
        if use_crlf:
            merged_bytes = merged_bytes.replace(b"\n", b"\r\n")
        merged_content = (
            None
            if kind == "tracked_deleted" and not merged_bytes
            else merged_bytes
        )
        staged_outputs.append((destination, merged_content))
        applied.append(rel)

    if conflicts:
        return json_result(
            {
                "ok": False,
                "error": "Dirty stash could not be merged completely.",
                "step": "reapply_stash_conflict",
                "conflicts": conflicts,
                "stop": True,
            }
        )
    for destination, content in staged_outputs:
        if content is None:
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.reapply.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)

    deletion_manifest = build_structural_diff(
        receipt["immutableBaselineDir"],
        source,
        canon,
    )
    if deletion_manifest.get("errors"):
        return json_result(
            {
                "ok": False,
                "error": "Reapplied source has structural parse/read errors.",
                "step": "reapply_stash_invalid_source",
                "deletionManifest": deletion_manifest,
                "stop": True,
            }
        )
    if not clear_pending_stash_receipt(
        canon,
        task=task,
        target=t,
        extension=ext_name,
    ):
        return json_result(
            {
                "ok": False,
                "error": "Three-way merge finished but pending receipt could not be cleared.",
                "step": "refuse_pending_reapply",
                "stop": True,
            }
        )
    return json_result(
        {
            "ok": True,
            "step": "stash_reapplied",
            "objects": canon,
            "task": task,
            "sourceDir": str(source),
            "appliedPaths": sorted(applied),
            "deletionManifest": deletion_manifest,
            "deletionManifestToken": create_manifest_token(deletion_manifest),
            "message": (
                "All dirty stash deltas were merged three-way onto the locked dump. "
                "Any removals still require the exact signed manifest at load."
            ),
        }
    )


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
