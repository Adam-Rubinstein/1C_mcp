@echo off
REM Start MCP SSE services on adam. Requires C:\Tools\1C_mcp\.env
set ROOT=C:\Tools\1C_mcp
set PY=%ROOT%\.venv\Scripts\python.exe
set LOG=%ROOT%\.tmp\logs
mkdir %LOG% 2>nul

for %%S in (dump:8761 load:8762 com:8763 files:8764 review:8765 journal:8766 debug:8767) do (
  for /f "tokens=1,2 delims=:" %%A in ("%%S") do (
    echo Starting %%A on %%B
    start "mcp-%%A" /MIN cmd /c "set MCP_TRANSPORT=sse&& set MCP_HOST=0.0.0.0&& set MCP_PORT=%%B&& %PY% %ROOT%\scripts\run_server.py %%A > %LOG%\%%A.log 2>&1"
  )
)

echo Done. Logs in %LOG%
