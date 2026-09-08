# 安龄智境 · 扫描46 一键自动部署脚本
# 在已 clone 的仓库根目录执行：
# powershell -ExecutionPolicy Bypass -File backend\scripts\deploy_scan46.ps1 `
#   -FullBundlePath "scan46_完整数据包.zip" `
#   -SupplementBundlePath "scan46_最新结果补充包.zip"
# 脚本会还原扫描45/46、覆盖当前最终结果并创建扫描46数据库快照。
param(
    [string]$BackendRoot = "",
    [string]$FullBundlePath = "",
    [string]$SupplementBundlePath = "",
    [switch]$With45
)

$ErrorActionPreference = "Stop"
$TAG = "v-scan46-data"
$Base = "https://github.com/adamzoeee/anjing-vision/releases/download/$TAG"

if (-not $BackendRoot) {
    $BackendRoot = Split-Path -Parent $PSScriptRoot
}
$BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
$tmp = Join-Path $env:TEMP ("anjing-scan46-deploy-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

if ($FullBundlePath) {
    $bundleArchive = (Resolve-Path -LiteralPath $FullBundlePath).Path
    Write-Host "[1/5] 使用本地完整数据包: $bundleArchive"
} else {
    $bundleArchive = Join-Path $tmp "scan46_部署包.zip"
    Write-Host "[1/5] 下载扫描46数据包..."
    curl.exe -L -o $bundleArchive "$Base/scan46_部署包.zip"
    if (-not (Test-Path -LiteralPath $bundleArchive)) { throw "下载失败" }
}

$bundleRoot = Join-Path $tmp "full-bundle"
New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
Write-Host "       解压中..."
Expand-Archive -LiteralPath $bundleArchive -DestinationPath $bundleRoot -Force

Write-Host "[2/5] 还原扫描45/46基础数据 ..."
foreach ($relative in @("work\45", "work\46", "media\46")) {
    $source = Join-Path (Join-Path $bundleRoot "data") $relative
    if (-not (Test-Path -LiteralPath $source)) {
        throw "完整数据包缺少必要目录: $source"
    }
    $destination = Join-Path (Join-Path $BackendRoot "data") $relative
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
}

if ($SupplementBundlePath) {
    $SupplementBundlePath = (Resolve-Path -LiteralPath $SupplementBundlePath).Path
    Write-Host "[3/5] 覆盖扫描46最新点云、评估和 PDF ..."
    Expand-Archive -LiteralPath $SupplementBundlePath -DestinationPath $BackendRoot -Force
} else {
    Write-Host "[3/5] 未提供最新结果补充包；将保留完整包内的旧结果"
}

if ($With45) {
    Write-Host "[4/5] 下载并还原旧版45分包（完整数据包通常不需要）..."
    foreach ($name in @("scan45_基线_slam3r_preds.zip", "scan45_基线_slam3r_frames.zip", "scan45_基线_其余.zip")) {
        $archivePath = Join-Path $tmp $name
        curl.exe -L -o $archivePath "$Base/$name"
        Expand-Archive -LiteralPath $archivePath -DestinationPath (Join-Path $BackendRoot "data") -Force
    }
} else {
    Write-Host "[4/5] 使用完整包自带的扫描45基线"
}

Write-Host "[5/5] 创建扫描46数据库快照（原数据库如存在会先备份）..."
$sqlPath = Join-Path $bundleRoot "scan46_db_import.sql"
if (-not (Test-Path -LiteralPath $sqlPath)) {
    throw "完整数据包缺少 scan46_db_import.sql"
}
$pythonExe = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
}
$databasePath = Join-Path $BackendRoot "anjing-local.db"
$newDatabasePath = Join-Path $BackendRoot "anjing-local.scan46-import.db"
if (Test-Path -LiteralPath $newDatabasePath) {
    Remove-Item -LiteralPath $newDatabasePath -Force
}
& $pythonExe -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.executescript(open(sys.argv[2], encoding='utf-8').read()); db.commit(); db.close()" $newDatabasePath $sqlPath
if ($LASTEXITCODE -ne 0) { throw "扫描46数据库导入失败" }
if (Test-Path -LiteralPath $databasePath) {
    $backupPath = "$databasePath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Move-Item -LiteralPath $databasePath -Destination $backupPath
    Write-Host "       原数据库已备份到: $backupPath"
}
Move-Item -LiteralPath $newDatabasePath -Destination $databasePath

Write-Host ""
Write-Host "=============================================="
Write-Host " 部署完成。启动方式："
Write-Host "   后端: cd $BackendRoot; .venv\Scripts\python -m uvicorn app.main:app --port 8000"
Write-Host "   前端: cd app; flutter run -d web-server --web-port 3000"
Write-Host " 打开 http://localhost:3000 → 登录 → 扫描46："
Write-Host "   点云图 / 3D空间结构图 / 2D结构图 / 通行图 / 风险评分 / PDF 可与源机一致"
Write-Host "=============================================="
