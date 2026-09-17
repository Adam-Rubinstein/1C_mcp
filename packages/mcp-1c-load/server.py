from __future__ import annotations

import shutil
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
    normalize_object_name,
    now_stamp,
    require_storage_path,
    resolve_ib,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402
from onec_mcp_shared.session import with_managed_session  # noqa: E402
from onec_mcp_shared.adopt_check import check_adopted_uuids  # noqa: E402
from onec_mcp_shared.config_root import (  # noqa: E402
    child_objects_count,
    configuration_ext_missing,
    copy_configuration_ext,
    copy_object_tree,
    gate_configuration_root_load,
    includes_configuration_root,
    patch_child_objects,
    sanity_check_configuration,
    write_prepared_marker,
)
from onec_mcp_shared.work_gates import (  # noqa: E402
    DesignerBusy,
    acquire_object_locks,
    check_lock_receipt,
    forms_incomplete_in_source,
    refuse_parent_object_without_confirm,
    require_work_task,
)
from onec_mcp_shared.work_integrity import (  # noqa: E402
    IntegrityError,
    build_structural_diff,
    check_dump_receipt as check_integrity_dump_receipt,
    check_pending_stash_receipt,
    compare_current_snapshot,
    compare_post_load_snapshot,
    copy_object_files,
    create_manifest_confirmation_token,
    hash_object_files,
    read_dump_receipt,
    verify_manifest_confirmation_token,
    write_load_receipt,
)

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env", Path(_ROOT).parent / ".env")

mcp = make_mcp("1c-load")


def _is_work_target(target: str) -> bool:
    return is_work_target(target)


def _is_dev_target(target: str) -> bool:
    return (target or "dev").strip().lower() in ("dev", "develop", "sandbox", "base2")


def _canon_objects(objects: list[str]) -> list[str]:
    return [normalize_object_name(o) for o in objects if (o or "").strip()]


def _dump_integrity_snapshot(
    *,
    ib: str,
    objects: list[str],
    target: str,
    extension: str | None,
    snapshot_dir: Path,
    force_close: bool,
    reopen_designer: bool,
):
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    list_file = snapshot_dir / "objects.txt"
    write_list_file(objects, list_file)
    args = ["/DumpConfigToFiles", str(snapshot_dir)]
    if extension:
        args.extend(["-Extension", extension])
    args.extend(["-listFile", str(list_file), "-Format", "Hierarchical"])

    def _do_snapshot():
        return run_designer(
            args,
            work_dir=snapshot_dir,
            objects=objects,
            target=target,
            attach_storage=True,
            extension_storage=bool(extension),
        )

    result, session_meta = with_managed_session(
        ib,
        _do_snapshot,
        force_close=force_close,
        reopen=reopen_designer,
        attach_storage=True,
    )
    result.dump_dir = str(snapshot_dir)
    result.dumped_paths = list_dumped_paths(snapshot_dir)
    return result, session_meta


def _health_payload(verbose: bool = False) -> dict:
    dev = env("ONEC_IB_DEV") or env("ONEC_IB")
    work = env("ONEC_IB_WORK")
    payload: dict = {
        "onecBin": env("ONEC_BIN"),
        "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
        "ibDev": dev,
        "ibDevExists": Path(dev or ".").is_dir(),
        "ibWork": work,
        "ibWorkExists": Path(work or ".").is_dir() if work else False,
        "repoCf": env("REPO_CF"),
        "repoCfe": env("REPO_CFE"),
        "ok": Path(env("ONEC_BIN", "") or ".").is_file() and Path(dev or ".").is_dir(),
        "storagePathSet": bool((env("ONEC_STORAGE_PATH") or "").strip()),
        "tools": [
            "load_prepare_work",
            "prepare_new_main_object",
            "restore_configuration_ext",
            "load_objects",
            "load_health",
        ],
        "mcpLoadRev": env("MCP_LOAD_REV") or "8-storage-attach-work",
        "note": (
            "Default target=dev. WORK requires exact get?→lock→dump→patch→precheck→"
            "load→post-dump and task-bound receipts. "
            "Never load Configuration.xml from git or without Ext/."
        ),
    }
    if verbose:
        payload["waitForUserPhrases"] = [
            "я захватил",
            "я всё захватил",
            "я все захватил",
            "делай",
            "можно грузить",
            "грузи",
        ]
    return payload


@mcp.tool(name="prepare_new_main_object")
def prepare_new_main_object(
    new_object: str,
    target: str = "work",
    manage_session: bool = True,
    force_close: bool = True,
) -> str:
    """
    Safe path for NEW main-CF object: dump Configuration from the same IB,
    patch ChildObjects, copy object+forms from REPO_CF into staging.
    Then capture root+object and load_objects(source_dir=stagingDir).
    """
    canon_obj = normalize_object_name(new_object)
    if "." not in canon_obj:
        return json_result({"ok": False, "error": "new_object must be Kind.Name (e.g. DataProcessor.X)"})
    kind, name = canon_obj.split(".", 1)
    repo_cf = Path(env("REPO_CF") or "")
    if not repo_cf.is_dir():
        return json_result({"ok": False, "error": f"REPO_CF missing: {repo_cf}"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    stamp = now_stamp()
    dump_dir = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-dump"))) / f"root_{stamp}"
    staging = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-load"))) / f"staging_{stamp}"
    dump_dir.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)

    list_file = dump_dir / "objects.txt"
    write_list_file(["Configuration"], list_file, for_load=False)
    args = ["/DumpConfigToFiles", str(dump_dir), "-listFile", str(list_file), "-Format", "Hierarchical"]

    def _do_dump():
        return run_designer(args, work_dir=dump_dir, objects=["Configuration"], target=target)

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do_dump,
                force_close=force_close,
                reopen=_is_work_target(target),
                restart_even_on_fail=False,
            )
        else:
            result = _do_dump()
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "session": session_meta})

    if result.exit_code != 0:
        payload = result.to_dict()
        payload["ok"] = False
        payload["message"] = "Failed to dump Configuration from IB for staging."
        return json_result(payload)

    dumped_cfg = dump_dir / "Configuration.xml"
    if not dumped_cfg.is_file():
        return json_result({"ok": False, "error": f"Configuration.xml not in dump: {dump_dir}"})

    baseline_count = child_objects_count(dumped_cfg)
    # Keep immutable baseline copy for sanity
    baseline_copy = staging / "_baseline_Configuration.xml"
    shutil.copy2(dumped_cfg, baseline_copy)
    shutil.copy2(dumped_cfg, staging / "Configuration.xml")

    try:
        # Ext MUST come from this IB dump — never from REPO_CF (5318 UI overwrite)
        ext_copied = copy_configuration_ext(dump_dir, staging)
        inserted = patch_child_objects(staging / "Configuration.xml", kind=kind, object_name=name)
        copied = copy_object_tree(repo_cf, staging, canon_obj)
    except Exception as exc:  # noqa: BLE001
        return json_result(
            {
                "ok": False,
                "step": "fix_configuration_root_source",
                "error": str(exc),
                "dumpDir": str(dump_dir),
                "stagingDir": str(staging),
                "message": (
                    "Staging failed. If Ext/ is missing from the IB dump, "
                    "first call restore_configuration_ext(target=..., ext_donor=dev) "
                    "(Ext was wiped — 5318), or reconnect storage and re-dump. "
                    "Do not copy Ext from git."
                ),
            }
        )

    sanity = sanity_check_configuration(
        staging / "Configuration.xml",
        baseline=baseline_copy,
        allow_extra_child=1,
    )
    if not sanity["ok"]:
        return json_result(
            {
                "ok": False,
                "step": "fix_configuration_root_source",
                "message": "Staging sanity failed: " + "; ".join(sanity["errors"]),
                "sanity": sanity,
                "stagingDir": str(staging),
            }
        )

    write_prepared_marker(
        staging,
        target=target,
        ib=ib,
        new_object=canon_obj,
        baseline_xml=baseline_copy,
        baseline_child_count=baseline_count,
    )

    objects_to_capture = ["Configuration", canon_obj]
    return json_result(
        {
            "ok": True,
            "step": "capture_then_approve",
            "target": target,
            "stagingDir": str(staging),
            "objectsToCapture": objects_to_capture,
            "childObjectInserted": inserted,
            "copiedPaths": copied,
            "extCopiedFromIbDump": ext_copied,
            "sanity": sanity,
            "session": session_meta,
            "message": (
                "Staging ready from IB Configuration dump.\n"
                "Capture in storage:\n"
                + "\n".join(f"- {o}" for o in objects_to_capture)
                + "\n\nThen: load_objects(objects=[...], source_dir=stagingDir, "
                "confirm=true, storage_captured=true, target=...)."
            ),
            "nextTool": {
                "name": "load_objects",
                "args": {
                    "objects": objects_to_capture,
                    "source_dir": str(staging),
                    "target": target,
                    "confirm": True,
                    "storage_captured": True,
                    "manage_session": True,
                    "force_close": True,
                },
            },
            "stop": True,
        }
    )


@mcp.tool(name="restore_configuration_ext")
def restore_configuration_ext(
    target: str = "work",
    ext_donor: str = "dev",
    manage_session: bool = True,
    force_close: bool = True,
) -> str:
    """
    Restore Configuration Ext/ onto target IB after a wipe (5318):
    dump Configuration.xml from target, copy Ext from donor IB dump, staging+marker.
    Does not change ChildObjects. Then capture Configuration and load_objects.
    """
    try:
        ib = resolve_ib(target)
        resolve_ib(ext_donor)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    stamp = now_stamp()
    dump_tmp = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-dump")))
    target_dump = dump_tmp / f"root_restore_{stamp}"
    donor_dump = dump_tmp / f"ext_donor_{stamp}"
    staging = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-load"))) / f"staging_ext_{stamp}"
    target_dump.mkdir(parents=True, exist_ok=True)
    donor_dump.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)

    def _dump_cfg(dump_dir: Path, tgt: str):
        list_file = dump_dir / "objects.txt"
        write_list_file(["Configuration"], list_file, for_load=False)
        args = [
            "/DumpConfigToFiles",
            str(dump_dir),
            "-listFile",
            str(list_file),
            "-Format",
            "Hierarchical",
        ]
        return run_designer(args, work_dir=dump_dir, objects=["Configuration"], target=tgt)

    session_meta = None
    try:
        if manage_session:
            result_t, session_meta = with_managed_session(
                ib,
                lambda: _dump_cfg(target_dump, target),
                force_close=force_close,
                reopen=_is_work_target(target),
                restart_even_on_fail=False,
            )
        else:
            result_t = _dump_cfg(target_dump, target)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": f"target dump: {exc}", "session": session_meta})

    if result_t.exit_code != 0 and not (target_dump / "Configuration.xml").is_file():
        payload = result_t.to_dict()
        payload["ok"] = False
        payload["message"] = "Failed to dump Configuration.xml from target IB."
        return json_result(payload)

    dumped_cfg = target_dump / "Configuration.xml"
    if not dumped_cfg.is_file():
        return json_result({"ok": False, "error": f"Configuration.xml missing in {target_dump}"})

    try:
        if manage_session and _is_work_target(ext_donor):
            result_d, _ = with_managed_session(
                resolve_ib(ext_donor),
                lambda: _dump_cfg(donor_dump, ext_donor),
                force_close=force_close,
                reopen=False,
                restart_even_on_fail=False,
            )
        else:
            result_d = _dump_cfg(donor_dump, ext_donor)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": f"donor dump: {exc}", "session": session_meta})

    if configuration_ext_missing(donor_dump):
        # WORK often omits Ext when storage disconnected; donor must have Ext
        return json_result(
            {
                "ok": False,
                "step": "fix_configuration_ext_incomplete",
                "message": (
                    f"Donor IB ({ext_donor}) dump has no Ext/. "
                    "Pick a donor that dumps Ext (usually dev), or reconnect storage."
                ),
                "donorDump": str(donor_dump),
                "missingExt": configuration_ext_missing(donor_dump),
            }
        )

    baseline_copy = staging / "_baseline_Configuration.xml"
    shutil.copy2(dumped_cfg, baseline_copy)
    shutil.copy2(dumped_cfg, staging / "Configuration.xml")
    try:
        ext_copied = copy_configuration_ext(donor_dump, staging)
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "stagingDir": str(staging)})

    baseline_count = child_objects_count(baseline_copy)
    sanity = sanity_check_configuration(
        staging / "Configuration.xml",
        baseline=baseline_copy,
        allow_extra_child=0,
    )
    if not sanity["ok"]:
        return json_result(
            {
                "ok": False,
                "step": "fix_configuration_root_source",
                "message": "Staging sanity failed: " + "; ".join(sanity["errors"]),
                "sanity": sanity,
                "stagingDir": str(staging),
            }
        )

    write_prepared_marker(
        staging,
        target=target,
        ib=ib,
        new_object="(ext-restore)",
        baseline_xml=baseline_copy,
        baseline_child_count=baseline_count,
        allow_extra_child=0,
    )

    return json_result(
        {
            "ok": True,
            "step": "capture_then_approve",
            "target": target,
            "extDonor": ext_donor,
            "stagingDir": str(staging),
            "objectsToCapture": ["Configuration"],
            "extCopiedFromDonorDump": ext_copied,
            "sanity": sanity,
            "session": session_meta,
            "message": (
                "Staging: target Configuration.xml + Ext from donor IB dump.\n"
                "Capture in storage:\n- Configuration\n\n"
                "Then: load_objects(objects=['Configuration'], source_dir=stagingDir, "
                "confirm=true, storage_captured=true, target=...)."
            ),
            "nextTool": {
                "name": "load_objects",
                "args": {
                    "objects": ["Configuration"],
                    "source_dir": str(staging),
                    "target": target,
                    "confirm": True,
                    "storage_captured": True,
                    "manage_session": True,
                    "force_close": True,
                },
            },
            "stop": True,
        }
    )


@mcp.tool(name="load_prepare_work")
def load_prepare_work(
    objects: list[str],
    extension: str | bool | None = None,
) -> str:
    """
    Checklist before WORK load when capture is NOT yet confirmed.
    Skip if user already said captured / «делай». Does not run Designer.
    """
    if not objects:
        return json_result({"ok": False, "error": "objects is required"})
    canon = _canon_objects(objects)
    if includes_configuration_root(canon):
        return json_result(
            {
                "ok": False,
                "step": "fix_configuration_root_source",
                "message": (
                    "Configuration root in objects list. "
                    "Do not load_prepare_work with git Configuration — "
                    "call prepare_new_main_object(new_object=...) instead."
                ),
                "objectsToCapture": canon,
                "stop": True,
            }
        )
    adopt = check_adopted_uuids(canon, repo_cf=env("REPO_CF"), repo_cfe=env("REPO_CFE"))
    if not adopt.get("ok", True) and not adopt.get("skipped"):
        return json_result(
            {
                "ok": False,
                "step": "fix_adopted_uuids",
                "target": "work",
                "objectsToCapture": canon,
                "adoptCheck": adopt,
                "message": (
                    (adopt.get("message") or "Adopted UUID mismatch.")
                    + "\nAlign cf Attribute uuid with cfe ExtendedConfigurationObject, "
                    "then capture/load **main** CF object first."
                ),
                "stop": True,
            }
        )
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    lines = [
        "Перед загрузкой в WORK:",
        "1) storage_lock(exact objects, task) — первый mutating шаг.",
        "2) dump_objects(target=work, task) → locked baseline receipt.",
        "3) точечный патч / reapply_stash three-way.",
        "4) load_objects(..., integrity_receipt_id=...) → frozen precheck + post-dump.",
        "",
        "Захватить:",
        "",
    ]
    for i, name in enumerate(canon, 1):
        lines.append(f"   {i}. {name}")
    if ext_name:
        lines.append("")
        lines.append(f"Расширение: {ext_name}")
        lines.append("(При новых Adopted — сначала объект **основной** КФ, не только расширение.)")
    lines.extend(
        [
            "",
            "Правильный порядок: storage_lock → dump-from-work → патч → frozen load+verify.",
            "Parent metadata object не покрывает Form; task/object/sourceDir должны совпадать.",
            "storage_commit разрешён только после verified load receipt и явной просьбы.",
            "До подтверждения агент НЕ вызывает load_objects на WORK.",
        ]
    )
    return json_result(
        {
            "ok": True,
            "step": "capture_then_approve",
            "target": "work",
            "objectsToCapture": canon,
            "extension": ext_name,
            "adoptCheck": adopt,
            "message": "\n".join(lines),
            "waitForUserPhrases": ["я захватил", "я всё захватил", "я все захватил", "делай", "можно грузить", "грузи"],
            "skipIfAlreadyCaptured": True,
            "nextTool": {
                "name": "load_objects",
                "args": {
                    "objects": canon,
                    "target": "work",
                    "confirm": True,
                    "storage_aligned": True,
                    "storage_captured": True,
                    "extension": extension if extension is not None else None,
                },
            },
            "stop": True,
        }
    )


@mcp.tool(name="load_objects")
def load_objects(
    objects: list[str],
    source_dir: str | None = None,
    extension: str | bool | None = None,
    confirm: bool = False,
    storage_captured: bool = False,
    storage_aligned: bool = False,
    confirm_parent_object: bool = False,
    target: str = "dev",
    manage_session: bool = False,
    force_close: bool = False,
    reopen_designer: bool | None = None,
    restart_even_on_fail: bool | None = None,
    task: str | None = None,
    integrity_receipt_id: str | None = None,
    confirm_foreign_deletions: bool = False,
    deletion_manifest_token: str | None = None,
    post_verify: bool = True,
) -> str:
    """WORK load from an exact locked dump with pre/post integrity verification."""
    if not confirm:
        return json_result(
            {
                "ok": False,
                "error": "Refusing load without confirm=true.",
                "hint": "WORK: exact storage_lock → dump → patch → frozen load with integrity receipt.",
            }
        )
    if not objects:
        return json_result({"ok": False, "error": "objects is required"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    t = (target or "dev").strip().lower()
    if _is_work_target(t):
        if not manage_session:
            return json_result(
                {
                    "ok": False,
                    "error": "Refusing WORK load without manage_session=true.",
                    "step": "require_manage_session",
                    "hint": "Pass manage_session=true and force_close=true. Agent must close/reopen Designer, not ask the user.",
                    "stop": True,
                }
            )
        manage_session = True
        force_close = True
    canon = _canon_objects(objects)
    parent_err = refuse_parent_object_without_confirm(
        canon, confirm_parent_object=confirm_parent_object
    )
    if parent_err:
        return json_result(parent_err)
    ext_name_preview = None
    if extension is True:
        ext_name_preview = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name_preview = extension

    adopt = check_adopted_uuids(
        canon,
        repo_cf=env("REPO_CF"),
        repo_cfe=env("REPO_CFE"),
        fail_if_repos_missing=_is_work_target(t),
    )
    if not adopt.get("ok", True):
        return json_result(
            {
                "ok": False,
                "error": "Adopted UUID gate failed before load.",
                "step": "fix_adopted_uuids",
                "adoptCheck": adopt,
                "objects": canon,
                "message": adopt.get("message") or adopt.get("reason"),
                "stop": True,
            }
        )

    src_preview = Path(
        source_dir or (env("REPO_CFE") if ext_name_preview else env("REPO_CF") or "")
    )
    root_gate = gate_configuration_root_load(
        canon,
        source_dir=src_preview,
        repo_cf=env("REPO_CF"),
    )
    if root_gate is not None and not root_gate.get("ok", False):
        return json_result(
            {
                "ok": False,
                "error": "Configuration root load refused.",
                **root_gate,
                "objects": canon,
                "stop": True,
            }
        )

    if _is_work_target(t):
        task_err = require_work_task(task, target=t)
        if task_err:
            return json_result(task_err)
        held_err = acquire_object_locks(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
            tool="load_objects",
        )
        if held_err:
            return json_result(held_err)
        lock_err = check_lock_receipt(
            canon,
            target=t,
            extension=ext_name_preview,
            storage_captured=storage_captured,
            task=task,
        )
        if lock_err:
            lock_err["objectsToCapture"] = canon
            lock_err["adoptCheck"] = adopt
            return json_result(lock_err)
        if not post_verify:
            return json_result(
                {
                    "ok": False,
                    "error": "post_verify=false is forbidden for WORK load.",
                    "step": "require_post_load_verify",
                    "stop": True,
                }
            )
        pending_err = check_pending_stash_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
        )
        if pending_err:
            pending_err["step"] = "refuse_pending_reapply"
            return json_result(pending_err)
        integrity_dump_err = check_integrity_dump_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
            source_dir=src_preview,
        )
        if integrity_dump_err:
            integrity_dump_err["step"] = "require_locked_dump"
            return json_result(integrity_dump_err)
        dump_receipt = read_dump_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name_preview,
        )
        if dump_receipt is None:
            return json_result(
                {
                    "ok": False,
                    "error": "Signed dump receipt could not be read.",
                    "step": "require_locked_dump",
                    "stop": True,
                }
            )
        if (
            not integrity_receipt_id
            or integrity_receipt_id != dump_receipt.get("receiptId")
        ):
            return json_result(
                {
                    "ok": False,
                    "error": "integrity_receipt_id does not match the exact locked dump.",
                    "step": "require_locked_dump",
                    "expectedIntegrityReceiptId": dump_receipt.get("receiptId"),
                    "stop": True,
                }
            )
        try:
            deletion_manifest = build_structural_diff(
                dump_receipt["immutableBaselineDir"],
                src_preview,
                canon,
            )
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "integrity_precheck_failed",
                    "stop": True,
                }
            )
        if deletion_manifest.get("errors"):
            return json_result(
                {
                    "ok": False,
                    "error": "Structural diff contains parse/read errors.",
                    "step": "integrity_precheck_failed",
                    "deletionManifest": deletion_manifest,
                    "stop": True,
                }
            )
        if deletion_manifest.get("hasRemovals"):
            manifest_token = create_manifest_confirmation_token(deletion_manifest)
            if (
                not confirm_foreign_deletions
                or not deletion_manifest_token
            ):
                return json_result(
                    {
                        "ok": False,
                        "error": "Locked baseline content would be deleted.",
                        "step": "refuse_foreign_deletion",
                        "deletionManifest": deletion_manifest,
                        "deletionManifestToken": manifest_token,
                        "stop": True,
                        "hint": (
                            "Review every removal against the approved TZ. Retry only with "
                            "confirm_foreign_deletions=true and this exact signed token."
                        ),
                    }
                )
            try:
                verify_manifest_confirmation_token(
                    deletion_manifest_token,
                    deletion_manifest,
                )
            except IntegrityError as exc:
                return json_result(
                    {
                        "ok": False,
                        "error": str(exc),
                        "step": "refuse_foreign_deletion",
                        "deletionManifest": deletion_manifest,
                        "deletionManifestToken": manifest_token,
                        "stop": True,
                    }
                )
        try:
            require_storage_path()
        except ValueError as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "require_storage_for_work",
                    "message": (
                        "WORK load requires ONEC_STORAGE_PATH. Offline LoadConfigFromFiles "
                        "desyncs local CF from storage (Get loop)."
                    ),
                    "stop": True,
                }
            )

    if reopen_designer is None:
        reopen_designer = False
    if restart_even_on_fail is None:
        restart_even_on_fail = not _is_work_target(t)
    if _is_dev_target(t):
        reopen_designer = False

    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension

    src = Path(source_dir or (env("REPO_CFE") if ext_name else env("REPO_CF") or ""))
    if not src.is_dir():
        return json_result({"ok": False, "error": f"source_dir not found: {src}"})

    missing_forms = forms_incomplete_in_source(canon, src)
    if missing_forms:
        return json_result(
            {
                "ok": False,
                "error": "source_dir missing Forms/.../Ext/Form.xml for Form objects.",
                "step": "fix_forms_incomplete",
                "missingForms": missing_forms,
                "stop": True,
                "hint": "Dump Form from WORK with storage attached; do not escalate to whole Document.",
            }
        )

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-load"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    load_source = src
    candidate_hashes: dict[str, str] | None = None
    pre_session_meta = None
    if _is_work_target(t):
        try:
            candidate_hashes = hash_object_files(src, canon)
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "integrity_candidate_hash_failed",
                    "stop": True,
                }
            )
        if not candidate_hashes:
            return json_result(
                {
                    "ok": False,
                    "error": "Candidate contains no files for the exact object list.",
                    "step": "integrity_candidate_hash_failed",
                    "stop": True,
                }
            )
        pre_dir = work / "pre-load-snapshot"
        try:
            pre_result, pre_session_meta = _dump_integrity_snapshot(
                ib=ib,
                objects=canon,
                target=target,
                extension=ext_name,
                snapshot_dir=pre_dir,
                force_close=force_close,
                reopen_designer=False,
            )
        except DesignerBusy as exc:
            return json_result(exc.payload)
        except Exception as exc:  # noqa: BLE001
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "work_preload_snapshot_failed",
                    "stop": True,
                }
            )
        if not pre_result.to_dict().get("ok"):
            return json_result(
                {
                    **pre_result.to_dict(),
                    "ok": False,
                    "step": "work_preload_snapshot_failed",
                    "session": pre_session_meta,
                    "stop": True,
                }
            )
        try:
            pre_hashes = hash_object_files(pre_dir, canon)
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "work_preload_snapshot_failed",
                    "session": pre_session_meta,
                    "stop": True,
                }
            )
        current_compare = compare_current_snapshot(
            dump_receipt["baselineHashes"],
            pre_hashes,
        )
        if not current_compare.get("ok"):
            return json_result(
                {
                    "ok": False,
                    "error": "WORK changed after the locked baseline dump.",
                    "step": "work_changed_since_dump",
                    "currentWorkCompare": current_compare,
                    "preLoadSnapshotDir": str(pre_dir),
                    "session": pre_session_meta,
                    "stop": True,
                    "hint": "Take a new locked dump and rebase the patch. Do not overwrite WORK.",
                }
            )
        receipt_recheck = check_integrity_dump_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name,
            source_dir=src,
        )
        latest_dump_receipt = read_dump_receipt(
            canon,
            task=task or "",
            target=t,
            extension=ext_name,
        )
        if (
            receipt_recheck
            or latest_dump_receipt is None
            or latest_dump_receipt.get("receiptId") != integrity_receipt_id
        ):
            return json_result(
                {
                    "ok": False,
                    "error": "Locked dump receipt changed during pre-load verification.",
                    "step": "require_locked_dump",
                    "receiptError": receipt_recheck,
                    "stop": True,
                }
            )
        try:
            candidate_recheck = compare_post_load_snapshot(
                candidate_hashes,
                hash_object_files(src, canon),
            )
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "candidate_changed_during_precheck",
                    "stop": True,
                }
            )
        if not candidate_recheck.get("ok"):
            return json_result(
                {
                    "ok": False,
                    "error": "Candidate source changed during pre-load verification.",
                    "step": "candidate_changed_during_precheck",
                    "candidateCompare": candidate_recheck,
                    "stop": True,
                }
            )
        frozen_source = work / "frozen-load-source"
        try:
            frozen_hashes = copy_object_files(
                src,
                canon,
                frozen_source,
            )
        except (IntegrityError, OSError) as exc:
            return json_result(
                {
                    "ok": False,
                    "error": str(exc),
                    "step": "freeze_load_source_failed",
                    "stop": True,
                }
            )
        frozen_compare = compare_post_load_snapshot(
            candidate_hashes,
            frozen_hashes,
        )
        if not frozen_compare.get("ok"):
            return json_result(
                {
                    "ok": False,
                    "error": "Frozen load source does not match the verified candidate.",
                    "step": "freeze_load_source_failed",
                    "frozenSourceCompare": frozen_compare,
                    "stop": True,
                }
            )
        load_source = frozen_source
    list_file = work / "objects.txt"
    canon = _canon_objects(objects)
    write_list_file(canon, list_file, for_load=True)

    args = ["/LoadConfigFromFiles", str(load_source)]
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-listFile", str(list_file), "-Format", "Hierarchical"])

    attach = True if _is_work_target(t) else False

    def _do_load():
        return run_designer(
            args,
            work_dir=work,
            objects=canon,
            target=target,
            attach_storage=attach,
            extension_storage=bool(ext_name),
        )

    session_meta = None
    load_reopen_designer = (
        False if _is_work_target(t) and post_verify else bool(reopen_designer)
    )
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do_load,
                force_close=force_close,
                reopen=load_reopen_designer,
                restart_even_on_fail=restart_even_on_fail,
                attach_storage=attach or None,
            )
        else:
            result = _do_load()
    except DesignerBusy as exc:
        return json_result({**exc.payload, "session": session_meta})
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "session": session_meta})

    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    if _is_work_target(t):
        payload["frozenLoadSourceDir"] = str(load_source)
    if session_meta:
        payload["session"] = session_meta
    if result.objects_to_get:
        payload["message"] = (
            "Need get from storage: "
            + ", ".join(result.objects_to_get)
            + ". Do not run storage_get in WORK; repeat exact storage_lock, then take a new dump."
        )
        payload["objectsToGet"] = result.objects_to_get
        payload["ok"] = False
    elif result.storage_offline and _is_work_target(t):
        payload["message"] = (
            "WORK load reported storage offline — not success. Check ONEC_STORAGE_*."
        )
        payload["ok"] = False
    elif result.storage_error or result.objects_to_capture:
        payload["message"] = (
            "Load failed due to configuration storage / object locks. "
            "Capture these objects, then retry: "
            + ", ".join(result.objects_to_capture or canon)
        )
        payload["objectsToCapture"] = result.objects_to_capture or canon
        payload["ok"] = False
    elif result.exit_code != 0:
        payload["message"] = "Designer failed. See logTail. Update DB configuration in Designer if metadata changed."
        payload["ok"] = False
    else:
        payload["ok"] = True
        payload["message"] = "Load finished. If metadata structure changed, update database configuration in Designer."
        if _is_work_target(t):
            post_dir = work / "post-load-snapshot"
            post_session_meta = None
            try:
                post_result, post_session_meta = _dump_integrity_snapshot(
                    ib=ib,
                    objects=canon,
                    target=target,
                    extension=ext_name,
                    snapshot_dir=post_dir,
                    force_close=force_close,
                    reopen_designer=bool(reopen_designer),
                )
            except DesignerBusy as exc:
                payload.update(exc.payload)
                payload["ok"] = False
                payload["step"] = "post_load_snapshot_failed"
                post_result = None
            except Exception as exc:  # noqa: BLE001
                payload["ok"] = False
                payload["error"] = str(exc)
                payload["step"] = "post_load_snapshot_failed"
                post_result = None

            if post_result is not None and not post_result.to_dict().get("ok"):
                payload["ok"] = False
                payload["step"] = "post_load_snapshot_failed"
                payload["postLoadResult"] = post_result.to_dict()
            elif post_result is not None:
                try:
                    post_hashes = hash_object_files(post_dir, canon)
                except (IntegrityError, OSError) as exc:
                    payload["ok"] = False
                    payload["error"] = str(exc)
                    payload["step"] = "post_load_snapshot_failed"
                else:
                    post_compare = compare_post_load_snapshot(
                        candidate_hashes or {},
                        post_hashes,
                    )
                    payload["postLoadSnapshotDir"] = str(post_dir)
                    payload["postLoadCompare"] = post_compare
                    if not post_compare.get("ok"):
                        payload["ok"] = False
                        payload["error"] = (
                            "Post-load WORK snapshot does not match the exact source."
                        )
                        payload["step"] = "post_load_regression"
                        payload["stop"] = True
                    else:
                        try:
                            load_receipt_path = write_load_receipt(
                                canon,
                                task=task or "",
                                target=t,
                                extension=ext_name,
                                verified_hashes=post_hashes,
                                post_load_snapshot_dir=post_dir,
                            )
                        except (IntegrityError, OSError) as exc:
                            payload["ok"] = False
                            payload["error"] = str(exc)
                            payload["step"] = "write_load_integrity_receipt"
                            payload["stop"] = True
                        else:
                            payload["integrityLoadReceiptId"] = load_receipt_path.name
                            payload["integrityLoadReceipt"] = str(load_receipt_path)
                            payload["warning"] = (
                                "WORK source and post-load dump match. Keep the object queue "
                                "until verified storage_commit or storage_unlock."
                            )
                            payload["message"] = (
                                "WORK load and post-load integrity verification finished."
                            )
            if post_session_meta:
                payload["postLoadSession"] = post_session_meta
                if post_session_meta.get("userAction"):
                    payload["userAction"] = post_session_meta["userAction"]
    if session_meta and session_meta.get("userAction") and not payload.get("userAction"):
        payload["userAction"] = session_meta["userAction"]
    if session_meta and session_meta.get("warning") and not payload.get("sessionWarning"):
        payload["sessionWarning"] = session_meta["warning"]
    return json_result(payload)


@mcp.tool(name="load_health")
def load_health(verbose: bool = False) -> str:
    """Health: ONEC_BIN, DEV/WORK IB, repo paths; lists load tools."""
    return json_result(_health_payload(verbose=verbose))


def main() -> None:
    run_mcp(mcp, default_port=18762)


if __name__ == "__main__":
    main()
