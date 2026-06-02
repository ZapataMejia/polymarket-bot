# Corre el bot V4C SOL-only con auto-reinicio.
# Mismo motor que V4A (edge >= 30pp, ultimos 5 min) pero filtrado a SOLO el
# series slug de SOL — el asset con mejor WR (67.5%) en el backtest 1 ano real.
# Resultado esperado (backtest 1 ano real): ~708 trades/ano, WR ~67.5%, DD ~-7%.
# Usa TELEGRAM_BOT_TOKEN_V4C del .env si esta, sino corre sin Telegram.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV4C = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V4C\s*=\s*(.+)$') { $tokenV4C = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV4C)) {
    Write-Host "[V4C] AVISO: no encontre TELEGRAM_BOT_TOKEN_V4C en .env; corro sin Telegram (solo log)." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V4C] Arrancando paper trader (Endgame 30pp SOL-only)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV4C)) {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.30 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --series solana-up-or-down-hourly `
            --instance-label V4C `
            --state-path data/paper_trading_v4c/state.json `
            --disable-telegram `
            --log-file logs/paper_v4c.log
    } else {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.30 `
            --max-seconds-to-resolution 300 `
            --min-seconds-to-resolution 90 `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --series solana-up-or-down-hourly `
            --instance-label V4C `
            --state-path data/paper_trading_v4c/state.json `
            --telegram-token $tokenV4C `
            --log-file logs/paper_v4c.log
    }
    Write-Host "[V4C] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
