# Registra una tarea programada para que los bots arranquen SOLOS
# cada vez que inicies sesión en el VPS (sobrevive reinicios).
# Correr UNA vez:  powershell -ExecutionPolicy Bypass -File vps\install_autostart.ps1
$ErrorActionPreference = "Stop"
$startScript = Join-Path $PSScriptRoot "start_bots.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "PolymarketBots" -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Arranca los bots de Polymarket (V1 + V2B) al iniciar sesion en el VPS."

Write-Host "Tarea 'PolymarketBots' registrada." -ForegroundColor Green
Write-Host "Los bots arrancaran solos al iniciar sesion en el VPS." -ForegroundColor Green
