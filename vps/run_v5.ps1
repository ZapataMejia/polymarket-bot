# Corre el bot V5 "Maker" (high-conviction selectivo) con auto-reinicio.
# Mismo motor que V1 (driftless log-normal), pero con filtros agresivos:
#   - edge >= 20pp (entre V2B 15pp y V4 30pp)
#   - skip noche US (21-02 UTC) y fines de semana
#   - min volumen 8k USD
#   - posicion mas grande por trade (mayor conviccion = mayor stake)
# Target: 75-85% WR. Usa TELEGRAM_BOT_TOKEN_V5 del .env.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV5 = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V5\s*=\s*(.+)$') { $tokenV5 = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV5)) {
    Write-Host "[V5] AVISO: no encontre TELEGRAM_BOT_TOKEN_V5 en .env; usare el bot por defecto." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V5] Arrancando paper trader (Maker / high-conviction)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV5)) {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.20 --min-volume 8000 `
            --skip-hours-utc 0 1 2 21 22 23 `
            --skip-weekdays Saturday Sunday `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V5 `
            --state-path data/paper_trading_v5/state.json `
            --log-file logs/paper_v5.log
    } else {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.20 --min-volume 8000 `
            --skip-hours-utc 0 1 2 21 22 23 `
            --skip-weekdays Saturday Sunday `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V5 `
            --state-path data/paper_trading_v5/state.json `
            --telegram-token $tokenV5 `
            --log-file logs/paper_v5.log
    }
    Write-Host "[V5] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
