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

Write-Host "==> Preparando carpetas de estado..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\paper_trading_v2b") | Out-Null
# Sembrado opcional: solo si existe una semilla en vps/seed/ (por defecto NO hay,
# así los bots arrancan de cero en $100).
$v1State = Join-Path $root "data\paper_trading\state.json"
$v2State = Join-Path $root "data\paper_trading_v2b\state.json"
$v1Seed = Join-Path $PSScriptRoot "seed\state_v1.json"
$v2Seed = Join-Path $PSScriptRoot "seed\state_v2b.json"
if ((Test-Path $v1Seed) -and -not (Test-Path $v1State)) { Copy-Item $v1Seed $v1State }
if ((Test-Path $v2Seed) -and -not (Test-Path $v2State)) { Copy-Item $v2Seed $v2State }

Write-Host ""
Write-Host "OK - Entorno listo." -ForegroundColor Green
Write-Host "Siguiente paso: crea el archivo .env (copia .env.example) y pega tus claves." -ForegroundColor Yellow
