# Setup LIVE-ONLY: arranca SOLO el bot V4A LIVE (USDC real, 30pp).
#
#   V4A LIVE  - USDC real 30pp -> bot Telegram LIVE (~$95.98 inicial)
#
# Las demos (V4A/V4B paper) y el dashboard quedan APAGADOS a propósito.
# El propio bot LIVE responde /status /balance /dia /posiciones en Telegram.
#
# Candado de instancia unica: si ya hay un LIVE corriendo con el mismo
# state-path, el segundo aborta solo (no se duplican ordenes reales).
# Reset limpio: powershell -File vps\reset_v4b_comparison.ps1
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\live_trading_v4a") | Out-Null

Write-Host "=== Setup LIVE-ONLY: solo V4A LIVE ===" -ForegroundColor Cyan
Write-Host "Deteniendo bots viejos primero..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop_all_bots.ps1")
Start-Sleep -Seconds 2
Write-Host "Arrancando UNICAMENTE el bot LIVE..." -ForegroundColor Green

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4a_live.ps1")

Write-Host ""
Write-Host "Bot activo:" -ForegroundColor Green
Write-Host "  [V4A-LIVE] USDC real 30pp - bot Telegram LIVE (~`$95.98)" -ForegroundColor Green
Write-Host ""
Write-Host "APAGADOS: V4A-DEMO, V4B-DEMO, Dashboard, V1, V2B, V4C, V4B-LIVE" -ForegroundColor Yellow
Write-Host "Log: live_v4a.log" -ForegroundColor Gray
Write-Host ""
Write-Host "Para volver a prender el dashboard (opcional):" -ForegroundColor DarkGray
Write-Host "  Start-Process powershell -ArgumentList '-File','vps\run_dashboard.ps1'" -ForegroundColor DarkGray
