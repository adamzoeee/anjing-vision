@echo off
setlocal

cd /d E:\anlingzhijing\anjing-vision\backend

set "REPO_ROOT=E:\anlingzhijing\anjing-vision"
set "VENV_ROOT=%REPO_ROOT%\.venv"
set "CUDA_HOME=E:\CUDA\v12.8"
set "VCVARS64_PATH=D:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TORCH_EXTENSIONS_DIR=%REPO_ROOT%\.torch_extensions"
set "TEMP=%REPO_ROOT%\.tmp"
set "TMP=%REPO_ROOT%\.tmp"

if not exist "%TORCH_EXTENSIONS_DIR%" mkdir "%TORCH_EXTENSIONS_DIR%"
if not exist "%TEMP%" mkdir "%TEMP%"

if not exist "%VCVARS64_PATH%" (
  echo [3DGS Worker] MSVC setup script missing: %VCVARS64_PATH%
  exit /b 1
)
call "%VCVARS64_PATH%" >nul
if errorlevel 1 (
  echo [3DGS Worker] Failed to initialize Visual Studio 2022 x64 environment
  exit /b 1
)

set "PATH=%VENV_ROOT%\Scripts;%CUDA_HOME%\bin;%CUDA_HOME%\lib\x64;%PATH%"

echo [3DGS Worker] Python: %VENV_ROOT%\python.exe
where cl || exit /b 1
where nvcc || exit /b 1
where ninja || exit /b 1

"%VENV_ROOT%\python.exe" -c "from pipeline.trainer import ensure_3dgs_runtime; ensure_3dgs_runtime(); from gsplat.cuda._backend import _C; assert _C is not None; print('[3DGS Worker] gsplat CUDA backend: OK')"
if errorlevel 1 (
  echo [3DGS Worker] Runtime preflight failed; Celery was not started
  exit /b 1
)

"%VENV_ROOT%\python.exe" -m celery -A app.tasks.celery_app worker --pool=solo --loglevel=info
