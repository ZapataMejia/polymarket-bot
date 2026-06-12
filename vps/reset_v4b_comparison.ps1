# Reinicia V4A/V4B demo y LIVE para comparacion limpia desde $95.98.
# Hace backup de los state.json viejos antes de borrar.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $root "data\backups\$ts"
New-Item -ItemType Directory -Force -Path $backup | Out-Null

$files = @(
    "data\paper_trading_v4\state.json",
    "data\paper_trading_v4b\state.json",
    "data\live_trading_v4a\state.json",
    "data\live_trading_v4b\state.json"
)
foreach ($rel in $files) {
    $path = Join-Path $root $rel
    if (Test-Path $path) {
        $dest = Join-Path $backup (Split-Path $rel -Leaf)
        Copy-Item $path $dest
        Remove-Item $path -Force
        Write-Host "  backup + reset: $rel" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "Reset listo. Backup en: $backup" -ForegroundColor Green
Write-Host "Al arrancar los bots:" -ForegroundColor Cyan
Write-Host "  V4A demo  -> empieza en `$95.98 (paper)" -ForegroundColor White
Write-Host "  V4B demo  -> empieza en `$95.98 (paper)" -ForegroundColor White
Write-Host "  V4A LIVE  -> lee balance real de Polymarket (~`$95.98)" -ForegroundColor White
Write-Host ""
Write-Host "Ahora corre: powershell -File vps\start_bots_focused.ps1" -ForegroundColor Yellow
