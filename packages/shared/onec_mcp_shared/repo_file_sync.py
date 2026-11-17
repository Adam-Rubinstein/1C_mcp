"""Copy a WORK configuration dump into src without a storage lock or load receipt.

Daily sync writes only the storage diff. A one-time full dump uses the same merge.
Neither path captures objects or produces a load baseline.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from . import TYPE_TO_FOLDER, env, normalize_object_name
from .git_run import run_git
from .work_gates import (
    _gates_root,
    _object_lock_stale,
    _object_lock_ttl_sec,
    _read_gate,
    stash_dirty_paths,
)

_ROOT_NAMES = {
    "configuration",
    "конфигурация",
    "configuration.xml",
    "управлениепредприятием",
}
_CHILD_FOLDER = {
    "Form": "Forms",
    "Форма": "Forms",
    "Command": "Commands",
    "Команда": "Commands",
    "Template": "Templates",
    "Макет": "Templates",
}
_VERSION_RE = re.compile(r"^Версия:\t(\d+)\s*$")
_SECTION_RE = re.compile(r"^\t(Добавлены|Изменены|Удалены)\t")
_OBJECT_RE = re.compile(r"^\t(\S.*?)\s*$")
_METADATA_NAME_RE = re.compile(r"<Metadata\b[^>]*\bname=\"([^\"]+)\"")


def parse_storage_report(
    text: str,
    *,
    n_begin: int | None = None,
    n_end: int | None = None,
    include_objects: bool = True,
) -> dict[str, Any]:
    """Version-grouped repository report.

    ``changedObjects`` and ``deletedObjects`` are filled only for versions inside
    the requested range. The configuration root is omitted. Last action wins.
    """
    version: int | None = None
    section: str | None = None
    tip = 0
    action: dict[str, str] = {}
    for raw in text.splitlines():
        version_match = _VERSION_RE.match(raw)
        if version_match:
            version = int(version_match.group(1))
            tip = max(tip, version)
            section = None
            continue
        if version is None:
            continue
        in_range = True
        if n_begin is not None and version < n_begin:
            in_range = False
        if n_end is not None and version > n_end:
            in_range = False
        section_match = _SECTION_RE.match(raw)
        if section_match:
            section = section_match.group(1) if in_range else None
            continue
        if not include_objects:
            continue
        object_match = _OBJECT_RE.match(raw)
        if not object_match or not section:
            continue
        name = object_match.group(1).strip()
        if _is_configuration_root(name):
            continue
        action[name] = "deleted" if section == "Удалены" else "changed"
    changed = [name for name, kind in action.items() if kind == "changed"]
    deleted = [name for name, kind in action.items() if kind == "deleted"]
    return {
        "tipVersion": tip or None,
        "changedObjects": changed,
        "deletedObjects": deleted,
    }


def _is_configuration_root(name: str) -> bool:
    token = name.strip().lower().replace("\\", "/")
    return token in _ROOT_NAMES or "." not in token


def project_root() -> Path:
    repo = Path(env("REPO_CF") or ".").resolve()
    if repo.parent.name.lower() == "src":
        return repo.parent.parent
    return repo


def marker_path() -> Path:
    return project_root() / ".cursor" / "tmp" / "storage-sync-date.txt"


def read_sync_marker() -> dict[str, str]:
    path = marker_path()
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            data[key] = value.strip()
    return data


def record_sync_tip(*, side: str, tip_version: int) -> dict[str, str]:
    """Record one storage tip. fileSync=ok only when both tips are present."""
    if side not in ("cf", "cfe"):
        raise ValueError("side must be cf or cfe")
    if tip_version < 1:
        raise ValueError("tip_version must be positive")
    data = read_sync_marker()
    data["date"] = date.today().isoformat()
    data["cfTip" if side == "cf" else "cfeTip"] = str(tip_version)
    if data.get("cfTip") and data.get("cfeTip"):
        data["fileSync"] = "ok"
    else:
        data.pop("fileSync", None)
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"date={data['date']}",
        f"cfTip={data['cfTip']}" if data.get("cfTip") else "",
        f"cfeTip={data['cfeTip']}" if data.get("cfeTip") else "",
        f"fileSync={data['fileSync']}" if data.get("fileSync") else "",
    ]
    path.write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
    return data


def foreign_object_locks() -> list[dict[str, Any]]:
    """Live object locks held by any task. File sync must not start over them."""
    root = _gates_root()
    now = time_now()
    ttl = _object_lock_ttl_sec()
    found: list[dict[str, Any]] = []
    for path in sorted(root.glob("objectlock_*.json")):
        data = _read_gate(path)
        if not data or _object_lock_stale(data, now=now, ttl=ttl):
            continue
        found.append(
            {
                "object": data.get("object"),
                "task": data.get("task"),
                "tool": data.get("tool"),
                "pid": data.get("pid"),
            }
        )
    return found


def time_now() -> float:
    import time

    return time.time()


def work_session_open(ib_path: str) -> list[dict[str, Any]]:
    from .session import find_ib_processes

    return [
        {"pid": proc.pid, "kind": proc.kind}
        for proc in find_ib_processes(ib_path)
        if proc.kind in ("designer", "enterprise")
    ]


def object_output_paths(name: str) -> list[Path]:
    """Repo-relative paths owned by one metadata object. Unknown types yield nothing."""
    canon = normalize_object_name(name.strip())
    if _is_configuration_root(canon):
        return []
    parts = canon.split(".")
    folder = TYPE_TO_FOLDER.get(parts[0])
    if not folder or len(parts) < 2:
        return []
    if len(parts) >= 4 and parts[2] in _CHILD_FOLDER:
        child = _CHILD_FOLDER[parts[2]]
        child_name = ".".join(parts[3:])
        base = Path(folder) / parts[1] / child / child_name
        return [Path(str(base) + ".xml"), base]
    base = Path(folder) / parts[1]
    return [Path(str(base) + ".xml"), base]


def metadata_names(config_dump_info: Path) -> set[str]:
    if not config_dump_info.is_file():
        return set()
    text = config_dump_info.read_text(encoding="utf-8", errors="replace")
    return set(_METADATA_NAME_RE.findall(text))


def remove_object_files(repo: Path, names: list[str]) -> list[str]:
    removed: list[str] = []
    repo_resolved = repo.resolve()
    for name in names:
        for relative in object_output_paths(name):
            target = (repo / relative).resolve()
            try:
                target.relative_to(repo_resolved)
            except ValueError:
                continue
            if target.is_file():
                target.unlink()
                removed.append(str(relative).replace("\\", "/"))
            elif target.is_dir():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                target.rmdir()
                removed.append(str(relative).replace("\\", "/") + "/")
    return removed


def dirty_paths_under(repo: Path) -> list[str]:
    """Git-root-relative dirty paths inside ``repo``. One git call, not one per file."""
    root = _git_root(repo)
    if root is None:
        return []
    try:
        prefix = repo.resolve().relative_to(root).as_posix()
    except ValueError:
        return []
    if prefix == ".":
        prefix = ""
    dirty: list[str] = []
    for args in (
        ["diff", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        result = run_git(
            ["-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            continue
        for line in (result.stdout or "").splitlines():
            posix = line.strip().replace("\\", "/")
            if not posix:
                continue
            if prefix and posix != prefix and not posix.startswith(prefix + "/"):
                continue
            dirty.append(posix)
    return sorted(set(dirty))


def _git_root(path: Path) -> Path | None:
    result = run_git(
        ["-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return Path(text) if text else None


def merge_stash_three_way(repo: Path, stash_dir: Path) -> dict[str, Any]:
    """Merge stashed local edits onto files already replaced by the dump.

    A conflict restores that path from the stash and leaves the rest of the dump.
    """
    root = _git_root(repo)
    manifest_path = stash_dir / "manifest.json"
    if root is None or not manifest_path.is_file():
        return {
            "ok": False,
            "conflicts": [{"path": "", "error": "stash manifest or git root is missing"}],
        }
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conflicts: list[dict[str, str]] = []
    applied: list[str] = []
    staged: list[tuple[Path, bytes | None]] = []
    for entry in manifest.get("files") or []:
        rel = str(entry.get("path") or "").replace("\\", "/")
        kind = str(entry.get("kind") or "")
        if not rel or ".." in Path(rel).parts:
            conflicts.append({"path": rel, "error": "unsafe stash path"})
            continue
        destination = (root / rel).resolve()
        try:
            destination.relative_to(repo.resolve())
        except ValueError:
            conflicts.append({"path": rel, "error": "stash path is outside the repo"})
            continue
        stashed_file = stash_dir / rel.replace("/", os.sep)
        if kind == "untracked":
            if not stashed_file.is_file():
                conflicts.append({"path": rel, "error": "stashed file is missing"})
                continue
            if destination.is_file() and destination.read_bytes() != stashed_file.read_bytes():
                conflicts.append(
                    {"path": rel, "error": "untracked file now exists with different content"}
                )
                continue
            staged.append((destination, stashed_file.read_bytes()))
            applied.append(rel)
            continue
        if kind not in {"tracked", "tracked_deleted"} or (
            kind != "tracked_deleted" and not stashed_file.is_file()
        ):
            conflicts.append({"path": rel, "error": "stashed file is missing"})
            continue
        base_result = run_git(
            ["-C", str(root), "show", f"HEAD:{rel}"],
            capture_output=True,
            check=False,
        )
        if base_result.returncode != 0:
            conflicts.append({"path": rel, "error": "cannot read HEAD base"})
            _restore_bytes(destination, None if kind == "tracked_deleted" else stashed_bytes)
            continue
        current_bytes = destination.read_bytes() if destination.is_file() else b""
        stashed_bytes = b"" if kind == "tracked_deleted" else stashed_file.read_bytes()
        use_crlf = b"\r\n" in current_bytes or b"\r\n" in stashed_bytes
        merged = _merge_file_bytes(current_bytes, base_result.stdout, stashed_bytes)
        if merged is None:
            conflicts.append({"path": rel, "error": "three-way merge conflict"})
            _restore_bytes(destination, None if kind == "tracked_deleted" else stashed_bytes)
            continue
        if use_crlf:
            merged = merged.replace(b"\n", b"\r\n")
        content = None if kind == "tracked_deleted" and not merged else merged
        staged.append((destination, content))
        applied.append(rel)
    if conflicts:
        for entry in manifest.get("files") or []:
            rel = str(entry.get("path") or "").replace("\\", "/")
            kind = str(entry.get("kind") or "")
            if not rel or ".." in Path(rel).parts:
                continue
            destination = (root / rel).resolve()
            try:
                destination.relative_to(repo.resolve())
            except ValueError:
                continue
            stashed_file = stash_dir / rel.replace("/", os.sep)
            if kind == "tracked_deleted":
                _restore_bytes(destination, None)
            elif stashed_file.is_file():
                _restore_bytes(destination, stashed_file.read_bytes())
        return {"ok": False, "conflicts": conflicts, "appliedPaths": []}
    for destination, content in staged:
        if content is None:
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.filesync.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    return {"ok": True, "conflicts": [], "appliedPaths": applied}


def _restore_bytes(destination: Path, content: bytes | None) -> None:
    if content is None:
        destination.unlink(missing_ok=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _merge_file_bytes(current: bytes, base: bytes, stash: bytes) -> bytes | None:
    current_n = current.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    base_n = base.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    stash_n = stash.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with tempfile.TemporaryDirectory(prefix="onec-filesync-merge-") as temp_name:
        temp = Path(temp_name)
        current_file = temp / "current"
        base_file = temp / "base"
        stash_file = temp / "stash"
        current_file.write_bytes(current_n)
        base_file.write_bytes(base_n)
        stash_file.write_bytes(stash_n)
        result = run_git(
            ["merge-file", "-p", str(current_file), str(base_file), str(stash_file)],
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        return None
    return result.stdout


def stash_repo_dirty(repo: Path, dirty: list[str]) -> Path:
    return stash_dirty_paths(repo, dirty)
