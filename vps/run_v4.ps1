# Corre el bot V4 Endgame con auto-reinicio.
# Mismo motor que V1 (driftless log-normal), pero solo entra cuando:
#   - edge >= 30pp (vs 5pp V1, 15pp V2B)
#   - faltan <= 5 minutos para resolucion
# Resultado esperado: muy pocos trades pero >90% WR.
# Usa TELEGRAM_BOT_TOKEN_V4 del .env (su propio bot).
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV4 = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V4\s*=\s*(.+)$') { $tokenV4 = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV4)) {
    Write-Host "[V4] AVISO: no encontre TELEGRAM_BOT_TOKEN_V4 en .env; usare el bot por defecto." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V4] Arrancando paper trader (Endgame)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV4)) {
        & $py scripts/run_paper_trader.py `
            --bankroll 95.98 --bankroll-floor 0 --threshold 0.30 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label "V4A-DEMO" `
            --state-path data/paper_trading_v4/state.json `
            --log-file logs/paper_v4.log
    } else {
        & $py scripts/run_paper_trader.py `
            --bankroll 95.98 --bankroll-floor 0 --threshold 0.30 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label "V4A-DEMO" `
            --state-path data/paper_trading_v4/state.json `
            --telegram-token $tokenV4 `
            --log-file logs/paper_v4.log
    }
    Write-Host "[V4] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
