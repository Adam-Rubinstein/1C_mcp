# Operator guide — 1C MCP Toolkit

**Server catalog (what each MCP does):** [MCP_SERVERS.md](MCP_SERVERS.md).  
**Workstation setup:** [AGENT_SETUP.md](AGENT_SETUP.md).  
**Cursor project template:** copy [mcp.json.example](../mcp.json.example); portable rules live in the Project scaffold (`Desktop/Project`).

## Architecture

```
Cursor / Agent
    │  stdio (local) or HTTP SSE + Bearer (remote)
    ▼
┌──────────┐  ┌──────┐  ┌──────┐  ┌─────────┐  ┌─────┐  ┌───────┐
│ platform │  │ dump │  │ load │  │ storage │  │ com │  │ files │ …
└────┬─────┘  └──┬───┘  └──┬───┘  └────┬────┘  └──┬──┘  └───┬───┘
     │           │         │           │          │         │
     ▼           ▼         ▼           ▼          ▼         ▼
  help/HBK    DESIGNER  DESIGNER   repository   COM     XML/BSL on disk
```

Python packages share `onec_mcp_shared` (env, Designer runner, listFile BOM, storage-error parsing, merge-copy, session/IBName matching).

## Environment variables

See [`.env.example`](../.env.example).

| Variable | Used by | Purpose |
|----------|---------|---------|
| `ONEC_BIN` | dump, load | Path to `1cv8.exe` |
| `ONEC_IB` or `ONEC_SERVER`+`ONEC_REF` | dump, load, com, journal | Infobase |
| `ONEC_USER` / `ONEC_PASSWORD` | same | Auth |
| `ONEC_EXTENSION` | dump, load | Extension name for `-Extension` |
| `REPO_CF` / `REPO_CFE` | dump, load, files | Config dump roots in git |
| `CONFIG_DUMP_DIR` | files | Search root (often = `REPO_CF`) |
| `ONEC_PLATFORM_PATH` | platform JAR | Version dir with help |
| `MCP_TRANSPORT` | all Python | `stdio` or `sse` |
| `MCP_TOKEN` | Python SSE | Bearer token |
| `DEBUG_SERVER_URL` | debug | `dbgs` HTTP endpoint |
| `REVIEW_RULES_PATH` | review | Optional YAML override |

## Dump

### Partial objects

Designer argument order (critical):

```
/DumpConfigToFiles <dir> [-Extension Name] -listFile <objects.txt> -Format Hierarchical
```

`objects.txt` is UTF-8 **with BOM**. Names may be Russian (`Документ.X`) or English (`Document.X`); shared code normalizes to English type prefixes.

Tool: `dump_objects(objects=[...], merge_into_repo=false, extension=false)`.

### Incremental

`dump_changes` uses:

```
-update -configDumpInfoForChanges <path/to/ConfigDumpInfo.xml> -Format Hierarchical
```

### Storage / locks

If Designer log contains capture/lock phrases, result has:

```json
{
  "ok": false,
  "storageError": true,
  "objectsToCapture": ["Document.Foo"],
  "message": "… Capture these objects …"
}
```

Agents must show that list and stop — never pretend success.

### Session management (`manage_session`)

- Closes **only** the IB from `target` (strict `/F` path or exact `/IBName` from `ibases.v8i`).
- Also matches cmdline forms like `/IBName"Title"` (thin client / Designer variants).
- After `force_close`, clears **stale** file-IB `.cfl` lock files when the IB process is gone.
- WORK `load_objects`: MCP requires / auto-enables `manage_session` + `force_close` (`require_manage_session` if missing).
- `reopen_designer`: default **False between** get/dump/lock/load; on the **last** successful WORK step after the agent closed Designer → **True**. Work reopen: `DESIGNER /IBName` + IB title (two argv) + IB user — not bare `/F`, not `/AppAutoCheckMode`, not one argv `/IBName"…"`. Do **not** re-pass `/ConfigurationRepository*` on interactive reopen (double auth → fail).
- Optional explicit storage CLI: set `ONEC_STORAGE_PATH` / `ONEC_STORAGE_USER` / `ONEC_STORAGE_PASSWORD`.
- Never default-open Designer when no session was open on that IB; never reopen DEV for the user.
- **Adopted UUID gate:** before prepare/load, `ExtendedConfigurationObject` in `REPO_CFE` must equal Attribute `uuid` in `REPO_CF` for the same attribute name. On mismatch → `ok=false`, step `fix_adopted_uuids`. New Adopted attrs require loading the **main** CF object, not only the extension.
- **Configuration root gate:** loading `Configuration` / `Configuration.xml` from git is refused. Loading Configuration **without** required `Ext/*.xml` is refused (wipes UI — 5318). Use `prepare_new_main_object` (new object) or `restore_configuration_ext` (Ext wipe recovery): dump root from target IB, Ext from IB dump/donor, stage with marker, then load.

## Load

```
/LoadConfigFromFiles <src> [-Extension Name] -listFile <objects.txt> -Format Hierarchical
```

- `confirm=true` required.
- Same `objectsToCapture` behavior on lock.
- After metadata change, user must update DB configuration in Designer (tool message reminds).

## COM

Windows. Connect via **comtypes** + `IV8COMConnector3` (win32com `Connect` is broken on 8.3.27 — `TYPE_E_LIBNOTREGISTERED`). Default IB is **WORK** (`ONEC_IB_WORK` + `ONEC_USER_WORK`). `target=dev` for the sandbox. Tools: `com_ping`, `com_query`, `com_get`, `com_write`, `com_post`, `com_unpost`, `com_metadata_find`. Write/post require `confirm=true`. Each tool **closes** the COM session (otherwise Configurator exclusive lock fails). Journal COM stays DEV-only.

## Files

Searches `CONFIG_DUMP_DIR`, `REPO_CF`, `REPO_CFE`. Paths outside those roots are rejected by `files_read`.

## Review

Default rules: `packages/mcp-1c-review/rules/default.yaml`. Project-specific rules: set `REVIEW_RULES_PATH` (do not commit secrets).

## Journal

COM unload of event log to a temp XML; best-effort parse. Needs privileges.

## Debug

HTTP client toward `DEBUG_SERVER_URL` (platform `dbgs` / community debug MCP shapes). Live attach needs a running debug server — see [PavRedAlex/1c-debug-mcp](https://github.com/PavRedAlex/1c-debug-mcp).

## Deploy on Windows host (optional remote SSE)

Only if the team runs a shared MCP host (not required for local stdio).

1. Clone repo to e.g. `C:\Tools\1C_mcp`.
2. `python -m venv .venv` && `pip install -r requirements.txt`.
3. Create `C:\Tools\1C_mcp\.env` from `.env.example` (IB paths, `MCP_TOKEN`).
4. Run `scripts\deploy\install_services.ps1` **or** `scripts\deploy\start_all.ps1` **or** start each server in a scheduled task / NSSM:

```powershell
$env:MCP_TRANSPORT = "sse"
$env:MCP_PORT = "8761"
# load .env somehow, or use dotenv in process
.\.venv\Scripts\python.exe scripts\run_server.py dump
```

5. Open firewall for ports you expose (or bind to VPN / private IP only).
6. Point Cursor `url` + `Authorization: Bearer …` — token only on client PC and server disk.
7. After git push of toolkit fixes: on the host `git pull --ff-only`, restart SSE processes, confirm `git rev-parse HEAD` matches.

## Smoke tests (no IB)

```bash
python scripts/smoke_test.py
```

Live dump/load/com require IB (configure later).

## Platform JAR rebuild (optional)

```bash
cd legacy/kotlin-platform
./gradlew build
# copy jar to packages/mcp-1c-platform/runtime/
```
