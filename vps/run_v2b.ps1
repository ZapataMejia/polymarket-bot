# Corre el bot V2B (Selective: threshold 0.15 + filtros) con auto-reinicio.
# Usa el segundo bot de Telegram (TELEGRAM_BOT_TOKEN_V2 del .env).
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

# Leer el token del segundo bot desde .env
$tokenV2 = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V2\s*=\s*(.+)$') { $tokenV2 = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV2)) {
    Write-Host "[V2B] AVISO: no encontré TELEGRAM_BOT_TOKEN_V2 en .env; usaré el bot por defecto." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V2B] Arrancando paper trader (Selective)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV2)) {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.15 --min-volume 5000 `
            --skip-hours-utc 21 23 --skip-weekdays Saturday `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V2B `
            --state-path data/paper_trading_v2b/state.json `
            --log-file logs/paper_v2b.log
    } else {
        & $py scripts/run_paper_trader.py `
            --bankroll 100 --threshold 0.15 --min-volume 5000 `
            --skip-hours-utc 21 23 --skip-weekdays Saturday `
            --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
            --instance-label V2B `
            --state-path data/paper_trading_v2b/state.json `
            --telegram-token $tokenV2 `
            --log-file logs/paper_v2b.log
    }
    Write-Host "[V2B] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
