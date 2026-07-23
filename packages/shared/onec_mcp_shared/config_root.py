"""Configuration root load safety — never load stale git Configuration.xml."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

MARKER_NAME = ".onec_mcp_root_prepared.json"

# Ext files that must come from the same-IB dump (never from git REPO_CF)
_REQUIRED_EXT_REL = (
    "Ext/HomePageWorkArea.xml",
    "Ext/ClientApplicationInterface.xml",
    "Ext/CommandInterface.xml",
    "Ext/MainSectionCommandInterface.xml",
)

_UUID_RE = re.compile(
    r'<MetaDataObject[^>]*>.*?<Configuration\s+uuid="([0-9a-fA-F-]{36})"',
    re.DOTALL | re.IGNORECASE,
)
_CHILD_RE = re.compile(r"<ChildObjects>(.*?)</ChildObjects>", re.DOTALL | re.IGNORECASE)
_CHILD_ITEM_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)>([^<]+)</\1>")

# Minimal markers that a full ERP Estet dump should keep
_REQUIRED_MARKERS = (
    "ЭСТ_РабочийСтолСклад",
    "Эст_Выпуск",
)


def includes_configuration_root(objects: list[str]) -> bool:
    for raw in objects:
        n = (raw or "").strip().replace("\\", "/")
        if not n:
            continue
        low = n.lower()
        if low in ("configuration", "конфигурация"):
            return True
        if low.endswith("configuration.xml") or low == "configuration.xml":
            return True
        if n.split(".")[0].lower() in ("configuration", "конфигурация"):
            return True
    return False


def prepared_marker_path(source_dir: Path) -> Path:
    return Path(source_dir) / MARKER_NAME


def is_prepared_staging(source_dir: str | Path | None) -> bool:
    if not source_dir:
        return False
    p = Path(source_dir)
    marker = prepared_marker_path(p)
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("ok")) and data.get("kind") == "configuration_root_prepared"


def read_configuration_uuid(xml_path: Path) -> str | None:
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    m = _UUID_RE.search(text)
    return m.group(1).lower() if m else None


def child_objects_block(xml_path: Path) -> str | None:
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    m = _CHILD_RE.search(text)
    return m.group(1) if m else None


def child_objects_count(xml_path: Path) -> int:
    block = child_objects_block(xml_path)
    if not block:
        return 0
    return len(_CHILD_ITEM_RE.findall(block))


def child_object_names(xml_path: Path) -> set[str]:
    block = child_objects_block(xml_path)
    if not block:
        return set()
    return {name for _tag, name in _CHILD_ITEM_RE.findall(block)}


def sanity_check_configuration(
    candidate: Path,
    *,
    baseline: Path | None = None,
    allow_extra_child: int = 0,
) -> dict[str, Any]:
    """
    Fail if candidate looks truncated vs baseline dump from the same IB,
    or missing required Estet markers.
    """
    errors: list[str] = []
    if not candidate.is_file():
        return {"ok": False, "errors": [f"missing Configuration.xml: {candidate}"]}

    cand_uuid = read_configuration_uuid(candidate)
    cand_count = child_objects_count(candidate)
    names = child_object_names(candidate)

    for marker in _REQUIRED_MARKERS:
        if marker not in names:
            errors.append(f"missing ChildObjects marker: {marker}")

    baseline_count = None
    baseline_uuid = None
    if baseline and baseline.is_file():
        baseline_uuid = read_configuration_uuid(baseline)
        baseline_count = child_objects_count(baseline)
        if cand_uuid and baseline_uuid and cand_uuid != baseline_uuid:
            errors.append(
                f"configuration uuid mismatch: candidate={cand_uuid} baseline={baseline_uuid}"
            )
        if baseline_count is not None:
            if cand_count < baseline_count:
                errors.append(
                    f"ChildObjects shrank: candidate={cand_count} baseline={baseline_count}"
                )
            if cand_count > baseline_count + allow_extra_child:
                errors.append(
                    f"ChildObjects grew too much: candidate={cand_count} "
                    f"baseline={baseline_count} allow_extra={allow_extra_child}"
                )

    # Absolute floor — empty/tiny root is never OK for Estet ERP
    if cand_count < 50:
        errors.append(f"ChildObjects count too low: {cand_count} (min 50)")

    return {
        "ok": not errors,
        "errors": errors,
        "candidateUuid": cand_uuid,
        "candidateChildCount": cand_count,
        "baselineUuid": baseline_uuid,
        "baselineChildCount": baseline_count,
    }


def gate_configuration_root_load(
    objects: list[str],
    *,
    source_dir: str | Path | None,
    repo_cf: str | Path | None,
) -> dict[str, Any] | None:
    """
    Returns None if gate N/A (no Configuration in objects).
    Returns dict with ok=False to refuse, or ok=True if prepared staging.
    """
    if not includes_configuration_root(objects):
        return None

    src = Path(source_dir) if source_dir else None
    repo = Path(repo_cf) if repo_cf else None

    # Explicit refuse: loading Configuration from REPO_CF tree without marker
    if src and repo and src.resolve() == repo.resolve():
        return {
            "ok": False,
            "step": "fix_configuration_root_source",
            "message": (
                "Refusing to load Configuration.xml from REPO_CF / git. "
                "Use prepare_new_main_object (dump root from the same IB, patch ChildObjects, staging)."
            ),
        }

    if is_prepared_staging(src):
        marker = json.loads(prepared_marker_path(src).read_text(encoding="utf-8"))
        cfg = src / "Configuration.xml"
        baseline = Path(marker["baselineConfigurationXml"]) if marker.get("baselineConfigurationXml") else None
        sanity = sanity_check_configuration(
            cfg,
            baseline=baseline if baseline and baseline.is_file() else None,
            allow_extra_child=int(marker.get("allowExtraChild") or 1),
        )
        if not sanity["ok"]:
            return {
                "ok": False,
                "step": "fix_configuration_root_source",
                "message": "Prepared staging failed sanity check: " + "; ".join(sanity["errors"]),
                "sanity": sanity,
            }
        missing_ext = configuration_ext_missing(src)
        if missing_ext:
            return {
                "ok": False,
                "step": "fix_configuration_root_source",
                "message": (
                    "Prepared staging missing Configuration Ext from IB dump: "
                    + ", ".join(missing_ext)
                    + ". Re-run prepare_new_main_object (Ext must come from IB, not git)."
                ),
                "missingExt": missing_ext,
            }
        return {"ok": True, "step": "configuration_root_prepared", "sanity": sanity, "marker": marker}

    return {
        "ok": False,
        "step": "fix_configuration_root_source",
        "message": (
            "Refusing Configuration root load without prepared staging. "
            "Call prepare_new_main_object(new_object=..., target=...) first, "
            "then load_objects from returned stagingDir with configuration_root_prepared implied by marker."
        ),
    }


def _xml_tag_for_meta(kind: str) -> str:
    """DataProcessor -> DataProcessor (ChildObjects tag matches English metadata kind)."""
    return kind


def patch_child_objects(configuration_xml: Path, *, kind: str, object_name: str) -> bool:
    """Insert <Kind>Name</Kind> into ChildObjects if missing. Returns True if inserted."""
    text = configuration_xml.read_text(encoding="utf-8")
    tag = _xml_tag_for_meta(kind)
    needle = f"<{tag}>{object_name}</{tag}>"
    if needle in text:
        return False
    m = _CHILD_RE.search(text)
    if not m:
        raise ValueError("ChildObjects block not found in Configuration.xml")
    insert = f"\t\t{needle}\n"
    # insert before </ChildObjects>
    end = m.end(1)
    # m.start(1) is inside ChildObjects; find closing tag position
    close = text.find("</ChildObjects>", m.start())
    if close < 0:
        raise ValueError("Closing ChildObjects not found")
    text = text[:close] + insert + text[close:]
    configuration_xml.write_text(text, encoding="utf-8")
    return True


def configuration_ext_missing(dump_or_staging: Path) -> list[str]:
    """Return required Ext relative paths missing under dump/staging root."""
    root = Path(dump_or_staging)
    missing: list[str] = []
    for rel in _REQUIRED_EXT_REL:
        if not (root / Path(rel)).is_file():
            missing.append(rel)
    return missing


def copy_configuration_ext(source_dump: Path, staging: Path) -> list[str]:
    """
    Copy Configuration Ext/ from an IB dump into staging.
    Never use REPO_CF Ext here — that overwrites live home-page / UI (5318).
    """
    src_ext = Path(source_dump) / "Ext"
    if not src_ext.is_dir():
        raise FileNotFoundError(
            f"Ext/ missing in IB Configuration dump: {source_dump}. "
            "Partial dump without Ext — reconnect storage / re-dump Configuration "
            "before prepare_new_main_object (do not copy Ext from git)."
        )
    missing = configuration_ext_missing(source_dump)
    if missing:
        raise FileNotFoundError(
            "IB Configuration dump incomplete, missing: "
            + ", ".join(missing)
            + ". Re-dump Configuration from the same IB with storage connected."
        )
    dst_ext = Path(staging) / "Ext"
    if dst_ext.exists():
        shutil.rmtree(dst_ext)
    shutil.copytree(src_ext, dst_ext)
    copied: list[str] = []
    for p in dst_ext.rglob("*"):
        if p.is_file():
            copied.append(str(p.relative_to(staging)).replace("\\", "/"))
    return copied


def copy_object_tree(repo_cf: Path, staging: Path, meta_name: str) -> list[str]:
    """Copy object xml + forms subtree from repo into staging. Returns relative paths."""
    canon = meta_name.strip()
    if "." not in canon:
        raise ValueError(f"expected Kind.Name, got {meta_name!r}")
    kind, name = canon.split(".", 1)
    from onec_mcp_shared import TYPE_TO_FOLDER  # local import to avoid cycles at module load

    folder = TYPE_TO_FOLDER.get(kind, kind + "s")
    copied: list[str] = []
    src_xml = repo_cf / folder / f"{name}.xml"
    if not src_xml.is_file():
        raise FileNotFoundError(f"object xml not in repo: {src_xml}")
    dst_xml = staging / folder / f"{name}.xml"
    dst_xml.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_xml, dst_xml)
    copied.append(f"{folder}/{name}.xml")
    src_dir = repo_cf / folder / name
    if src_dir.is_dir():
        dst_dir = staging / folder / name
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        for p in dst_dir.rglob("*"):
            if p.is_file():
                copied.append(str(p.relative_to(staging)).replace("\\", "/"))
    return copied


def write_prepared_marker(
    staging: Path,
    *,
    target: str,
    ib: str,
    new_object: str,
    baseline_xml: Path,
    baseline_child_count: int,
) -> Path:
    marker = {
        "ok": True,
        "kind": "configuration_root_prepared",
        "target": target,
        "ib": ib,
        "newObject": new_object,
        "baselineConfigurationXml": str(baseline_xml),
        "baselineChildCount": baseline_child_count,
        "allowExtraChild": 1,
    }
    path = prepared_marker_path(staging)
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
