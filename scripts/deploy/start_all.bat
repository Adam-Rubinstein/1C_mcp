@echo off
REM Start MCP SSE services on adam. Requires C:\Tools\1C_mcp\.env
set ROOT=C:\Tools\1C_mcp
set PY=%ROOT%\.venv\Scripts\python.exe
set LOG=%ROOT%\.tmp\logs
mkdir %LOG% 2>nul

for %%S in (dump:18761 load:18762 com:18763 files:18764 review:18765 journal:18766 debug:18767) do (
  for /f "tokens=1,2 delims=:" %%A in ("%%S") do (
    echo Starting %%A on %%B
    start "mcp-%%A" /MIN cmd /c "set MCP_TRANSPORT=sse&& set MCP_HOST=0.0.0.0&& set MCP_PORT=%%B&& %PY% %ROOT%\scripts\run_server.py %%A > %LOG%\%%A.log 2>&1"
  )
)

echo Done. Logs in %LOG%
