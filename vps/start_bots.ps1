# Lanza los DOS bots (V1 y V2B), cada uno en su propia ventana minimizada.
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v1.ps1")

Start-Process powershell -WindowStyle Minimized -ArgumentList `
    "-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run_v2b.ps1")

Write-Host "Bots V1 y V2B lanzados en ventanas minimizadas." -ForegroundColor Green
Write-Host "Logs: logs\paper_v1.log  y  logs\paper_v2b.log" -ForegroundColor Gray
