@echo off
REM Prefer start_all.ps1 / start_all.py — no visible consoles.
set ROOT=C:\Tools\1C_mcp
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\deploy\start_all.py"
echo Done. Logs in %ROOT%\.tmp\logs
