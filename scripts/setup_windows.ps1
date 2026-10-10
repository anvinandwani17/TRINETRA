# Run from PowerShell: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
# Uses the project's interpreter directly; activation and administrator rights are unnecessary.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Command, [string[]]$ArgumentList)
    & $Command @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Command. Setup stopped; no success claimed."
    }
}

Push-Location $ProjectRoot
try {
    $Launcher = (Get-Command py.exe -ErrorAction Stop).Source
    $Node = (Get-Command node.exe -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    Invoke-Checked -Command $Launcher -ArgumentList @("-3.12", "-c", "import sys,struct; assert sys.version_info[:2] == (3,12); assert struct.calcsize('P') == 8; print('Python 3.12 x64:', sys.executable)")
    Invoke-Checked -Command $Node -ArgumentList @("-e", "const [a,b]=process.versions.node.split('.').map(Number); if(a<22 || (a===22 && b<12)) { console.error('Install Node.js 22.12+ x64 with npm'); process.exit(1); } console.log('Node:',process.version)")

    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Invoke-Checked -Command $Launcher -ArgumentList @("-3.12", "-m", "venv", ".venv")
    }
    Invoke-Checked -Command $VenvPython -ArgumentList @("-c", "import sys,struct; assert sys.version_info[:2] == (3,12), 'Existing .venv uses a different Python; rename it and rerun'; assert struct.calcsize('P') == 8")
    Invoke-Checked -Command $VenvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
    # Local package is editable; all index-resolved dependencies must be wheels.
    Invoke-Checked -Command $VenvPython -ArgumentList @("-m", "pip", "install", "--only-binary=:all:", "-r", "requirements-anvi.txt")
    Invoke-Checked -Command $VenvPython -ArgumentList @("-m", "pip", "install", "-r", "requirements-ml-cpu.txt")
    Invoke-Checked -Command $VenvPython -ArgumentList @("-m", "pip", "check")

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
    }
    Push-Location (Join-Path $ProjectRoot "web")
    try {
        if (Test-Path "package-lock.json") {
            Invoke-Checked -Command $Npm -ArgumentList @("ci", "--no-audit", "--no-fund")
        } else {
            Invoke-Checked -Command $Npm -ArgumentList @("install", "--no-audit", "--no-fund")
        }
    } finally {
        Pop-Location
    }

    Invoke-Checked -Command $VenvPython -ArgumentList @("-m", "pytest")
    Invoke-Checked -Command $VenvPython -ArgumentList @("scripts\smoke_env.py", "--report", "artifacts\reports\environment-smoke.json")
    # Save resolved package versions for debugging; not a cross-platform lockfile.
    $Freeze = & $VenvPython -m pip freeze
    if ($LASTEXITCODE -ne 0) { throw "Could not record Python package versions." }
    $Freeze | Set-Content -Encoding UTF8 "artifacts\reports\python-packages.txt"
    Write-Host "TRINETRA environment smoke tests passed on this computer."
    Write-Host "No datasets downloaded, no training, and no satellite pipeline started."
} finally {
    Pop-Location
}
