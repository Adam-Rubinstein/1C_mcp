"""WORK pipeline gates: storage_get aligned marker, lock receipt, staging HMAC."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import subprocess
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


# write_aligned_marker / write_lock_receipt: per-object files (see below).


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


# check_storage_aligned / check_lock_receipt: per-object + captured-skip-get (see below).


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


# --- Parallel agents / Designer mutex (2026-08-19 incidents 5359 / 5361) ---

_SKIP_DIRTY = frozenset({"objects.txt", "designer.out", "configdumpinfo.xml"})
_DESIGNER_BUSY_HINTS = (
    "уже открыта конфигуратором",
    "информационная база уже открыта",
    "already opened by configurator",
)


class DesignerBusy(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("error") or "work_designer_busy")
        self.payload = payload


def _is_work(target: str) -> bool:
    return (target or "").strip().lower() in ("work", "prod", "base3")


def _object_file_key(obj: str) -> str:
    canon = normalize_object_name(obj)
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in canon)[:180]


def _object_marker_path(kind: str, target: str, extension: str | None, obj: str) -> Path:
    return _gates_root() / f"{kind}_{_key(target, extension)}__{_object_file_key(obj)}.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except SystemError:
        return False


def _int_env(name: str, default: int) -> int:
    raw = env(name, str(default))
    try:
        return max(1, int(raw or default))
    except ValueError:
        return default


def require_work_task(task: str | None, *, target: str) -> dict[str, Any] | None:
    if not _is_work(target):
        return None
    if (task or "").strip():
        return None
    return {
        "ok": False,
        "error": "WORK dump/load/get/lock requires task= (TZ number). Refusing without it.",
        "step": "require_task",
        "hint": "Pass task='5359' (your TZ number). Same task may re-enter the object lock.",
        "stop": True,
    }


def write_aligned_marker(
    objects: list[str],
    *,
    target: str = "work",
    extension: str | None = None,
) -> Path:
    canon = _norm_objects(objects)
    ts = time.time()
    for obj in canon:
        path = _object_marker_path("aligned", target, extension, obj)
        path.write_text(
            json.dumps(
                {
                    "kind": "storage_aligned",
                    "target": (target or "work").strip().lower(),
                    "extension": extension,
                    "objects": [obj],
                    "entire": False,
                    "ts": ts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    bundle = _gates_root() / f"aligned_{_key(target, extension)}.json"
    prev = _read_gate(bundle) or {}
    have = set(_norm_objects(list(prev.get("objects") or [])))
    have.update(canon)
    merged = sorted(have)
    entire = bool(prev.get("entire")) and not canon
    bundle.write_text(
        json.dumps(
            {
                "kind": "storage_aligned",
                "target": (target or "work").strip().lower(),
                "extension": extension,
                "objects": merged,
                "entire": entire,
                "ts": ts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return bundle


def write_lock_receipt(
    objects: list[str],
    *,
    target: str = "work",
    extension: str | None = None,
) -> Path:
    canon = _norm_objects(objects)
    ts = time.time()
    for obj in canon:
        path = _object_marker_path("lock", target, extension, obj)
        path.write_text(
            json.dumps(
                {
                    "kind": "storage_lock_receipt",
                    "target": (target or "work").strip().lower(),
                    "extension": extension,
                    "objects": [obj],
                    "entire": False,
                    "ts": ts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    bundle = _gates_root() / f"lock_{_key(target, extension)}.json"
    prev = _read_gate(bundle) or {}
    have = set(_norm_objects(list(prev.get("objects") or [])))
    have.update(canon)
    merged = sorted(have)
    entire = bool(prev.get("entire")) and not canon
    bundle.write_text(
        json.dumps(
            {
                "kind": "storage_lock_receipt",
                "target": (target or "work").strip().lower(),
                "extension": extension,
                "objects": merged,
                "entire": entire,
                "ts": ts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return bundle


def _object_covered(kind: str, obj: str, *, target: str, extension: str | None) -> bool:
    path = _object_marker_path(kind, target, extension, obj)
    data = _read_gate(path)
    if data and data.get("kind") in ("storage_aligned", "storage_lock_receipt"):
        age = time.time() - float(data.get("ts") or 0)
        if age <= _ttl_sec():
            return True
    bundle = _gates_root() / f"{kind}_{_key(target, extension)}.json"
    data = _read_gate(bundle)
    if not data:
        return False
    age = time.time() - float(data.get("ts") or 0)
    if age > _ttl_sec():
        return False
    return _covers(list(data.get("objects") or []), bool(data.get("entire")), [obj])


def objects_covered_by_lock(objects: list[str], *, target: str, extension: str | None) -> bool:
    needed = _norm_objects(objects)
    if not needed:
        bundle = _gates_root() / f"lock_{_key(target, extension)}.json"
        data = _read_gate(bundle)
        return bool(data and data.get("entire"))
    return all(_object_covered("lock", obj, target=target, extension=extension) for obj in needed)


def refuse_get_captured(
    objects: list[str],
    *,
    target: str,
    extension: str | None,
    confirm_get_captured: bool = False,
) -> dict[str, Any] | None:
    if confirm_get_captured:
        return None
    captured = [
        obj
        for obj in _norm_objects(objects)
        if _object_covered("lock", obj, target=target, extension=extension)
    ]
    if not captured:
        return None
    return {
        "ok": False,
        "error": (
            "Refusing storage_get on already-captured objects "
            f"({', '.join(captured)}). Get pulls last Put and wipes unpublished WORK edits."
        ),
        "step": "refuse_get_captured",
        "capturedObjects": captured,
        "hint": "Dump from WORK IB instead. confirm_get_captured=true only if user asked to roll back to storage.",
        "stop": True,
    }


def check_storage_aligned(
    objects: list[str],
    *,
    target: str,
    extension: str | None,
    storage_aligned: bool,
) -> dict[str, Any] | None:
    """Return error dict if WORK load must refuse; None if OK.

    Already-captured objects do not need a fresh storage_get (incident 5359).
    """
    if objects_covered_by_lock(objects, target=target, extension=extension):
        return None
    if not storage_aligned:
        return {
            "ok": False,
            "error": "Refusing WORK load without storage_aligned=true.",
            "step": "storage_get_then_aligned",
            "hint": (
                "If already captured: dump from WORK, then load with storage_captured=true "
                "(no Get). Else storage_get first."
            ),
            "stop": True,
        }
    needed = _norm_objects(objects)
    missing = [
        obj for obj in needed if not _object_covered("aligned", obj, target=target, extension=extension)
    ]
    if missing:
        return {
            "ok": False,
            "error": "No storage_aligned marker for all load objects. Run storage_get first (if not captured).",
            "step": "storage_get_then_aligned",
            "needed": missing,
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
    if not storage_captured:
        return {
            "ok": False,
            "error": "Refusing load to WORK without storage_captured=true.",
            "step": "capture_then_approve",
            "stop": True,
        }
    if objects_covered_by_lock(objects, target=target, extension=extension):
        return None
    path = _gates_root() / f"lock_{_key(target, extension)}.json"
    return {
        "ok": False,
        "error": "No lock receipt. Run storage_lock (or UI capture then storage_lock) first.",
        "step": "storage_lock_receipt",
        "markerPath": str(path),
        "needed": _norm_objects(objects),
        "hint": "storage_captured=true alone is not enough; MCP needs a receipt from storage_lock.",
        "stop": True,
    }


def _object_lock_path(target: str, extension: str | None, obj: str) -> Path:
    return _gates_root() / f"objectlock_{_key(target, extension)}__{_object_file_key(obj)}.json"


def acquire_object_locks(
    objects: list[str],
    *,
    task: str,
    target: str,
    extension: str | None,
    tool: str = "",
) -> dict[str, Any] | None:
    if not _is_work(target):
        return None
    task_s = (task or "").strip()
    if not task_s:
        return require_work_task(task, target=target)
    now = time.time()
    ttl = _ttl_sec()
    held: list[dict[str, Any]] = []
    for obj in _norm_objects(objects):
        path = _object_lock_path(target, extension, obj)
        data = _read_gate(path)
        if data:
            other = str(data.get("task") or "").strip()
            age = now - float(data.get("ts") or 0)
            stale = age > ttl or not _pid_alive(int(data.get("pid") or 0))
            if other and other != task_s and not stale:
                held.append(
                    {
                        "object": obj,
                        "task": other,
                        "pid": data.get("pid"),
                        "tool": data.get("tool"),
                    }
                )
                continue
        path.write_text(
            json.dumps(
                {
                    "kind": "object_lock",
                    "task": task_s,
                    "object": obj,
                    "pid": os.getpid(),
                    "ts": now,
                    "tool": tool,
                    "target": (target or "work").strip().lower(),
                    "extension": extension,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if held:
        return {
            "ok": False,
            "error": "Object held by another task. Do not dump/load/get the same module.",
            "step": "object_held_by_other_task",
            "held": held,
            "task": task_s,
            "hint": "Wait until that agent unlocks, or work on a different object.",
            "stop": True,
        }
    return None


def release_object_locks(
    objects: list[str],
    *,
    task: str | None,
    target: str,
    extension: str | None,
) -> None:
    task_s = (task or "").strip()
    for obj in _norm_objects(objects):
        path = _object_lock_path(target, extension, obj)
        data = _read_gate(path)
        if not data:
            continue
        if task_s and str(data.get("task") or "").strip() not in ("", task_s):
            continue
        try:
            path.unlink()
        except OSError:
            pass


def clear_lock_receipts(objects: list[str], *, target: str, extension: str | None) -> None:
    for obj in _norm_objects(objects):
        path = _object_marker_path("lock", target, extension, obj)
        try:
            path.unlink()
        except OSError:
            pass
    bundle = _gates_root() / f"lock_{_key(target, extension)}.json"
    data = _read_gate(bundle)
    if not data:
        return
    drop = set(_norm_objects(objects))
    remain = [o for o in _norm_objects(list(data.get("objects") or [])) if o not in drop]
    if remain:
        data["objects"] = remain
        data["entire"] = False
        bundle.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        try:
            bundle.unlink()
        except OSError:
            pass


def _git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def dirty_paths_vs_git(dest_dir: Path, rel_paths: list[str]) -> list[str]:
    """Paths under dest_dir that differ from HEAD or are untracked (not dump junk)."""
    root = _git_root(dest_dir)
    if root is None:
        return []
    dirty: list[str] = []
    for rel in rel_paths:
        name = Path(rel).name.lower()
        if name in _SKIP_DIRTY:
            continue
        dest = dest_dir / rel.replace("/", os.sep)
        if not dest.is_file():
            continue
        try:
            rel_to_root = dest.resolve().relative_to(root)
        except ValueError:
            continue
        posix = str(rel_to_root).replace("\\", "/")
        try:
            diff = subprocess.run(
                ["git", "-C", str(root), "diff", "--name-only", "HEAD", "--", posix],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            untracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "--", posix],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if (diff.stdout or "").strip() or (untracked.stdout or "").strip():
            dirty.append(posix)
    return dirty


def refuse_dirty_repo(
    src_dir: Path,
    dest_dir: Path,
    *,
    confirm_overwrite_dirty: bool = False,
) -> dict[str, Any] | None:
    if confirm_overwrite_dirty:
        return None
    rels: list[str] = []
    if src_dir.is_dir():
        for src in src_dir.rglob("*"):
            if src.is_file():
                rels.append(str(src.relative_to(src_dir)).replace("\\", "/"))
    dirty = dirty_paths_vs_git(dest_dir, rels)
    if not dirty:
        return None
    return {
        "ok": False,
        "error": "Refusing merge_into_repo over dirty git files (would wipe another agent's work).",
        "step": "refuse_dirty_repo",
        "dirtyPaths": dirty,
        "hint": "Do not set confirm_overwrite_dirty unless the user explicitly asked to overwrite.",
        "stop": True,
    }


def _designer_lock_path(target: str) -> Path:
    name = "work_designer.lock" if _is_work(target) else "dev_designer.lock"
    return _gates_root() / name


def acquire_designer_lock(target: str, *, tool: str = "") -> dict[str, Any] | None:
    if not _is_work(target):
        return None
    path = _designer_lock_path(target)
    wait_sec = _int_env("MCP_DESIGNER_LOCK_WAIT_SEC", 720)
    ttl_sec = _int_env("MCP_DESIGNER_LOCK_TTL_SEC", 1200)
    poll = 0.5
    deadline = time.time() + wait_sec
    my_pid = os.getpid()
    while True:
        data = _read_gate(path)
        if data:
            pid = int(data.get("pid") or 0)
            depth = int(data.get("depth") or 1)
            age = time.time() - float(data.get("ts") or 0)
            if pid == my_pid:
                data["depth"] = depth + 1
                data["ts"] = time.time()
                data["tool"] = tool or data.get("tool")
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return None
            stale = (not _pid_alive(pid)) or age > ttl_sec
            if stale:
                try:
                    path.unlink()
                except OSError:
                    pass
                data = None
        if data is None:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    payload = json.dumps(
                        {
                            "kind": "designer_lock",
                            "pid": my_pid,
                            "ts": time.time(),
                            "tool": tool,
                            "depth": 1,
                            "target": (target or "work").strip().lower(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    os.write(fd, payload.encode("utf-8"))
                finally:
                    os.close(fd)
                return None
            except FileExistsError:
                pass
        if time.time() >= deadline:
            holder = _read_gate(path) or {}
            return {
                "ok": False,
                "error": "WORK Configurator busy (designer mutex wait timed out).",
                "step": "work_designer_busy",
                "holder": holder,
                "hint": "Wait; do not taskkill /IM 1cv8.exe. Retry after the other agent finishes.",
                "stop": True,
            }
        time.sleep(poll)


def release_designer_lock(target: str) -> None:
    if not _is_work(target):
        return
    path = _designer_lock_path(target)
    data = _read_gate(path)
    if not data:
        return
    if int(data.get("pid") or 0) != os.getpid():
        return
    depth = int(data.get("depth") or 1) - 1
    if depth > 0:
        data["depth"] = depth
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    try:
        path.unlink()
    except OSError:
        pass


@contextlib.contextmanager
def designer_mutex(target: str, *, tool: str = ""):
    err = acquire_designer_lock(target, tool=tool)
    if err:
        raise DesignerBusy(err)
    try:
        yield
    finally:
        release_designer_lock(target)


def log_looks_designer_busy(log_text: str) -> bool:
    low = (log_text or "").lower()
    return any(h in low for h in _DESIGNER_BUSY_HINTS)


def designer_busy_payload(*, log_text: str, exit_code: int, ib: str | None = None) -> dict[str, Any] | None:
    empty = not (log_text or "").strip()
    hinted = log_looks_designer_busy(log_text)
    live = False
    if ib:
        try:
            from .session import find_ib_processes

            live = any(p.kind == "designer" for p in find_ib_processes(ib))
        except Exception:
            live = False
    if hinted or (empty and exit_code != 0 and live) or (empty and exit_code != 0 and hinted):
        return {
            "ok": False,
            "error": "WORK infobase is already open in Configurator (or Designer busy with empty log).",
            "step": "work_designer_busy",
            "hint": "Wait for work_designer.lock; do not taskkill /IM 1cv8.exe.",
            "stop": True,
        }
    if empty and exit_code != 0:
        return {
            "ok": False,
            "error": "Designer failed with empty log (often 'база уже открыта').",
            "step": "work_designer_busy",
            "hint": "Wait for the other agent / mutex; do not taskkill /IM 1cv8.exe.",
            "stop": True,
        }
    return None
