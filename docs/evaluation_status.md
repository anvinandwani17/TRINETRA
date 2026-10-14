# Evaluation and remaining work — 14 October 2026

This records observed execution and previously measured test results. It is not
a fresh flood-accuracy evaluation, a completion percentage, or a revised judge score.

## Frozen pilot accuracy evidence

- Training: 104 patches from 15 events; checkpoint selection: 2 validation patches
  from 2 events. The selection-validation results are not independent test evidence.
- Separate official-test subset: 4 patches from 2 events; IoU 0.3194 and precision
  approximately 0.384, as recorded in the prior evaluation. It is not the full test
  split and is too small to establish broad performance.
- No new threshold tuning, retraining or label-based accuracy computation was done
  during the HAND or cleanup work. Scores remain uncalibrated.
- EMSR927 Bidur labels represent landslides/mass movement, not water-flood ground
  truth. Do not use them to claim flood IoU or precision.

## Execution verified on the laptop

General AI: custom case `case-435fb0dcca0c2834`, requested 13 September 2026,
observations 5 → 17 September. Sigma0 processing, frozen inference, integrated
job/export and corrected report were verified. Zero candidates in that case do
not establish absence of flooding. This verifies one area/date execution only.

HAND: original Trishuli AI has 27,868 candidates, including 16,765 (60.2%) on
slopes ≥30°. Optional HAND retains 6,719 candidates, including 125 on those slopes.
The baseline HAND variant has 154 candidates versus the original baseline's 151;
it replaces the baseline slope cap, so it is not just an intersection of masks.
Neither comparison measures false-positive or true-positive rates.

Job `93ca6f0a0f14` was independently checked from the uploaded export:

| Export field | Observed value |
|---|---:|
| Method | experimental_ai_hand |
| AOI-clipped candidate pixels | 6,702 |
| Candidate area | 0.6702 km² |
| Assessable fraction | 6.464% |
| Unknown area | 102.0056 km² |
| Candidate-intersecting roads / bridges / buildings | 26 / 5 / 84 |
| Settlements with unknown access | 12 of 12 |

The full saved raster has 6,719 candidates; requested-polygon clipping accounts
for the exported 6,702. Summary, report and infrastructure feature counts agree.
ZIP integrity passed. Recorded hashes identify source rasters/checkpoint; the
export does not contain those rasters or weights, so this export check does not
independently rehash them. Source pair: 16 → 28 August, event date 26 August 2026.

HAND parameters are provisional: 60 m routing grid, 1 km² drainage initiation,
10 m height ceiling. Missing/nonpositive DEM and rejected terrain stay unknown.
The screen does not model water depth, debris or pluvial flooding. Approximately
6.5% assessability does not resolve the request for broader assessable coverage.

## Cleanup scope

The uploaded source audit found fabricated geometric fallbacks in the unused
`api/jobs.py` and `scripts/run_case.py` prototypes. Both are retired and now fail
before analysis or cache reuse. The active API already uses `api.integrated_jobs`.
Old cached outputs are preserved and are not migrated into verified results.
The follow-up archive supplied root network/report modules and integration
requirements/configs. The old network.ohsome synthesizer, uncalibrated Monte
Carlo and landing-zone modules, and reports.generator are now retired with
explicit errors. Active verified_osm/observed_access/grounded code is retained.
The Windows setup now targets integration requirements and active API health.
Other root assets and third-party redistribution terms are not certified here.

## Remaining work, in order

1. Commit the MIT license and matching documentation updates.
2. Fresh Windows setup passed 28 selected offline tests and the frontend build.
   Local bundle hashes/restoration and actual backend startup passed. Job
   b4fe3df7fdbb reproduced the earlier AI raster hashes and 27,809 AOI-clipped
   candidates. Its blank OSM key caused infrastructure to remain unavailable.
   Live infrastructure reproduction and public release-asset delivery remain
   unresolved; public uploads are currently paused.
3. MIT is selected for project code (LICENSE); verify third-party asset redistribution terms.
4. Expand independent flood-label coverage with fixed thresholds and per-event
   metrics; compare baseline, AI and HAND on the same labelled pixels while
   reporting abstention/coverage. No such accuracy comparison is complete yet.
5. Complete EMSR927 corridor analysis as landslide context, broader town/buffer
   connectivity and snapping sensitivity. Sentinel-2, debris classification and
   multilingual reports remain separate extensions.

Tests/build/integration evidence and accuracy evidence must remain distinct.
