# Lanza los bots activos del portafolio actual:
#   V1        - Alerts          (edge >= 5pp, sin filtros)
#   V2B       - Selective       (edge >= 10pp + skip horas/sab + vol $5k)
#   V4A       - Endgame 30pp    (edge >= 30pp en los ultimos 5 min)        <- backbone
#   V4B       - Endgame 40pp    (edge >= 40pp en los ultimos 5 min)        <- version tight
#   V4C       - SOL-only 30pp   (edge >= 30pp ultimos 5 min, solo SOL)     <- mejor asset
#   Dashboard - Bot Telegram unificado que lee los 5 state.json y responde /all, /today, /week
#
# V3 (SumOne) y V5 (Maker) fueron desactivados:
#   - V3: ROI estimado ~$11/mes con bankroll $100 no justifica el slot
#   - V5: solo 216 trades/ano, $98/mes — slot desperdiciado
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading_v4b") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading_v4c") | Out-Null

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v1.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v2b.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4b.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4c.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_dashboard.ps1")

Write-Host "Bots V1, V2B, V4A, V4B, V4C y Dashboard lanzados en ventanas minimizadas." -ForegroundColor Green
Write-Host "Logs: logs\paper_v1.log, paper_v2b.log, paper_v4.log, paper_v4b.log, paper_v4c.log, dashboard_bot.log" -ForegroundColor Gray
Write-Host "Dashboard: bot Telegram unificado, comandos /all /today /week /best /trades /help" -ForegroundColor Cyan
Write-Host "V3 SumOne y V5 Maker desactivados por decision (ROI marginal)." -ForegroundColor Yellow
