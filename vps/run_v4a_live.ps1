# V4A LIVE — ordenes reales en Polymarket (USDC), umbral 30pp.
# Igual que V4B LIVE pero con threshold 0.30: dispara mas temprano
# (mas segundos antes del cierre) => mas liquidez en el libro => llena mejor,
# y ~7 senales/dia vs ~2.7 de V4B. Win rate mas bajo (~54%).
#
# REGLA DE PRECIO (aprendida de 2 semanas en vivo, 12-26 jun):
#   El precio de compra == win rate de equilibrio. Con ~57% de aciertos,
#   pagar >55c es -EV. Los trades caros (74c-87c) nos costaron la plata;
#   los baratos (34c-48c) la generaron. Por eso: --max-fill-price 0.55.
#
# IMPORTANTE: correr UN SOLO bot LIVE a la vez. No arrancar V4B LIVE junto.
# Requiere en .env: POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS,
#   POLYMARKET_SIGNATURE_TYPE=3, POLYMARKET_MAX_SLIPPAGE_CENTS, TELEGRAM_*.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path (Join-Path $root "data\live_trading_v4a") | Out-Null

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
    Write-Host "[V4A-LIVE] ERROR: falta token Telegram (TELEGRAM_BOT_TOKEN_LIVE o TELEGRAM_BOT_TOKEN)" -ForegroundColor Red
    exit 1
}

while ($true) {
    Write-Host "[V4A-LIVE] Arrancando trader LIVE (30pp, Kelly 50%K cap 20%)..." -ForegroundColor Green
    & $py scripts/run_paper_trader.py `
        --live `
        --bankroll 95.98 --bankroll-floor 0 `
        --sizing-mode kelly `
        --kelly-fraction 0.50 --max-pct-per-trade 0.10 `
        --max-position-usd 25 `
        --threshold 0.30 `
        --max-fill-price 0.55 `
        --min-poly-price 0.05 `
        --max-seconds-to-resolution 480 `
        --min-seconds-to-resolution 120 `
        --poll-sec 20 `
        --series btc-up-or-down-hourly eth-up-or-down-hourly `
        --max-concurrent 2 `
        --instance-label "V4A-LIVE" `
        --state-path data/live_trading_v4a/state.json `
        --telegram-token $tokenLive `
        --telegram-chat-id $chatId `
        --log-file logs/live_v4a.log
    Write-Host "[V4A-LIVE] Detenido. Reintento en 10s... (Ctrl+C para salir)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
