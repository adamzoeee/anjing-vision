# One-shot Windows native setup for the vid2scene reconstruction backend.
# Usage (PowerShell 5.1+):
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup_vid2scene.ps1
#
# Optional parameters:
#   -RepoRoot <dir>   vid2scene clone location        (default: E:\.PJs\vid2scene)
#   -CondaEnv <name>  conda env name                  (default: vid2scene)
#
# Requirements before running: Git, Miniconda, ffmpeg (>=5, recommended 9.x),
# NVIDIA GPU driver, CUDA Toolkit 12.8, MSVC Build Tools 2022.
# All log messages are ASCII on purpose (PowerShell 5.1 reads scripts as ANSI).
param(
    [string]$RepoRoot = "E:\.PJs\vid2scene",
    [string]$CondaEnv = "vid2scene"
)

$ErrorActionPreference = 'Stop'
$log = Join-Path $RepoRoot 'setup_vid2scene.log'
New-Item -ItemType Directory -Force -Path $RepoRoot | Out-Null
Remove-Item $log -ErrorAction SilentlyContinue
function Log($m) { Write-Host $m; $m | Out-File -FilePath $log -Append -Encoding utf8 }

# --- 1. Locate CUDA Toolkit (latest version under the standard install path) ---
$cudaRoot = Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue |
    Sort-Object { [version]$_.Name } | Select-Object -Last 1
if (-not $cudaRoot -or -not (Test-Path (Join-Path $cudaRoot.FullName 'bin\nvcc.exe'))) {
    if ($env:CUDA_HOME -and (Test-Path (Join-Path $env:CUDA_HOME 'bin\nvcc.exe'))) {
        $cudaRoot = Get-Item $env:CUDA_HOME
    } else {
        throw "CUDA Toolkit not found. Install CUDA 12.x from https://developer.nvidia.com/cuda-toolkit"
    }
}
$env:CUDA_HOME = $cudaRoot.FullName
Log "CUDA_HOME = $($cudaRoot.FullName)"

# --- 2. Locate MSVC (vswhere -> BuildTools default -> VS2022 default) ---
$vcvars = $null
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path $vswhere) {
    $ip = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1
    if ($ip) { $vcvars = Join-Path $ip 'VC\Auxiliary\Build\vcvars64.bat' }
}
if (-not $vcvars -or -not (Test-Path $vcvars)) {
    $vcvars = "$env:USERPROFILE\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
}
if (-not (Test-Path $vcvars)) {
    throw "MSVC vcvars64.bat not found. Install Visual Studio Build Tools 2022 with the C++ workload."
}
Log "vcvars64 = $vcvars"

# --- 3. Conda env: python 3.12 + COLMAP CLI ---
Log '=== conda create ==='
conda create -y -n $CondaEnv python=3.12 pip 2>&1 | Tee-Object -FilePath $log -Append
Log '=== conda install colmap ==='
conda install -y -n $CondaEnv -c conda-forge colmap=4.1.1 2>&1 | Tee-Object -FilePath $log -Append

$py = Join-Path $env:USERPROFILE ".conda\envs\$CondaEnv\python.exe"
if (-not (Test-Path $py)) {
    $base = (& conda info --base 2>$null).Trim()
    $py = Join-Path $base "envs\$CondaEnv\python.exe"
}
if (-not (Test-Path $py)) { throw "conda env python not found: $py" }
Log "conda env python = $py"

# Isolated python runs: blank PYTHONPATH/PYTHONHOME so other venvs never leak in.
$env:PYTHONPATH = ''
$env:PYTHONHOME = ''
$env:PYTHONUTF8 = '1'

# --- 4. PyTorch (cu128: RTX 50 / sm_120 needs torch >= 2.7) ---
Log '=== pip install torch ==='
& $py -I -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128 2>&1 | Tee-Object -FilePath $log -Append

# --- 5. Python deps (numpy 2.x is required by torch 2.7 ABI) ---
Log '=== pip install python deps ==='
& $py -I -m pip install "numpy==2.4.3" ninja jaxtyping "rich>=13.3.3,<15" "watchdog==5.0.3" "pillow==10.4.0" "pilgram==1.2.1" pyyaml plyfile opencv-python imageio "imageio[ffmpeg]" tyro viser tqdm "torchmetrics[image]" tensorboard tensorly matplotlib splines scikit-learn scipy pycolmap==4.1.1 requests pyparsing python-dateutil markdown protobuf "websockets>=13.1,<17" typing-extensions jinja2 filelock fsspec setuptools wheel sympy networkx mpmath "git+https://github.com/nerfstudio-project/nerfview@4538024fe0d15fd1a0e4d760f3695fc44ca72787" 2>&1 | Tee-Object -FilePath $log -Append

# --- 6. hloc (vendored submodule) ---
Log '=== pip install hloc ==='
& $py -I -m pip install -e (Join-Path $RepoRoot 'Hierarchical-Localization') 2>&1 | Tee-Object -FilePath $log -Append

# --- 7. pycolmap_parser (HEAD + Windows/numpy2/pycolmap4.x patches) ---
Log '=== pip install pycolmap_parser ==='
$parserDir = Join-Path $RepoRoot 'pycolmap_parser-local'
if (-not (Test-Path (Join-Path $parserDir 'pycolmap_parser'))) {
    git clone --depth 1 https://github.com/samusynth/pycolmap_parser $parserDir
}
git -C $parserDir apply (Join-Path $PSScriptRoot 'pycolmap-parser-windows.patch')
& $py -I -m pip install --force-reinstall --no-deps $parserDir 2>&1 | Tee-Object -FilePath $log -Append

# --- 8. MSVC environment for CUDA extension builds ---
$env:DISTUTILS_USE_SDK = '1'
$env:TORCH_EXTENSIONS_DIR = Join-Path $RepoRoot '.torch_extensions'
New-Item -ItemType Directory -Force -Path $env:TORCH_EXTENSIONS_DIR | Out-Null
cmd /c "call `"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        $k = $Matches[1]; $v = $Matches[2]
        if ($k -match '^(PATH|INCLUDE|LIB|LIBPATH|WindowsSdkDir|VSCMD|UCRTVersion|UniversalCRTSdkDir|VSINSTALLDIR)$') {
            [Environment]::SetEnvironmentVariable($k, $v, 'Process')
        }
    }
}
$envPath = Join-Path $env:USERPROFILE ".conda\envs\$CondaEnv"
$env:PATH = "$envPath;$envPath\Scripts;$envPath\Library\bin;$($cudaRoot.FullName)\bin;" + $env:PATH

# --- 9. CUDA extensions: fused-ssim, fused-bilagrid, gsplat fork ---
Log '=== pip install fused-ssim ==='
& $py -I -m pip install "git+https://github.com/rahul-goel/fused-ssim@a7c48d6dd7ac6dc39a7958c7c4452e0b10418f38" --no-build-isolation 2>&1 | Tee-Object -FilePath $log -Append
Log '=== pip install fused-bilagrid ==='
& $py -I -m pip install "git+https://github.com/harry7557558/fused-bilagrid@49f0ef06c9f81810fb9b5dd9027cf1844950cc16" --no-build-isolation 2>&1 | Tee-Object -FilePath $log -Append
Log '=== pip install gsplat fork ==='
& $py -I -m pip install --no-build-isolation (Join-Path $RepoRoot 'gsplat') 2>&1 | Tee-Object -FilePath $log -Append

# --- 10. Smoke test ---
Log '=== smoke test ==='
& $py -I -c "import torch, gsplat, pycolmap, hloc; from gsplat.cuda._backend import _C; import fused_ssim, fused_bilagrid; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); print('ALL OK')" 2>&1 | Tee-Object -FilePath $log -Append
Log '=== SETUP DONE ==='
