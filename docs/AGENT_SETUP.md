# Agent setup — configure 1C MCP for a workstation

Use this checklist when a user wants MCP servers for 1C development. **Ask only what is missing.** Write secrets to **gitignored** paths (`.local/`, user `.env`, Cursor `mcp.json` on the machine). Never commit passwords, tokens, or IB paths that identify private infrastructure unless the user explicitly wants them in a private repo.

## Checklist questions

1. **Platform install path** — directory like `C:/Program Files/1cv8/8.3.27.1719` (`ONEC_PLATFORM_PATH`, `ONEC_BIN=…/bin/1cv8.exe`).
2. **Infobase** — file (`ONEC_IB`) or server (`ONEC_SERVER` + `ONEC_REF`).
3. **IB user / password** (`ONEC_USER`, `ONEC_PASSWORD`).
4. **Config dump roots** in the project (`REPO_CF`, `REPO_CFE`, `CONFIG_DUMP_DIR`).
5. **Extension name** if used (`ONEC_EXTENSION`).
6. **Where to run MCP** — local stdio on the dev PC, or remote HTTP on a server.
7. **Remote host/URL** and whether Bearer token already exists (`MCP_TOKEN`).
8. **Which packages** — platform, dump, load, com, files, review, journal, debug, bsl.
9. **Java** for platform JAR (`JAVA_BIN`).
10. **Debug** — is `dbgs` / debug HTTP used? (`DEBUG_SERVER_URL`).

## Actions

### A. Local stdio

1. Ensure toolkit is cloned (e.g. `C:/Tools/1C_mcp`).
2. `pip install -r requirements.txt` into a venv.
3. Write `.local/mcp.env` (gitignored) or inject `env` in Cursor `mcp.json`.
4. Patch `.cursor/mcp.json` (or user MCP config) using [mcp.json.example](../mcp.json.example).
5. Run `python scripts/smoke_test.py`.
6. Reload Cursor window; verify tools appear.

### B. Remote SSE

1. On server: clone, venv, `.env` with `MCP_TRANSPORT=sse`, `MCP_TOKEN`, IB paths.
2. Start services (see `scripts/deploy/`).
3. On client: only `url` + `Authorization: Bearer …` in `mcp.json` — **no IB password on the laptop** if COM/dump run only on server.
4. Health: `GET http://host:port/health` (no auth). Tools need Bearer.

### C. Platform JAR

Prefer direct Java entrypoint for full `search` / `info` / `getMember` tools:

```text
java -Dfile.encoding=UTF-8 -jar …/1C_mcp_bsl.jar --platform-path <ONEC_PLATFORM_PATH>
```

For public SSE, put Bearer in front (`scripts/sse_auth_proxy.py` or reverse proxy).

## Output files (agent-written)

| Path | Contents |
|------|----------|
| `<toolkit>/.local/mcp.env` | All secrets |
| `<project>/.cursor/mcp.json` | Server entries (token/url or env) |
| `<toolkit>/.local/mcp.json.fragment` | Optional fragment to merge |

Paths under `.local/` must remain gitignored.

## Verification

- [ ] `smoke_test.py` green
- [ ] Platform `search("Запрос")` returns hits (JAR)
- [ ] `files_status` sees dump roots
- [ ] `dump_status` sees `ONEC_BIN` + IB (live dump only when IB available)
- [ ] `load_health` ok; `load_objects` without `confirm` returns error
- [ ] Storage error path returns `objectsToCapture` (when reproducible)
- [ ] Cursor GetMcpTools for `1c-com` lists `com_get`, `com_write`, `com_post` (bump `MCP_COM_REV`)

## Cursor catalog (tool count mismatch)

UI **Tools & MCPs** «N tools enabled» can be **lower** than Python `list_tools` even when the missing tool is registered on disk.

| Do | Don't |
|----|-------|
| Diff **tool names**: Python `list_tools` vs Cursor `GetMcpTools` | Blame «prepare not pushed» / remote host when stdio is local |
| Keep MCP tool descriptions **short** (1–3 lines); put gate details in README/rules | Put ~900-char gate essays in `load_objects` docstring |
| Bump `MCP_LOAD_REV` in project `mcp.json`, then Reload Window if needed | Kill all Python MCP processes as the first fix |

Incident (2026-07-23): Cursor dropped `load_status` while keeping `load_prepare_work` + `load_objects` → UI showed **2**. Fix in toolkit: short descriptions + rename to `load_health`.

## WORK load / storage hard gates (1286)

MCP **refuses** soft honor-system alone. Chain:

1. `storage_get` → writes **aligned marker**
2. `dump_objects(target=work)` → Form objects must yield `…/Ext/Form.xml` or dump refuses
3. Patch on dump snapshot
4. `storage_lock` → writes **lock receipt**
5. `load_objects(..., confirm=true, storage_aligned=true, storage_captured=true)` — both flags required; markers must match objects/IB

| Refuse | Why |
|--------|-----|
| WORK load without get marker / without lock receipt | Honor-system `storage_captured` alone was abused (1286) |
| `merge_into_repo` from DEV without `confirm_merge_dev` | Stale DEV overwrites live repo |
| Form dump without Form.xml | Incomplete Designer dump |
| com/journal against WORK IB path | Accidental live IB COM |
| `entire_config` storage without `ALLOW_ENTIRE_STORAGE_OPS=1` | Whole-config lock/get |
| Empty `ConfigurationRepositoryP` flag | Some Designers treat empty `/P` as auth fail — omit flag when password empty |

`Document.X.Form.Y` listFile → `Documents/X/Forms/Y.xml` (not under Document.xml). Prefer Form-scoped lock/load; do **not** lock whole Document to load one form.

Optional env: `MCP_STAGING_SECRET`, `MCP_GATE_TTL_SEC`, `ALLOW_ENTIRE_STORAGE_OPS`, `ONEC_DEBUG_DENY_WORK`.

Bump `MCP_LOAD_REV` / `MCP_STORAGE_REV` / `MCP_COM_REV` after toolkit update so Cursor reloads tool schemas.

## Estet example (illustrative only)

Do **not** commit:

```text
ONEC_IB=C:\Users\…\InfoBase2
ONEC_USER=Администратор
ONEC_EXTENSION=Эстет_Доработки
REPO_CF=…/src/cf
REPO_CFE=…/src/cfe
```

Extension name and prefixes are project-specific; read the project profile in the user's repo.
