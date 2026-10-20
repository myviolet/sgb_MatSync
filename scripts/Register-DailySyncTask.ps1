param(
    [string]$TaskName = "MySQL-Material-Sync",
    [string]$StartTime = "02:00"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonPath)) {
    throw "Python interpreter not found: $pythonPath"
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Configuration file not found: $(Join-Path $projectRoot '.env')"
}

$parsedTime = [datetime]::ParseExact($StartTime, "HH:mm", $null)
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "-m mysql_table_sync.cli sync" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $parsedTime
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Daily incremental sync from SAP_RW_Material_Move to record_frozen_data" `
    -Force | Out-Null

Write-Output "Registered daily task '$TaskName' at $StartTime."
Write-Output "It runs as $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) when that user is logged on."
Write-Output "Logs: $(Join-Path $projectRoot 'logs\mysql_table_sync.log')"
