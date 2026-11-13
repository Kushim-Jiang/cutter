# 开发：后端 8010 + 前端 5173（/api 由 Vite 代理到后端）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "$root/.venv")) {
    Write-Host "创建 Python 环境..." -ForegroundColor Cyan
    uv sync
}

if (-not (Test-Path "$root/frontend/node_modules")) {
    Write-Host "安装前端依赖..." -ForegroundColor Cyan
    Push-Location "$root/frontend"
    npm install
    Pop-Location
}

Write-Host "启动后端 http://127.0.0.1:8010 ..." -ForegroundColor Green
$backend = Start-Process -FilePath "uv" `
    -ArgumentList "run", "uvicorn", "app.main:app", "--app-dir", "backend", "--reload", "--port", "8010" `
    -WorkingDirectory $root -PassThru

Write-Host "启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Green
Push-Location "$root/frontend"
try {
    npm run dev
} finally {
    Pop-Location
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id }
}
