from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "shared"))

from onec_mcp_shared.repo_file_sync import (
    object_output_paths,
    parse_storage_report,
    read_sync_marker,
    record_sync_tip,
)


REPORT = """\
Версия:\t10
\tИзменены\t2
\tДокумент.Эст_Выпуск
\tУправлениеПредприятием

Версия:\t11
\tУдалены\t1
\tОбработка.Эст_Старая

Версия:\t12
\tИзменены\t1
\tДокумент.Эст_Выпуск
"""


def test_parse_storage_report_range_skips_configuration_root() -> None:
    parsed = parse_storage_report(REPORT, n_begin=11)
    assert parsed["tipVersion"] == 12
    assert parsed["changedObjects"] == ["Документ.Эст_Выпуск"]
    assert parsed["deletedObjects"] == ["Обработка.Эст_Старая"]


def test_parse_storage_report_tip_only() -> None:
    parsed = parse_storage_report(REPORT, include_objects=False)
    assert parsed["tipVersion"] == 12
    assert parsed["changedObjects"] == []
    assert parsed["deletedObjects"] == []


def test_object_output_paths_document() -> None:
    paths = object_output_paths("Документ.Эст_Выпуск")
    assert Path("Documents/Эст_Выпуск.xml") in paths
    assert Path("Documents/Эст_Выпуск") in paths
    assert object_output_paths("УправлениеПредприятием") == []


def test_marker_file_sync_waits_for_both_tips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "src" / "cf"
    repo.mkdir(parents=True)
    monkeypatch.setenv("REPO_CF", str(repo))
    first = record_sync_tip(side="cf", tip_version=1400)
    assert first.get("fileSync") is None
    assert read_sync_marker()["cfTip"] == "1400"
    second = record_sync_tip(side="cfe", tip_version=80)
    assert second["fileSync"] == "ok"
    assert second["cfeTip"] == "80"


def test_storage_report_passes_version_range(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_work_server_gates import _load_server

    server = _load_server("storage_server_report_range", "mcp-1c-storage")
    seen: dict[str, object] = {}

    def _capture(designer_args, **_kwargs):
        seen["args"] = list(designer_args)
        Path(designer_args[1]).write_text(REPORT, encoding="utf-8")
        return {"ok": True, "exitCode": 0}

    monkeypatch.setattr(server, "_run_storage_op", _capture)
    payload = json.loads(
        server.storage_report(target="work", n_begin=11, n_end=12, manage_session=False)
    )
    assert payload["ok"] is True
    assert "-NBegin" in seen["args"]
    assert "11" in seen["args"]
    assert "-NEnd" in seen["args"]
    assert payload["tipVersion"] == 12
    assert payload["changedObjects"] == ["Документ.Эст_Выпуск"]
    assert payload["deletedObjects"] == ["Обработка.Эст_Старая"]


def test_sync_files_refuses_open_designer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_work_server_gates import _load_server

    monkeypatch.setenv("ONEC_IB_WORK", str(tmp_path / "ib"))
    monkeypatch.setenv("ONEC_STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("REPO_CF", str(tmp_path / "src" / "cf"))
    server = _load_server("dump_server_filesync_busy", "mcp-1c-dump")
    monkeypatch.setattr(
        "onec_mcp_shared.repo_file_sync.work_session_open",
        lambda _ib: [{"pid": 1, "kind": "designer"}],
    )
    payload = json.loads(
        server.sync_files_from_work(
            objects=["Документ.Эст_Выпуск"],
            target="work",
            tip_version=12,
        )
    )
    assert payload["ok"] is False
    assert payload["step"] == "work_designer_busy"
