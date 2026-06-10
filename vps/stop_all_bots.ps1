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

# Cerrar loops PowerShell de bots (NO matar start_bots_focused.ps1 — el regex
# anterior coincidia con "start_bots" dentro de "start_bots_focused").
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -match "start_bots_focused") { return }
    if ($cmd -match "run_v1\.ps1|run_v2b\.ps1|run_v4c\.ps1|run_v4\.ps1|run_v4b_demo\.ps1|run_v4b_live\.ps1|run_v4b\.ps1|run_dashboard\.ps1|start_bots\.ps1") {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  killed ps $($_.ProcessId): $($cmd.Substring(0, [Math]::Min(60, $cmd.Length)))..." -ForegroundColor Gray
    }
}

Write-Host "Listo. Verifica que no queden ventanas [V1][V2B][V4][V4C][Dashboard]." -ForegroundColor Green
