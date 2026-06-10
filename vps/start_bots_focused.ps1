# Setup foco: V4A demo + V4B demo + V4B LIVE + Dashboard.
# Comparacion demo vs live en Telegram (2 bots distintos, mismo chat opcional).
#
#   V4A demo  - paper 30pp   -> bot V4A  (TELEGRAM_BOT_TOKEN_V4)  $95.98
#   V4B demo  - paper 40pp   -> bot V4B  (TELEGRAM_BOT_TOKEN_V4B)  $95.98
#   V4B LIVE  - USDC real    -> bot V1   (TELEGRAM_BOT_TOKEN_LIVE) ~$95.98
#   Dashboard -> /week /all
#
# Reset limpio: powershell -File vps\reset_v4b_comparison.ps1
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\live_trading_v4b") | Out-Null

Write-Host "=== Setup FOCO: V4A demo + V4B demo + V4B LIVE + Dashboard ===" -ForegroundColor Cyan
Write-Host "Deteniendo bots viejos primero..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop_all_bots.ps1")
Start-Sleep -Seconds 2
Write-Host "Arrancando bots nuevos..." -ForegroundColor Green

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4b_demo.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4b_live.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_dashboard.ps1")

Write-Host ""
Write-Host "Bots activos:" -ForegroundColor Green
Write-Host "  [V4A-DEMO] paper 30pp   - bot Telegram V4A  (desde `$95.98)" -ForegroundColor White
Write-Host "  [V4B-DEMO] paper 40pp   - bot Telegram V4B  (desde `$95.98)" -ForegroundColor White
Write-Host "  [V4B-LIVE] USDC real    - bot V1 reutilizado (~`$95.98)" -ForegroundColor Green
Write-Host "  [Dashboard] /week /all - demo vs live" -ForegroundColor Cyan
Write-Host ""
Write-Host "APAGADOS: V1, V2B, V4C" -ForegroundColor Yellow
Write-Host "Logs: paper_v4.log, paper_v4b.log, live_v4b.log, dashboard_bot.log" -ForegroundColor Gray
