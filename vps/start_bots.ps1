# Lanza TODOS los bots (V1, V2B, V3, V4, V5), cada uno en su propia ventana minimizada.
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v1.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v2b.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v3.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v4.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v5.ps1")

Write-Host "Bots V1, V2B, V3, V4 y V5 lanzados en ventanas minimizadas." -ForegroundColor Green
Write-Host "Logs: logs\paper_v1.log, paper_v2b.log, paper_v3.log, paper_v4.log, paper_v5.log" -ForegroundColor Gray
