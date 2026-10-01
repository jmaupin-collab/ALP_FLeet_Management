<#
Stops whatever is listening on the backend and frontend ports.

Targets the process actually holding the socket rather than every python or node
on the machine, so this cannot take down unrelated work.
#>

foreach ($port in 9000, 5173) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        Write-Output "${port}: nothing listening"
        continue
    }
    # Not $pid: PowerShell owns that name and assigning to it is an error.
    foreach ($owner in ($listeners.OwningProcess | Select-Object -Unique)) {
        $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Write-Output "${port}: stopped $name (pid $owner)"
    }
}
