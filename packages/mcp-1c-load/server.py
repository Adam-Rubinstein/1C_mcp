from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "shared"))

from onec_mcp_shared import (  # noqa: E402
    env,
    json_result,
    load_env_files,
    normalize_object_name,
    now_stamp,
    resolve_ib,
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402
from onec_mcp_shared.session import with_managed_session  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env", Path(_ROOT).parent / ".env")

mcp = make_mcp("1c-load")


def _is_work_target(target: str) -> bool:
    return (target or "dev").strip().lower() in ("work", "prod", "base3")


def _is_dev_target(target: str) -> bool:
    return (target or "dev").strip().lower() in ("dev", "develop", "sandbox", "base2")


def _canon_objects(objects: list[str]) -> list[str]:
    return [normalize_object_name(o) for o in objects if (o or "").strip()]


def _health_payload(verbose: bool = False) -> dict:
    dev = env("ONEC_IB_DEV") or env("ONEC_IB")
    work = env("ONEC_IB_WORK")
    payload: dict = {
        "onecBin": env("ONEC_BIN"),
        "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
        "ibDev": dev,
        "ibDevExists": Path(dev or ".").is_dir(),
        "ibWork": work,
        "ibWorkExists": Path(work or ".").is_dir() if work else False,
        "repoCf": env("REPO_CF"),
        "repoCfe": env("REPO_CFE"),
        "ok": Path(env("ONEC_BIN", "") or ".").is_file() and Path(dev or ".").is_dir(),
        "storagePathSet": bool((env("ONEC_STORAGE_PATH") or "").strip()),
        "tools": ["load_prepare_work", "load_objects", "load_health"],
        "mcpLoadRev": env("MCP_LOAD_REV") or "3",
        "note": (
            "Default target=dev. WORK needs confirm=true and storage_captured=true. "
            "Use load_prepare_work only if capture not confirmed; if user said do-it — load immediately."
        ),
    }
    if verbose:
        payload["waitForUserPhrases"] = [
            "я захватил",
            "я всё захватил",
            "я все захватил",
            "делай",
            "можно грузить",
            "грузи",
        ]
    return payload


@mcp.tool(name="load_prepare_work")
def load_prepare_work(
    objects: list[str],
    extension: str | bool | None = None,
) -> str:
    """
    Checklist before WORK load when capture is NOT yet confirmed.
    Skip if user already said captured / «делай». Does not run Designer.
    """
    if not objects:
        return json_result({"ok": False, "error": "objects is required"})
    canon = _canon_objects(objects)
    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension
    lines = [
        "Перед загрузкой в WORK захватите в хранилище конфигурации:",
        "",
    ]
    for i, name in enumerate(canon, 1):
        lines.append(f"{i}. {name}")
    if ext_name:
        lines.append("")
        lines.append(f"Расширение: {ext_name}")
    lines.extend(
        [
            "",
            "После захвата напишите: «я захватил» / «делай» / «можно грузить».",
            "Если уже захватили — сразу «делай»: агент грузит без повторного стопа.",
            "До подтверждения захвата агент НЕ вызывает load_objects на WORK.",
        ]
    )
    return json_result(
        {
            "ok": True,
            "step": "capture_then_approve",
            "target": "work",
            "objectsToCapture": canon,
            "extension": ext_name,
            "message": "\n".join(lines),
            "waitForUserPhrases": ["я захватил", "я всё захватил", "я все захватил", "делай", "можно грузить", "грузи"],
            "skipIfAlreadyCaptured": True,
            "nextTool": {
                "name": "load_objects",
                "args": {
                    "objects": canon,
                    "target": "work",
                    "confirm": True,
                    "storage_captured": True,
                    "extension": extension if extension is not None else None,
                },
            },
            "stop": True,
        }
    )


@mcp.tool(name="load_objects")
def load_objects(
    objects: list[str],
    source_dir: str | None = None,
    extension: str | bool | None = None,
    confirm: bool = False,
    storage_captured: bool = False,
    target: str = "dev",
    manage_session: bool = False,
    force_close: bool = False,
    reopen_designer: bool | None = None,
    restart_even_on_fail: bool = True,
) -> str:
    """Partial load into IB. confirm=true required; WORK also needs storage_captured=true."""
    if not confirm:
        return json_result(
            {
                "ok": False,
                "error": "Refusing load without confirm=true.",
                "hint": "For WORK: call load_prepare_work first, wait until user captured objects, then confirm=true and storage_captured=true.",
            }
        )
    if not objects:
        return json_result({"ok": False, "error": "objects is required"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

    t = (target or "dev").strip().lower()
    if _is_work_target(t) and not storage_captured:
        canon = _canon_objects(objects)
        return json_result(
            {
                "ok": False,
                "error": "Refusing load to WORK without storage_captured=true.",
                "step": "capture_then_approve",
                "objectsToCapture": canon,
                "message": (
                    "Сначала захватите в хранилище:\n"
                    + "\n".join(f"- {o}" for o in canon)
                    + "\n\nКогда готово — напишите «я захватил» / «делай». "
                    "Агент вызовет load_prepare_work / load_objects с storage_captured=true."
                ),
                "stop": True,
                "hint": "Call load_prepare_work(objects=...) and stop until user approval.",
            }
        )

    if reopen_designer is None:
        reopen_designer = _is_work_target(t)
    # Never pop DEV Configurator for the user
    if _is_dev_target(t):
        reopen_designer = False

    ext_name = None
    if extension is True:
        ext_name = env("ONEC_EXTENSION")
    elif isinstance(extension, str) and extension:
        ext_name = extension

    src = Path(source_dir or (env("REPO_CFE") if ext_name else env("REPO_CF") or ""))
    if not src.is_dir():
        return json_result({"ok": False, "error": f"source_dir not found: {src}"})

    work = Path(env("DUMP_TMP_ROOT", str(Path.cwd() / ".tmp" / "1c-load"))) / now_stamp()
    work.mkdir(parents=True, exist_ok=True)
    list_file = work / "objects.txt"
    canon = _canon_objects(objects)
    write_list_file(canon, list_file, for_load=True)

    args = ["/LoadConfigFromFiles", str(src)]
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-listFile", str(list_file), "-Format", "Hierarchical"])

    def _do_load():
        return run_designer(args, work_dir=work, objects=canon, target=target)

    session_meta = None
    try:
        if manage_session:
            result, session_meta = with_managed_session(
                ib,
                _do_load,
                force_close=force_close,
                reopen=reopen_designer,
                restart_even_on_fail=restart_even_on_fail,
            )
        else:
            result = _do_load()
    except Exception as exc:  # noqa: BLE001
        return json_result({"ok": False, "error": str(exc), "session": session_meta})

    payload = result.to_dict()
    payload["ib"] = ib
    payload["target"] = target
    if session_meta:
        payload["session"] = session_meta
    if result.storage_error or result.objects_to_capture:
        payload["message"] = (
            "Load failed due to configuration storage / object locks. "
            "Capture these objects, then retry: "
            + ", ".join(result.objects_to_capture or canon)
        )
        payload["objectsToCapture"] = result.objects_to_capture or canon
        payload["ok"] = False
    elif result.exit_code != 0:
        payload["message"] = "Designer failed. See logTail. Update DB configuration in Designer if metadata changed."
        payload["ok"] = False
    else:
        payload["ok"] = True
        payload["message"] = "Load finished. If metadata structure changed, update database configuration in Designer."
    if session_meta and session_meta.get("userAction"):
        payload["userAction"] = session_meta["userAction"]
    if session_meta and session_meta.get("warning"):
        payload["sessionWarning"] = session_meta["warning"]
    return json_result(payload)


@mcp.tool(name="load_health")
def load_health(verbose: bool = False) -> str:
    """Health: ONEC_BIN, DEV/WORK IB, repo paths; lists load tools."""
    return json_result(_health_payload(verbose=verbose))


def main() -> None:
    run_mcp(mcp, default_port=18762)


if __name__ == "__main__":
    main()
