<#
Starts the Fleet Command backend and frontend and leaves them running.

Registered as a logon scheduled task (see register-logon-task.ps1), and safe to
run by hand at any time: anything already listening is left alone, so running it
twice cannot produce the competing uvicorn processes that have bitten before.
#>

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Test-Port([int]$Port) {
    $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-Server([string]$Exe, [string[]]$ServerArgs, [string]$WorkingDirectory, [string]$Name) {
    # Launched directly rather than through cmd.exe: a command line carrying a
    # quoted interpreter path *and* a quoted redirect target gets mangled by
    # cmd's quote stripping, which is why the backend silently failed to start.
    # Start-Process wants the two streams in separate files.
    Start-Process -FilePath $Exe `
        -ArgumentList $ServerArgs `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "$Name.log") `
        -RedirectStandardError (Join-Path $logs "$Name.err.log")
}

if (Test-Port 9000) {
    Write-Output "backend: already listening on 9000, left alone"
} else {
    $python = Join-Path $root "backend\.venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { throw "No virtualenv at $python" }
    # No --reload: its supervisor can die and leave a child holding the socket
    # while no longer watching files, which looks exactly like a wedged server.
    Start-Server $python @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "9000") `
        (Join-Path $root "backend") "backend"
    Write-Output "backend: starting on 9000"
}

if (Test-Port 5173) {
    Write-Output "frontend: already listening on 5173, left alone"
} else {
    # npm.cmd, not npm: Start-Process needs the real executable on disk.
    Start-Server "npm.cmd" @("run", "dev") (Join-Path $root "frontend") "frontend"
    Write-Output "frontend: starting on 5173"
}
