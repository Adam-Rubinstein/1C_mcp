# Deploy helpers for Windows host (adam). Run as Administrator if installing services.
# Secrets: create C:\Tools\1C_mcp\.env first (never commit).

param(
    [string]$Root = "C:\Tools\1C_mcp",
    [string]$Python = "",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
if (-not $Python) {
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}
if (-not (Test-Path $Python)) {
    Write-Host "Creating venv..."
    python -m venv (Join-Path $Root ".venv")
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
}

$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning "Missing $envFile — copy .env.example and fill secrets."
}

$services = @(
    @{ Name = "dump"; Port = 8761 },
    @{ Name = "load"; Port = 8762 },
    @{ Name = "com"; Port = 8763 },
    @{ Name = "files"; Port = 8764 },
    @{ Name = "review"; Port = 8765 },
    @{ Name = "journal"; Port = 8766 },
    @{ Name = "debug"; Port = 8767 }
)

$logs = Join-Path $Root ".tmp\logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Start-McpService($name, $port) {
    $log = Join-Path $logs "$name.log"
    $arg = "`"$Python`" `"$Root\scripts\run_server.py`" $name"
    # Load env via python-dotenv inside servers; set transport/port here
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python
    $psi.Arguments = "`"$Root\scripts\run_server.py`" $name"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.Environment["MCP_TRANSPORT"] = "sse"
    $psi.Environment["MCP_HOST"] = "0.0.0.0"
    $psi.Environment["MCP_PORT"] = "$port"
    # dotenv loads MCP_TOKEN and ONEC_* from .env
    $p = [System.Diagnostics.Process]::Start($psi)
    "Started $name pid=$($p.Id) port=$port log=$log"
}

if ($StartNow) {
    foreach ($s in $services) {
        Start-McpService $s.Name $s.Port
    }
    Write-Host "Platform JAR: start separately, e.g."
    Write-Host '  java -Dfile.encoding=UTF-8 -jar packages\mcp-1c-platform\runtime\1C_mcp_bsl.jar --platform-path "...\8.3.27.1719" --mode sse --port 8760'
    Write-Host "Optional Bearer proxy: MCP_TOKEN=... UPSTREAM_URL=http://127.0.0.1:8760 python scripts\sse_auth_proxy.py"
} else {
    Write-Host "Usage: .\install_services.ps1 -StartNow"
    Write-Host "For production prefer NSSM/WinSW scheduled tasks wrapping run_server.py per package."
}
