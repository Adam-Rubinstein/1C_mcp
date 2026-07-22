"""Shared utilities for 1C MCP toolkit packages."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

TYPE_MAP_RU_TO_EN = {
    "Документ": "Document",
    "Справочник": "Catalog",
    "ОбщийМодуль": "CommonModule",
    "РегистрСведений": "InformationRegister",
    "РегистрНакопления": "AccumulationRegister",
    "Обработка": "DataProcessor",
    "Отчет": "Report",
    "Перечисление": "Enum",
    "ПланВидовХарактеристик": "ChartOfCharacteristicTypes",
    "БизнесПроцесс": "BusinessProcess",
    "Задача": "Task",
    "Константа": "Constant",
    "Роль": "Role",
    "Подсистема": "Subsystem",
    "РегистрБухгалтерии": "AccountingRegister",
    "РегистрРасчета": "CalculationRegister",
    "ПланСчетов": "ChartOfAccounts",
    "ПланОбмена": "ExchangePlan",
    "ПланВидовРасчета": "ChartOfCalculationTypes",
    "ХранилищеНастроек": "SettingsStorage",
    "ОбщаяФорма": "CommonForm",
    "ОбщаяКоманда": "CommonCommand",
    "ОбщийМакет": "CommonTemplate",
    "ОбщаяКартинка": "CommonPicture",
    "ОпределяемыйТип": "DefinedType",
    "ФункциональнаяОпция": "FunctionalOption",
    "ПараметрСеанса": "SessionParameter",
    "КритерийОтбора": "FilterCriterion",
    "HTTPСервис": "HTTPService",
    "WebСервис": "WebService",
    "WSСсылка": "WSReference",
    "ЭлементСтиля": "StyleItem",
    "Стиль": "Style",
    "Язык": "Language",
    "Интерфейс": "Interface",
    "XDTOПакет": "XDTOPackage",
    "ВнешнийИсточникДанных": "ExternalDataSource",
}

STORAGE_HINTS = (
    "хранилищ",
    "захват",
    "захвачен",
    "не захвачен",
    "configuration repository",
    "locked by",
    "object is locked",
    "не удалось захватить",
)


def load_env_files(*paths: str | Path) -> None:
    for p in paths:
        path = Path(p)
        if path.is_file():
            load_dotenv(path, override=False)
    # also default .env in cwd
    load_dotenv(override=False)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise ValueError(f"Missing required env: {name}")
    return value


def normalize_object_name(name: str) -> str:
    name = name.strip()
    if not name:
        return name
    if "." not in name:
        return name
    prefix, rest = name.split(".", 1)
    mapped = TYPE_MAP_RU_TO_EN.get(prefix, prefix)
    return f"{mapped}.{rest}"


def redact_cmd(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            out.append("***")
            continue
        if arg in ("/P", "-P") or arg.startswith("/P") and len(arg) > 2:
            if arg in ("/P", "-P"):
                out.append(arg)
                skip_next = True
            else:
                out.append(arg[:2] + "***")
            continue
        if arg.startswith("/P"):
            out.append("/P***")
            continue
        out.append(arg)
    return out


@dataclass
class DesignerResult:
    exit_code: int
    log_path: str
    log_tail: str
    command: list[str]
    dump_dir: str | None = None
    dumped_paths: list[str] = field(default_factory=list)
    objects_to_capture: list[str] = field(default_factory=list)
    storage_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exitCode": self.exit_code,
            "logPath": self.log_path,
            "logTail": self.log_tail,
            "command": self.command,
            "dumpDir": self.dump_dir,
            "dumpedPaths": self.dumped_paths,
            "objectsToCapture": self.objects_to_capture,
            "storageError": self.storage_error,
            "ok": self.exit_code == 0 and not self.storage_error,
        }


def parse_storage_errors(log_text: str, objects: list[str]) -> tuple[bool, list[str]]:
    low = log_text.lower()
    if not any(h in low for h in STORAGE_HINTS):
        return False, []
    # Prefer explicit objects from request; also try to extract names from log lines
    found: list[str] = []
    for obj in objects:
        short = obj.split(".")[-1]
        if obj in log_text or short in log_text:
            found.append(obj)
    if not found:
        found = list(objects)
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for o in found:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return True, uniq


def list_dumped_paths(dump_dir: Path) -> list[str]:
    if not dump_dir.is_dir():
        return []
    paths: list[str] = []
    for p in dump_dir.rglob("*"):
        if p.is_file():
            paths.append(str(p.relative_to(dump_dir)).replace("\\", "/"))
    return sorted(paths)


def build_ib_args() -> list[str]:
    ib = env("ONEC_IB")
    server = env("ONEC_SERVER")
    ref = env("ONEC_REF")
    user = env("ONEC_USER", "")
    password = env("ONEC_PASSWORD", "")
    args: list[str] = []
    if ib:
        args.extend(["/F", ib])
    elif server and ref:
        args.extend(["/S", f"{server}\\{ref}"])
    else:
        raise ValueError("Set ONEC_IB (file) or ONEC_SERVER+ONEC_REF (server)")
    if user:
        args.extend(["/N", user])
    # Always pass /P (may be empty)
    args.extend(["/P", password or ""])
    return args


def run_designer(
    dump_or_load_args: list[str],
    *,
    work_dir: Path | None = None,
    objects: list[str] | None = None,
    timeout_sec: int = 3600,
) -> DesignerResult:
    onec_bin = require_env("ONEC_BIN")
    work = work_dir or Path(tempfile.mkdtemp(prefix="1c-mcp-"))
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "designer.out"
    argv = [
        onec_bin,
        "DESIGNER",
        *build_ib_args(),
        "/DisableStartupDialogs",
        "/Out",
        str(log_path),
        *dump_or_load_args,
    ]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    log_text = ""
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if proc.stdout:
        log_text += "\n" + proc.stdout
    if proc.stderr:
        log_text += "\n" + proc.stderr
    tail = "\n".join(log_text.splitlines()[-80:])
    objs = objects or []
    storage_error, to_capture = parse_storage_errors(log_text, objs)
    # Non-zero exit or storage phrases => not ok
    exit_code = proc.returncode
    if storage_error and exit_code == 0:
        # force attention
        exit_code = 1
    return DesignerResult(
        exit_code=exit_code,
        log_path=str(log_path),
        log_tail=tail,
        command=redact_cmd(argv),
        objects_to_capture=to_capture,
        storage_error=storage_error,
    )


def write_list_file(objects: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [normalize_object_name(o) for o in objects if o.strip()]
    # UTF-8 with BOM helps Designer on Windows with Cyrillic
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def merge_copy(src_dir: Path, dest_dir: Path) -> dict[str, Any]:
    overwrite: list[str] = []
    created: list[str] = []
    for src in src_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        dst = dest_dir / rel
        if dst.exists():
            overwrite.append(str(rel).replace("\\", "/"))
        else:
            created.append(str(rel).replace("\\", "/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    return {"overwrite": overwrite, "created": created}


def json_result(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
