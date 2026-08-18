<#
  Gaussian 连续场景分支一键部署：把 slam3r conda 环境克隆到纯 ASCII 路径并源码编译 gsplat。

  为什么必须这样：
    1. gsplat 在 PyPI 没有 Windows 轮子，需要 nvcc 源码编译；
    2. 若用户名/路径含中文（如 C:\Users\吕昊东），MSVC 编译会报
       “无法打开源文件: 吕”，必须把环境放到纯 ASCII 路径（如 D:\conda\slam3r）；
    3. 编译依赖系统 CUDA（ASCII 路径）与 MSVC。

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File setup_gaussian.ps1
  可选参数：
    -SkipClone    已克隆过环境时跳过
    -SkipBuild    已编译过 gsplat 时跳过
  完成后在 backend/.env 写入（如路径不同）：
    GAUSSIAN_PYTHON=D:\conda\slam3r\python.exe
#>
param(
    [switch]$SkipClone,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$conda = 'C:\anaconda3\Scripts\conda.exe'
if (-not (Test-Path $conda)) { $conda = 'D:\med\Scripts\conda.exe' }
if (-not (Test-Path $conda)) { throw '未找到 conda.exe（C:\anaconda3 或 D:\med）' }

$asciiPython = 'D:\conda\slam3r\python.exe'
$tmpDir = 'D:\tmp-build'
$extDir = 'D:\torch-ext-build'
New-Item -ItemType Directory -Force $tmpDir, $extDir | Out-Null

if (-not $SkipClone -and -not (Test-Path $asciiPython)) {
    Write-Host '=== 克隆 slam3r 环境到 ASCII 路径 ==='
    & $conda create -y -p 'D:\conda\slam3r' --clone slam3r
    if ($LASTEXITCODE -ne 0) { throw 'conda clone 失败' }
}

if (-not $SkipBuild) {
    Write-Host '=== 源码编译 gsplat ==='
    $env:TMP = $tmpDir; $env:TEMP = $tmpDir; $env:TMPDIR = $tmpDir
    $env:PIP_CACHE_DIR = Join-Path $tmpDir 'pip-cache'
    $env:TORCH_EXTENSIONS_DIR = $extDir
    $cudaHome = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8'
    if (-not (Test-Path $cudaHome)) { throw "缺少系统 CUDA 12.8：$cudaHome" }
    $env:CUDA_HOME = $cudaHome; $env:CUDA_PATH = $cudaHome
    $env:TORCH_CUDA_ARCH_LIST = '12.0'
    $env:PATH = "D:\conda\slam3r\Scripts;D:\conda\slam3r\Library\bin;$cudaHome\bin;$env:PATH"
    & $asciiPython -m pip install --no-binary gsplat --no-build-isolation --no-deps gsplat==1.5.3
    if ($LASTEXITCODE -ne 0) { throw 'gsplat 编译失败（检查 CUDA/MSVC 与 PATH）' }
}

Write-Host '=== 冒烟自检：gsplat CUDA 栅格化 ==='
$env:TMP = $tmpDir; $env:TEMP = $tmpDir; $env:TORCH_EXTENSIONS_DIR = $extDir
& $asciiPython -c @"
import torch
from gsplat import rasterization
means = torch.randn(100, 3, device='cuda')
quats = torch.randn(100, 4, device='cuda'); quats = quats / quats.norm(dim=1, keepdim=True)
scales = torch.rand(100, 3, device='cuda') * 0.1 + 0.05
opa = torch.sigmoid(torch.randn(100, device='cuda'))
colors = torch.rand(100, 1, 3, device='cuda')
vm = torch.eye(4, device='cuda').repeat(2, 1, 1)
K = torch.tensor([[280., 0, 112.], [0, 280., 112.], [0, 0, 1]], device='cuda').repeat(2, 1, 1)
out, _, _ = rasterization(means, quats, scales, opa, colors, vm, K, 224, 224, sh_degree=0, render_mode='RGB')
print('GSPLAT_RASTER_OK', out.shape)
"@
if ($LASTEXITCODE -ne 0) { throw 'gsplat 冒烟自检失败' }

Write-Host '=== Gaussian 栈部署完成：backend/.env 请确认 ==='
Write-Host 'GAUSSIAN_PYTHON=D:\conda\slam3r\python.exe'
Write-Host '（不写则默认使用该路径）'
