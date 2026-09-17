from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from onec_mcp_shared import (  # noqa: E402
    merge_copy,
    normalize_object_name,
    object_to_list_entry,
    parse_storage_errors,
    redact_cmd,
    write_list_file,
)


def test_bsl_units_and_callgraph(tmp_path: Path):
    from onec_mcp_shared.bsl_callgraph import query_edges, rebuild_graph
    from onec_mcp_shared.bsl_units import extract_unit, list_units

    sample = (
        "&НаСервере\n"
        "Процедура Альфа(Х)\n"
        "\tБета();\n"
        "КонецПроцедуры\n"
        "\n"
        "Функция Бета()\n"
        "\tВозврат 1;\n"
        "КонецФункции\n"
    )
    units = list_units(sample)
    assert [u.name for u in units] == ["Альфа", "Бета"]
    found = extract_unit(sample, "Альфа")
    assert found is not None
    assert "КонецПроцедуры" in found[1]

    mod = tmp_path / "CommonModules" / "Эст_X" / "Ext"
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text(sample, encoding="utf-8")
    idx = rebuild_graph([tmp_path], prefixes=["Эст_"])
    assert idx["ok"] is True
    assert idx["moduleCount"] == 1
    callees = query_edges(idx, unit="Альфа", direction="callees")
    assert any(e["callee_name"] == "Бета" for e in callees)


def test_normalize_object_name_ru():
    assert normalize_object_name("Документ.Эст_Выпуск") == "Document.Эст_Выпуск"
    assert normalize_object_name("Document.Foo") == "Document.Foo"
    assert normalize_object_name("ОбщийМодуль.Эст_Дополнительно") == "CommonModule.Эст_Дополнительно"


def test_object_to_list_entry_configuration_load():
    assert object_to_list_entry("Configuration", for_load=True) == "Configuration.xml"
    assert object_to_list_entry("Конфигурация", for_load=True) == "Configuration.xml"
    assert object_to_list_entry("Configuration", for_load=False) == "Configuration"
    assert object_to_list_entry("Document.A", for_load=True) == "Documents/A.xml"
    assert (
        object_to_list_entry("Document.ПроизводствоБезЗаказа.Form.Эст_ПБЗ_Мини_ГруппаЗакрепки", for_load=True)
        == "Documents/ПроизводствоБезЗаказа/Forms/Эст_ПБЗ_Мини_ГруппаЗакрепки.xml"
    )
    assert (
        object_to_list_entry("Document.X.Form.Y", for_load=False) == "Document.X.Form.Y"
    )


def test_work_gates_aligned_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    objs = ["Document.Foo", "CommonModule.Bar"]
    wg.write_aligned_marker(objs, target="work", extension=None)
    assert wg.check_storage_aligned(objs, target="work", extension=None, storage_aligned=False) is not None
    assert wg.check_storage_aligned(objs, target="work", extension=None, storage_aligned=True) is None
    assert (
        wg.check_storage_aligned(["Document.Other"], target="work", extension=None, storage_aligned=True)
        is not None
    )
    wg.write_lock_receipt(objs, target="work", extension=None)
    assert wg.check_lock_receipt(objs, target="work", extension=None, storage_captured=True) is None
    assert wg.check_lock_receipt(objs, target="work", extension=None, storage_captured=False) is not None


def test_entire_config_env(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared.work_gates import entire_config_allowed, refuse_entire_without_env

    monkeypatch.delenv("ALLOW_ENTIRE_STORAGE_OPS", raising=False)
    assert refuse_entire_without_env(entire_config=True) is not None
    monkeypatch.setenv("ALLOW_ENTIRE_STORAGE_OPS", "1")
    assert entire_config_allowed() is True
    assert refuse_entire_without_env(entire_config=True) is None


def test_write_list_file_bom(tmp_path: Path):
    p = tmp_path / "objects.txt"
    write_list_file(["Документ.A", "Catalog.B"], p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = p.read_text(encoding="utf-8-sig")
    assert "Document.A" in text
    assert "Catalog.B" in text


def test_parse_storage_errors():
    log = "Объект Документ.Foo не захвачен в хранилище конфигурации"
    err, objs = parse_storage_errors(log, ["Document.Foo", "Document.Bar"])
    assert err is True
    assert "Document.Foo" in objs


def test_parse_storage_not_connected_is_not_lock():
    err, objs = parse_storage_errors("Соединение с хранилищем конфигурации не установлено", ["Document.Foo"])
    assert err is False
    assert objs == []


def test_parse_storage_get_required():
    from onec_mcp_shared import parse_storage_get_required

    log = (
        "! Для выполнения операции требуется получение объектов:\n"
        "| Документ.Эст_Выпуск\n"
        "----- Операция с хранилищем конфигурации отменена -----"
    )
    need, objs = parse_storage_get_required(log, ["Document.Эст_Выпуск", "Document.Other"])
    assert need is True
    assert "Document.Эст_Выпуск" in objs


def test_is_storage_offline_and_access():
    from onec_mcp_shared import is_storage_access_error, is_storage_offline

    assert is_storage_offline("Соединение с хранилищем конфигурации не установлено")
    assert is_storage_access_error("Не удалось заблокировать таблицу 'OBJECTS'")
    assert not is_storage_offline("Объект не захвачен")


def test_resolve_ib_auth_per_target(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import resolve_ib_auth

    monkeypatch.setenv("ONEC_USER", "Admin")
    monkeypatch.setenv("ONEC_PASSWORD", "a")
    monkeypatch.setenv("ONEC_USER_WORK", "WorkUser")
    monkeypatch.setenv("ONEC_PASSWORD_WORK", "w")
    monkeypatch.setenv("ONEC_USER_DEV", "DevUser")
    monkeypatch.setenv("ONEC_PASSWORD_DEV", "d")
    assert resolve_ib_auth("work") == ("WorkUser", "w")
    assert resolve_ib_auth("dev") == ("DevUser", "d")


def test_redact_password():
    cmd = ["1cv8", "DESIGNER", "/F", "C:\\ib", "/N", "Admin", "/P", "secret"]
    red = redact_cmd(cmd)
    assert "secret" not in red
    assert "***" in red


def test_merge_copy(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "Documents").mkdir(parents=True)
    (src / "Documents" / "A.xml").write_text("<x/>", encoding="utf-8")
    (dst / "Documents").mkdir(parents=True)
    (dst / "Documents" / "A.xml").write_text("<old/>", encoding="utf-8")
    report = merge_copy(src, dst)
    assert "Documents/A.xml" in report["overwrite"]
    assert (dst / "Documents" / "A.xml").read_text(encoding="utf-8") == "<x/>"


def test_cmdline_matches_ib_exact_name(monkeypatch: pytest.MonkeyPatch):
    """Prefix IB titles must not cross-match (ERP КОПИЯ vs ERP КОПИЯ запасная)."""
    from onec_mcp_shared import session as sess

    mapping = {
        "ERP КОПИЯ": r"C:\Users\rubinshtein\Documents\InfoBase3",
        "ERP КОПИЯ запасная": r"C:\Users\rubinshtein\Documents\InfoBase2",
    }
    monkeypatch.setattr(sess, "_ibases_v8i_paths", lambda: mapping)
    cmd_work = r'DESIGNER /IBName "ERP КОПИЯ"'
    cmd_dev = r'DESIGNER /IBName "ERP КОПИЯ запасная" /Lru'
    assert sess._cmdline_matches_ib(cmd_dev, mapping["ERP КОПИЯ запасная"])
    assert not sess._cmdline_matches_ib(cmd_dev, mapping["ERP КОПИЯ"])
    assert sess._cmdline_matches_ib(cmd_work, mapping["ERP КОПИЯ"])
    assert not sess._cmdline_matches_ib(cmd_work, mapping["ERP КОПИЯ запасная"])


def test_cmdline_does_not_cross_match_infobase_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bare InfoBase must not match InfoBase2/3 under the same parent."""
    from onec_mcp_shared import session as sess

    monkeypatch.setattr(sess, "_ibases_v8i_paths", lambda: {})
    parent = tmp_path / "Documents"
    ib = parent / "InfoBase"
    ib2 = parent / "InfoBase2"
    ib3 = parent / "InfoBase3"
    for p in (ib, ib2, ib3):
        p.mkdir(parents=True)
    cmd2 = rf'DESIGNER /F "{ib2}"'
    cmd3 = rf'DESIGNER /F "{ib3}"'
    assert sess._cmdline_matches_ib(cmd2, ib2)
    assert sess._cmdline_matches_ib(cmd3, ib3)
    assert not sess._cmdline_matches_ib(cmd2, ib)
    assert not sess._cmdline_matches_ib(cmd2, ib3)
    assert not sess._cmdline_matches_ib(cmd3, ib2)


def test_managed_session_reopen_false_does_not_start(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import session as sess

    calls: list[str] = []

    monkeypatch.setattr(
        sess,
        "find_ib_processes",
        lambda _ib: [sess.IbProcess(pid=1, kind="designer", cmdline="x")],
    )
    monkeypatch.setattr(sess, "close_ib_sessions", lambda _ib, force=False: [{"pid": 1, "closed": True}])
    monkeypatch.setattr(
        sess,
        "start_ib_session",
        lambda *a, **k: calls.append("start") or {"pid": 2},
    )
    result, meta = sess.with_managed_session("C:/ib", lambda: "ok", reopen=False)
    assert result == "ok"
    assert calls == []
    assert meta["started"] == []
    assert "userAction" in meta


def test_ib_name_for_path(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import session as sess

    mapping = {
        "ERP КОПИЯ": r"C:\Users\rubinshtein\Documents\InfoBase3",
        "ERP КОПИЯ запасная": r"C:\Users\rubinshtein\Documents\InfoBase2",
    }
    monkeypatch.setattr(sess, "_ibases_v8i_paths", lambda: mapping)
    assert sess.ib_name_for_path(mapping["ERP КОПИЯ"]) == "ERP КОПИЯ"
    assert sess.ib_name_for_path(mapping["ERP КОПИЯ запасная"]) == "ERP КОПИЯ запасная"
    assert sess.ib_name_for_path(r"C:\nope") is None


def test_storage_cli_args(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import session as sess

    monkeypatch.setenv("ONEC_STORAGE_PATH", r"\\server\repo")
    monkeypatch.setenv("ONEC_STORAGE_USER", "dev")
    monkeypatch.setenv("ONEC_STORAGE_PASSWORD", "secret")
    args = sess.storage_cli_args()
    assert args[0] == "/ConfigurationRepositoryF"
    assert args[1] == r"\\server\repo"
    assert "/ConfigurationRepositoryN" in args
    assert "dev" in args
    assert "/ConfigurationRepositoryP" in args
    assert "secret" in args


def test_storage_cli_empty_password_not_ib_fallback(monkeypatch: pytest.MonkeyPatch):
    """Empty storage password: omit /P (Designer auth); never use ONEC_PASSWORD_WORK."""
    from onec_mcp_shared import session as sess

    monkeypatch.setenv("ONEC_STORAGE_PATH", r"\\1cmini\ХранилищеЕрп5_23\\")
    monkeypatch.setenv("ONEC_STORAGE_USER", "РубинштейнА")
    monkeypatch.setenv("ONEC_STORAGE_PASSWORD", "")
    monkeypatch.setenv("ONEC_PASSWORD_WORK", "123321")
    args = sess.storage_cli_args()
    assert "/ConfigurationRepositoryN" in args
    assert "РубинштейнА" in args
    # Empty password: flag omitted (some builds treat empty /P as auth failure).
    assert "/ConfigurationRepositoryP" not in args
    assert "123321" not in args


def test_write_storage_objects_file(tmp_path: Path):
    from onec_mcp_shared import write_storage_objects_file

    p = tmp_path / "objects.xml"
    write_storage_objects_file(
        ["Document.Эст_КвитанцияФПП", "Configuration"],
        p,
        include_child_objects=True,
    )
    text = p.read_text(encoding="utf-8")
    assert 'xmlns="http://v8.1c.ru/8.3/config/objects"' in text
    assert 'fullName="Document.Эст_КвитанцияФПП"' in text
    assert 'includeChildObjects="true"' in text
    assert "<Configuration " in text
    assert not text.startswith("\ufeff")


def test_designer_result_json_roundtrip():

    from onec_mcp_shared import DesignerResult

    r = DesignerResult(
        exit_code=1,
        log_path="x",
        log_tail="захват объекта Document.Foo",
        command=["1cv8"],
        storage_error=True,
        objects_to_capture=["Document.Foo"],
    )
    d = r.to_dict()
    assert d["ok"] is False
    assert d["objectsToCapture"] == ["Document.Foo"]
    assert json.loads(json.dumps(d))


def test_files_search_on_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "cf"
    mod = root / "Documents" / "X" / "Ext"
    mod.mkdir(parents=True)
    (mod / "ObjectModule.bsl").write_text("Процедура Тест()\n\tА = ТекущаяДата();\nКонецПроцедуры\n", encoding="utf-8")
    monkeypatch.setenv("REPO_CF", str(root))
    monkeypatch.setenv("CONFIG_DUMP_DIR", str(root))

    # import files server helpers
    sys.path.insert(0, str(ROOT / "packages" / "mcp-1c-files"))
    # load module as file
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "files_server", ROOT / "packages" / "mcp-1c-files" / "server.py"
    )
    mod_s = importlib.util.module_from_spec(spec)
    # Avoid running main
    assert spec and spec.loader
    # server imports mcp — may fail if mcp not installed; skip if so
    try:
        spec.loader.exec_module(mod_s)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"mcp not importable: {exc}")
    result = json.loads(mod_s.files_search("ТекущаяДата"))
    assert result["ok"] is True
    assert result["count"] >= 1


def test_review_check_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sys.path.insert(0, str(ROOT / "packages" / "mcp-1c-review"))
    import importlib.util

    rules = ROOT / "packages" / "mcp-1c-review" / "rules" / "default.yaml"
    monkeypatch.setenv("REVIEW_RULES_PATH", str(rules))
    spec = importlib.util.spec_from_file_location(
        "review_server", ROOT / "packages" / "mcp-1c-review" / "server.py"
    )
    assert spec and spec.loader
    mod_s = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod_s)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"mcp not importable: {exc}")
    # unicode escapes so source file encoding cannot break the test
    sample = "\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430 \u0410()\n\t\u0414 = \u0422\u0435\u043a\u0443\u0449\u0430\u044f\u0414\u0430\u0442\u0430();\n\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b\n"
    result = json.loads(mod_s.review_check(text=sample))
    assert result["ok"] is True
    ids = {f["rule"] for f in result["findings"]}
    assert "no-current-date" in ids

def test_refuse_parent_object_without_confirm():
    from onec_mcp_shared.work_gates import refuse_parent_object_without_confirm as r

    assert r(["Document.Foo"]) is not None
    assert r(["Document.Foo.Form.Bar"]) is None
    assert r(["CommonModule.Foo"]) is None
    assert r(["Document.Foo"], confirm_parent_object=True) is None


def test_aligned_per_object_does_not_clobber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    wg.write_aligned_marker(["CommonModule.A"], target="work", extension="Эстет")
    wg.write_aligned_marker(["CommonModule.B"], target="work", extension="Эстет")
    assert wg.check_storage_aligned(["CommonModule.A"], target="work", extension="Эстет", storage_aligned=True) is None
    assert wg.check_storage_aligned(["CommonModule.B"], target="work", extension="Эстет", storage_aligned=True) is None


def test_lock_does_not_substitute_for_get_alignment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    objs = ["CommonModule.ПроизводствоБезЗаказаЛокализация"]
    wg.write_lock_receipt(objs, target="work", extension="Эстет")
    assert (
        wg.check_storage_aligned(
            objs, target="work", extension="Эстет", storage_aligned=False
        )
        is not None
    )
    assert wg.refuse_get_captured(objs, target="work", extension="Эстет") is not None
    assert wg.refuse_get_captured(objs, target="work", extension="Эстет", confirm_get_captured=True) is None


def test_lock_receipt_is_task_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    objs = ["Document.Эст_Выпуск.Form.ФормаДокумента"]
    wg.write_lock_receipt(objs, target="work", extension=None, task="1359")

    assert (
        wg.check_lock_receipt(
            objs,
            target="work",
            extension=None,
            storage_captured=True,
            task="1359",
        )
        is None
    )
    assert (
        wg.check_lock_receipt(
            objs,
            target="work",
            extension=None,
            storage_captured=True,
            task="restore-1194",
        )
        is not None
    )


def test_object_lock_queues_then_acquires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import threading
    import time

    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_OBJECT_LOCK_WAIT_SEC", "5")
    monkeypatch.setenv("MCP_OBJECT_LOCK_TTL_SEC", "1800")
    objs = ["CommonModule.Foo"]
    assert wg.acquire_object_locks(objs, task="5359", target="work", extension="e") is None

    def _release_later():
        time.sleep(0.4)
        wg.release_object_locks(objs, task="5359", target="work", extension="e")

    threading.Thread(target=_release_later, daemon=True).start()
    assert wg.acquire_object_locks(objs, task="5389", target="work", extension="e") is None
    wg.release_object_locks(objs, task="5389", target="work", extension="e")


def test_object_lock_same_task_reenters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    objs = ["CommonModule.Foo"]
    assert wg.acquire_object_locks(objs, task="5359", target="work", extension="e") is None
    # Same PID may re-enter
    assert wg.acquire_object_locks(objs, task="5359", target="work", extension="e") is None
    wg.release_object_locks(objs, task="5359", target="work", extension="e")


def test_object_lock_same_task_hands_off_between_mcp_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_OBJECT_LOCK_TTL_SEC", "1800")
    objs = ["CommonModule.Bar"]
    assert wg.acquire_object_locks(objs, task="1359", target="work", extension="e") is None
    # dump/storage/load are separate stdio processes for the same task.
    path = wg._object_lock_path("work", "e", "CommonModule.Bar")
    data = wg._read_gate(path)
    assert data is not None
    data["pid"] = 1  # other holder
    monkeypatch.setattr(wg, "_pid_alive", lambda pid: True)
    path.write_text(__import__("json").dumps(data), encoding="utf-8")
    assert wg.acquire_object_locks(objs, task="1359", target="work", extension="e") is None
    updated = wg._read_gate(path)
    assert updated is not None
    assert updated["pid"] == __import__("os").getpid()
    wg.release_object_locks(objs, task="1359", target="work", extension="e")


def test_object_lock_timeout_still_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_OBJECT_LOCK_WAIT_SEC", "1")
    monkeypatch.setenv("MCP_OBJECT_LOCK_TTL_SEC", "1800")
    objs = ["CommonModule.Foo"]
    assert wg.acquire_object_locks(objs, task="5359", target="work", extension="e") is None
    err = wg.acquire_object_locks(objs, task="5389", target="work", extension="e")
    assert err is not None
    assert err["step"] == "object_held_by_other_task"
    wg.release_object_locks(objs, task="5359", target="work", extension="e")

def test_require_work_task():
    from onec_mcp_shared.work_gates import require_work_task

    assert require_work_task(None, target="work") is not None
    assert require_work_task("5359", target="work") is None
    assert require_work_task(None, target="dev") is None


def test_designer_mutex_reentrant_and_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import work_gates as wg

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_DESIGNER_LOCK_WAIT_SEC", "2")
    monkeypatch.setenv("MCP_DESIGNER_LOCK_TTL_SEC", "1")
    assert wg.acquire_designer_lock("work", tool="t1") is None
    assert wg.acquire_designer_lock("work", tool="t1") is None
    wg.release_designer_lock("work")
    wg.release_designer_lock("work")
    assert wg.acquire_designer_lock("dev", tool="x") is None


def test_cmdline_matches_ib_guillemet_quotes(monkeypatch: pytest.MonkeyPatch):
    from onec_mcp_shared import session as sess

    mapping = {
        "ERP КОПИЯ": r"C:\Users\rubinshtein\Documents\InfoBase3",
        "ERP КОПИЯ запасная": r"C:\Users\rubinshtein\Documents\InfoBase2",
    }
    monkeypatch.setattr(sess, "_ibases_v8i_paths", lambda: mapping)
    cmd = "DESIGNER /IBName «ERP КОПИЯ» /Lru"
    assert sess._cmdline_matches_ib(cmd, mapping["ERP КОПИЯ"])
    assert not sess._cmdline_matches_ib(cmd, mapping["ERP КОПИЯ запасная"])


def test_designer_busy_payload_empty_log():
    from onec_mcp_shared.work_gates import designer_busy_payload, log_looks_designer_busy

    assert log_looks_designer_busy("Информационная база уже открыта Конфигуратором")
    busy = designer_busy_payload(log_text="", exit_code=1, ib=None)
    assert busy is not None
    assert busy["step"] == "work_designer_busy"


def test_refuse_dirty_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess

    from onec_mcp_shared.work_gates import refuse_dirty_repo

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    subprocess.run(["git", "init"], cwd=dst, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=dst,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=dst,
        check=True,
        capture_output=True,
    )
    (dst / "a.bsl").write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "a.bsl"], cwd=dst, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=dst,
        check=True,
        capture_output=True,
    )
    (dst / "a.bsl").write_text("old-dirty", encoding="utf-8")
    (src / "a.bsl").write_text("new", encoding="utf-8")
    # confirm_overwrite_dirty alone must NOT wipe (1346)
    blocked = refuse_dirty_repo(src, dst, confirm_overwrite_dirty=True, auto_stash=False)
    assert blocked is not None
    assert blocked["step"] == "refuse_dirty_repo"
    assert blocked.get("stop") is True
    # explicit discard still allows wipe
    assert (
        refuse_dirty_repo(
            src, dst, confirm_overwrite_dirty=True, confirm_discard_local_edits=True
        )
        is None
    )


def test_refuse_dirty_repo_auto_stash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess

    from onec_mcp_shared import work_gates

    repo = tmp_path / "repo"
    dump = tmp_path / "dump"
    repo.mkdir()
    dump.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tracked = repo / "Module.bsl"
    tracked.write_text("from-git\n", encoding="utf-8")
    subprocess.run(["git", "add", "Module.bsl"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tracked.write_text("local-patch-1346\n", encoding="utf-8")
    (dump / "Module.bsl").write_text("from-ib-dump\n", encoding="utf-8")

    gates = tmp_path / "gates"
    gates.mkdir()
    monkeypatch.setattr(work_gates, "_gates_root", lambda: gates)

    info = work_gates.refuse_dirty_repo(dump, repo, auto_stash=True)
    assert info is not None
    assert info.get("ok") is True
    assert info["step"] == "reapply_stash"
    assert tracked.read_text(encoding="utf-8") == "from-git\n"
    stash = Path(info["stashDir"])
    assert (stash / "Module.bsl").read_text(encoding="utf-8") == "local-patch-1346\n"


def test_refuse_dirty_repo_stashes_tracked_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import json
    import subprocess

    from onec_mcp_shared import work_gates

    repo = tmp_path / "repo"
    dump = tmp_path / "dump"
    repo.mkdir()
    dump.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    tracked = repo / "Module.bsl"
    tracked.write_text("from-git\n", encoding="utf-8")
    subprocess.run(["git", "add", "Module.bsl"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tracked.unlink()
    (dump / "Module.bsl").write_text("from-ib-dump\n", encoding="utf-8")
    gates = tmp_path / "gates"
    gates.mkdir()
    monkeypatch.setattr(work_gates, "_gates_root", lambda: gates)

    info = work_gates.refuse_dirty_repo(dump, repo, auto_stash=True)

    assert info is not None
    assert info["step"] == "reapply_stash"
    assert tracked.read_text(encoding="utf-8") == "from-git\n"
    manifest = json.loads(
        (Path(info["stashDir"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["files"] == [{"path": "Module.bsl", "kind": "tracked_deleted"}]


def test_storage_dump_version_builds_exact_version_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import importlib.util

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path / "storage"))
    spec = importlib.util.spec_from_file_location(
        "storage_server_test", ROOT / "packages" / "mcp-1c-storage" / "server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        exported = Path(args[1])
        exported.parent.mkdir(parents=True, exist_ok=True)
        exported.write_bytes(b"historical-cf")
        return {"ok": True}

    monkeypatch.setattr(module, "_run_storage_op", fake_run)
    out = tmp_path / "history" / "v1193.cf"
    payload = json.loads(
        module.storage_dump_version(
            version=1193,
            output_path=str(out),
            keep_configuration=True,
        )
    )

    assert payload["ok"] is True
    assert payload["version"] == 1193
    assert payload["configurationExists"] is True
    assert calls == [["/ConfigurationRepositoryDumpCfg", str(out), "-v", "1193"]]


def test_storage_dump_version_extracts_with_valid_file_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import importlib.util

    storage_root = tmp_path / "storage"
    onec_bin = tmp_path / "1cv8.exe"
    onec_bin.write_bytes(b"test")
    monkeypatch.setenv("DUMP_TMP_ROOT", str(storage_root))
    monkeypatch.setenv("ONEC_BIN", str(onec_bin))
    spec = importlib.util.spec_from_file_location(
        "storage_server_extract_test", ROOT / "packages" / "mcp-1c-storage" / "server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    storage_calls: list[list[str]] = []
    onec_calls: list[list[str]] = []

    def fake_storage_run(args, **_kwargs):
        storage_calls.append(list(args))
        exported = Path(args[1])
        exported.parent.mkdir(parents=True, exist_ok=True)
        exported.write_bytes(b"historical-cf")
        return {"ok": True}

    def fake_local_onec(args, _log):
        onec_calls.append(list(args))
        if "CREATEINFOBASE" in args:
            connection = args[2]
            ib_dir = Path(connection.removeprefix("File=").removesuffix(";"))
            ib_dir.mkdir(parents=True, exist_ok=True)
            (ib_dir / "1Cv8.1CD").write_bytes(b"test")
        if "/DumpConfigToFiles" in args:
            extracted = Path(args[args.index("/DumpConfigToFiles") + 1])
            module_path = (
                extracted
                / "Documents"
                / "Эст_Выпуск"
                / "Forms"
                / "ФормаДокумента"
                / "Ext"
                / "Form"
                / "Module.bsl"
            )
            module_path.parent.mkdir(parents=True, exist_ok=True)
            module_path.write_text("Процедура Тест()\nКонецПроцедуры\n", encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(module, "_run_storage_op", fake_storage_run)
    monkeypatch.setattr(module, "_run_local_onec", fake_local_onec)
    out = tmp_path / "history" / "v1193.cf"
    extracted = tmp_path / "history" / "v1193"
    payload = json.loads(
        module.storage_dump_version(
            version=1193,
            output_path=str(out),
            objects=["Document.Эст_Выпуск.Form.ФормаДокумента"],
            extract_dir=str(extracted),
            keep_configuration=True,
        )
    )

    assert payload["ok"] is True
    assert storage_calls == [["/ConfigurationRepositoryDumpCfg", str(out), "-v", "1193"]]
    create_args = next(args for args in onec_calls if "CREATEINFOBASE" in args)
    assert create_args[2].startswith("File=")
    assert create_args[2].endswith(";")
    assert '"' not in create_args[2]
    assert "/AddInListN" not in create_args


def test_storage_dump_version_rejects_invalid_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    monkeypatch.setenv("DUMP_TMP_ROOT", str(tmp_path / "storage"))
    spec = importlib.util.spec_from_file_location(
        "storage_server_invalid_test", ROOT / "packages" / "mcp-1c-storage" / "server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = json.loads(module.storage_dump_version(version=0))
    assert payload["ok"] is False
    assert payload["stop"] is True