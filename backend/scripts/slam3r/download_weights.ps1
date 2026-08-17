$ErrorActionPreference = 'Stop'
$log = 'E:\.PJs\deploy\download_weights.log'
Start-Transcript -Path $log -Force

function Get-AiFastHubFile {
    param([string]$Repo, [string]$File, [string]$DestDir, [long]$ExpectSize)
    $url = "https://aifasthub.com/siyan824/$Repo/resolve/main/$File"
    $dest = Join-Path $DestDir $File
    New-Item -ItemType Directory -Force $DestDir | Out-Null
    if (Test-Path $dest) { Remove-Item $dest -Force }
    Write-Host "downloading $File ($Repo) via aifasthub"
    & curl.exe -fL --retry 12 --retry-delay 3 -o $dest $url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    $sz = (Get-Item $dest).Length
    if ($ExpectSize -and $sz -ne $ExpectSize) { throw "size mismatch for ${File}: got $sz expected $ExpectSize" }
    Write-Host "  -> $dest ($sz bytes)"
}

Get-AiFastHubFile -Repo 'slam3r_i2p' -File 'slam3r_i2p.pth' -DestDir 'E:\.PJs\models\slam3r_i2p' -ExpectSize 2132077978
Get-AiFastHubFile -Repo 'slam3r_i2p' -File 'config.json' -DestDir 'E:\.PJs\models\slam3r_i2p'
Get-AiFastHubFile -Repo 'slam3r_l2w' -File 'slam3r_l2w.pth' -DestDir 'E:\.PJs\models\slam3r_l2w' -ExpectSize 2132070570
Get-AiFastHubFile -Repo 'slam3r_l2w' -File 'config.json' -DestDir 'E:\.PJs\models\slam3r_l2w'

Write-Host '=== SpatialLM1.1-Qwen-0.5B via ModelScope SDK ==='
if (Test-Path 'E:\.PJs\models\SpatialLM1.1-Qwen-0.5B') { Remove-Item -Recurse -Force 'E:\.PJs\models\SpatialLM1.1-Qwen-0.5B' }
& C:\anaconda3\python.exe -c "
from modelscope import snapshot_download
p = snapshot_download('manycore-research/SpatialLM1.1-Qwen-0.5B', local_dir=r'E:\.PJs\models\SpatialLM1.1-Qwen-0.5B', revision='master')
print('SpatialLM downloaded to', p)
"
if ($LASTEXITCODE -ne 0) { throw 'spatiallm download failed' }

Write-Host '=== verify config.json files are real JSON ==='
foreach ($f in 'E:\.PJs\models\slam3r_i2p\config.json','E:\.PJs\models\slam3r_l2w\config.json','E:\.PJs\models\SpatialLM1.1-Qwen-0.5B\config.json') {
    $head = (Get-Content $f -TotalCount 1).Trim()
    Write-Host "$f : first chars = $($head.Substring(0,[Math]::Min(40,$head.Length)))"
    if (-not $head.StartsWith('{')) { throw "$f is not JSON!" }
}

Write-Host '=== ALL MODEL FILES DOWNLOADED ==='
Get-ChildItem E:\.PJs\models -Recurse -File | Select-Object Length, FullName | Sort-Object FullName
Stop-Transcript
