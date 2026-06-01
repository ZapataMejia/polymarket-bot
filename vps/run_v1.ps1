# Corre el bot V1 (configuración por defecto, threshold 0.05) con auto-reinicio.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

while ($true) {
    Write-Host "[V1] Arrancando paper trader..." -ForegroundColor Cyan
    & $py scripts/run_paper_trader.py `
        --bankroll 100 `
        --instance-label V1 `
        --state-path data/paper_trading/state.json `
        --log-file logs/paper_v1.log
    Write-Host "[V1] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
