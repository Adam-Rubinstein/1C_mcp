# Hidden restart of MCP SSE on adam. No visible cmd windows.
# Survives SSH disconnect via start_all.py (DETACHED_PROCESS | CREATE_NO_WINDOW).
$ErrorActionPreference = "Stop"
$Root = "C:\Tools\1C_mcp"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Starter = Join-Path $Root "scripts\deploy\start_all.py"
if (-not (Test-Path $Py)) { throw "Missing venv python: $Py" }
if (-not (Test-Path $Starter)) { throw "Missing $Starter" }
Set-Location $Root
& $Py $Starter
if ($LASTEXITCODE -ne 0) { throw "start_all.py failed: $LASTEXITCODE" }
Write-Host "OK: MCP SSE started hidden. Logs: $Root\.tmp\logs (storage port 18769)"
