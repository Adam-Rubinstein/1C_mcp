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


@mcp.tool()
def load_status() -> str:
    dev = env("ONEC_IB_DEV") or env("ONEC_IB")
    work = env("ONEC_IB_WORK")
    return json_result(
        {
            "onecBin": env("ONEC_BIN"),
            "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
            "ibDev": dev,
            "ibDevExists": Path(dev or ".").is_dir(),
            "ibWork": work,
            "ibWorkExists": Path(work or ".").is_dir() if work else False,
            "repoCf": env("REPO_CF"),
            "repoCfe": env("REPO_CFE"),
            "ok": Path(env("ONEC_BIN", "") or ".").is_file() and Path(dev or ".").is_dir(),
            "note": "Default target=dev. Use target=work only when ready; manage_session closes/reopens 1C.",
        }
    )


@mcp.tool()
def load_objects(
    objects: list[str],
    source_dir: str | None = None,
    extension: str | bool | None = None,
    confirm: bool = False,
    target: str = "dev",
    manage_session: bool = False,
    force_close: bool = False,
    restart_even_on_fail: bool = True,
) -> str:
    """
    Partial load XML into IB via /LoadConfigFromFiles -listFile.
    confirm=true required.
    target: 'dev' (InfoBase2, default) or 'work' (InfoBase3).
    manage_session: close 1C on that IB, load, restart with /N /P.
    On storage lock returns objectsToCapture.
    """
    if not confirm:
        return json_result(
            {
                "ok": False,
                "error": "Refusing load without confirm=true. Capture objects in configuration repository first if used.",
            }
        )
    if not objects:
        return json_result({"ok": False, "error": "objects is required"})

    try:
        ib = resolve_ib(target)
    except ValueError as exc:
        return json_result({"ok": False, "error": str(exc)})

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
    canon = [normalize_object_name(o) for o in objects]
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
    return json_result(payload)


def main() -> None:
    run_mcp(mcp, default_port=18762)


if __name__ == "__main__":
    main()
