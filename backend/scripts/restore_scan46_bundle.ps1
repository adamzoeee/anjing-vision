param(
    [Parameter(Mandatory = $true)]
    [string]$BackendRoot
)

$ErrorActionPreference = "Stop"
$bundleRoot = Split-Path -Parent $PSScriptRoot
$targetRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
$targetData = Join-Path $targetRoot "data"

foreach ($relative in @("work\45", "work\46", "media\46")) {
    $source = Join-Path $bundleRoot ("data\" + $relative)
    if (-not (Test-Path -LiteralPath $source)) {
        throw "压缩包缺少必要目录: $source"
    }
    $destination = Join-Path $targetData $relative
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
}

Write-Host "扫描45/46训练产物与扫描46原视频已恢复到: $targetData"
Write-Host "无需重新训练。请启动后端和前端，并打开扫描46。"
