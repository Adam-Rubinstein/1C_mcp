"""Check Adopted ExtendedConfigurationObject UUIDs against main CF Attribute uuid."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ATTR_BLOCK = re.compile(
    r"<Attribute\s+uuid=\"([0-9a-fA-F-]{36})\">(.*?)</Attribute>",
    re.DOTALL | re.IGNORECASE,
)
_NAME = re.compile(r"<Name>([^<]+)</Name>")
_EXTENDED = re.compile(
    r"<ExtendedConfigurationObject>([0-9a-fA-F-]{36})</ExtendedConfigurationObject>",
    re.IGNORECASE,
)
_BELONGING = re.compile(r"<ObjectBelonging>\s*Adopted\s*</ObjectBelonging>", re.IGNORECASE)
_ANY_ATTR_UUID = re.compile(r"<Attribute\s+uuid=\"([0-9a-fA-F-]{36})\"", re.IGNORECASE)

_KIND_DIRS = {
    "Catalog": "Catalogs",
    "Document": "Documents",
    "DataProcessor": "DataProcessors",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "Enum": "Enums",
    "CommonModule": "CommonModules",
}


def _object_xml_path(root: Path, meta_name: str) -> Path | None:
    """Catalog.СерииНоменклатуры → Catalogs/СерииНоменклатуры.xml"""
    parts = (meta_name or "").split(".")
    if len(parts) < 2:
        return None
    kind, name = parts[0], parts[1]
    folder = _KIND_DIRS.get(kind)
    if not folder:
        return None
    path = root / folder / f"{name}.xml"
    return path if path.is_file() else None


def _all_attr_uuids(cf_xml: Path) -> set[str]:
    """All Attribute uuid= values in main CF object XML (incl. tabular sections)."""
    text = cf_xml.read_text(encoding="utf-8", errors="replace")
    return {m.group(1).lower() for m in _ANY_ATTR_UUID.finditer(text)}


def _name_by_uuid(cf_xml: Path) -> dict[str, str]:
    text = cf_xml.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for m in _ATTR_BLOCK.finditer(text):
        uid = m.group(1).lower()
        nm = _NAME.search(m.group(2))
        if nm:
            out[uid] = nm.group(1)
    return out


def _adopted_attrs(cfe_xml: Path) -> list[dict[str, str]]:
    """List Adopted attributes with Name + ExtendedConfigurationObject."""
    text = cfe_xml.read_text(encoding="utf-8", errors="replace")
    found: list[dict[str, str]] = []
    for m in _ATTR_BLOCK.finditer(text):
        body = m.group(2)
        if not _BELONGING.search(body):
            continue
        nm = _NAME.search(body)
        ext = _EXTENDED.search(body)
        if not nm or not ext:
            continue
        found.append(
            {
                "name": nm.group(1),
                "extended": ext.group(1).lower(),
                "cfeAttributeUuid": m.group(1).lower(),
            }
        )
    return found


def check_adopted_uuids(
    objects: list[str],
    *,
    repo_cf: str | Path | None,
    repo_cfe: str | Path | None,
    fail_if_repos_missing: bool = False,
) -> dict[str, Any]:
    """
    For each Adopted ExtendedConfigurationObject in cfe, require that uuid
    exists as Attribute uuid in the matching main CF object XML.
    """
    cf_root = Path(repo_cf) if repo_cf else None
    cfe_root = Path(repo_cfe) if repo_cfe else None
    if not cf_root or not cf_root.is_dir() or not cfe_root or not cfe_root.is_dir():
        if fail_if_repos_missing:
            return {
                "ok": False,
                "skipped": False,
                "reason": "REPO_CF / REPO_CFE not set or missing (required on WORK)",
                "mismatches": [],
                "missingInMain": [],
                "message": "Set REPO_CF and REPO_CFE for Adopted UUID gate on WORK load.",
            }
        return {
            "ok": True,
            "skipped": True,
            "reason": "REPO_CF / REPO_CFE not set or missing",
            "mismatches": [],
            "missingInMain": [],
        }

    missing: list[dict[str, str]] = []
    name_warnings: list[dict[str, str]] = []
    checked: list[str] = []

    for raw in objects:
        meta = (raw or "").strip()
        if not meta or meta.lower() in ("configuration", "конфигурация"):
            continue
        parts = meta.replace("\\", "/").split("/")
        head = parts[0]
        if head.count(".") < 1:
            continue
        kind_name = ".".join(head.split(".")[:2])
        cfe_xml = _object_xml_path(cfe_root, kind_name)
        if not cfe_xml:
            continue
        adopted = _adopted_attrs(cfe_xml)
        if not adopted:
            continue
        checked.append(kind_name)
        cf_xml = _object_xml_path(cf_root, kind_name)
        if not cf_xml:
            for a in adopted:
                missing.append(
                    {
                        "object": kind_name,
                        "attribute": a["name"],
                        "extended": a["extended"],
                        "problem": "main CF object XML not found",
                    }
                )
            continue
        main_uids = _all_attr_uuids(cf_xml)
        names = _name_by_uuid(cf_xml)
        for a in adopted:
            if a["extended"] not in main_uids:
                missing.append(
                    {
                        "object": kind_name,
                        "attribute": a["name"],
                        "extended": a["extended"],
                        "problem": "ExtendedConfigurationObject uuid not found as Attribute uuid in main CF",
                    }
                )
            else:
                main_name = names.get(a["extended"])
                if main_name and main_name != a["name"]:
                    name_warnings.append(
                        {
                            "object": kind_name,
                            "cfeName": a["name"],
                            "cfName": main_name,
                            "uuid": a["extended"],
                            "problem": "uuid present in main but Name differs",
                        }
                    )

    ok = not missing
    return {
        "ok": ok,
        "skipped": False,
        "checkedObjects": checked,
        "mismatches": [],
        "missingInMain": missing,
        "nameWarnings": name_warnings,
        "message": (
            None
            if ok
            else (
                "Adopted UUID gate failed: each cfe ExtendedConfigurationObject must exist "
                "as Attribute uuid in main CF. Do not invent UUIDs; load main first."
            )
        ),
    }
