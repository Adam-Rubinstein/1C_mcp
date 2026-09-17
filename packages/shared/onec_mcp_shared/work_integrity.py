"""Fail-closed integrity primitives for the 1C WORK pipeline.

The helpers in this module deliberately operate on exact metadata object names.
A parent object never implies ownership of a child form: ``Document.X`` covers
only ``Documents/X.xml`` and ``Documents/X/Ext/**``, while
``Document.X.Form.Y`` covers its own metadata file and subtree.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from . import normalize_object_name, object_to_list_entry
from .work_gates import _gates_root, staging_secret

JsonObject = dict[str, Any]

_CHILD_KINDS = {
    "Form": "Form",
    "Форма": "Form",
    "Command": "Command",
    "Команда": "Command",
    "Template": "Template",
    "Макет": "Template",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MARKER_RE = re.compile(
    r"^\s*//\s*(?:Эстет|Eugene|Таланцева|БИТ)(?:\b|_)",
    re.IGNORECASE,
)
_UNIT_START_RE = re.compile(
    r"^\s*(?:Асинх\s+|Async\s+)?"
    r"(Процедура|Функция|Procedure|Function)\s+"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(",
    re.IGNORECASE,
)
_UNIT_END_RE = {
    "procedure": re.compile(r"^\s*(?:КонецПроцедуры|EndProcedure)\b", re.IGNORECASE),
    "function": re.compile(r"^\s*(?:КонецФункции|EndFunction)\b", re.IGNORECASE),
}
_NON_EXECUTABLE_RE = re.compile(
    r"^\s*(?:(?:Асинх\s+|Async\s+)?"
    r"(?:Процедура|Функция|Procedure|Function)\b|"
    r"(?:КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b|"
    r"(?:Перем|Var)\b)",
    re.IGNORECASE,
)
_OUTSIDE_STRING_TOKEN_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*|"
    r"\d+(?:\.\d+)?|"
    r"<>|<=|>=|:=|"
    r"[^\s]",
    re.UNICODE,
)


class IntegrityError(ValueError):
    """Raised when an integrity artifact cannot be safely created or read."""


def normalize_objects(objects: Iterable[str]) -> list[str]:
    """Return sorted, unique canonical object names.

    Empty lists, malformed names, path separators and traversal are rejected.
    Child metadata aliases are canonicalized as well as top-level Russian
    aliases handled by :func:`normalize_object_name`.
    """

    if isinstance(objects, (str, bytes)):
        raise TypeError("objects must be an iterable of object names, not a string")

    normalized: set[str] = set()
    for raw in objects:
        if not isinstance(raw, str):
            raise TypeError("every object name must be a string")
        canon = normalize_object_name(raw.strip())
        if not canon:
            raise IntegrityError("object names must not be empty")
        if "/" in canon or "\\" in canon or "\x00" in canon:
            raise IntegrityError(f"unsafe object name: {raw!r}")

        if canon.casefold() in {"configuration", "конфигурация", "configuration.xml"}:
            canon = "Configuration"
        else:
            parts = canon.split(".")
            if len(parts) < 2 or any(not part for part in parts):
                raise IntegrityError(f"malformed object name: {raw!r}")
            if len(parts) > 2 and (len(parts) != 4 or parts[2] not in _CHILD_KINDS):
                raise IntegrityError(f"unsupported nested object name: {raw!r}")
            if len(parts) == 4:
                parts[2] = _CHILD_KINDS[parts[2]]
                canon = ".".join(parts)

        entry = object_to_list_entry(canon, for_load=True).replace("\\", "/")
        _validate_relative_path(entry)
        normalized.add(canon)

    if not normalized:
        raise IntegrityError("at least one explicit object is required")
    return sorted(normalized)


def _validate_relative_path(value: str) -> str:
    rel = value.replace("\\", "/")
    path = PurePosixPath(rel)
    if (
        not rel
        or path.is_absolute()
        or ".." in path.parts
        or any(":" in part or part in {"", "."} for part in path.parts)
    ):
        raise IntegrityError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _object_scope(obj: str) -> tuple[str, tuple[str, ...]]:
    metadata = _validate_relative_path(object_to_list_entry(obj, for_load=True))
    if not metadata.lower().endswith(".xml"):
        raise IntegrityError(f"object does not map to hierarchical XML: {obj!r}")

    if obj == "Configuration":
        return metadata, ("Ext",)
    base = metadata[:-4]
    parts = obj.split(".")
    is_child = len(parts) >= 4 and parts[2] in set(_CHILD_KINDS.values())
    if is_child:
        return metadata, (base,)
    return metadata, (f"{base}/Ext",)


def _checked_file(root: Path, path: Path) -> Path:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise IntegrityError(f"object file escapes root: {path}") from exc
    if path.is_symlink():
        raise IntegrityError(f"symbolic links are not allowed in object snapshots: {path}")
    return path


def collect_object_files(root: str | Path, objects: Iterable[str]) -> list[str]:
    """Collect slash-relative files belonging to the exact object list."""

    root_path = Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"snapshot root does not exist: {root_path}")

    result: set[str] = set()
    for obj in normalize_objects(objects):
        metadata, trees = _object_scope(obj)
        metadata_path = root_path / metadata.replace("/", os.sep)
        if metadata_path.is_file():
            _checked_file(root_path, metadata_path)
            result.add(metadata)

        for tree in trees:
            tree_path = root_path / tree.replace("/", os.sep)
            if not tree_path.is_dir():
                continue
            for file_path in tree_path.rglob("*"):
                if not file_path.is_file():
                    continue
                _checked_file(root_path, file_path)
                result.add(file_path.relative_to(root_path).as_posix())

    return sorted(result)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_object_files(root: str | Path, objects: Iterable[str]) -> dict[str, str]:
    """Return deterministic SHA256 hashes keyed by slash-relative paths."""

    root_path = Path(root)
    return {
        rel: _sha256_file(root_path / rel.replace("/", os.sep))
        for rel in collect_object_files(root_path, objects)
    }


def copy_object_files(
    root: str | Path,
    objects: Iterable[str],
    immutable_baseline_dir: str | Path,
) -> dict[str, str]:
    """Copy an exact object snapshot into a new, never-overwritten baseline.

    The destination must not already exist. The returned mapping contains
    hashes read back from the completed baseline.
    """

    source = Path(root)
    destination = Path(immutable_baseline_dir)
    files = collect_object_files(source, objects)
    if not files:
        raise FileNotFoundError("none of the requested object files exist in the snapshot")
    if destination.exists():
        raise FileExistsError(f"immutable baseline already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for rel in files:
            src = source / rel.replace("/", os.sep)
            dst = destination / rel.replace("/", os.sep)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return hash_object_files(destination, objects)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _form_elements(text: str) -> list[JsonObject]:
    root = ET.fromstring(text)
    elements: list[JsonObject] = []
    for element in root.iter():
        tag = _local_name(element.tag)
        name = element.attrib.get("name") or element.attrib.get("Name")
        element_id = element.attrib.get("id") or element.attrib.get("ID")
        call_type = element.attrib.get("callType") or element.attrib.get("CallType")
        event_handler = (
            " ".join((element.text or "").split()) or None
            if tag.casefold() == "event"
            else None
        )
        data_path: str | None = None
        action: str | None = None
        title: str | None = None
        for child in element:
            child_tag = _local_name(child.tag).casefold()
            if child_tag == "datapath":
                data_path = " ".join((child.text or "").split()) or None
            elif child_tag == "action":
                action = " ".join(" ".join(child.itertext()).split()) or None
            elif child_tag in {"title", "tooltip"}:
                title = " ".join(" ".join(child.itertext()).split()) or None
        if (
            name is None
            and element_id is None
            and data_path is None
            and event_handler is None
        ):
            continue
        elements.append(
            {
                "tag": tag,
                "name": name,
                "id": element_id,
                "dataPath": data_path,
                "action": action,
                "title": title,
                "callType": call_type,
                "eventHandler": event_handler,
            }
        )
    return elements


def _form_key(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        str(item.get("tag") or "").casefold(),
        str(item.get("name") or ""),
        str(item.get("id") or ""),
        str(item.get("dataPath") or ""),
        str(item.get("action") or "").casefold(),
        str(item.get("title") or "").casefold(),
        str(item.get("callType") or "").casefold(),
        str(item.get("eventHandler") or "").casefold(),
    )


def _bsl_units(text: str) -> list[JsonObject]:
    lines = text.splitlines()
    units: list[JsonObject] = []
    index = 0
    while index < len(lines):
        match = _UNIT_START_RE.match(lines[index])
        if not match:
            index += 1
            continue
        keyword = match.group(1).casefold()
        kind = "function" if keyword in {"функция", "function"} else "procedure"
        end_re = _UNIT_END_RE[kind]
        end_index: int | None = None
        for candidate in range(index + 1, len(lines)):
            if end_re.match(lines[candidate]):
                end_index = candidate
                break
            if _UNIT_START_RE.match(lines[candidate]):
                break
        if end_index is None:
            index += 1
            continue
        units.append(
            {
                "kind": kind,
                "name": match.group(2),
                "line": index + 1,
            }
        )
        index = end_index + 1
    return units


def _unit_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return str(item["kind"]), str(item["name"]).casefold()


def _strip_bsl_comment(line: str) -> str:
    in_string = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if in_string and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        if not in_string and char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            return line[:index]
        index += 1
    return line


def _normalize_bsl_code(line: str) -> str:
    code = _strip_bsl_comment(line).strip()
    if not code:
        return ""

    tokens: list[str] = []
    index = 0
    while index < len(code):
        if code[index] == '"':
            start = index
            index += 1
            while index < len(code):
                if code[index] != '"':
                    index += 1
                    continue
                if index + 1 < len(code) and code[index + 1] == '"':
                    index += 2
                    continue
                index += 1
                break
            tokens.append(code[start:index])
            continue

        next_quote = code.find('"', index)
        end = len(code) if next_quote < 0 else next_quote
        for token in _OUTSIDE_STRING_TOKEN_RE.findall(code[index:end]):
            if token[0].isalpha() or token[0] == "_":
                tokens.append(token.casefold())
            else:
                tokens.append(token)
        index = end

    return " ".join(tokens)


def _marker_lines(text: str) -> list[JsonObject]:
    result: list[JsonObject] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _MARKER_RE.match(line):
            continue
        normalized = " ".join(line.strip().split())
        result.append(
            {
                "line": line_number,
                "text": line.strip(),
                "normalized": normalized,
                "hash": hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest(),
            }
        )
    return result


def _executable_lines(text: str) -> list[JsonObject]:
    result: list[JsonObject] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if (
            not stripped
            or stripped.startswith("//")
            or stripped.startswith("#")
            or stripped.startswith("&")
            or _NON_EXECUTABLE_RE.match(line)
        ):
            continue
        normalized = _normalize_bsl_code(line)
        if not normalized:
            continue
        result.append(
            {
                "line": line_number,
                "normalized": normalized,
                "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            }
        )
    return result


def _removed_records(
    baseline: list[JsonObject],
    candidate: list[JsonObject],
    *,
    key,
) -> list[JsonObject]:
    available = Counter(key(item) for item in candidate)
    removed: list[JsonObject] = []
    for item in baseline:
        item_key = key(item)
        if available[item_key]:
            available[item_key] -= 1
        else:
            removed.append(item)
    return removed


def build_structural_diff(
    baseline_root: str | Path,
    candidate_root: str | Path,
    objects: Iterable[str],
) -> JsonObject:
    """Build a deterministic, JSON-compatible removal manifest."""

    baseline_path = Path(baseline_root)
    candidate_path = Path(candidate_root)
    canonical_objects = normalize_objects(objects)
    baseline_files = collect_object_files(baseline_path, canonical_objects)
    candidate_files = collect_object_files(candidate_path, canonical_objects)
    baseline_set = set(baseline_files)
    candidate_set = set(candidate_files)

    removed_form_elements: list[JsonObject] = []
    removed_units: list[JsonObject] = []
    removed_markers: list[JsonObject] = []
    removed_executable: list[JsonObject] = []
    errors: list[JsonObject] = []

    for rel in sorted(baseline_set):
        baseline_file = baseline_path / rel.replace("/", os.sep)
        candidate_file = candidate_path / rel.replace("/", os.sep)
        candidate_exists = candidate_file.is_file()

        if rel.casefold().endswith("/form.xml"):
            try:
                before_elements = _form_elements(_read_text(baseline_file))
            except (OSError, ET.ParseError) as exc:
                errors.append({"side": "baseline", "path": rel, "error": str(exc)})
                before_elements = []
            if candidate_exists:
                try:
                    after_elements = _form_elements(_read_text(candidate_file))
                except (OSError, ET.ParseError) as exc:
                    errors.append({"side": "candidate", "path": rel, "error": str(exc)})
                    after_elements = []
            else:
                after_elements = []
            for item in _removed_records(before_elements, after_elements, key=_form_key):
                removed_form_elements.append({"path": rel, **item})

        if rel.casefold().endswith(".bsl"):
            before_text = _read_text(baseline_file)
            after_text = _read_text(candidate_file) if candidate_exists else ""

            for item in _removed_records(
                _bsl_units(before_text),
                _bsl_units(after_text),
                key=_unit_key,
            ):
                removed_units.append({"path": rel, **item})
            for item in _removed_records(
                _marker_lines(before_text),
                _marker_lines(after_text),
                key=lambda value: value["hash"],
            ):
                removed_markers.append({"path": rel, **item})
            for item in _removed_records(
                _executable_lines(before_text),
                _executable_lines(after_text),
                key=lambda value: value["hash"],
            ):
                removed_executable.append({"path": rel, **item})

    changed_files: list[JsonObject] = []
    for rel in sorted(baseline_set & candidate_set):
        before_hash = _sha256_file(baseline_path / rel.replace("/", os.sep))
        after_hash = _sha256_file(candidate_path / rel.replace("/", os.sep))
        if before_hash != after_hash:
            changed_files.append(
                {
                    "path": rel,
                    "baselineSha256": before_hash,
                    "candidateSha256": after_hash,
                }
            )

    removed_form_elements.sort(
        key=lambda value: (
            value["path"],
            str(value["tag"]).casefold(),
            str(value.get("name") or ""),
            str(value.get("id") or ""),
            str(value.get("dataPath") or ""),
            str(value.get("action") or "").casefold(),
            str(value.get("title") or "").casefold(),
            str(value.get("callType") or "").casefold(),
            str(value.get("eventHandler") or "").casefold(),
        )
    )
    removed_units.sort(
        key=lambda value: (
            value["path"],
            value["kind"],
            str(value["name"]).casefold(),
            value["line"],
        )
    )
    removed_markers.sort(key=lambda value: (value["path"], value["line"], value["hash"]))
    removed_executable.sort(key=lambda value: (value["path"], value["line"], value["hash"]))
    errors.sort(key=lambda value: (value["path"], value["side"], value["error"]))

    removed_files = sorted(baseline_set - candidate_set)
    added_files = sorted(candidate_set - baseline_set)
    has_removals = bool(
        removed_files
        or removed_form_elements
        or removed_units
        or removed_markers
        or removed_executable
    )
    return {
        "schemaVersion": 1,
        "objects": canonical_objects,
        "files": {
            "baseline": baseline_files,
            "candidate": candidate_files,
            "removed": removed_files,
            "added": added_files,
            "changed": changed_files,
        },
        "removedFormElements": removed_form_elements,
        "removedBslUnits": removed_units,
        "removedMarkers": removed_markers,
        "removedExecutableLines": removed_executable,
        "errors": errors,
        "hasRemovals": has_removals,
        "ok": not has_removals and not errors,
    }


def structural_diff(
    baseline_root: str | Path,
    candidate_root: str | Path,
    objects: Iterable[str],
) -> JsonObject:
    """Alias with a concise name for :func:`build_structural_diff`."""

    return build_structural_diff(baseline_root, candidate_root, objects)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"manifest is not JSON-compatible: {exc}") from exc
    return text.encode("utf-8")


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise IntegrityError("invalid base64url token component")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise IntegrityError("invalid base64url token component") from exc
    if _b64_encode(decoded) != value:
        raise IntegrityError("non-canonical base64url token component")
    return decoded


def create_manifest_token(manifest: Mapping[str, Any]) -> str:
    """Return a compact HMAC-SHA256 token containing the manifest."""

    payload = _canonical_json(manifest)
    signature = hmac.new(staging_secret(), payload, hashlib.sha256).digest()
    return f"v1.{_b64_encode(payload)}.{_b64_encode(signature)}"


def verify_manifest_token(
    token: str,
    expected_manifest: Mapping[str, Any] | None = None,
) -> JsonObject:
    """Verify and decode a manifest token, raising on every mismatch."""

    if not isinstance(token, str):
        raise IntegrityError("manifest token must be a string")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise IntegrityError("unsupported manifest token")
    payload = _b64_decode(parts[1])
    supplied_signature = _b64_decode(parts[2])
    expected_signature = hmac.new(staging_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise IntegrityError("manifest token signature is invalid")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("manifest token payload is invalid") from exc
    if not isinstance(decoded, dict):
        raise IntegrityError("manifest token payload must be a JSON object")
    if expected_manifest is not None and _canonical_json(decoded) != _canonical_json(expected_manifest):
        raise IntegrityError("manifest token does not match the expected manifest")
    return decoded


def create_manifest_confirmation_token(manifest: Mapping[str, Any]) -> str:
    """Return a short HMAC token bound to the exact expected manifest."""

    digest = hashlib.sha256(_canonical_json(manifest)).digest()
    signature = hmac.new(
        staging_secret(),
        b"onec-mcp-manifest-confirmation-v1\x00" + digest,
        hashlib.sha256,
    ).digest()
    return f"c1.{_b64_encode(digest)}.{_b64_encode(signature)}"


def verify_manifest_confirmation_token(
    token: str,
    expected_manifest: Mapping[str, Any],
) -> None:
    """Verify a short token against a freshly calculated manifest."""

    if not isinstance(token, str):
        raise IntegrityError("manifest confirmation token must be a string")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "c1":
        raise IntegrityError("unsupported manifest confirmation token")
    supplied_digest = _b64_decode(parts[1])
    supplied_signature = _b64_decode(parts[2])
    expected_digest = hashlib.sha256(_canonical_json(expected_manifest)).digest()
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise IntegrityError("manifest confirmation token does not match the expected manifest")
    expected_signature = hmac.new(
        staging_secret(),
        b"onec-mcp-manifest-confirmation-v1\x00" + expected_digest,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise IntegrityError("manifest confirmation token signature is invalid")


def sign_manifest(manifest: Mapping[str, Any]) -> str:
    """Compatibility name for creating a signed manifest token."""

    return create_manifest_token(manifest)


def verify_manifest(manifest: Mapping[str, Any], token: str) -> bool:
    """Return whether *token* authentically represents *manifest*."""

    try:
        verify_manifest_token(token, manifest)
    except IntegrityError:
        return False
    return True


def _normalize_context(
    objects: Iterable[str],
    *,
    task: str,
    target: str,
    extension: str | None,
) -> tuple[list[str], str, str, str | None]:
    canonical_objects = normalize_objects(objects)
    task_value = str(task or "").strip()
    if not task_value:
        raise IntegrityError("task must not be empty")
    target_value = str(target or "").strip().lower()
    if not target_value:
        raise IntegrityError("target must not be empty")
    extension_value = None if extension is None else str(extension).strip() or None
    return canonical_objects, task_value, target_value, extension_value


def _safe_context_key(target: str, extension: str | None) -> str:
    raw = f"{target}__{extension or '_main'}"
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in raw)[:120]


def _receipt_dir() -> Path:
    path = _gates_root() / "work_integrity"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _identity_digest(
    kind: str,
    objects: list[str],
    task: str,
    target: str,
    extension: str | None,
) -> str:
    identity = {
        "kind": kind,
        "objects": objects,
        "task": task,
        "target": target,
        "extension": extension,
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]


def _receipt_path(
    kind: str,
    objects: list[str],
    task: str,
    target: str,
    extension: str | None,
) -> Path:
    digest = _identity_digest(kind, objects, task, target, extension)
    return _receipt_dir() / f"{kind}_{_safe_context_key(target, extension)}_{digest}.json"


def _pending_path(objects: list[str], target: str, extension: str | None) -> Path:
    identity = {
        "kind": "pending_stash",
        "objects": objects,
        "target": target,
        "extension": extension,
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]
    return _receipt_dir() / (
        f"pending_stash_{_safe_context_key(target, extension)}_{digest}.json"
    )


def _timestamp(value: float | None) -> float:
    result = time.time() if value is None else float(value)
    if not math.isfinite(result) or result <= 0:
        raise IntegrityError("receipt timestamp must be a positive finite number")
    return result


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _signed_receipt(payload: Mapping[str, Any]) -> JsonObject:
    result = dict(payload)
    result["token"] = create_manifest_token(payload)
    return result


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> Path:
    _atomic_write_json(path, _signed_receipt(payload))
    return path


def _read_signed_path(path: Path) -> tuple[JsonObject | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(stored, dict):
        return None, "receipt is not a JSON object"
    token = stored.pop("token", None)
    if not isinstance(token, str):
        return None, "receipt token is missing"
    try:
        verify_manifest_token(token, stored)
    except IntegrityError as exc:
        return None, str(exc)
    return stored, None


def _base_receipt(
    kind: str,
    objects: list[str],
    task: str,
    target: str,
    extension: str | None,
    *,
    ts: float | None,
) -> JsonObject:
    return {
        "kind": kind,
        "objects": objects,
        "task": task,
        "target": target,
        "extension": extension,
        "ts": _timestamp(ts),
    }


def write_lock_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    ts: float | None = None,
) -> Path:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    payload = _base_receipt(
        "lock",
        canonical,
        task_value,
        target_value,
        extension_value,
        ts=ts,
    )
    path = _receipt_path("lock", canonical, task_value, target_value, extension_value)
    payload["receiptId"] = path.name
    return _write_receipt(path, payload)


def _normalize_hashes(hashes: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_path, raw_digest in hashes.items():
        rel = _validate_relative_path(str(raw_path))
        digest = str(raw_digest).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise IntegrityError(f"invalid SHA256 for {rel}")
        if rel in result and result[rel] != digest:
            raise IntegrityError(f"conflicting hashes for {rel}")
        result[rel] = digest
    return {path: result[path] for path in sorted(result)}


def write_dump_receipt(
    objects: Iterable[str],
    *,
    task: str,
    source_dir: str | Path,
    immutable_baseline_dir: str | Path,
    baseline_hashes: Mapping[str, str] | None = None,
    target: str = "work",
    extension: str | None = None,
    ts: float | None = None,
) -> Path:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    source_path = Path(source_dir).resolve()
    baseline_path = Path(immutable_baseline_dir).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"dump source directory does not exist: {source_path}")
    if not baseline_path.is_dir():
        raise FileNotFoundError(f"immutable baseline directory does not exist: {baseline_path}")

    actual_baseline_hashes = hash_object_files(baseline_path, canonical)
    if not actual_baseline_hashes:
        raise IntegrityError("immutable baseline contains no requested object files")
    hashes = (
        actual_baseline_hashes
        if baseline_hashes is None
        else _normalize_hashes(baseline_hashes)
    )
    if hashes != actual_baseline_hashes:
        raise IntegrityError("baselineHashes do not match immutableBaselineDir")

    payload = _base_receipt(
        "dump",
        canonical,
        task_value,
        target_value,
        extension_value,
        ts=ts,
    )
    payload.update(
        {
            "sourceDir": str(source_path),
            "immutableBaselineDir": str(baseline_path),
            "baselineHashes": hashes,
        }
    )
    path = _receipt_path("dump", canonical, task_value, target_value, extension_value)
    payload["receiptId"] = path.name
    return _write_receipt(path, payload)


def write_load_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    ts: float | None = None,
    verified_hashes: Mapping[str, str] | None = None,
    post_load_snapshot_dir: str | Path | None = None,
) -> Path:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    payload = _base_receipt(
        "load",
        canonical,
        task_value,
        target_value,
        extension_value,
        ts=ts,
    )
    if verified_hashes is not None:
        payload["verifiedHashes"] = _normalize_hashes(verified_hashes)
    if post_load_snapshot_dir is not None:
        snapshot = Path(post_load_snapshot_dir).resolve()
        if not snapshot.is_dir():
            raise FileNotFoundError(
                f"post-load snapshot directory does not exist: {snapshot}"
            )
        payload["postLoadSnapshotDir"] = str(snapshot)
    path = _receipt_path("load", canonical, task_value, target_value, extension_value)
    payload["receiptId"] = path.name
    return _write_receipt(path, payload)


def _read_receipt(
    kind: str,
    objects: Iterable[str],
    *,
    task: str,
    target: str,
    extension: str | None,
) -> JsonObject | None:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    path = _receipt_path(kind, canonical, task_value, target_value, extension_value)
    receipt, error = _read_signed_path(path)
    if error is not None or receipt is None:
        return None
    expected = {
        "kind": kind,
        "objects": canonical,
        "task": task_value,
        "target": target_value,
        "extension": extension_value,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return None
    return receipt


def read_lock_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
) -> JsonObject | None:
    return _read_receipt(
        "lock",
        objects,
        task=task,
        target=target,
        extension=extension,
    )


def read_dump_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
) -> JsonObject | None:
    return _read_receipt(
        "dump",
        objects,
        task=task,
        target=target,
        extension=extension,
    )


def read_load_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
) -> JsonObject | None:
    return _read_receipt(
        "load",
        objects,
        task=task,
        target=target,
        extension=extension,
    )


def _receipt_ttl() -> float:
    raw = os.environ.get("MCP_GATE_TTL_SEC", "86400")
    try:
        value = float(raw)
    except ValueError:
        return 86400.0
    return value if math.isfinite(value) and value > 0 else 86400.0


def _error(step: str, message: str, **details: Any) -> JsonObject:
    return {
        "ok": False,
        "step": step,
        "error": message,
        "stop": True,
        **details,
    }


def _check_receipt(
    kind: str,
    objects: Iterable[str],
    *,
    task: str,
    target: str,
    extension: str | None,
    now: float | None,
    max_age_seconds: float | None,
) -> JsonObject | None:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    path = _receipt_path(kind, canonical, task_value, target_value, extension_value)
    receipt, read_error = _read_signed_path(path)
    if read_error is not None or receipt is None:
        return _error(
            f"require_{kind}_receipt",
            f"Exact {kind} receipt is missing or invalid.",
            receiptPath=str(path),
            reason=read_error,
            objects=canonical,
            task=task_value,
        )
    expected = {
        "kind": kind,
        "objects": canonical,
        "task": task_value,
        "target": target_value,
        "extension": extension_value,
    }
    mismatches = {
        key: {"expected": value, "actual": receipt.get(key)}
        for key, value in expected.items()
        if receipt.get(key) != value
    }
    if mismatches:
        return _error(
            f"require_{kind}_receipt",
            f"Exact {kind} receipt context does not match.",
            mismatches=mismatches,
        )
    try:
        timestamp = float(receipt["ts"])
    except (KeyError, TypeError, ValueError):
        return _error(f"require_{kind}_receipt", f"{kind} receipt timestamp is invalid.")
    current_time = time.time() if now is None else float(now)
    ttl = _receipt_ttl() if max_age_seconds is None else float(max_age_seconds)
    if not math.isfinite(timestamp) or timestamp <= 0:
        return _error(f"require_{kind}_receipt", f"{kind} receipt timestamp is invalid.")
    if timestamp > current_time + 300:
        return _error(f"require_{kind}_receipt", f"{kind} receipt timestamp is in the future.")
    if not math.isfinite(ttl) or ttl <= 0 or current_time - timestamp > ttl:
        return _error(
            f"require_{kind}_receipt",
            f"{kind} receipt is stale.",
            receiptTs=timestamp,
            now=current_time,
            maxAgeSeconds=ttl,
        )
    return None


def check_lock_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    now: float | None = None,
    max_age_seconds: float | None = None,
) -> JsonObject | None:
    return _check_receipt(
        "lock",
        objects,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )


def check_dump_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    source_dir: str | Path | None = None,
    immutable_baseline_dir: str | Path | None = None,
    baseline_hashes: Mapping[str, str] | None = None,
    now: float | None = None,
    max_age_seconds: float | None = None,
) -> JsonObject | None:
    canonical = normalize_objects(objects)
    error = _check_receipt(
        "dump",
        canonical,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if error:
        return error
    lock_error = check_lock_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if lock_error:
        return lock_error

    dump_receipt = read_dump_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
    )
    lock_receipt = read_lock_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
    )
    if dump_receipt is None or lock_receipt is None:
        return _error("require_dump_receipt", "Signed dump/lock receipt could not be read.")
    if float(lock_receipt["ts"]) > float(dump_receipt["ts"]):
        return _error(
            "dump_before_lock",
            "Dump receipt predates the exact lock receipt.",
            lockTs=lock_receipt["ts"],
            dumpTs=dump_receipt["ts"],
        )
    try:
        actual_baseline_hashes = hash_object_files(
            dump_receipt["immutableBaselineDir"],
            canonical,
        )
    except (KeyError, IntegrityError, OSError) as exc:
        return _error(
            "require_dump_receipt",
            f"Immutable baseline cannot be verified: {exc}",
        )
    if actual_baseline_hashes != dump_receipt.get("baselineHashes"):
        return _error(
            "immutable_baseline_changed",
            "Immutable locked baseline hashes changed after dump.",
            expectedHashes=dump_receipt.get("baselineHashes"),
            actualHashes=actual_baseline_hashes,
        )

    if source_dir is not None and dump_receipt.get("sourceDir") != str(Path(source_dir).resolve()):
        return _error("require_dump_receipt", "Dump sourceDir does not match.")
    if (
        immutable_baseline_dir is not None
        and dump_receipt.get("immutableBaselineDir")
        != str(Path(immutable_baseline_dir).resolve())
    ):
        return _error("require_dump_receipt", "Dump immutableBaselineDir does not match.")
    if baseline_hashes is not None:
        try:
            expected_hashes = _normalize_hashes(baseline_hashes)
        except IntegrityError as exc:
            return _error("require_dump_receipt", str(exc))
        if dump_receipt.get("baselineHashes") != expected_hashes:
            return _error("require_dump_receipt", "Dump baselineHashes do not match.")
    return None


def check_load_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    now: float | None = None,
    max_age_seconds: float | None = None,
) -> JsonObject | None:
    return _check_receipt(
        "load",
        objects,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )


def check_load_receipt_for_commit(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    now: float | None = None,
    max_age_seconds: float | None = None,
) -> JsonObject | None:
    """Require an exact load receipt and the ordered lock→dump→load chain."""

    canonical = normalize_objects(objects)
    load_error = check_load_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if load_error:
        return load_error
    dump_error = check_dump_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if dump_error:
        return dump_error
    load_receipt = read_load_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
    )
    dump_receipt = read_dump_receipt(
        canonical,
        task=task,
        target=target,
        extension=extension,
    )
    if load_receipt is None or dump_receipt is None:
        return _error("require_load_receipt", "Signed load/dump receipt could not be read.")
    if float(dump_receipt["ts"]) > float(load_receipt["ts"]):
        return _error(
            "load_before_dump",
            "Load receipt predates the exact dump receipt.",
            dumpTs=dump_receipt["ts"],
            loadTs=load_receipt["ts"],
        )
    verified_hashes = load_receipt.get("verifiedHashes")
    snapshot_dir = load_receipt.get("postLoadSnapshotDir")
    if not isinstance(verified_hashes, dict) or not verified_hashes or not snapshot_dir:
        return _error(
            "require_verified_load_receipt",
            "Load receipt has no verified post-load hashes.",
        )
    try:
        snapshot_compare = compare_object_snapshot(
            snapshot_dir,
            canonical,
            verified_hashes,
        )
    except (IntegrityError, OSError) as exc:
        return _error(
            "require_verified_load_receipt",
            f"Post-load snapshot cannot be verified: {exc}",
        )
    if not snapshot_compare.get("ok"):
        return _error(
            "require_verified_load_receipt",
            "Post-load snapshot hashes no longer match the signed load receipt.",
            postLoadCompare=snapshot_compare,
        )
    return None


def _pending_paths(target: str, extension: str | None) -> list[Path]:
    prefix = f"pending_stash_{_safe_context_key(target, extension)}_"
    return sorted(_receipt_dir().glob(f"{prefix}*.json"), key=lambda path: path.name)


def _pending_receipts(
    target: str,
    extension: str | None,
) -> tuple[list[tuple[Path, JsonObject]], list[tuple[Path, str]]]:
    valid: list[tuple[Path, JsonObject]] = []
    invalid: list[tuple[Path, str]] = []
    for path in _pending_paths(target, extension):
        receipt, error = _read_signed_path(path)
        if receipt is None or error is not None:
            invalid.append((path, error or "invalid"))
        else:
            valid.append((path, receipt))
    return valid, invalid


def write_pending_stash_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    stash_dir: str | Path | None = None,
    ts: float | None = None,
) -> Path:
    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    requested = set(canonical)
    valid, invalid = _pending_receipts(target_value, extension_value)
    if invalid:
        raise IntegrityError(f"invalid pending-stash receipt blocks creation: {invalid[0][0]}")
    for path, receipt in valid:
        existing_objects = set(receipt.get("objects") or [])
        if not requested.intersection(existing_objects):
            continue
        raise IntegrityError(
            "overlapping pending stash must be resolved before another dump "
            f"(owner task {receipt.get('task')!r}): {path}"
        )

    payload = _base_receipt(
        "pending_stash",
        canonical,
        task_value,
        target_value,
        extension_value,
        ts=ts,
    )
    if stash_dir is not None:
        payload["stashDir"] = str(Path(stash_dir).resolve())
    path = _pending_path(canonical, target_value, extension_value)
    return _write_receipt(path, payload)


def read_pending_stash_receipt(
    objects: Iterable[str],
    *,
    target: str = "work",
    extension: str | None = None,
) -> JsonObject | None:
    canonical = normalize_objects(objects)
    target_value = str(target or "").strip().lower()
    extension_value = None if extension is None else str(extension).strip() or None
    receipt, error = _read_signed_path(_pending_path(canonical, target_value, extension_value))
    if error is not None or receipt is None:
        return None
    expected = {
        "kind": "pending_stash",
        "objects": canonical,
        "target": target_value,
        "extension": extension_value,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return None
    return receipt


def check_pending_stash_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
) -> JsonObject | None:
    """Block on every overlapping pending stash, without a TTL bypass."""

    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    valid, invalid = _pending_receipts(target_value, extension_value)
    if invalid:
        path, reason = invalid[0]
        return _error(
            "reapply_stash",
            "Invalid pending-stash receipt blocks the WORK pipeline.",
            receiptPath=str(path),
            reason=reason,
        )
    requested = set(canonical)
    for path, receipt in valid:
        existing = set(receipt.get("objects") or [])
        if not requested.intersection(existing):
            continue
        return _error(
            "reapply_stash",
            "Pending stash must be reapplied and cleared before continuing.",
            receiptPath=str(path),
            objects=receipt.get("objects"),
            ownerTask=receipt.get("task"),
            requestedTask=task_value,
            sameTask=receipt.get("task") == task_value,
            exactObjects=receipt.get("objects") == canonical,
        )
    return None


def clear_pending_stash_receipt(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
) -> bool:
    """Clear only a valid receipt owned by the exact task and object set."""

    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    path = _pending_path(canonical, target_value, extension_value)
    receipt, error = _read_signed_path(path)
    if error is not None or receipt is None:
        return False
    expected = {
        "kind": "pending_stash",
        "objects": canonical,
        "task": task_value,
        "target": target_value,
        "extension": extension_value,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def clear_integrity_receipts(
    objects: Iterable[str],
    *,
    task: str,
    target: str = "work",
    extension: str | None = None,
    kinds: Iterable[str] = ("lock", "dump", "load"),
) -> list[str]:
    """Remove selected exact-task receipts after invalidation, unlock or commit."""

    canonical, task_value, target_value, extension_value = _normalize_context(
        objects,
        task=task,
        target=target,
        extension=extension,
    )
    removed: list[str] = []
    selected = tuple(dict.fromkeys(str(kind) for kind in kinds))
    if any(kind not in {"lock", "dump", "load"} for kind in selected):
        raise IntegrityError("receipt kind must be lock, dump or load")
    for kind in selected:
        path = _receipt_path(
            kind,
            canonical,
            task_value,
            target_value,
            extension_value,
        )
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(str(path))
    return removed


def compare_hashes(
    expected_hashes: Mapping[str, str],
    actual_hashes: Mapping[str, str],
) -> JsonObject:
    """Pure, deterministic comparison of two slash-relative hash maps."""

    expected = {str(path).replace("\\", "/"): str(value) for path, value in expected_hashes.items()}
    actual = {str(path).replace("\\", "/"): str(value) for path, value in actual_hashes.items()}
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed = [
        {
            "path": path,
            "expectedSha256": expected[path],
            "actualSha256": actual[path],
        }
        for path in sorted(set(expected) & set(actual))
        if expected[path] != actual[path]
    ]
    return {
        "ok": not missing and not unexpected and not changed,
        "missing": missing,
        "unexpected": unexpected,
        "changed": changed,
    }


def compare_current_snapshot(
    baseline_hashes: Mapping[str, str],
    current_snapshot_hashes: Mapping[str, str],
) -> JsonObject:
    """Compare a current WORK snapshot with the immutable dump baseline."""

    return compare_hashes(baseline_hashes, current_snapshot_hashes)


def compare_post_load_snapshot(
    candidate_hashes: Mapping[str, str],
    post_load_snapshot_hashes: Mapping[str, str],
) -> JsonObject:
    """Compare a post-load dump with the exact candidate that was loaded."""

    return compare_hashes(candidate_hashes, post_load_snapshot_hashes)


def compare_object_snapshot(
    root: str | Path,
    objects: Iterable[str],
    expected_hashes: Mapping[str, str],
) -> JsonObject:
    """Hash an object snapshot and compare it with an expected hash map."""

    return compare_hashes(expected_hashes, hash_object_files(root, objects))


__all__ = [
    "IntegrityError",
    "build_structural_diff",
    "check_dump_receipt",
    "check_load_receipt",
    "check_load_receipt_for_commit",
    "check_lock_receipt",
    "check_pending_stash_receipt",
    "clear_integrity_receipts",
    "clear_pending_stash_receipt",
    "collect_object_files",
    "compare_current_snapshot",
    "compare_hashes",
    "compare_object_snapshot",
    "compare_post_load_snapshot",
    "copy_object_files",
    "create_manifest_confirmation_token",
    "create_manifest_token",
    "hash_object_files",
    "normalize_objects",
    "read_dump_receipt",
    "read_load_receipt",
    "read_lock_receipt",
    "read_pending_stash_receipt",
    "sign_manifest",
    "structural_diff",
    "verify_manifest",
    "verify_manifest_confirmation_token",
    "verify_manifest_token",
    "write_dump_receipt",
    "write_load_receipt",
    "write_lock_receipt",
    "write_pending_stash_receipt",
]
