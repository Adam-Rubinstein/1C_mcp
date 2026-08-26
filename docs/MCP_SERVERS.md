# MCP servers — what each one does

Canonical overview for agents and humans. Operator details: [GUIDE.md](GUIDE.md). Workstation setup: [AGENT_SETUP.md](AGENT_SETUP.md).

Transport: **local stdio** (Cursor on the same PC as the toolkit) or **HTTP SSE + Bearer** (remote host). Cursor project config: copy [mcp.json.example](../mcp.json.example) into `<project>/.cursor/mcp.json` (gitignored secrets).

## Server catalog

| Server | Main tools | Needs IB | Default IB | Typical use |
|--------|------------|----------|------------|-------------|
| `1c-platform` | `platform_status`, `search`, `info`, `getMember`, `getMembers`, `getConstructors` | no | — | BSL types, methods, query/language help from platform install |
| `1c-dump` | `dump_status`, `dump_objects`, `dump_changes` | yes | DEV or WORK via `target` | Partial / incremental dump Designer → files; optional merge into `REPO_CF`/`REPO_CFE` |
| `1c-load` | `load_health`, `load_prepare_work`, `load_objects`, `prepare_new_main_object`, `restore_configuration_ext` | yes | DEV smoke; WORK only on explicit request | Load XML/BSL into IB; WORK hard-gates (storage markers, session) |
| `1c-storage` | `storage_status`, `storage_get`, `storage_lock`, `storage_unlock`, `storage_commit`, `storage_report` | WORK + storage UNC | WORK | Configuration repository get/lock/put |
| `1c-com` | `com_status`, `com_ping`, `com_query`, `com_get`, `com_write`, `com_post`, `com_unpost`, `com_metadata_find` | yes | **WORK** (use `target=dev` for sandbox) | Live data via COM; write/post need `confirm=true` |
| `1c-files` | `files_status`, `files_search`, `files_find_usages`, `files_read` | no | — | Search/read under `REPO_CF` / `REPO_CFE` / `CONFIG_DUMP_DIR` |
| `1c-review` | `review_status`, `review_list_rules`, `review_check` | no | — | Static checklist before handoff |
| `1c-journal` | `journal_status`, `journal_recent` | yes | **DEV only** | Event log read |
| `1c-debug` | `debug_*` (attach, breakpoints, step, eval) | debug server | DEV (`ONEC_DEBUG_DENY_WORK=1`) | Attach to platform `dbgs` / debug HTTP |
| `1c-bsl` | `bsl_status`, `bsl_launch_help` | no | — | Open/platform BSL help helpers |

## Phrase → tool map (agents)

| User says | Do |
|-----------|-----|
| «актуальное из Конфигуратора», «из конфигуратора» | `dump_objects(target=work, merge_into_repo=true, task=…)` — **not** DEV |
| «залей в песочницу / в DEV» | `load_objects(target=dev, confirm=true)` |
| «залей в рабочую / WORK» | WORK pipeline below — only on explicit request |
| BSL API / signature | `1c-platform` `search` → `info` / `getMember` |
| Find code on disk | `1c-files` |
| Query live data | `1c-com` (`target=dev` preferred for smoke) |

## WORK pipeline (storage + load)

Hard order (MCP refuses skips):

```
storage_get          # skip if already captured → refuse_get_captured → dump WORK instead
→ dump / patch in repo
→ storage_lock       # lock receipt
→ load_objects(target=work, confirm, storage_captured, task=…,
               manage_session, force_close)
→ last success: reopen_designer=true
```

Important refuse / recovery `step` values:

| `step` | Meaning |
|--------|---------|
| `refuse_get_captured` | Object already locked — dump from IB, do not Get |
| `storage_lock_receipt` | Need `storage_lock` before WORK load |
| `require_manage_session` | WORK load must manage Designer session |
| `work_designer_busy` | Wait / force_close; never `taskkill /IM 1cv8.exe` |
| `refuse_parent_object` | Prefer `Document.X.Form.Y`, not whole Document |
| `fix_forms_incomplete` | Form dump missing `Ext/Form.xml` |
| `fix_configuration_ext_incomplete` | Loading Configuration without `Ext/` wipes UI |
| `fix_adopted_uuids` | Extension Adopted uuid ≠ main Attribute uuid |

Session notes (after toolkit `2a0b780`+):

- Match Designer by `/IBName"Title"` as well as `/F` path.
- After `force_close`, clear stale file-IB `.cfl` locks when safe.
- Interactive WORK reopen: `/IBName` + IB user — **do not** re-pass `/ConfigurationRepository*` (double auth fails).
- Never reopen DEV for the user.

## COM exclusive lock

Each `1c-com` tool must open and **close** the V83 session (`session()` + `atexit`). A leaked COM session blocks Designer exclusive lock. Recovery: stop only the `run_server.py com` process — not all Python, not the user’s Designer. See GUIDE § COM.

## Cursor catalog (tool count)

UI «N tools enabled» can drop tools if descriptions are huge. Keep tool descriptions short; bump `MCP_LOAD_REV` / `MCP_STORAGE_REV` / `MCP_COM_REV` in project `mcp.json` after toolkit changes, then Reload Window if needed. Diff **tool names** (Python `list_tools` vs Cursor), not only counts. Details: [AGENT_SETUP.md](AGENT_SETUP.md) § Cursor catalog.

## Local stdio vs remote SSE

| Mode | Client has | Secrets |
|------|------------|---------|
| stdio | `command` → toolkit `.venv` + `run_server.py` | IB/storage in `mcp.json` `env` (local, gitignored) |
| SSE | `url` + `Authorization: Bearer` | IB on server `.env`; client holds token only |

Remote deploy: clone toolkit on host, `MCP_TRANSPORT=sse`, start `scripts/deploy/start_all.ps1` (or NSSM). Optional — only if the team runs a shared MCP host.

## REV bump cheat sheet

After changing tool schemas/parameters on disk:

1. Edit project `.cursor/mcp.json`: bump `MCP_LOAD_REV`, `MCP_STORAGE_REV`, and/or `MCP_COM_REV`.
2. Prefer Reload Window over killing all MCP Python processes.
3. Verify with Cursor tool list = expected business tool names.
