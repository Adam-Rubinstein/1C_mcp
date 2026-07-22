# Launch MCP SSE services outside the SSH job object (survive disconnect).
$ErrorActionPreference = "Stop"
$Root = "C:\Tools\1C_mcp"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Run = Join-Path $Root "scripts\run_server.py"
$Logs = Join-Path $Root ".tmp\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$token = ""
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*MCP_TOKEN=(.+)$') { $token = $Matches[1].Trim() }
    }
}

$services = @(
    @{ Name = "dump"; Port = 18761 },
    @{ Name = "load"; Port = 18762 },
    @{ Name = "com"; Port = 18763 },
    @{ Name = "files"; Port = 18764 },
    @{ Name = "review"; Port = 18765 },
    @{ Name = "journal"; Port = 18766 },
    @{ Name = "debug"; Port = 18767 }
)

foreach ($s in $services) {
    $name = $s.Name
    $port = $s.Port
    $log = Join-Path $Logs "$name.log"
    # cmd.exe sets env then runs python; Win32_Process.Create is outside SSH job
    $cmd = "cmd.exe /c `"set MCP_TRANSPORT=sse&& set MCP_HOST=0.0.0.0&& set MCP_PORT=$port&& set MCP_TOKEN=$token&& `"$Py`" `"$Run`" $name > `"$log`" 2>&1`""
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $Root }
    if ($r.ReturnValue -ne 0) {
        Write-Host "FAILED $name code=$($r.ReturnValue)"
    } else {
        Write-Host "started $name pid=$($r.ProcessId) port=$port"
    }
}

Write-Host "Ports 18761-18767. Health: http://HOST:18765/health"
