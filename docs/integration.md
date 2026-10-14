# TRINETRA integration

Anvi's `src/trinetra` satellite/ML package and Arnav's root `api`, `network`, `reports`, and `web` share one repository. The active API entry point is `api.main:app`. The integration consumes already-produced Trishuli outputs; it does not automatically process arbitrary new cases.

## Active flow

Select the saved Trishuli AOI/date and a detection method → validate satellite hashes, pair/date, grids and AOI coverage → read candidate and unknown pixels → load bounded pre-event OSM history → intersect infrastructure → model conditional access to mapped medical facilities → display layers → export a grounded report and GeoJSON bundle.

The API uses `api/integrated_jobs.py`, `api/satellite.py`, `network/verified_osm.py`, `network/observed_access.py`, and `reports/grounded.py`. Earlier job/OSM/CLI, probability/landing-zone and report prototypes are retired with explicit errors. Use `scripts/run_integrated_case.py` for the connected CLI; it accepts baseline, experimental_ai, baseline_hand and experimental_ai_hand. `scripts/run_satellite_case.py` remains a separate acquisition/download command. See clean_install.md for the updated setup and offline local asset restore.

## Data contract

Local case files live in `data/processed/trishuli/`: `processing_manifest.json`, `observability.tif`, `baseline/{baseline_manifest.json,flood_mask.tif,evidence_score.tif,flood_candidates.geojson}`, and `ai_flood_experimental/{inference_manifest.json,flood_candidates.tif,flood_score.tif,flood_candidates.geojson}`. Portable lookup uses these relative folders and each manifest's recorded filename/hash, not the original Windows absolute paths.

The API rejects another date, an AOI outside `examples/trishuli_satellite.geojson`, missing files, changed hashes, inconsistent source pairs, inconsistent score/mask grids, invalid classes, and any assessable flood-mask pixel that is unobservable in the original mask.

Baseline and AI layers stay separate. `0` means no candidate evidence, `1` means candidate flood evidence, and `255` means unknown or unsupported. No class certifies dry ground. Gray polygons on the map include the selected method's unsupported terrain. The baseline is much more restrictive than the radar observability screen: only approximately 11.4% of this AOI is assessable by the baseline, while approximately 90.6% passes the original satellite observability screen.

Actual baseline: 151 candidate pixels / 0.0604 km². The uploaded AI export contains 27,868 candidate pixels, including cells at edges inherited from the 20 m mask. Exact clipping to the original AOI's 10 m cell centers yields 27,809 / 2.7809 km² in this integration. These counts are recomputed from unchanged rasters; no threshold tuning or training occurs.

## Historical OSM

The default is the official ohsome v2 GeoParquet extraction API at `https://api.heigit.org/ohsome-api/v2-rc`. It needs a free `OHSOME_API_KEY` in the private root `.env`. Sign up at https://account.heigit.org/. Do not send the key in chat or commit it. `pyarrow` reads the provider's required GeoParquet format; no new database or service is needed.

The default snapshot is the day before the event, bounded by the verified temporal coverage from v2 `/metadata`. In the development check, v1 metadata ended on 2026-07-27T09:00Z and v1 extraction returned HTTP 403; current v2 metadata ended on 2026-10-09T07:25:28Z. These provider checks are dated observations, not fixed production constants. v2 authenticated extraction remains to be verified with the team's key. Tests use labelled unit transport/topology fixtures only; these are never served to the UI.

Queries are restricted to this AOI. Successful raw extracts are cached per endpoint/AOI/filter/snapshot and checked against stored SHA256 identities before reuse. Responses over 50 MiB are rejected. Missing credentials, unavailable service, invalid responses and empty queries do not produce invented infrastructure. Impact counts become unknown where source data or medical destinations are unavailable. An empty OSM query does not establish absence on the ground.

API documentation: https://docs.ohsome.org/ohsome-api/v2/migration_guide.html and https://docs.ohsome.org/ohsome-api/v2/how-to-guides/examples/notebook_extraction.html.

## Connectivity and reporting

Infrastructure intersections are potential impacts. Road edges intersecting candidate evidence are removed only for a conditional scenario. A remaining path through unknown/unsupported terrain cannot certify access. Unknown medical-facility locations are not usable targets. Roads outside the observed AOI remain uncertain. Graph nodes use mapped coordinates, segment distances use a local metric CRS, and settlement/facility snapping is bounded to 500 m.

The result is access to mapped medical facilities within this AOI, not absolute isolation from every destination. Missing map topology, bridges' structural condition, possible routes outside the AOI, and missing infrastructure can change the conclusion. No physical road/bridge closure is verified. Monte Carlo closure probabilities, landing zones, Sentinel-2 availability, and restoration simulation are not provided by this integration; the restoration endpoint returns a clear 501. The grounded report currently supports English and browser printing/PDF. Earlier ungrounded bilingual report text is not reused.

Reports and exports are job-specific and include satellite IDs, timestamps, method, hashes, requested historical OSM snapshot, data availability, and limitations. Unknown counts are not represented as zero. EMSR927 landslide polygons are not treated as water-flood ground truth. No Trishuli or EMSR927 training or threshold tuning occurs.

## Run on Windows

Use your active Python 3.12 virtual environment. From `TRINETRA-shared`:

```powershell
python -m pip install -r requirements-integration.txt
python scripts/check_integration.py
python -m pytest tests -q --tb=short
```

If no private `.env` exists, copy `configs/integration.env.example` to `.env`. If `.env` already exists, add the settings to it without removing existing CDSE/SNAP settings. Add your own `OHSOME_API_KEY` locally. The API loads the project-root `.env` without overriding already-set environment variables.

Terminal 1:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Terminal 2, from `TRINETRA-shared/web`:

```powershell
npm ci
npm run build
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173. Select Saved cases, choose Trishuli and load the saved area. Select baseline and run the analysis with scene downloads unchecked. Inspect candidate and gray unknown layers, availability states, report and ZIP. Repeat with experimental AI or its optional HAND variant only when the corresponding saved assets exist. Without the OSM key, real satellite results still render and infrastructure is explicitly unavailable.

For the connected CLI:

```powershell
python scripts/run_integrated_case.py --method baseline
```

New local job outputs are under `data/runs/integrated/<job_id>/`. Protect this directory, model weights, private `.env`, downloaded scenes and processed rasters from Git. Do not force-push or overwrite Arnav's commits. Commit only reviewed source/config/template/docs files on the shared feature branch after local checks.

## Validation

Integration has been checked against uploaded Trishuli baseline/AI outputs and later laptop custom-case AI and HAND runs. API tests cover hashes, date/AOI rejection, mode separation, masked geometry, safe serialization, report/export isolation, unknown access, empty/missing OSM and GeoParquet cache reuse. The laptop frontend build and selected live API/UI/export runs passed. See evaluation_status.md for their exact scope; clean-checkout setup, broader scene coverage and model accuracy remain separate acceptance checks.
