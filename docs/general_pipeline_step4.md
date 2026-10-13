# General pipeline — step 4: deterministic open-water baseline

Requires completed general-case discovery, download and physical preprocessing.
This change generalizes input/output handling and independent output checks.
The detector in baseline.py and all thresholds/filtering rules are unchanged.

```powershell
python -m pytest tests -q --tb=short
python scripts/run_general_baseline.py --aoi examples/terai_discovery.geojson --date 2026-09-10
```

The runner takes no product IDs or threshold-tuning options. It verifies the
processing case against pinned discovery. The raster loader verifies recorded
AOI/date identity, quality checks, hashes and the exact AOI-derived metric
20 m grid. General cases can use northern or southern UTM zones. Legacy
Trishuli inputs retain their UTM45N grid checks and default locations.

Terrain comes from the already processed, hash-verified pre/post SNAP DEM
cache. No new scenes, terrain datasets, neural model or training are requested.
The detector still requires dark post-event backscatter in VV and VH, a
significant decrease, adequate filter support, slope <=10 degrees, exclusion
of pre-existing dark pixels, and minimum connected area 2,000 m2.

Outputs go only to `data/processed/case-<digest>/baseline/`:

- baseline_manifest.json: case/pair/config, hashes, summary and checks.
- flood_mask.tif: 0=no candidate evidence; 1=candidate; 255=unknown/outside.
- evidence_score.tif: uncalibrated heuristic, never a probability.
- flood_candidates.geojson: candidate polygons in WGS84.
- slope_deg.tif, reasons.tif and filtered before/after/change rasters.
- previews/flood_mask.png, evidence_score.png and slope_deg.png.

Outputs are marked complete only after independent hash, raster/vector,
backscatter-rule, terrain and unknown-pixel checks pass. Shadows and other
unobservable pixels remain 255/NaN, not dry. The report distinguishes unknown
AOI pixels from full-grid unknown pixels, which include outside-AOI cells.

Zero candidates is a valid result if assessable pixels exist. This example
date is a software test, not a verified flood event; do not tune thresholds
to produce a positive result. Baseline accuracy on this case is unmeasured.
High slopes remain unsupported; HAND, debris detection and calibration are
separate future improvements. General AI and dashboard wiring are not added
by this step. Existing Trishuli output files are not touched by the installer.
