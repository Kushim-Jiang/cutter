# 生产式本地运行：构建前端，单端口 8010 由 FastAPI 托管
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "$root/.venv")) { uv sync }

if (-not (Test-Path "$root/frontend/node_modules")) {
    Push-Location "$root/frontend"; npm install; Pop-Location
}

Write-Host "构建前端..." -ForegroundColor Cyan
Push-Location "$root/frontend"
npm run build
Pop-Location

Write-Host "http://127.0.0.1:8010/  （Ctrl+C 退出）" -ForegroundColor Green
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8010
