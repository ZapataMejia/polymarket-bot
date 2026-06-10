# Corre el bot Dashboard de Telegram con auto-reinicio.
# Este bot vive en el mismo VPS que los demas bots y lee directamente los
# state.json para resumen V4A demo + V4B demo + V4B LIVE.
#
# Usa TELEGRAM_BOT_TOKEN_DASHBOARD del .env (token del bot Telegram dedicado).
# Si no esta seteado, el script se detiene con error.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

while ($true) {
    Write-Host "[Dashboard] Arrancando bot dashboard..." -ForegroundColor Cyan
    & $py scripts/run_dashboard_bot.py
    Write-Host "[Dashboard] El bot se detuvo (codigo $LASTEXITCODE). Reintentando en 10s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
