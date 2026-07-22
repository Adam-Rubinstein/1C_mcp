# 1C MCP Toolkit

Public monorepo of MCP servers for 1C:Enterprise development: platform API help, partial Designer dump/load, COM queries, dump-file search, review checklist, event journal, and HTTP debug.

**Repo:** [Adam-Rubinstein/1C_mcp](https://github.com/Adam-Rubinstein/1C_mcp) (formerly `1C_mcp_bsl`).

## Packages

| Package | Port (SSE default) | Tools (summary) |
|---------|--------------------|-----------------|
| `mcp-1c-platform` | 18760 | Platform API via legacy JAR (`search`, `info`, `getMember`, …) |
| `mcp-1c-dump` | 18761 | `dump_objects`, `dump_changes`, `dump_status` |
| `mcp-1c-load` | 18762 | `load_objects` (requires `confirm=true`; storage lock → `objectsToCapture`) |
| `mcp-1c-com` | 18763 | `com_query`, `com_metadata_find`, `com_ping` |
| `mcp-1c-files` | 18764 | `files_search`, `files_find_usages`, `files_read` |
| `mcp-1c-review` | 18765 | YAML checklist `review_check` |
| `mcp-1c-journal` | 18766 | Event log via COM |
| `mcp-1c-debug` | 18767 | HTTP debug (`dbgs` protocol) |
| `mcp-1c-bsl` | 18768 | BSL Language Server wiring |

Default deploy ports use **1876x** (avoids clashes with other tools on 876x).

Shared code: `packages/shared/onec_mcp_shared/`.

Legacy Kotlin sources: `legacy/kotlin-platform/` (build produces the platform JAR). Prebuilt JAR: `packages/mcp-1c-platform/runtime/1C_mcp_bsl.jar`.

## Quick start (local stdio)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
pip install pytest
copy .env.example .env          # edit paths
python scripts/smoke_test.py
```

Cursor `mcp.json` (stdio example for dump):

```json
{
  "mcpServers": {
    "1c-dump": {
      "command": "python",
      "args": ["C:/Tools/1C_mcp/scripts/run_server.py", "dump"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "ONEC_BIN": "C:/Program Files/1cv8/8.3.27.1719/bin/1cv8.exe",
        "ONEC_IB": "C:/path/to/InfoBase",
        "ONEC_USER": "Администратор",
        "ONEC_PASSWORD": "",
        "REPO_CF": "C:/path/to/repo/src/cf"
      }
    },
    "1c-platform": {
      "command": "java",
      "args": [
        "-Dfile.encoding=UTF-8",
        "-jar",
        "C:/Tools/1C_mcp/packages/mcp-1c-platform/runtime/1C_mcp_bsl.jar",
        "--platform-path",
        "C:/Program Files/1cv8/8.3.27.1719"
      ]
    }
  }
}
```

## Remote HTTP (adam / any host)

```bash
set MCP_TRANSPORT=sse
set MCP_HOST=0.0.0.0
set MCP_PORT=8761
set MCP_TOKEN=<long-random>
python scripts/run_server.py dump
```

Cursor:

```json
{
  "mcpServers": {
    "1c-dump": {
      "url": "http://YOUR_HOST:8761/sse",
      "headers": {
        "Authorization": "Bearer <same-token>"
      }
    }
  }
}
```

Secrets stay on the server (`.env`) and in local Cursor config — **never in git**.

## Platform JAR

Full tools (`search`, `info`, `getMember`, `getMembers`, `getConstructors`) are implemented in the legacy JAR. Use `java -jar …` for stdio, or `--mode sse --port 8760` for HTTP. Put a reverse proxy with Bearer in front for public exposure (`scripts/sse_auth_proxy.py` or Caddy/nginx).

## Safety

- **No full ERP dump into repo** without an explicit partial object list (or incremental `dump_changes` against `ConfigDumpInfo.xml`).
- **`load_objects` requires `confirm=true`.**
- On configuration storage / lock errors, tools return **`objectsToCapture`** — never a silent success.
- Passwords are redacted from returned `command` arrays.

## Docs

- [docs/GUIDE.md](docs/GUIDE.md) — operator guide
- [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) — agent checklist to configure a workstation
- [mcp.json.example](mcp.json.example)

## License

See repository license file (legacy project terms apply to the JAR/Kotlin code).
