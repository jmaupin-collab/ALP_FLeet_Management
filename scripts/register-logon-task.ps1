<#
Registers start-servers.ps1 to run at logon as "FleetCommandDevServers".

Already registered on this machine; kept so the setup is reproducible and can be
re-created after a rebuild. Runs as the current user, so no elevation is needed.

    powershell -ExecutionPolicy Bypass -File scripts\register-logon-task.ps1

Remove it with:

    Unregister-ScheduledTask -TaskName FleetCommandDevServers -Confirm:$false
#>

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "start-servers.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# No execution time limit: the task is the servers, and they are meant to stay up.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Register-ScheduledTask -TaskName "FleetCommandDevServers" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts the Fleet Command backend (9000) and frontend (5173) at logon." `
    -Force | Select-Object TaskName, State
