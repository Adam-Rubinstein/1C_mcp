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
    run_designer,
    write_list_file,
)
from onec_mcp_shared.server_run import make_mcp, run_mcp  # noqa: E402

load_env_files(Path(__file__).with_name(".env"), Path.cwd() / ".env")

mcp = make_mcp("1c-load")


@mcp.tool()
def load_status() -> str:
    return json_result(
        {
            "onecBin": env("ONEC_BIN"),
            "onecBinExists": Path(env("ONEC_BIN", "") or ".").is_file(),
            "repoCf": env("REPO_CF"),
            "repoCfe": env("REPO_CFE"),
            "ok": Path(env("ONEC_BIN", "") or ".").is_file()
            and bool(env("ONEC_IB") or (env("ONEC_SERVER") and env("ONEC_REF"))),
        }
    )


@mcp.tool()
def load_objects(
    objects: list[str],
    source_dir: str | None = None,
    extension: str | bool | None = None,
    confirm: bool = False,
) -> str:
    """
    Partial load XML into IB via /LoadConfigFromFiles -listFile.
    confirm=true is required. On storage lock errors returns objectsToCapture list.
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
    write_list_file(canon, list_file)

    args = ["/LoadConfigFromFiles", str(src)]
    if ext_name:
        args.extend(["-Extension", ext_name])
    args.extend(["-listFile", str(list_file), "-Format", "Hierarchical"])

    result = run_designer(args, work_dir=work, objects=canon)
    payload = result.to_dict()
    if result.storage_error or result.objects_to_capture:
        payload["message"] = (
            "Load failed due to configuration storage / object locks. "
            "Capture these objects for editing, then retry load_objects: "
            + ", ".join(result.objects_to_capture or canon)
        )
        payload["objectsToCapture"] = result.objects_to_capture or canon
        payload["ok"] = False
    elif result.exit_code != 0:
        payload["message"] = "Designer failed. See logTail. If metadata structure changed, update DB configuration in Designer."
        payload["ok"] = False
    else:
        payload["ok"] = True
        payload["message"] = "Load finished. If metadata changed, update database configuration in Designer."
    return json_result(payload)


def main() -> None:
    run_mcp(mcp, default_port=8762)


if __name__ == "__main__":
    main()
