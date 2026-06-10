# V4B LIVE — ordenes reales en Polymarket (USDC).
# IMPORTANTE: detener run_v4b.ps1 (paper) antes de arrancar este script
# para no duplicar senales en el mismo mercado.
#
# Config = igual paper V4B: Kelly 50%, cap 20%, 40pp endgame, stop $67.
# Requiere en .env:
#   POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS,
#   POLYMARKET_SIGNATURE_TYPE=3 (cuenta Google nueva), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path (Join-Path $root "data\live_trading_v4b") | Out-Null

# Token LIVE: reutiliza el bot viejo de V1 (TELEGRAM_BOT_TOKEN_LIVE o TELEGRAM_BOT_TOKEN).
# Si no hay, usa TELEGRAM_BOT_TOKEN_V4B.
$tokenLive = ""
$chatId = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_LIVE\s*=\s*(.+)$') { $tokenLive = $Matches[1].Trim() }
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*(.+)$' -and [string]::IsNullOrWhiteSpace($tokenLive)) {
        $tokenLive = $Matches[1].Trim()
    }
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V4B\s*=\s*(.+)$' -and [string]::IsNullOrWhiteSpace($tokenLive)) {
        $tokenLive = $Matches[1].Trim()
    }
    if ($line -match '^\s*TELEGRAM_CHAT_ID_LIVE\s*=\s*(.+)$') { $chatId = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($chatId)) {
    foreach ($line in Get-Content (Join-Path $root ".env")) {
        if ($line -match '^\s*TELEGRAM_CHAT_ID\s*=\s*(.+)$') { $chatId = $Matches[1].Trim(); break }
    }
}
if ([string]::IsNullOrWhiteSpace($tokenLive)) {
    Write-Host "[V4B-LIVE] ERROR: falta token Telegram (TELEGRAM_BOT_TOKEN_LIVE o TELEGRAM_BOT_TOKEN)" -ForegroundColor Red
    exit 1
}

while ($true) {
    Write-Host "[V4B-LIVE] Arrancando trader LIVE (40pp, Kelly 50%K cap 20%)..." -ForegroundColor Green
    & $py scripts/run_paper_trader.py `
        --live `
        --bankroll 95.98 --bankroll-floor 0 `
        --sizing-mode kelly `
        --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
        --max-position-usd 50 `
        --threshold 0.40 `
        --max-seconds-to-resolution 300 `
        --min-seconds-to-resolution 90 `
        --max-concurrent 2 `
        --instance-label "V4B-LIVE" `
        --state-path data/live_trading_v4b/state.json `
        --telegram-token $tokenLive `
        --telegram-chat-id $chatId `
        --log-file logs/live_v4b.log
    Write-Host "[V4B-LIVE] Detenido. Reintento en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
