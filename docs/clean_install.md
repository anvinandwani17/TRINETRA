# Clean-checkout verification

Use a separate checkout after committing this patch. Do not replace the working
project's environment or copy its private .env into a submitted archive.

## Windows prerequisites and setup

The setup script uses Python Launcher with Python 3.12 x64 and Node.js 22.12+
with npm. It creates or reuses the checkout's .venv, installs
requirements-integration.txt and optionally CPU PyTorch, runs pip check,
builds the frontend, checks the active API and runs selected offline tests.
Existing .env is preserved; a missing one is copied from
configs/integration.env.example with blank keys. No scene downloads/training run.

```powershell
powershell -NoProfile -File .\scripts\setup_windows.ps1
# Include the optional frozen-model runtime:
# powershell -NoProfile -File .\scripts\setup_windows.ps1 -IncludeAI
```

Outputs are artifacts/reports/clean-install.json and python-packages.txt.
The Python smoke report verifies imports and api.main:app through TestClient.
The separate setup frontend step runs npm ci and npm run build. Passing this
does not prove assets can be restored or a satellite case can be processed.
The older scripts/smoke_env.py remains a scaffold/developer diagnostic and is
not the integrated-app clean-install acceptance check.

## Actual server check

From the checkout, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, verify /health and start the UI:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$env:VITE_API_URL = 'http://127.0.0.1:8000'
npm.cmd --prefix web run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Port 8000 must be free; stop the old project's backend before this check.
Stop its frontend before binding the new checkout to port 5173.
Application root / on port 8000 is not a UI route; use port 5173.

## Saved-case acceptance

Restore verified assets with scripts/release_assets.py and the manifest's
hash checks. Public v0.2.0 asset downloads were not verified and remain paused;
do not advertise a working download until the files exist as actual release
assets. A locally retained bundle can be restored with the new --bundle-file
option and the same manifest/checksum/conflict guards:

```powershell
.\.venv\Scripts\python.exe scripts/release_assets.py fetch `
    --manifest C:\path\to\assets_manifest.json `
    --bundle-file C:\path\to\TRINETRA_case_assets.zip --repo .
```

Replace those two paths with the actual saved release files. This local restore
does not certify public GitHub asset delivery. Consult docs/reproducibility.md.

Configure the checkpoint/run config and source-case DEM paths separately when
testing AI/HAND. They are not supplied by pip installation. Provide a private
OHSOME_API_KEY for live infrastructure; missing keys yield unknown/unavailable.
Run a restored saved case with downloads disabled, inspect method/case/date,
unknown coverage, report and export. This is distinct from a fresh SNAP run.

## Retirement and limitations

Legacy network.ohsome, network.monte_carlo, network.landing_zones and
reports.generator now raise explicit migration/unavailable errors. They cannot
fabricate infrastructure, calibrated probabilities, landing suitability or
default-filled reports. api.jobs and scripts/run_case.py were already retired.
Use network.verified_osm, network.observed_access and reports.grounded via the
active integrated API. The older graph/isolation/normalization helpers and
report schema remain as legacy utilities; they are not active pipeline results.

TRINETRA project code is licensed under MIT; see the repository LICENSE. Third-party satellite/OSM data, datasets, model assets and dependencies remain subject to their own terms. The code license does not authorize redistribution of those assets. Broader flood validation,
HAND coverage/accuracy, Sentinel-2, debris and corridor/routing extensions
remain pending. Do not describe retired prototypes as completed capabilities.
