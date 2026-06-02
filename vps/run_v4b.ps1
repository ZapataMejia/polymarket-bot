# Corre el bot V4B Endgame TIGHT con auto-reinicio.
# Mismo motor que V4A pero con threshold mas exigente:
#   - edge >= 40pp (vs 30pp V4A)
#   - faltan <= 5 minutos para resolucion
# Resultado esperado (backtest 1 año): 73 trades/año, WR 72.6%, DD -7.1%.
# Usa TELEGRAM_BOT_TOKEN_V4B del .env si esta, sino corre sin Telegram.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV4B = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V4B\s*=\s*(.+)$') { $tokenV4B = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV4B)) {
    Write-Host "[V4B] AVISO: no encontre TELEGRAM_BOT_TOKEN_V4B en .env; corro sin Telegram (solo log)." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V4B] Arrancando paper trader (Endgame TIGHT 40pp)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV4B)) {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.40 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V4B `
            --state-path data/paper_trading_v4b/state.json `
            --disable-telegram `
            --log-file logs/paper_v4b.log
    } else {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.40 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V4B `
            --state-path data/paper_trading_v4b/state.json `
            --telegram-token $tokenV4B `
            --log-file logs/paper_v4b.log
    }
    Write-Host "[V4B] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
