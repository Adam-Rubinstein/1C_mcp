# -*- coding: utf-8 -*-
"""Tests for Configuration root load gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onec_mcp_shared import config_root as cr


def _write_cfg(path: Path, *, uuid: str, children: list[tuple[str, str]]) -> None:
    items = "\n".join(f"\t\t<{k}>{v}</{k}>" for k, v in children)
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.20">
\t<Configuration uuid="{uuid}">
\t\t<ChildObjects>
{items}
\t\t</ChildObjects>
\t</Configuration>
</MetaDataObject>
""",
        encoding="utf-8",
    )


def test_includes_configuration_root():
    assert cr.includes_configuration_root(["Configuration"])
    assert cr.includes_configuration_root(["Configuration.xml"])
    assert cr.includes_configuration_root(["DataProcessor.X", "Configuration"])
    assert not cr.includes_configuration_root(["Document.Эст_Выпуск"])


def test_gate_refuses_repo_cf(tmp_path: Path):
    repo = tmp_path / "cf"
    repo.mkdir()
    (repo / "Configuration.xml").write_text("<x/>", encoding="utf-8")
    g = cr.gate_configuration_root_load(
        ["Configuration"],
        source_dir=repo,
        repo_cf=repo,
    )
    assert g is not None
    assert g["ok"] is False
    assert g["step"] == "fix_configuration_root_source"


def test_gate_refuses_unprepared_staging(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    g = cr.gate_configuration_root_load(
        ["Configuration"],
        source_dir=staging,
        repo_cf=tmp_path / "cf",
    )
    assert g is not None
    assert g["ok"] is False


def test_sanity_detects_shrink(tmp_path: Path):
    uid = "11111111-1111-1111-1111-111111111111"
    base = tmp_path / "base.xml"
    cand = tmp_path / "cand.xml"
    kids = [("DataProcessor", f"P{i}") for i in range(60)]
    kids.append(("DataProcessor", "ЭСТ_РабочийСтолСклад"))
    kids.append(("Document", "Эст_Выпуск"))
    _write_cfg(base, uuid=uid, children=kids)
    _write_cfg(cand, uuid=uid, children=kids[:10])
    s = cr.sanity_check_configuration(cand, baseline=base)
    assert s["ok"] is False
    assert any("shrank" in e for e in s["errors"])


def _write_required_ext(root: Path) -> None:
    ext = root / "Ext"
    ext.mkdir(parents=True, exist_ok=True)
    for rel in (
        "HomePageWorkArea.xml",
        "ClientApplicationInterface.xml",
        "CommandInterface.xml",
        "MainSectionCommandInterface.xml",
    ):
        (ext / rel).write_text(f"<stub>{rel}</stub>", encoding="utf-8")


def test_prepared_staging_ok(tmp_path: Path):
    uid = "22222222-2222-2222-2222-222222222222"
    staging = tmp_path / "staging"
    staging.mkdir()
    kids = [("DataProcessor", f"P{i}") for i in range(60)]
    kids.append(("DataProcessor", "ЭСТ_РабочийСтолСклад"))
    kids.append(("Document", "Эст_Выпуск"))
    kids.append(("DataProcessor", "NewOne"))
    baseline = staging / "_baseline_Configuration.xml"
    cfg = staging / "Configuration.xml"
    _write_cfg(baseline, uuid=uid, children=kids[:-1])
    _write_cfg(cfg, uuid=uid, children=kids)
    _write_required_ext(staging)
    cr.write_prepared_marker(
        staging,
        target="work",
        ib="C:/ib",
        new_object="DataProcessor.NewOne",
        baseline_xml=baseline,
        baseline_child_count=len(kids) - 1,
    )
    g = cr.gate_configuration_root_load(
        ["Configuration", "DataProcessor.NewOne"],
        source_dir=staging,
        repo_cf=tmp_path / "other",
    )
    assert g is not None
    assert g["ok"] is True


def test_gate_refuses_prepared_without_ext(tmp_path: Path):
    uid = "33333333-3333-3333-3333-333333333333"
    staging = tmp_path / "staging"
    staging.mkdir()
    kids = [("DataProcessor", f"P{i}") for i in range(60)]
    kids.append(("DataProcessor", "ЭСТ_РабочийСтолСклад"))
    kids.append(("Document", "Эст_Выпуск"))
    baseline = staging / "_baseline_Configuration.xml"
    cfg = staging / "Configuration.xml"
    _write_cfg(baseline, uuid=uid, children=kids)
    _write_cfg(cfg, uuid=uid, children=kids)
    cr.write_prepared_marker(
        staging,
        target="work",
        ib="C:/ib",
        new_object="DataProcessor.X",
        baseline_xml=baseline,
        baseline_child_count=len(kids),
    )
    g = cr.gate_configuration_root_load(
        ["Configuration"],
        source_dir=staging,
        repo_cf=tmp_path / "other",
    )
    assert g is not None
    assert g["ok"] is False
    assert "missingExt" in g or "Ext" in g.get("message", "")


def test_copy_configuration_ext(tmp_path: Path):
    dump = tmp_path / "dump"
    staging = tmp_path / "staging"
    dump.mkdir()
    staging.mkdir()
    _write_required_ext(dump)
    (dump / "Ext" / "extra.xml").write_text("<e/>", encoding="utf-8")
    copied = cr.copy_configuration_ext(dump, staging)
    assert "Ext/HomePageWorkArea.xml" in copied
    assert (staging / "Ext" / "extra.xml").is_file()
    bad = tmp_path / "bad"
    bad.mkdir()
    with pytest.raises(FileNotFoundError):
        cr.copy_configuration_ext(bad, staging)
