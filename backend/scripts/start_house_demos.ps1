<# Start the two shipped, local-only household demos. No model inference required. #>
param(
    [string]$PythonPath = '',
    [switch]$OpenBrowser
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $PythonPath) {
    foreach ($relative in @('backend/.venv/Scripts/python.exe', '.venv/Scripts/python.exe', 'backend/.venv/python.exe', '.venv/python.exe')) {
        $candidate = Join-Path $repoRoot $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $PythonPath = $candidate; break }
    }
    if (-not $PythonPath) {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command) { $PythonPath = $command.Source }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python 3 not found. Install Python or pass -PythonPath <python.exe>.'
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
& $PythonPath -c 'import sys; assert sys.version_info >= (3, 8), "Python 3.8+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python validation failed.' }

$demos = @(
    @{Port=8095; Directory='backend/data/demos/house_demo_risk_20261013'; Metadata='manifest.json'; Expected='risk-v4'},
    @{Port=8096; Directory='backend/data/demos/house_render_20261014_final'; Metadata='render_spec.json'; Expected='render'}
)
function Test-DemoReady($demo) {
    try {
        $payload = Invoke-RestMethod -Uri "http://127.0.0.1:$($demo.Port)/$($demo.Metadata)" -TimeoutSec 2
        if ($demo.Expected -eq 'render') {
            return $payload.schema_version -eq 1 -and $payload.unit -eq 'm' -and $payload.objects.Count -gt 0
        }
        return $payload.synthetic -eq $true -and $payload.scene_version -eq $demo.Expected
    } catch { return $false }
}

# Validate both shipped packages before starting either server.
foreach ($demo in $demos) {
    $demo.Path = Join-Path $repoRoot $demo.Directory
    foreach ($file in @('index.html', $demo.Metadata, 'three.min.js', 'OrbitControls.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $demo.Path $file) -PathType Leaf)) {
            throw "Demo package missing: $($demo.Directory)/$file. Download the current complete repository."
        }
    }
    if (Get-NetTCPConnection -LocalPort $demo.Port -State Listen -ErrorAction SilentlyContinue) {
        if (-not (Test-DemoReady $demo)) {
            throw "Port $($demo.Port) is occupied by a different service. No process was stopped."
        }
    }
}
$logRoot = Join-Path $repoRoot 'backend/tmp/house-demo-servers'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
foreach ($demo in $demos) {
    if (-not (Test-DemoReady $demo)) {
        $argsList = @('-m', 'http.server', "$($demo.Port)", '--bind', '127.0.0.1', '--directory', ('"' + $demo.Path + '"'))
        $process = Start-Process -FilePath $PythonPath -ArgumentList $argsList -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot "$($demo.Port).log") `
            -RedirectStandardError (Join-Path $logRoot "$($demo.Port).error.log")
        $ready = $false
        for ($attempt = 0; $attempt -lt 12; $attempt++) {
            if (Test-DemoReady $demo) { $ready = $true; break }
            if ($process.HasExited) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "Demo $($demo.Port) did not start. Check $logRoot." }
        Write-Host "READY http://127.0.0.1:$($demo.Port)/ (PID $($process.Id))"
    } else { Write-Host "READY http://127.0.0.1:$($demo.Port)/ (existing service)" }
    if ($OpenBrowser) { Start-Process "http://127.0.0.1:$($demo.Port)/" }
}
Write-Host 'These are local computer addresses, not GitHub-hosted websites. Keep the server processes running.'
