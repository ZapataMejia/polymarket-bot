# Corre el bot V3 SumOne (sum-to-one arbitrage) con auto-reinicio.
# Usa TELEGRAM_BOT_TOKEN_V3 del .env (su propio bot).
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV3 = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V3\s*=\s*(.+)$') { $tokenV3 = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($tokenV3)) {
    Write-Host "[V3] AVISO: no encontre TELEGRAM_BOT_TOKEN_V3 en .env; usare el bot por defecto." -ForegroundColor Yellow
}

while ($true) {
    Write-Host "[V3] Arrancando SumOne (sum-to-one arbitrage)..." -ForegroundColor Cyan
    if ([string]::IsNullOrWhiteSpace($tokenV3)) {
        & $py scripts/run_paper_sumone.py `
            --bankroll 100 --poll-sec 15 `
            --max-pct-per-arb 0.10 --max-position-usd 200 `
            --margin 0.005 `
            --instance-label V3 `
            --state-path data/paper_trading_v3/state.json `
            --log-file logs/paper_v3.log
    } else {
        & $py scripts/run_paper_sumone.py `
            --bankroll 100 --poll-sec 15 `
            --max-pct-per-arb 0.10 --max-position-usd 200 `
            --margin 0.005 `
            --instance-label V3 `
            --state-path data/paper_trading_v3/state.json `
            --telegram-token $tokenV3 `
            --log-file logs/paper_v3.log
    }
    Write-Host "[V3] El bot se detuvo. Reintentando en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
