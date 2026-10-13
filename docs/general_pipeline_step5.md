# General pipeline — step 5: API, map and one-command baseline chain

The baseline path now chains supported AOI/date discovery, selected product
resolution, raw cache/download verification, physical SNAP preprocessing,
deterministic detection, historical OSM, conditional access and a grounded
report. It retains the original saved Trishuli baseline/AI demo.

## First test the already processed case

Run the full Python tests and `npm run build` in web after applying the patch.
Stop the old backend with Ctrl+C, then from TRINETRA-shared with the active
Python virtual environment:

```powershell
$env:SNAP_GPT = 'C:\Program Files\esa-snap\bin\gpt.exe'
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Use a second terminal for the frontend:

```powershell
cd web
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

At http://127.0.0.1:5173 select the saved `case-8e50e3c78e746c36` for
2026-09-10, load it, keep baseline selected and downloads unchecked, then run.
The loader verifies the existing files; it does not rerun SNAP or transfer
scenes. Historical OSM may query/cache the bounded AOI. Missing OSM/key is
reported as unavailable/unknown, never fabricated features.

## New local AOI/date

Draw a Polygon; choose a historical UTC event day. Maximum AOI is 300 km2,
with the existing local extent/vertex/grid constraints. Baseline is supported;
general-case neural inference is not added by this patch. A suitable same-orbit
IW GRDH VV/VH pair with 99% common coverage is required. No suitable pair or
failed source/physical checks results in FAILED with a reason, not a dry map.

To permit missing raw scenes, enable the two-scene download checkbox. Each
scene is bounded to 3 GiB; catalogue estimates and selected names are exposed
in the job. With downloads unchecked, required cached scenes can be reused;
if bytes are missing the pipeline stops before requesting them. Public
metadata may still be queried. SNAP may fetch uncached local DEM/orbit files.

S3 reads the existing private CDSE_S3_ACCESS_KEY/CDSE_S3_SECRET_KEY. OData
uses CDSE_USERNAME/CDSE_PASSWORD; interactive 2FA is not supported in the web
job, so use the standalone downloader for 2FA accounts. OHSOME_API_KEY is the
existing historical infrastructure credential. No credentials are in requests,
responses, frontend code, timing records or export manifests.

SNAP_GPT must resolve to an installed GPT. The original physical operators,
shadow/layover policy and baseline thresholds remain unchanged. Cached files
are hash-checked rather than trusted by existence alone. A corrupted completed
mask fails; the system does not silently replace it or switch methods.

## API and stages

- GET /api/v1/cases: saved-case metadata (integrity checked when analysed).
- POST /api/v1/pipeline-jobs: baseline general pipeline; HTTP 202.
- POST /api/v1/jobs: retained cached-output endpoint, including Trishuli AI.
- Existing status/layers/provenance/report/export endpoints work for both.
- Whitelisted case-preview PNG endpoints expose available generated previews.

Only one satellite pipeline runs at a time, with one queued job allowed.
Stages and measured elapsed times are shown; no invented progress percentage
or processing-time guarantee is supplied. Full satellite runs have not yet
been benchmarked on three fresh AOIs. The browser can follow long jobs; reload
does not cancel a running server job. Saved completed job results can be read
after a restart. Interrupted jobs are marked failed on reload; cached files
remain. Crash locks are not force-removed—confirm old processes stopped.

Per-case satellite trace is `data/runs/case-<digest>/pipeline_manifest.json`.
`satellite_ready` means satellite stages finished, not final infrastructure
completion. API jobs persist status, summary, provenance, GeoJSON layers and
SITREP under `data/runs/integrated/<job-id>/`. Completed full jobs are COMPLETE.
Raw input manifests for newly orchestrated physical runs use immutable hash-
named snapshots; previous manually processed cases retain their original
source provenance.

## CLI and configuration

```powershell
python scripts/run_general_pipeline.py --aoi examples/terai_discovery.geojson --date 2026-09-10
# Explicitly permit selected-scene transfers only when needed:
python scripts/run_general_pipeline.py --aoi examples/terai_discovery.geojson --date 2026-09-10 --allow-downloads --transport s3
```

Private TRINETRA_DATA_DIR can relocate general-case data/results. Existing
TRINETRA_CASE_DIR still overrides the legacy Trishuli saved data location.
VITE_API_URL configures the frontend backend address; see
configs/frontend.env.example. No new frontend dependencies are required.

## Validation and remaining limits

API tests include actual previously provided Trishuli outputs, general-case
synthetic raster/vector fixtures, cache-only operation, missing-case delegation,
strict request validation, queue bounds, persistence, reports/ZIP, and blocked
scene transfers when downloads are disabled. Synthetic fixtures exist only in
tests. Fresh AOI orchestration with real GPT and authenticated services must
still be verified on Anvi's laptop; passing fixtures does not establish flood
accuracy. The original pre/post Trishuli AI inference remains experimental.

This step does not add Sentinel-2, HAND/debris mapping, better town routing,
held-out Himalayan metrics, or delete deprecated prototype files. Those are
separate remaining limitations. A fresh clone still needs local SNAP and
credentials for uncached processing or separately provided saved case assets.
