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
    "не захвачен",
    "захвачен другим",
    "не удалось захватить",
    "object is locked",
    "locked by",
    "требуется захват",
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
    secret_flags = {"/P", "-P", "/ConfigurationRepositoryP"}
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            out.append("***")
            continue
        if arg in secret_flags:
            out.append(arg)
            skip_next = True
            continue
        if arg.startswith("/P") and len(arg) > 2:
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


def resolve_ib(target: str = "dev") -> str:
    """Resolve file IB path for target: dev (sandbox) or work (with storage)."""
    t = (target or "dev").strip().lower()
    if t in ("dev", "develop", "sandbox", "base2"):
        path = env("ONEC_IB_DEV") or env("ONEC_IB")
        label = "ONEC_IB_DEV / ONEC_IB"
    elif t in ("work", "prod", "base3"):
        path = env("ONEC_IB_WORK")
        label = "ONEC_IB_WORK"
    else:
        raise ValueError(f"Unknown IB target: {target!r} (use 'dev' or 'work')")
    if not path:
        raise ValueError(f"Set {label} for target={t}")
    return path


def resolve_ib_auth(target: str = "dev") -> tuple[str, str]:
    """
    Per-IB credentials.

    WORK: ONEC_USER_WORK / ONEC_PASSWORD_WORK (fallback ONEC_USER / ONEC_PASSWORD)
    DEV:  ONEC_USER_DEV / ONEC_PASSWORD_DEV (fallback ONEC_USER / ONEC_PASSWORD)
    """
    t = (target or "dev").strip().lower()
    if t in ("work", "prod", "base3"):
        user = env("ONEC_USER_WORK") or env("ONEC_USER", "") or ""
        password = env("ONEC_PASSWORD_WORK")
        if password is None:
            password = env("ONEC_PASSWORD", "") or ""
    else:
        user = env("ONEC_USER_DEV") or env("ONEC_USER", "") or ""
        password = env("ONEC_PASSWORD_DEV")
        if password is None:
            password = env("ONEC_PASSWORD", "") or ""
    return user, password or ""


def auth_for_ib_path(ib_path: str | Path) -> tuple[str, str]:
    """Pick WORK vs DEV credentials by comparing resolved IB paths."""
    needle = str(Path(ib_path).resolve()).lower().replace("/", "\\")
    work = env("ONEC_IB_WORK") or ""
    if work and str(Path(work).resolve()).lower().replace("/", "\\") == needle:
        return resolve_ib_auth("work")
    return resolve_ib_auth("dev")


def build_ib_args(target: str = "dev") -> list[str]:
    """Build /F or /S auth args. File IB prefers ONEC_IB_DEV/WORK; server uses ONEC_SERVER+ONEC_REF."""
    user, password = resolve_ib_auth(target)
    args: list[str] = []
    # Prefer explicit file targets
    try:
        ib = resolve_ib(target)
        args.extend(["/F", ib])
    except ValueError:
        server = env("ONEC_SERVER")
        ref = env("ONEC_REF")
        if server and ref:
            args.extend(["/S", f"{server}\\{ref}"])
        else:
            raise
    if user:
        args.extend(["/N", user])
    args.extend(["/P", password])
    return args


def run_designer(
    dump_or_load_args: list[str],
    *,
    work_dir: Path | None = None,
    objects: list[str] | None = None,
    timeout_sec: int = 3600,
    target: str = "dev",
) -> DesignerResult:
    onec_bin = require_env("ONEC_BIN")
    work = work_dir or Path(tempfile.mkdtemp(prefix="1c-mcp-"))
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "designer.out"
    argv = [
        onec_bin,
        "DESIGNER",
        *build_ib_args(target=target),
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


TYPE_TO_FOLDER = {
    "Document": "Documents",
    "Catalog": "Catalogs",
    "CommonModule": "CommonModules",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "DataProcessor": "DataProcessors",
    "Report": "Reports",
    "Enum": "Enums",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks",
    "Constant": "Constants",
    "Role": "Roles",
    "Subsystem": "Subsystems",
    "AccountingRegister": "AccountingRegisters",
    "CalculationRegister": "CalculationRegisters",
    "ChartOfAccounts": "ChartsOfAccounts",
    "ExchangePlan": "ExchangePlans",
    "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "CommonForm": "CommonForms",
    "CommonCommand": "CommonCommands",
    "CommonTemplate": "CommonTemplates",
    "CommonPicture": "CommonPictures",
    "DefinedType": "DefinedTypes",
    "FunctionalOption": "FunctionalOptions",
    "SessionParameter": "SessionParameters",
    "FilterCriterion": "FilterCriteria",
    "HTTPService": "HTTPServices",
    "WebService": "WebServices",
    "WSReference": "WSReferences",
    "XDTOPackage": "XDTOPackages",
}


def object_to_list_entry(name: str, *, for_load: bool = False) -> str:
    """Metadata name for dump listFile; hierarchical relative path for load listFile."""
    canon = normalize_object_name(name)
    low = canon.lower().replace("\\", "/")
    if for_load and low in ("configuration", "конфигурация", "configuration.xml"):
        return "Configuration.xml"
    if not for_load or "." not in canon:
        return canon
    kind, obj = canon.split(".", 1)
    folder = TYPE_TO_FOLDER.get(kind, kind + "s")
    return f"{folder}/{obj}.xml"


def write_list_file(objects: list[str], path: Path, *, for_load: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [object_to_list_entry(o, for_load=for_load) for o in objects if o.strip()]
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
