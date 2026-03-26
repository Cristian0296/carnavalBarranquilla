param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) {
    throw "No se encontro Python del entorno virtual en: $pythonExe"
}

Set-Location $ProjectRoot
& $pythonExe "manage.py" "send_raffle_reminders"
