# From a clean checkout: powershell -NoProfile -File .\scripts\setup_windows.ps1
# Optional frozen-model runtime: add -IncludeAI. No admin rights or activation needed.
[CmdletBinding()]
param([switch]$IncludeAI)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Command, [string[]]$ArgumentList)
    & $Command @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Command" }
}

Push-Location $ProjectRoot
try {
    $Launcher = (Get-Command py.exe -ErrorAction Stop).Source
    $Node = (Get-Command node.exe -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    Invoke-Checked $Launcher @('-3.12', '-c', "import sys,struct; assert sys.version_info[:2] == (3,12); assert struct.calcsize('P') == 8")
    Invoke-Checked $Node @('-e', "const [a,b]=process.versions.node.split('.').map(Number); if(a<22 || (a===22 && b<12)) { console.error('Setup requires Node.js 22.12+'); process.exit(1); }")

    $VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $VenvPython)) {
        Invoke-Checked $Launcher @('-3.12', '-m', 'venv', '.venv')
    }
    Invoke-Checked $VenvPython @('-c', "import sys,struct; assert sys.version_info[:2] == (3,12), 'Existing .venv uses a different Python'; assert struct.calcsize('P') == 8")
    Invoke-Checked $VenvPython @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
    Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-r', 'requirements-integration.txt')
    if ($IncludeAI) {
        Invoke-Checked $VenvPython @('-m', 'pip', 'install', 'torch>=2.6,<3', '--index-url', 'https://download.pytorch.org/whl/cpu')
    }
    Invoke-Checked $VenvPython @('-m', 'pip', 'check')
    if (-not (Test-Path '.env')) {
        Copy-Item 'configs/integration.env.example' '.env'
    }

    Push-Location (Join-Path $ProjectRoot 'web')
    try {
        Invoke-Checked $Npm @('ci', '--no-audit', '--no-fund')
        Invoke-Checked $Npm @('run', 'build')
    } finally { Pop-Location }

    $CheckArgs = @('scripts/check_clean_install.py', '--report', 'artifacts/reports/clean-install.json')
    if ($IncludeAI) { $CheckArgs += '--require-ai' }
    Invoke-Checked $VenvPython $CheckArgs
    Invoke-Checked $VenvPython @('-m', 'pytest', 'tests/test_api.py', 'tests/test_legacy_retirement.py', 'tests/test_remaining_retirement.py', 'tests/test_submission_reports.py', 'tests/test_general_jobs.py', '-q', '--tb=short')
    $Freeze = & $VenvPython -m pip freeze
    if ($LASTEXITCODE -ne 0) { throw 'Could not record installed package versions.' }
    $Freeze | Set-Content -Encoding UTF8 'artifacts/reports/python-packages.txt'
    Write-Host 'Integrated API startup, selected offline tests and frontend build passed.'
    Write-Host 'Saved assets, actual Windows server startup, SNAP processing and model accuracy need separate verification.'
} finally { Pop-Location }
