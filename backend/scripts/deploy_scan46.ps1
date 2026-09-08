# 安龄智境 · 扫描46 一键自动部署脚本
# 用法（在已 clone 的仓库根目录下）：
#   powershell -ExecutionPolicy Bypass -File backend\scripts\deploy_scan46.ps1 -BackendRoot <backend目录>
# 或（默认 backend 目录）：
#   powershell -ExecutionPolicy Bypass -File backend\scripts\deploy_scan46.ps1
# 流程：自动下载 GitHub Release 上的数据包 → 解压 → 还原到 backend\data →
#       导入数据库（只显示扫描46）→ 提示启动后端/前端。
param(
    [string]$BackendRoot = "",
    [switch]$With45          # 同时下载并还原 45 基线（将来从头重建才需要）
)

$ErrorActionPreference = "Stop"
$TAG = "v-scan46-data"
$Base = "https://github.com/adamzoeee/anjing-vision/releases/download/$TAG"

if (-not $BackendRoot) {
    $BackendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
}
$BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
$tmp = Join-Path $env:TEMP "anjing-scan46-deploy"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Set-Location $tmp

Write-Host "[1/4] 下载 46 部署包（约 1.7GB）..."
curl.exe -L -o scan46_部署包.zip "$Base/scan46_部署包.zip"
if (-not (Test-Path "scan46_部署包.zip")) { throw "下载失败" }
Write-Host "       解压中..."
Expand-Archive -Path "scan46_部署包.zip" -DestinationPath . -Force

Write-Host "[2/4] 还原 46 数据到 backend\data ..."
powershell -ExecutionPolicy Bypass -File .\restore_scan46_bundle.ps1 -BackendRoot $BackendRoot

if ($With45) {
    Write-Host "[3/4] 下载并还原 45 基线（可选，约 2.5GB）..."
    foreach ($name in @("scan45_基线_slam3r_preds.zip", "scan45_基线_slam3r_frames.zip", "scan45_基线_其余.zip")) {
        curl.exe -L -o $name "$Base/$name"
        Expand-Archive -Path $name -DestinationPath (Join-Path $BackendRoot "data") -Force
    }
} else {
    Write-Host "[3/4] 跳过 45 基线（如需从头重建，加 -With45 重新执行）"
}

Write-Host "[4/4] 导入数据库（页面只显示扫描46）..."
Copy-Item .\scan46_db_import.sql $BackendRoot -Force
Copy-Item .\导入数据库.bat $BackendRoot -Force
Push-Location $BackendRoot
& ".\导入数据库.bat"
Pop-Location

Write-Host ""
Write-Host "=============================================="
Write-Host " 部署完成。启动方式："
Write-Host "   后端: cd $BackendRoot; .venv\Scripts\python -m uvicorn app.main:app --port 8000"
Write-Host "   前端: flutter run -d web-server --port 3000"
Write-Host " 打开 http://localhost:3000 → 登录 → 扫描46："
Write-Host "   点云图 / 3D空间结构图 / 2D结构图 / 通行图 / 长度 全部与源机一致"
Write-Host "=============================================="
