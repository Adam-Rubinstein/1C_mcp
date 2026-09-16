from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from onec_mcp_shared import DesignerResult  # noqa: E402
from onec_mcp_shared import work_gates as wg  # noqa: E402
from onec_mcp_shared import work_integrity as wi  # noqa: E402


def _load_server(name: str, package: str):
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "packages" / package / "server.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_work_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work_ib = tmp_path / "work-ib"
    work_ib.mkdir()
    repo_cf = tmp_path / "repo-cf"
    repo_cf.mkdir()
    monkeypatch.setenv("ONEC_IB_WORK", str(work_ib))
    monkeypatch.setenv("REPO_CF", str(repo_cf))
    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("MCP_GATES_ROOT", str(tmp_path / "gates"))
    monkeypatch.setenv("MCP_STAGING_SECRET", "server-gate-test-secret")
    monkeypatch.setenv("ONEC_STORAGE_PATH", str(tmp_path / "storage"))


def _write_form(root: Path, *, owner: bool = True) -> None:
    wrapper = root / "Documents" / "X" / "Forms" / "Y.xml"
    form = root / "Documents" / "X" / "Forms" / "Y" / "Ext" / "Form.xml"
    module = (
        root
        / "Documents"
        / "X"
        / "Forms"
        / "Y"
        / "Ext"
        / "Form"
        / "Module.bsl"
    )
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("<Form/>", encoding="utf-8")
    form.parent.mkdir(parents=True, exist_ok=True)
    owner_xml = (
        '<InputField name="ВыходныеИзделияСобственник" id="939">'
        "<DataPath>Объект.ВыходныеИзделия.Собственник</DataPath>"
        "</InputField>"
        if owner
        else ""
    )
    form.write_text(f"<Form><ChildItems>{owner_xml}</ChildItems></Form>", encoding="utf-8")
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "Процедура Проверка()\n\tРезультат = 1;\nКонецПроцедуры\n",
        encoding="utf-8",
    )


def test_work_dump_refuses_before_exact_storage_lock(tmp_path: Path) -> None:
    server = _load_server("dump_server_before_lock", "mcp-1c-dump")
    payload = json.loads(
        server.dump_objects(
            objects=["Document.X.Form.Y"],
            target_dir=str(tmp_path / "dump"),
            merge_into_repo=False,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "require_lock_before_dump"


def test_work_dump_never_accepts_automated_local_discard(tmp_path: Path) -> None:
    server = _load_server("dump_server_discard", "mcp-1c-dump")
    payload = json.loads(
        server.dump_objects(
            objects=["Document.X.Form.Y"],
            target_dir=str(tmp_path / "dump"),
            merge_into_repo=False,
            confirm_discard_local_edits=True,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "refuse_work_discard"


def test_work_dump_never_writes_directly_into_repo(
    tmp_path: Path,
) -> None:
    server = _load_server("dump_server_direct_repo", "mcp-1c-dump")
    payload = json.loads(
        server.dump_objects(
            objects=["Document.X.Form.Y"],
            target_dir=str(tmp_path / "repo-cf"),
            merge_into_repo=False,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "refuse_repo_target_dir"


def test_work_dump_after_lock_writes_immutable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _load_server("dump_server_after_lock", "mcp-1c-dump")
    objects = ["Document.X.Form.Y"]
    wi.write_lock_receipt(objects, task="restore-1194")

    dump_dir = tmp_path / "dump"

    def fake_designer(*_args, **_kwargs):
        _write_form(dump_dir)
        return DesignerResult(
            exit_code=0,
            log_path=str(dump_dir / "designer.out"),
            log_tail="",
            command=["1cv8"],
        )

    monkeypatch.setattr(server, "run_designer", fake_designer)
    monkeypatch.setattr(
        server,
        "with_managed_session",
        lambda _ib, callback, **_kwargs: (callback(), {}),
    )
    payload = json.loads(
        server.dump_objects(
            objects=objects,
            target_dir=str(dump_dir),
            merge_into_repo=False,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
        )
    )

    assert payload["ok"] is True, payload
    assert payload["integrityReceiptId"].startswith("dump_")
    baseline = Path(payload["immutableBaselineDir"])
    assert baseline.is_dir()
    assert wi.check_dump_receipt(
        objects,
        task="restore-1194",
        source_dir=dump_dir,
    ) is None
    wg.release_object_locks(
        objects,
        task="restore-1194",
        target="work",
        extension=None,
    )


def test_work_load_blocks_removed_form_element_before_designer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _load_server("load_server_deletion", "mcp-1c-load")
    objects = ["Document.X.Form.Y"]
    baseline_source = tmp_path / "baseline-source"
    baseline = tmp_path / "immutable-baseline"
    candidate = tmp_path / "candidate"
    _write_form(baseline_source, owner=True)
    _write_form(candidate, owner=False)
    hashes = wi.copy_object_files(baseline_source, objects, baseline)
    now = time.time()
    wi.write_lock_receipt(objects, task="restore-1194", ts=now - 1)
    wg.write_lock_receipt(objects, task="restore-1194")
    receipt_path = wi.write_dump_receipt(
        objects,
        task="restore-1194",
        source_dir=candidate,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=now,
    )
    monkeypatch.setattr(server, "check_adopted_uuids", lambda *_args, **_kwargs: {"ok": True})

    payload = json.loads(
        server.load_objects(
            objects=objects,
            source_dir=str(candidate),
            confirm=True,
            storage_captured=True,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
            integrity_receipt_id=receipt_path.name,
        )
    )

    assert payload["ok"] is False
    assert payload["step"] == "refuse_foreign_deletion"
    assert any(
        item["name"] == "ВыходныеИзделияСобственник"
        for item in payload["deletionManifest"]["removedFormElements"]
    )
    assert payload["deletionManifestToken"]


def test_work_load_prechecks_and_writes_verified_post_load_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _load_server("load_server_verified_success", "mcp-1c-load")
    objects = ["Document.X.Form.Y"]
    candidate = tmp_path / "candidate"
    baseline = tmp_path / "baseline"
    _write_form(candidate, owner=True)
    hashes = wi.copy_object_files(candidate, objects, baseline)
    now = time.time()
    wi.write_lock_receipt(objects, task="restore-1194", ts=now - 1)
    wg.write_lock_receipt(objects, task="restore-1194")
    receipt_path = wi.write_dump_receipt(
        objects,
        task="restore-1194",
        source_dir=candidate,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=now,
    )
    monkeypatch.setattr(server, "check_adopted_uuids", lambda *_args, **_kwargs: {"ok": True})

    def fake_snapshot(*, snapshot_dir: Path, **_kwargs):
        shutil.copytree(candidate, snapshot_dir, dirs_exist_ok=True)
        return (
            DesignerResult(
                exit_code=0,
                log_path=str(snapshot_dir / "designer.out"),
                log_tail="",
                command=["1cv8"],
            ),
            {},
        )

    monkeypatch.setattr(server, "_dump_integrity_snapshot", fake_snapshot)
    monkeypatch.setattr(
        server,
        "run_designer",
        lambda *_args, **_kwargs: DesignerResult(
            exit_code=0,
            log_path=str(tmp_path / "load.out"),
            log_tail="",
            command=["1cv8"],
        ),
    )
    monkeypatch.setattr(
        server,
        "with_managed_session",
        lambda _ib, callback, **_kwargs: (callback(), {}),
    )

    payload = json.loads(
        server.load_objects(
            objects=objects,
            source_dir=str(candidate),
            confirm=True,
            storage_captured=True,
            target="work",
            manage_session=True,
            force_close=True,
            task="restore-1194",
            integrity_receipt_id=receipt_path.name,
            reopen_designer=True,
        )
    )

    assert payload["ok"] is True, payload
    assert payload["postLoadCompare"]["ok"] is True
    assert Path(payload["frozenLoadSourceDir"]).is_dir()
    assert wi.hash_object_files(payload["frozenLoadSourceDir"], objects) == hashes
    load_receipt = wi.read_load_receipt(objects, task="restore-1194")
    assert load_receipt is not None
    assert load_receipt["verifiedHashes"] == hashes
    wg.release_object_locks(
        objects,
        task="restore-1194",
        target="work",
        extension=None,
    )


def test_storage_commit_requires_verified_exact_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _load_server("storage_server_commit_gate", "mcp-1c-storage")
    objects = ["Document.X.Form.Y"]
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _write_form(source)
    hashes = wi.copy_object_files(source, objects, baseline)
    now = time.time()
    wi.write_lock_receipt(objects, task="restore-1194", ts=now - 2)
    wi.write_dump_receipt(
        objects,
        task="restore-1194",
        source_dir=source,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=now - 1,
    )

    blocked = json.loads(
        server.storage_commit(
            objects=objects,
            comment="restore",
            confirm=True,
            target="work",
            task="restore-1194",
        )
    )
    assert blocked["ok"] is False
    assert blocked["step"] == "require_load_receipt"

    wi.write_load_receipt(
        objects,
        task="restore-1194",
        ts=now,
        verified_hashes=hashes,
        post_load_snapshot_dir=source,
    )
    monkeypatch.setattr(server, "_run_storage_op", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(server, "hash_object_files", lambda *_args, **_kwargs: hashes)
    committed = json.loads(
        server.storage_commit(
            objects=objects,
            comment="restore",
            confirm=True,
            target="work",
            task="restore-1194",
        )
    )
    assert committed["ok"] is True
    assert len(committed["clearedIntegrityReceipts"]) == 3


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"confirm_get_captured": True},
        {"revised": True, "confirm_revised": True},
        {"force": True, "confirm_force": True},
    ],
)
def test_work_storage_get_is_always_forbidden(
    arguments: dict[str, bool],
) -> None:
    server = _load_server(
        "storage_server_get_" + (next(iter(arguments)) if arguments else "plain"),
        "mcp-1c-storage",
    )
    payload = json.loads(
        server.storage_get(
            objects=["Document.X.Form.Y"],
            target="work",
            task="restore-1194",
            **arguments,
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "use_storage_lock"


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("storage_lock", {"revised": True, "confirm_revised": True}),
        ("storage_unlock", {"force": True, "confirm_force": True}),
        (
            "storage_commit",
            {
                "force": True,
                "confirm_force": True,
                "confirm": True,
                "comment": "forbidden",
            },
        ),
    ],
)
def test_work_storage_rejects_other_destructive_flags(
    method: str,
    arguments: dict[str, object],
) -> None:
    server = _load_server(
        f"storage_server_destructive_{method}",
        "mcp-1c-storage",
    )
    payload = json.loads(
        getattr(server, method)(
            objects=["Document.X.Form.Y"],
            target="work",
            task="restore-1194",
            **arguments,
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "refuse_work_destructive_storage"


def test_work_storage_rejects_entire_configuration() -> None:
    server = _load_server(
        "storage_server_entire_configuration",
        "mcp-1c-storage",
    )
    payload = json.loads(
        server.storage_lock(
            entire_config=True,
            confirm_entire=True,
            target="work",
            task="restore-1194",
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "require_exact_storage_objects"


def test_pending_stash_blocks_storage_lock_before_work_can_change(
    tmp_path: Path,
) -> None:
    server = _load_server(
        "storage_server_pending_lock",
        "mcp-1c-storage",
    )
    objects = ["Document.X.Form.Y"]
    stash = tmp_path / "stash"
    stash.mkdir()
    wi.write_pending_stash_receipt(
        objects,
        task="restore-1194",
        stash_dir=stash,
    )

    payload = json.loads(
        server.storage_lock(
            objects=objects,
            target="work",
            task="other-task",
        )
    )

    assert payload["ok"] is False
    assert payload["step"] == "refuse_pending_reapply"


def test_storage_commit_blocks_when_work_changed_after_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _load_server("storage_server_precommit_change", "mcp-1c-storage")
    objects = ["Document.X.Form.Y"]
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _write_form(source)
    hashes = wi.copy_object_files(source, objects, baseline)
    now = time.time()
    wi.write_lock_receipt(objects, task="restore-1194", ts=now - 2)
    wi.write_dump_receipt(
        objects,
        task="restore-1194",
        source_dir=source,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=now - 1,
    )
    wi.write_load_receipt(
        objects,
        task="restore-1194",
        ts=now,
        verified_hashes=hashes,
        post_load_snapshot_dir=source,
    )
    calls: list[list[str]] = []

    def fake_storage(args, **_kwargs):
        calls.append(args)
        return {"ok": True}

    changed_hashes = dict(hashes)
    changed_path = next(iter(changed_hashes))
    changed_hashes[changed_path] = "f" * 64
    monkeypatch.setattr(server, "_run_storage_op", fake_storage)
    monkeypatch.setattr(
        server,
        "hash_object_files",
        lambda *_args, **_kwargs: changed_hashes,
    )

    payload = json.loads(
        server.storage_commit(
            objects=objects,
            comment="restore",
            confirm=True,
            target="work",
            task="restore-1194",
        )
    )

    assert payload["ok"] is False
    assert payload["step"] == "work_changed_after_load"
    assert len(calls) == 1
    assert calls[0][0] == "/DumpConfigToFiles"


def test_reapply_stash_uses_three_way_merge_and_clears_pending(
    tmp_path: Path,
) -> None:
    server = _load_server("dump_server_reapply", "mcp-1c-dump")
    repo = tmp_path / "repo"
    source = repo / "src" / "cf"
    module = source / "CommonModules" / "Guard" / "Ext" / "Module.bsl"
    metadata = source / "CommonModules" / "Guard.xml"
    module.parent.mkdir(parents=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("<CommonModule/>", encoding="utf-8")
    filler = "".join(f"\tФоноваяСтрока{number} = {number};\n" for number in range(40))
    base_text = (
        "Процедура Проверка()\n"
        "\tБаза = 1;\n"
        "КонецПроцедуры\n\n"
        "Процедура Вторая()\n"
        "\tБазаВторая = 2;\n"
        f"{filler}"
        "КонецПроцедуры\n"
    )
    module.write_text(base_text, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "tests"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)

    locked_text = base_text
    local_text = (
        "Процедура Проверка()\n"
        "\tБаза = 1;\n"
        "КонецПроцедуры\n\n"
        "Процедура Вторая()\n"
        "\tБазаВторая = 2;\n"
        f"{filler}"
        "\tЛокальнаяПравка = 1;\n"
        "КонецПроцедуры\n"
    )
    module.write_text(locked_text, encoding="utf-8")
    objects = ["CommonModule.Guard"]
    baseline = tmp_path / "baseline"
    hashes = wi.copy_object_files(source, objects, baseline)
    now = time.time()
    wi.write_lock_receipt(objects, task="1246", ts=now - 1)
    receipt_path = wi.write_dump_receipt(
        objects,
        task="1246",
        source_dir=source,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=now,
    )
    stash = tmp_path / "stash"
    stashed_module = stash / "src" / "cf" / "CommonModules" / "Guard" / "Ext" / "Module.bsl"
    stashed_module.parent.mkdir(parents=True)
    stashed_module.write_text(local_text, encoding="utf-8")
    (stash / "manifest.json").write_text(
        json.dumps(
            {
                "dirtyPaths": [
                    "src/cf/CommonModules/Guard/Ext/Module.bsl",
                ],
                "files": [
                    {
                        "path": "src/cf/CommonModules/Guard/Ext/Module.bsl",
                        "kind": "tracked",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wi.write_pending_stash_receipt(
        objects,
        task="1246",
        stash_dir=stash,
    )

    payload = json.loads(
        server.reapply_stash(
            objects=objects,
            task="1246",
            integrity_receipt_id=receipt_path.name,
            stash_dir=str(stash),
            source_dir=str(source),
            confirm=True,
        )
    )

    assert payload["ok"] is True, payload
    merged = module.read_text(encoding="utf-8")
    assert "ЛокальнаяПравка = 1;" in merged
    assert wi.check_pending_stash_receipt(objects, task="1246") is None
