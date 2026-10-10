# Run from the repository root after installing requirements-baseline.txt.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $baselinePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (!(Test-Path $baselinePython)) { throw 'Create the project .venv first.' }
    & $baselinePython -m unittest discover -s tests -p test_flood_baseline.py -v
    if ($LASTEXITCODE -ne 0) { throw 'Baseline tests failed; processing was not started.' }
    & $baselinePython scripts\run_flood_baseline.py
    if ($LASTEXITCODE -ne 0) { throw 'Baseline processing failed; inspect the error and baseline_manifest.json.' }
    & $baselinePython scripts\check_flood_baseline.py
    if ($LASTEXITCODE -ne 0) { throw 'Baseline validation failed; do not use the outputs.' }
    Write-Host 'Trishuli baseline complete. Previews: data\processed\trishuli\baseline\previews'
} finally {
    Pop-Location
}
