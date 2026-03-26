param(
    [string]$TaskName = "Boletas_SendRaffleReminders_Daily",
    [string]$RunAt = "09:00"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptPath = Join-Path $projectRoot "scripts\send_raffle_reminders_daily.ps1"

if (!(Test-Path $scriptPath)) {
    throw "No se encontro el script diario: $scriptPath"
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$createArgs = @(
    "/Create",
    "/SC", "DAILY",
    "/TN", $TaskName,
    "/TR", $action,
    "/ST", $RunAt,
    "/F"
)

& schtasks.exe @createArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "No fue posible crear la tarea programada (exit code: $LASTEXITCODE)."
}

Write-Output "Tarea programada creada/actualizada correctamente: $TaskName a las $RunAt"
