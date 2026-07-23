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


def test_normalize_object_name_ru():
    assert normalize_object_name("Документ.Эст_Выпуск") == "Document.Эст_Выпуск"
    assert normalize_object_name("Document.Foo") == "Document.Foo"
    assert normalize_object_name("ОбщийМодуль.Эст_Дополнительно") == "CommonModule.Эст_Дополнительно"


def test_object_to_list_entry_configuration_load():
    assert object_to_list_entry("Configuration", for_load=True) == "Configuration.xml"
    assert object_to_list_entry("Конфигурация", for_load=True) == "Configuration.xml"
    assert object_to_list_entry("Configuration", for_load=False) == "Configuration"
    assert object_to_list_entry("Document.A", for_load=True) == "Documents/A.xml"


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
