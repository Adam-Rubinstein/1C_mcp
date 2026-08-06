"""WORK pipeline gates: storage_get aligned marker, lock receipt, staging HMAC."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from . import env, normalize_object_name


def _gates_root() -> Path:
    """Shared gates dir for dump/load/storage (stdio are separate processes)."""
    explicit = (env("MCP_GATES_ROOT") or "").strip()
    if explicit:
        path = Path(explicit)
    else:
        dump_tmp = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c")) or ".tmp/1c")
        # …/.tmp/load and …/.tmp/storage → shared …/.tmp/gates
        path = dump_tmp.parent / "gates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(target: str, extension: str | None) -> str:
    t = (target or "work").strip().lower()
    ext = (extension or "_main").strip() or "_main"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{t}__{ext}")
    return safe


def _norm_objects(objects: list[str]) -> list[str]:
    return sorted({normalize_object_name(o) for o in objects if (o or "").strip()})


def _ttl_sec() -> int:
    raw = env("MCP_GATE_TTL_SEC", "86400")
    try:
        return max(60, int(raw or "86400"))
    except ValueError:
        return 86400


def write_aligned_marker(
    objects: list[str],
    *,
    target: str = "work",
    extension: str | None = None,
) -> Path:
    canon = _norm_objects(objects)
    path = _gates_root() / f"aligned_{_key(target, extension)}.json"
    data = {
        "kind": "storage_aligned",
        "target": (target or "work").strip().lower(),
        "extension": extension,
        "objects": canon,
        "entire": len(canon) == 0,
        "ts": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_lock_receipt(
    objects: list[str],
    *,
    target: str = "work",
    extension: str | None = None,
) -> Path:
    canon = _norm_objects(objects)
    path = _gates_root() / f"lock_{_key(target, extension)}.json"
    data = {
        "kind": "storage_lock_receipt",
        "target": (target or "work").strip().lower(),
        "extension": extension,
        "objects": canon,
        "entire": len(canon) == 0,
        "ts": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_gate(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _covers(marker_objects: list[str], entire: bool, needed: list[str]) -> bool:
    if entire:
        return True
    have = {normalize_object_name(o) for o in marker_objects}
    need = set(_norm_objects(needed))
    return need <= have


def check_storage_aligned(
    objects: list[str],
    *,
    target: str,
    extension: str | None,
    storage_aligned: bool,
) -> dict[str, Any] | None:
    """Return error dict if WORK load must refuse; None if OK."""
    if not storage_aligned:
        return {
            "ok": False,
            "error": "Refusing WORK load without storage_aligned=true.",
            "step": "storage_get_then_aligned",
            "hint": "Call storage_get(objects=..., target=work) first, then load with storage_aligned=true.",
            "stop": True,
        }
    path = _gates_root() / f"aligned_{_key(target, extension)}.json"
    data = _read_gate(path)
    if not data or data.get("kind") != "storage_aligned":
        return {
            "ok": False,
            "error": "No storage_aligned marker. Run storage_get for these objects first.",
            "step": "storage_get_then_aligned",
            "markerPath": str(path),
            "stop": True,
        }
    age = time.time() - float(data.get("ts") or 0)
    if age > _ttl_sec():
        return {
            "ok": False,
            "error": "storage_aligned marker expired. Re-run storage_get.",
            "step": "storage_get_then_aligned",
            "ageSec": int(age),
            "stop": True,
        }
    if not _covers(list(data.get("objects") or []), bool(data.get("entire")), objects):
        return {
            "ok": False,
            "error": "storage_aligned marker does not cover all load objects.",
            "step": "storage_get_then_aligned",
            "markerObjects": data.get("objects"),
            "needed": _norm_objects(objects),
            "stop": True,
        }
    return None


def check_lock_receipt(
    objects: list[str],
    *,
    target: str,
    extension: str | None,
    storage_captured: bool,
) -> dict[str, Any] | None:
    """Return error dict if WORK load must refuse; None if OK."""
    if not storage_captured:
        return {
            "ok": False,
            "error": "Refusing load to WORK without storage_captured=true.",
            "step": "capture_then_approve",
            "stop": True,
        }
    path = _gates_root() / f"lock_{_key(target, extension)}.json"
    data = _read_gate(path)
    if not data or data.get("kind") != "storage_lock_receipt":
        return {
            "ok": False,
            "error": "No lock receipt. Run storage_lock (or UI capture then storage_lock) first.",
            "step": "storage_lock_receipt",
            "markerPath": str(path),
            "hint": "storage_captured=true alone is not enough; MCP needs a receipt from storage_lock.",
            "stop": True,
        }
    age = time.time() - float(data.get("ts") or 0)
    if age > _ttl_sec():
        return {
            "ok": False,
            "error": "Lock receipt expired. Re-run storage_lock.",
            "step": "storage_lock_receipt",
            "ageSec": int(age),
            "stop": True,
        }
    if not _covers(list(data.get("objects") or []), bool(data.get("entire")), objects):
        return {
            "ok": False,
            "error": "Lock receipt does not cover all load objects.",
            "step": "storage_lock_receipt",
            "receiptObjects": data.get("objects"),
            "needed": _norm_objects(objects),
            "stop": True,
        }
    return None


def entire_config_allowed() -> bool:
    return (env("ALLOW_ENTIRE_STORAGE_OPS") or "").strip() == "1"


def refuse_entire_without_env(*, entire_config: bool) -> dict[str, Any] | None:
    if entire_config and not entire_config_allowed():
        return {
            "ok": False,
            "error": "entire_config requires env ALLOW_ENTIRE_STORAGE_OPS=1.",
            "stop": True,
        }
    return None


def staging_secret() -> bytes:
    secret = (env("MCP_STAGING_SECRET") or "onec-mcp-staging-default").encode("utf-8")
    return secret


def hash_ext_files(staging: Path) -> dict[str, str]:
    """SHA256 of required Ext files relative to staging."""
    required = (
        "Ext/HomePageWorkArea.xml",
        "Ext/ClientApplicationInterface.xml",
        "Ext/CommandInterface.xml",
        "Ext/MainSectionCommandInterface.xml",
    )
    out: dict[str, str] = {}
    for rel in required:
        p = staging / rel
        if p.is_file():
            out[rel.replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def sign_prepared_marker(marker: dict[str, Any]) -> str:
    body = json.dumps(
        {k: marker[k] for k in sorted(marker) if k != "signature"},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hmac.new(staging_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_prepared_marker(source_dir: Path) -> dict[str, Any] | None:
    """None if OK; else error dict."""
    from .config_root import prepared_marker_path

    marker_path = prepared_marker_path(source_dir)
    if not marker_path.is_file():
        return {"ok": False, "error": "Prepared marker missing.", "stop": True}
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"Bad prepared marker: {exc}", "stop": True}
    sig = data.get("signature")
    if not sig or sig != sign_prepared_marker(data):
        return {
            "ok": False,
            "error": "Prepared marker signature invalid (forgeable/git Ext refused).",
            "step": "fix_prepared_marker_signature",
            "stop": True,
        }
    tmp_root = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp")) or ".tmp").resolve()
    try:
        source_dir.resolve().relative_to(tmp_root)
    except ValueError:
        return {
            "ok": False,
            "error": f"Prepared staging must be under DUMP_TMP_ROOT ({tmp_root}).",
            "step": "fix_prepared_staging_path",
            "stop": True,
        }
    expected = data.get("extHashes") or {}
    actual = hash_ext_files(source_dir)
    for rel, digest in expected.items():
        if actual.get(rel) != digest:
            return {
                "ok": False,
                "error": f"Ext hash mismatch for {rel} (staging tampered).",
                "step": "fix_prepared_ext_hash",
                "stop": True,
            }
    return None


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_forbidden_secret_path(path: Path) -> bool:
    name = path.name.lower()
    if name in ("mcp.json", ".env", ".env.local", "credentials.json"):
        return True
    if name.endswith(".pem") or name.endswith(".key"):
        return True
    return False


def refuse_work_ib_path(ib: str | None) -> dict[str, Any] | None:
    """Refuse if resolved IB path equals ONEC_IB_WORK."""
    if not ib:
        return None
    work = (env("ONEC_IB_WORK") or "").strip()
    if not work:
        return None
    try:
        if Path(ib).resolve() == Path(work).resolve():
            return {
                "ok": False,
                "error": "Refusing COM/journal against ONEC_IB_WORK. Use DEV only.",
                "stop": True,
            }
    except OSError:
        if os.path.normcase(os.path.normpath(ib)) == os.path.normcase(os.path.normpath(work)):
            return {
                "ok": False,
                "error": "Refusing COM/journal against ONEC_IB_WORK. Use DEV only.",
                "stop": True,
            }
    return None


_PARENT_KINDS = {
    "document",
    "catalog",
    "dataprocessor",
    "report",
    "chartofcharacteristictypes",
    "chartofaccounts",
    "chartofcalculationtypes",
    "businessprocess",
    "task",
    "exchangeplan",
    "documentjournal",
    "enum",
    "informationregister",
    "accumulationregister",
    "accountingregister",
    "calculationregister",
}


def refuse_parent_object_without_confirm(
    objects: list[str],
    *,
    confirm_parent_object: bool = False,
) -> dict[str, Any] | None:
    """Refuse bare Document.X / Catalog.X (no .Form.) unless confirm_parent_object.

    Incident 1286: locking/loading whole Document to push one Form overwrote all forms.
    """
    if confirm_parent_object:
        return None
    parents: list[str] = []
    for raw in objects:
        canon = normalize_object_name(raw)
        parts = canon.split(".")
        if len(parts) != 2:
            continue
        if parts[0].lower() in _PARENT_KINDS:
            parents.append(canon)
    if not parents:
        return None
    return {
        "ok": False,
        "error": (
            "Refusing parent metadata object without confirm_parent_object=true "
            f"(got: {', '.join(parents)}). Lock/load only Document.X.Form.Y "
            "or set confirm_parent_object=true deliberately (incident 1286)."
        ),
        "step": "refuse_parent_object",
        "parentObjects": parents,
        "stop": True,
        "hint": "Use Document.X.Form.Y (and Module via form path), not Document.X.",
    }


def forms_incomplete_after_dump(
    objects: list[str],
    dump_dir: Path,
    dumped_paths: list[str],
) -> list[str]:
    """Return missing Ext/Form.xml paths for managed Form objects in the dump list."""
    from . import object_to_list_entry

    missing: list[str] = []
    path_set = {p.replace("\\", "/") for p in dumped_paths}
    for raw in objects:
        canon = normalize_object_name(raw)
        parts = canon.split(".")
        if len(parts) < 4:
            continue
        if parts[2].lower() not in ("form", "форма"):
            continue
        entry = object_to_list_entry(canon, for_load=True)
        if not entry.endswith(".xml"):
            continue
        meta_path = dump_dir / entry.replace("/", os.sep)
        if meta_path.is_file():
            try:
                meta_txt = meta_path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                meta_txt = ""
            if "<FormType>Ordinary</FormType>" in meta_txt or "<FormType>OrdinaryForm</FormType>" in meta_txt:
                # Ordinary (thick) forms have no Ext/Form.xml in hierarchical dump.
                continue
        base = entry[:-4]
        need = f"{base}/Ext/Form.xml"
        if need not in path_set and not (dump_dir / need.replace("/", os.sep)).is_file():
            missing.append(need)
    for p in list(path_set):
        if "/Forms/" in p and p.endswith(".xml") and "/Ext/" not in p:
            meta = dump_dir / p.replace("/", os.sep)
            if meta.is_file():
                try:
                    meta_txt = meta.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    meta_txt = ""
                if "<FormType>Ordinary</FormType>" in meta_txt:
                    continue
            base = p[:-4]
            need = f"{base}/Ext/Form.xml"
            if need not in path_set and not (dump_dir / need.replace("/", os.sep)).is_file():
                if need not in missing:
                    missing.append(need)
    return missing


def forms_incomplete_in_source(objects: list[str], source_dir: Path) -> list[str]:
    """Missing Ext/Form.xml under source_dir for managed Form objects (load gate)."""
    from . import object_to_list_entry

    missing: list[str] = []
    for raw in objects:
        canon = normalize_object_name(raw)
        parts = canon.split(".")
        if len(parts) < 4 or parts[2].lower() not in ("form", "форма"):
            continue
        entry = object_to_list_entry(canon, for_load=True)
        if not entry.endswith(".xml"):
            continue
        meta_path = source_dir / entry.replace("/", os.sep)
        if meta_path.is_file():
            try:
                meta_txt = meta_path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                meta_txt = ""
            if "<FormType>Ordinary</FormType>" in meta_txt:
                continue
        need = f"{entry[:-4]}/Ext/Form.xml"
        if not (source_dir / need.replace("/", os.sep)).is_file():
            missing.append(need)
    return missing
