# V4B paper (demo) CON Telegram — comparar lado a lado con LIVE.
# Usa bot V4B (TELEGRAM_BOT_TOKEN_V4B). LIVE usa el bot viejo V1.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

$tokenV4B = ""
$chatId = ""
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN_V4B\s*=\s*(.+)$') { $tokenV4B = $Matches[1].Trim() }
    if ($line -match '^\s*TELEGRAM_CHAT_ID_DEMO\s*=\s*(.+)$') { $chatId = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($chatId)) {
    foreach ($line in Get-Content (Join-Path $root ".env")) {
        if ($line -match '^\s*TELEGRAM_CHAT_ID\s*=\s*(.+)$') { $chatId = $Matches[1].Trim(); break }
    }
}
if ([string]::IsNullOrWhiteSpace($tokenV4B)) {
    Write-Host "[V4B-DEMO] ERROR: falta TELEGRAM_BOT_TOKEN_V4B en .env" -ForegroundColor Red
    exit 1
}

while ($true) {
    Write-Host "[V4B-DEMO] Paper 40pp -> Telegram V4B (bankroll `$95.98)..." -ForegroundColor Cyan
    & $py scripts/run_paper_trader.py `
        --bankroll 95.98 --bankroll-floor 0 `
        --threshold 0.40 `
        --max-seconds-to-resolution 300 `
        --min-seconds-to-resolution 90 `
        --kelly-fraction 0.50 --max-pct-per-trade 0.20 `
        --instance-label "V4B-DEMO" `
        --state-path data/paper_trading_v4b/state.json `
        --telegram-token $tokenV4B `
        --telegram-chat-id $chatId `
        --log-file logs/paper_v4b.log
    Write-Host "[V4B-DEMO] Reintentando en 10s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
