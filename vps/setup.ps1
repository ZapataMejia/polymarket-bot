# ============================================================
#  setup.ps1  —  Instala el entorno de los bots (correr UNA vez)
#  Uso:  click derecho > "Ejecutar con PowerShell"
#        o en PowerShell:  powershell -ExecutionPolicy Bypass -File vps\setup.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "==> Python detectado:" -ForegroundColor Cyan
python --version

Write-Host "==> Creando entorno virtual (.venv)..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }

$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "==> Actualizando pip..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip

Write-Host "==> Instalando dependencias (requirements-bot.txt)..." -ForegroundColor Cyan
& $py -m pip install -r (Join-Path $root "requirements-bot.txt")

Write-Host "==> Sembrando estado de paper trading (histórico V1/V2B)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading_v2b") | Out-Null
$v1State = Join-Path $root "data\paper_trading\state.json"
$v2State = Join-Path $root "data\paper_trading_v2b\state.json"
if (-not (Test-Path $v1State)) { Copy-Item (Join-Path $PSScriptRoot "seed\state_v1.json") $v1State }
if (-not (Test-Path $v2State)) { Copy-Item (Join-Path $PSScriptRoot "seed\state_v2b.json") $v2State }

Write-Host ""
Write-Host "OK - Entorno listo." -ForegroundColor Green
Write-Host "Siguiente paso: crea el archivo .env (copia .env.example) y pega tus claves." -ForegroundColor Yellow
