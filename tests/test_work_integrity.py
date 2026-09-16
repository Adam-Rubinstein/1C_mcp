from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from onec_mcp_shared import work_integrity as wi  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_integrity_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path / "dump-tmp"))
    monkeypatch.setenv("MCP_GATES_ROOT", str(tmp_path / "gates"))
    monkeypatch.setenv("MCP_STAGING_SECRET", "test-work-integrity-secret")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_common_module(root: Path, name: str = "Guard") -> None:
    _write(root / "CommonModules" / f"{name}.xml", "<CommonModule/>")
    _write(
        root / "CommonModules" / name / "Ext" / "Module.bsl",
        "Процедура Проверка()\n\tРезультат = 1;\nКонецПроцедуры\n",
    )


def _form_xml(*, include_owner: bool) -> str:
    owner = """
      <InputField name="ВыходныеИзделияСобственник" id="41">
        <DataPath>Объект.ВыходныеИзделия.Собственник</DataPath>
      </InputField>""" if include_owner else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="http://v8.1c.ru/8.3/xcf/logform">
  <ChildItems>{owner}
    <InputField name="Количество" id="42">
      <DataPath>Объект.Количество</DataPath>
    </InputField>
  </ChildItems>
</Form>
"""


def test_parent_does_not_include_child_form(tmp_path: Path) -> None:
    root = tmp_path / "snapshot"
    _write(root / "Documents" / "X.xml", "<Document/>")
    _write(root / "Documents" / "X" / "Ext" / "ObjectModule.bsl", "А = 1;\n")
    _write(root / "Documents" / "X" / "Forms" / "Y.xml", "<Form/>")
    _write(root / "Documents" / "X" / "Forms" / "Y" / "Ext" / "Form.xml", "<Form/>")
    _write(
        root / "Documents" / "X" / "Forms" / "Y" / "Ext" / "Form" / "Module.bsl",
        "Б = 2;\n",
    )

    parent_files = wi.collect_object_files(root, ["Document.X"])
    assert parent_files == [
        "Documents/X.xml",
        "Documents/X/Ext/ObjectModule.bsl",
    ]
    assert not any("/Forms/" in path for path in parent_files)

    form_files = wi.collect_object_files(root, ["Документ.X.Форма.Y"])
    assert form_files == [
        "Documents/X/Forms/Y.xml",
        "Documents/X/Forms/Y/Ext/Form.xml",
        "Documents/X/Forms/Y/Ext/Form/Module.bsl",
    ]
    assert "Documents/X.xml" not in form_files


@pytest.mark.parametrize(
    ("object_name", "folder"),
    [
        ("Catalog.X", "Catalogs"),
        ("DataProcessor.X", "DataProcessors"),
    ],
)
def test_other_parent_types_exclude_forms(
    tmp_path: Path,
    object_name: str,
    folder: str,
) -> None:
    root = tmp_path / "snapshot"
    _write(root / folder / "X.xml", "<Object/>")
    _write(root / folder / "X" / "Ext" / "ObjectModule.bsl", "А = 1;\n")
    _write(root / folder / "X" / "Forms" / "Y.xml", "<Form/>")
    assert wi.collect_object_files(root, [object_name]) == [
        f"{folder}/X.xml",
        f"{folder}/X/Ext/ObjectModule.bsl",
    ]


def test_hash_and_immutable_baseline_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _make_common_module(source)

    source_hashes = wi.hash_object_files(source, ["CommonModule.Guard"])
    copied_hashes = wi.copy_object_files(
        source,
        ["ОбщийМодуль.Guard"],
        baseline,
    )
    assert copied_hashes == source_hashes
    assert all("\\" not in path for path in copied_hashes)
    with pytest.raises(FileExistsError):
        wi.copy_object_files(source, ["CommonModule.Guard"], baseline)


def test_removed_form_input_field_is_reported(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    rel_meta = Path("Documents/X/Forms/Y.xml")
    rel_form = Path("Documents/X/Forms/Y/Ext/Form.xml")
    _write(baseline / rel_meta, "<Form/>")
    _write(candidate / rel_meta, "<Form/>")
    _write(baseline / rel_form, _form_xml(include_owner=True))
    _write(candidate / rel_form, _form_xml(include_owner=False))

    manifest = wi.build_structural_diff(
        baseline,
        candidate,
        ["Document.X.Form.Y"],
    )

    removed = manifest["removedFormElements"]
    assert any(
        item["tag"] == "InputField"
        and item["name"] == "ВыходныеИзделияСобственник"
        and item["id"] == "41"
        and item["dataPath"] == "Объект.ВыходныеИзделия.Собственник"
        for item in removed
    )
    assert manifest["hasRemovals"] is True
    assert json.loads(json.dumps(manifest, ensure_ascii=False)) == manifest


def test_removed_duplicate_form_event_binding_is_reported(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    rel_meta = Path("Catalogs/X/Forms/Y.xml")
    rel_form = Path("Catalogs/X/Forms/Y/Ext/Form.xml")
    for root in (baseline, candidate):
        _write(root / rel_meta, "<Form/>")
    _write(
        baseline / rel_form,
        """<Form><Events>
<Event name="OnCreateAtServer" callType="After">БИТ_ПриСозданииНаСервереПосле</Event>
<Event name="OnCreateAtServer" callType="After">Эст_ПриСозданииНаСервереПосле</Event>
</Events><Commands><Command name="Проверка" id="7"><Action>ОбработатьПроверку</Action></Command></Commands></Form>""",
    )
    _write(
        candidate / rel_form,
        """<Form><Events>
<Event name="OnCreateAtServer" callType="After">Эст_ПриСозданииНаСервереПосле</Event>
</Events><Commands><Command name="Проверка" id="7"/></Commands></Form>""",
    )

    manifest = wi.build_structural_diff(
        baseline,
        candidate,
        ["Catalog.X.Form.Y"],
    )

    assert any(
        item["name"] == "OnCreateAtServer"
        and item["callType"] == "After"
        and item["eventHandler"] == "БИТ_ПриСозданииНаСервереПосле"
        for item in manifest["removedFormElements"]
    )
    assert any(
        item["name"] == "Проверка"
        and item["id"] == "7"
        and item["action"] == "ОбработатьПроверку"
        for item in manifest["removedFormElements"]
    )
    assert manifest["hasRemovals"] is True


def test_removed_bsl_unit_markers_and_executable_line(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    for root in (baseline, candidate):
        _write(root / "CommonModules" / "Guard.xml", "<CommonModule/>")
    module = Path("CommonModules/Guard/Ext/Module.bsl")
    _write(
        baseline / module,
        """// Eugene - important
// Эстет - Рубинштейн - 17.09.2026 - 1246 - Проверка {
//Таланцева - important
// БИТ - important
Процедура Удаленная()
    КритичноеЗначение = 42;
КонецПроцедуры

Процедура Осталась()
    Сообщить("ok");
КонецПроцедуры
""",
    )
    _write(
        candidate / module,
        """Процедура Осталась()
    Сообщить("ok");
КонецПроцедуры
""",
    )

    manifest = wi.structural_diff(
        baseline,
        candidate,
        ["CommonModule.Guard"],
    )

    assert any(item["name"] == "Удаленная" for item in manifest["removedBslUnits"])
    removed_marker_text = {item["text"] for item in manifest["removedMarkers"]}
    assert "// Eugene - important" in removed_marker_text
    assert any(text.startswith("// Эстет") for text in removed_marker_text)
    assert "//Таланцева - important" in removed_marker_text
    assert "// БИТ - important" in removed_marker_text
    assert any(
        item["normalized"] == "критичноезначение = 42 ;"
        and len(item["hash"]) == 64
        for item in manifest["removedExecutableLines"]
    )


def test_manifest_token_rejects_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = {"objects": ["Document.X.Form.Y"], "ok": False}
    token = wi.create_manifest_token(manifest)
    assert wi.verify_manifest_token(token) == manifest
    assert wi.verify_manifest(manifest, token)

    version, payload, signature = token.split(".")
    replacement = "A" if payload[3] != "A" else "B"
    tampered = f"{version}.{payload[:3]}{replacement}{payload[4:]}.{signature}"
    with pytest.raises(wi.IntegrityError):
        wi.verify_manifest_token(tampered)

    monkeypatch.setenv("MCP_STAGING_SECRET", "different-secret")
    with pytest.raises(wi.IntegrityError):
        wi.verify_manifest_token(token)


def test_receipts_require_exact_objects_and_task(tmp_path: Path) -> None:
    objects = ["Document.B", "CommonModule.A"]
    wi.write_lock_receipt(
        objects,
        task="1246",
        target="work",
        extension="Эстет",
        ts=1000,
    )

    receipt = wi.read_lock_receipt(
        list(reversed(objects)),
        task="1246",
        target="work",
        extension="Эстет",
    )
    assert receipt is not None
    assert receipt["objects"] == ["CommonModule.A", "Document.B"]
    assert receipt["receiptId"].startswith("lock_")
    assert wi.check_lock_receipt(
        objects,
        task="1246",
        target="work",
        extension="Эстет",
        now=1001,
        max_age_seconds=10,
    ) is None
    assert wi.check_lock_receipt(
        ["Document.B"],
        task="1246",
        target="work",
        extension="Эстет",
        now=1001,
        max_age_seconds=10,
    ) is not None
    assert wi.check_lock_receipt(
        objects,
        task="9999",
        target="work",
        extension="Эстет",
        now=1001,
        max_age_seconds=10,
    ) is not None
    assert wi.check_lock_receipt(
        ["Document.B.Form.Y"],
        task="1246",
        target="work",
        extension="Эстет",
        now=1001,
        max_age_seconds=10,
    ) is not None


def test_stale_and_different_task_receipts_reject() -> None:
    objects = ["CommonModule.Guard"]
    wi.write_lock_receipt(objects, task="1246", ts=10)
    stale = wi.check_lock_receipt(
        objects,
        task="1246",
        now=100,
        max_age_seconds=20,
    )
    assert stale is not None
    assert stale["error"].endswith("is stale.")
    assert wi.check_lock_receipt(
        objects,
        task="1247",
        now=11,
        max_age_seconds=20,
    ) is not None


def test_dump_before_lock_rejects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _make_common_module(source)
    wi.copy_object_files(source, ["CommonModule.Guard"], baseline)
    objects = ["CommonModule.Guard"]

    wi.write_dump_receipt(
        objects,
        task="1246",
        source_dir=source,
        immutable_baseline_dir=baseline,
        ts=200,
    )
    wi.write_lock_receipt(objects, task="1246", ts=201)

    error = wi.check_dump_receipt(
        objects,
        task="1246",
        now=202,
        max_age_seconds=100,
    )
    assert error is not None
    assert error["step"] == "dump_before_lock"


def test_modified_immutable_baseline_rejects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _make_common_module(source)
    objects = ["CommonModule.Guard"]
    hashes = wi.copy_object_files(source, objects, baseline)
    wi.write_lock_receipt(objects, task="1246", ts=200)
    wi.write_dump_receipt(
        objects,
        task="1246",
        source_dir=source,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=201,
    )
    _write(
        baseline / "CommonModules" / "Guard" / "Ext" / "Module.bsl",
        "Процедура Подмена()\nКонецПроцедуры\n",
    )

    error = wi.check_dump_receipt(
        objects,
        task="1246",
        now=202,
        max_age_seconds=100,
    )

    assert error is not None
    assert error["step"] == "immutable_baseline_changed"


def test_load_receipt_chain_is_valid_for_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    _make_common_module(source)
    hashes = wi.copy_object_files(source, ["CommonModule.Guard"], baseline)
    objects = ["CommonModule.Guard"]

    wi.write_lock_receipt(objects, task="1246", ts=300)
    wi.write_dump_receipt(
        objects,
        task="1246",
        source_dir=source,
        immutable_baseline_dir=baseline,
        baseline_hashes=hashes,
        ts=301,
    )
    wi.write_load_receipt(
        objects,
        task="1246",
        ts=302,
        verified_hashes=hashes,
        post_load_snapshot_dir=source,
    )

    assert wi.check_load_receipt_for_commit(
        objects,
        task="1246",
        now=303,
        max_age_seconds=100,
    ) is None
    assert wi.check_load_receipt_for_commit(
        objects,
        task="other",
        now=303,
        max_age_seconds=100,
    ) is not None
    assert len(
        wi.clear_integrity_receipts(
            objects,
            task="1246",
            kinds=("dump", "load"),
        )
    ) == 2
    assert wi.check_lock_receipt(
        objects,
        task="1246",
        now=303,
        max_age_seconds=100,
    ) is None
    assert len(wi.clear_integrity_receipts(objects, task="1246")) == 1
    assert wi.check_load_receipt_for_commit(
        objects,
        task="1246",
        now=303,
        max_age_seconds=100,
    ) is not None


def test_pending_stash_has_no_ttl_or_other_task_bypass(tmp_path: Path) -> None:
    objects = ["CommonModule.Guard"]
    stash = tmp_path / "stash"
    stash.mkdir()
    wi.write_pending_stash_receipt(
        objects,
        task="1246",
        stash_dir=stash,
        ts=1,
    )

    same_task = wi.check_pending_stash_receipt(objects, task="1246")
    other_task = wi.check_pending_stash_receipt(objects, task="9999")
    assert same_task is not None
    assert same_task["step"] == "reapply_stash"
    assert other_task is not None
    assert other_task["sameTask"] is False
    with pytest.raises(wi.IntegrityError):
        wi.write_pending_stash_receipt(
            objects,
            task="1246",
            stash_dir=stash,
        )
    assert wi.clear_pending_stash_receipt(objects, task="9999") is False
    assert wi.clear_pending_stash_receipt(objects, task="1246") is True
    assert wi.check_pending_stash_receipt(objects, task="1246") is None


def test_pre_and_post_hash_mismatches_are_deterministic() -> None:
    baseline = {
        "CommonModules/A.xml": "a" * 64,
        "CommonModules/A/Ext/Module.bsl": "b" * 64,
    }
    current = {
        "CommonModules/A.xml": "c" * 64,
        "CommonModules/A/Ext/New.bsl": "d" * 64,
    }
    pre = wi.compare_current_snapshot(baseline, current)
    assert pre == {
        "ok": False,
        "missing": ["CommonModules/A/Ext/Module.bsl"],
        "unexpected": ["CommonModules/A/Ext/New.bsl"],
        "changed": [
            {
                "path": "CommonModules/A.xml",
                "expectedSha256": "a" * 64,
                "actualSha256": "c" * 64,
            }
        ],
    }

    post = wi.compare_post_load_snapshot(current, dict(current))
    assert post == {
        "ok": True,
        "missing": [],
        "unexpected": [],
        "changed": [],
    }
    assert wi.compare_post_load_snapshot(current, baseline)["ok"] is False
