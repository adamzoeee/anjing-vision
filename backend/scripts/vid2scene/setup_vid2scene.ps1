# One-shot Windows native setup for the vid2scene reconstruction backend.
# Usage (PowerShell 5.1+):
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup_vid2scene.ps1 -RepoRoot D:\vid2scene
#
# Parameters:
#   -RepoRoot <dir>   REQUIRED: where vid2scene source will be cloned
#   -CondaEnv <name>  conda env name                  (default: vid2scene)
#   -SkipClone        skip cloning vid2scene (source already present at -RepoRoot)
#
# Requirements before running: Git, Miniconda (in PATH), ffmpeg (>=5, recommended 9.x),
# NVIDIA GPU driver, CUDA Toolkit 12.8, MSVC Build Tools 2022.
# All log messages are ASCII on purpose (PowerShell 5.1 reads scripts as ANSI).
param(
    [string]$RepoRoot = "",
    [string]$CondaEnv = "vid2scene",
    [switch]$SkipClone
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    throw "Usage: setup_vid2scene.ps1 -RepoRoot <dir> [-CondaEnv vid2scene] [-SkipClone]"
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction SilentlyContinue).Path
if (-not $RepoRoot) {
    New-Item -ItemType Directory -Force -Path $RepoRoot | Out-Null
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}

$log = Join-Path $RepoRoot 'setup_vid2scene.log'
Remove-Item $log -ErrorAction SilentlyContinue
function Log($m) { Write-Host $m; $m | Out-File -FilePath $log -Append -Encoding utf8 }

function Step([scriptblock]$block, [string]$name) {
    Log "=== $name ==="
    & $block
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: $name (exit $LASTEXITCODE), see $log" }
}

# --- 0. Clone vid2scene source + submodules + Windows patches ---
if (-not $SkipClone) {
    if (-not (Test-Path (Join-Path $RepoRoot 'vid2scene_core'))) {
        Step { git clone https://github.com/samuelm2/vid2scene.git $RepoRoot } "git clone vid2scene"
    } else {
        Log "vid2scene source already present, skip clone"
    }
    Step { git -C $RepoRoot submodule update --init gsplat glomap Hierarchical-Localization spz } "submodule update"
    Step { git -C $RepoRoot submodule update --init --recursive -- gsplat } "gsplat glm submodule"
    # Windows patches (idempotent: skip if already applied)
    foreach ($pair in @(
        @("$PSScriptRoot\vid2scene-core-windows.patch", $RepoRoot),
        @("$PSScriptRoot\sharp-frame-filter-windows.patch", $RepoRoot),
        @("$PSScriptRoot\gsplat-windows.patch", (Join-Path $RepoRoot 'gsplat'))
    )) {
        $patch, $target = $pair
        if (-not (Test-Path $patch)) { throw "patch not found: $patch" }
        git -C $target apply --check $patch 2>$null
        if ($LASTEXITCODE -eq 0) {
            Step { git -C $target apply $patch } "apply patch $(Split-Path $patch -Leaf)"
        } else {
            Log "patch already applied or not applicable: $(Split-Path $patch -Leaf)"
        }
    }
}

# --- 1. Locate CUDA Toolkit ---
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

# --- 2. Locate MSVC ---
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

# --- 3. Conda env: python 3.12 + COLMAP CLI (fail on error, retry once) ---
$condaCreated = $false
for ($attempt = 1; $attempt -le 2 -and -not $condaCreated; $attempt++) {
    Log "=== conda create (attempt $attempt) ==="
    conda create -y -n $CondaEnv python=3.12 pip 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -eq 0) { $condaCreated = $true; break }
    Log "conda create failed (exit $LASTEXITCODE); retrying after channel fallback ..."
    conda config --set channel_priority flexible 2>&1 | Out-Null
}
if (-not $condaCreated) { throw "conda create failed twice; check network / conda mirrors, see $log" }
Step { conda install -y -n $CondaEnv -c conda-forge colmap=4.1.1 } "conda install colmap"

$py = Join-Path $env:USERPROFILE ".conda\envs\$CondaEnv\python.exe"
if (-not (Test-Path $py)) {
    $base = (& conda info --base 2>$null).Trim()
    $py = Join-Path $base "envs\$CondaEnv\python.exe"
}
if (-not (Test-Path $py)) { throw "conda env python not found: $py" }
Log "conda env python = $py"

$env:PYTHONPATH = ''
$env:PYTHONHOME = ''
$env:PYTHONUTF8 = '1'

# --- 4. PyTorch cu128 (RTX 50 / sm_120 needs torch >= 2.7) ---
Step { & $py -I -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128 } "pip install torch"

# --- 5. Python deps ---
Step {
    & $py -I -m pip install "numpy==2.4.3" ninja jaxtyping "rich>=13.3.3,<15" "watchdog==5.0.3" "pillow==10.4.0" "pilgram==1.2.1" pyyaml plyfile opencv-python imageio "imageio[ffmpeg]" tyro viser tqdm "torchmetrics[image]" tensorboard tensorly matplotlib splines scikit-learn scipy pycolmap==4.1.1 requests pyparsing python-dateutil markdown protobuf "websockets>=13.1,<17" typing-extensions jinja2 filelock fsspec setuptools wheel sympy networkx mpmath pupil-apriltags "git+https://github.com/nerfstudio-project/nerfview@4538024fe0d15fd1a0e4d760f3695fc44ca72787"
} "pip install python deps"

# --- 6. hloc (vendored submodule) ---
Step { & $py -I -m pip install -e (Join-Path $RepoRoot 'Hierarchical-Localization') } "pip install hloc"

# --- 7. pycolmap_parser (HEAD + Windows patches) ---
$parserDir = Join-Path $RepoRoot 'pycolmap_parser-local'
if (-not (Test-Path (Join-Path $parserDir 'pycolmap_parser'))) {
    Step { git clone --depth 1 https://github.com/samusynth/pycolmap_parser $parserDir } "clone pycolmap_parser"
}
git -C $parserDir apply --check (Join-Path $PSScriptRoot 'pycolmap-parser-windows.patch') 2>$null
if ($LASTEXITCODE -eq 0) {
    Step { git -C $parserDir apply (Join-Path $PSScriptRoot 'pycolmap-parser-windows.patch') } "apply pycolmap_parser patch"
} else {
    Log "pycolmap_parser patch already applied"
}
Step { & $py -I -m pip install --force-reinstall --no-deps $parserDir } "pip install pycolmap_parser"

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
if (-not (Test-Path $envPath)) {
    $base = (& conda info --base 2>$null).Trim()
    $envPath = Join-Path $base "envs\$CondaEnv"
}
$env:PATH = "$envPath;$envPath\Scripts;$envPath\Library\bin;$($cudaRoot.FullName)\bin;" + $env:PATH

# --- 9. CUDA extensions: fused-ssim, fused-bilagrid, gsplat fork ---
Step { & $py -I -m pip install "git+https://github.com/rahul-goel/fused-ssim@a7c48d6dd7ac6dc39a7958c7c4452e0b10418f38" --no-build-isolation } "pip install fused-ssim"
Step { & $py -I -m pip install "git+https://github.com/harry7557558/fused-bilagrid@49f0ef06c9f81810fb9b5dd9027cf1844950cc16" --no-build-isolation } "pip install fused-bilagrid"
Step { & $py -I -m pip install --no-build-isolation (Join-Path $RepoRoot 'gsplat') } "pip install gsplat fork"

# --- 10. Smoke test ---
Step { & $py -I -c "import torch, gsplat, pycolmap, hloc; from gsplat.cuda._backend import _C; import fused_ssim, fused_bilagrid; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); print('ALL OK')" } "smoke test"

Log "=== SETUP DONE ==="
Log "Next steps:"
Log "  1) backend: pip install -r requirements.txt  (torch cu128: --index-url https://download.pytorch.org/whl/cu128)"
Log "  2) backend: python scripts/download_models.py  (SAM checkpoint ~2.4GB)"
Log "  3) backend: copy .env.example to .env and set VID2SCENE_PYTHON=$py"
Log "  4) backend: python scripts/smoke_reconstruction.py <video.mp4> <outdir>"
