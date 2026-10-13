# General pipeline — step 2: selected-product plan and downloads

Requires the step-1 case identity and completed discovery for the same AOI/date.
No existing Trishuli scripts, API or processed outputs are changed.

## Safe initial test

```powershell
python -m pytest tests/test_general_download.py -q --tb=short
python scripts/download_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10
```

Default mode resolves exactly the two pinned names in the public official CDSE
OData catalogue, saves their UUIDs and size estimates, and transfers no scenes.
It sends no credentials. Later planning reuses the recorded product metadata.
A currently offline product may fail at transfer time; no replacement is guessed.

## Explicit transfer (do not run until reviewing sizes and disk space)

```powershell
python scripts/download_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10 --download --transport s3
```

S3 uses private CDSE_S3_ACCESS_KEY/CDSE_S3_SECRET_KEY environment settings;
OData uses CDSE_USERNAME/CDSE_PASSWORD and optional --two-factor. Secrets are
never written into manifests. Existing python-dotenv/boto3 dependencies are
reused. No account key is required for the default planning mode.

Raw products remain in `data/raw/sentinel1/<product-uuid>/`. Different cases
share this product cache. The existing download function verifies hashes of
marked finals, validates unmarked archives, retains interrupted partial data,
and checks ZIP integrity, SAFE metadata and VV/VH raster reads before success.
Locks prevent concurrent appending. A stale crash lock requires confirming the
process stopped before removing it; the runner does not remove another lock.

Per-case plan: `data/runs/case-<digest>/download_plan.json`.
Per-case transfer report: `data/runs/case-<digest>/download_manifest.json`.
The report is complete only after both selected products pass verification.
Reruns call the same product cache and recheck existing files rather than
blindly trusting a previously complete case report. Recorded identities and
the discovery pair hash must agree; corrupt identity records fail closed.

Catalogue sizes are estimates. The default per-product transfer limit is
3 GiB; an explicit finite limit up to 6 GiB is supported. Only two products
can be transferred. Official full SAFE products can still be large: raw
measurement data and calibration/geometry metadata are required for SNAP.
The AOI is not clipped remotely by this stage. Do not describe the download
as an AOI-only raster extract.

This step does not generalize SNAP preprocessing, flood detection, AI or the
API. Those stages still need connecting. Synthetic bytes/metadata used in unit
tests are explicitly test fixtures and are never provided as real outputs.
