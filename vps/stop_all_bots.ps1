# Detiene TODOS los bots Polymarket (ventanas PowerShell con run_*.ps1).
# Correr antes de arrancar el setup foco (V4A demo + V4B demo + V4B live).
$ErrorActionPreference = "Continue"
Write-Host "Deteniendo bots Polymarket..." -ForegroundColor Yellow

Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -match "run_paper_trader|run_dashboard_bot|run_paper_sumone") {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  killed pid $($_.ProcessId)" -ForegroundColor Gray
    }
}

# Tambien cerrar loops PowerShell de los scripts run_*.ps1
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -match "run_v1|run_v2b|run_v4|run_v4b|run_v4c|run_dashboard|start_bots") {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  killed ps $($_.ProcessId): $($cmd.Substring(0, [Math]::Min(60, $cmd.Length)))..." -ForegroundColor Gray
    }
}

Write-Host "Listo. Verifica que no queden ventanas [V1][V2B][V4][V4C][Dashboard]." -ForegroundColor Green
