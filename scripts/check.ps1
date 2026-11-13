# 工作区检查：ruff → pyright → pytest → svelte-check → 构建
# 用法：  .\scripts\check.ps1            # 全跑
#         .\scripts\check.ps1 -Fast      # 只跑 lint + 类型
param(
    [switch]$Fast
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$results = [ordered]@{}

function Step($name, [scriptblock]$body) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    & $body
    $code = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } else { 0 }
    $script:results[$name] = $code
    if ($code -ne 0) { Write-Host "  -> 失败（退出码 $code）" -ForegroundColor Red }
}

Step 'ruff check' { uv run ruff check backend scripts }
Step 'ruff format --check' { uv run ruff format --check backend scripts }
Step 'pyright' { uv run pyright }

if (-not $Fast) {
    Step 'pytest' { uv run pytest }
    Step 'svelte-check' {
        Push-Location frontend
        npx svelte-check --threshold warning --output human
        Pop-Location
    }
    Step 'vite build' {
        Push-Location frontend
        npm run build --silent
        Pop-Location
    }
}

Write-Host ""
Write-Host "=== 汇总 ===" -ForegroundColor Cyan
$bad = 0
foreach ($k in $results.Keys) {
    if ($results[$k] -eq 0) {
        Write-Host ("  [ok]   {0}" -f $k) -ForegroundColor Green
    } else {
        Write-Host ("  [fail] {0}（{1}）" -f $k, $results[$k]) -ForegroundColor Red
        $bad++
    }
}

if ($bad -gt 0) { exit 1 } else { Write-Host "全部通过。" -ForegroundColor Green }
