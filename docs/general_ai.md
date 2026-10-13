# General-case experimental AI

The new pipeline accepts baseline or experimental_ai for a bounded supported polygon/event day. Suitable Sentinel-1 scenes, compatible source pairs and completed verified physical preprocessing are still required. Missing scene downloads remain disabled unless explicitly enabled. No training, threshold adjustment, synthetic imagery or fallback to a different requested method occurs.

## Input path

Physical 20 m Gamma0 outputs are not fed to the model. Their successful SNAP source graphs are transformed into the same separate 10 m linear Sigma0 branch already used for the Trishuli pilot. Lee Sigma settings and model transform remain fixed. The projected CRS derives from the physical case, not a fixed UTM zone. Pre/post Sigma0 grids must match the doubled physical grid, and the inherited physical observability is clipped at the new grid's AOI cell centers. Physical unknown terrain can never become AI observable terrain.

Case/date fingerprint, source pair, raw hashes, physical processing-manifest hash, input/output hashes and checkpoint hash remain distinct and checked. Completed general Sigma0/inference corruption stops execution; existing different completed outputs are not silently regenerated. The 10 m grid is limited to four million pixels, including its bounding grid; a smaller area may be required even under the 300 km² baseline limit. Separate AI pipeline/preprocessing locks prevent overlapping writes. Preserve lock files when processes are still active.

## First laptop test from existing physical inputs

From TRINETRA-shared, with the existing Python environment:

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
python scripts/run_general_ai.py --manifest data/processed/case-435fb0dcca0c2834/processing_manifest.json --checkpoint "$original\artifacts\models\siamese_v1\pilot\best.pt" --run-config "$original\artifacts\models\siamese_v1\pilot\run_config.json"
```

This builds new ai_sigma0_10m and ai_flood_experimental folders inside that case. Physical/baseline outputs and model files are read-only inputs. It transfers no new Sentinel scene. SNAP can still fetch uncached DEM/orbit files. Existing Sigma0 graph outputs can be reused after checks. The stage trace is data/runs/case-435fb0dcca0c2834/ai_pipeline_manifest.json. Confirm complete_experimental in inference_manifest.json; a satellite_ready trace is not a completed infrastructure report.

## API/browser

Before starting the backend, point it to the already trained model:

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
$env:TRINETRA_AI_CHECKPOINT = "$original\artifacts\models\siamese_v1\pilot\best.pt"
$env:TRINETRA_AI_RUN_CONFIG = "$original\artifacts\models\siamese_v1\pilot\run_config.json"
$env:SNAP_GPT = 'C:\Program Files\esa-snap\bin\gpt.exe'
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The API also loads project-root .env without overriding these variables. Use New area/date, draw a bounded polygon, select a supported historical event day and Experimental AI — frozen pilot. Keep downloads disabled when scenes are already cached. New AI requests use /api/v1/pipeline-jobs; saved legacy Trishuli AI still uses the existing cached endpoint. Stages include AI_PREPROCESSING and INFERENCE, followed by OSM/access/report. The selected AI preview is separate from the baseline. Before changing pipeline source, wait for active jobs to finish and stop the backend.

## Limits

Code tests cover routing, identities, physical mask inheritance, grid/unknown semantics, prepare-only graph generation, cache integrity and reports. They do not verify new live SNAP runs or numerical model results on a new area. This environment lacks PyTorch; run the existing inference/model tests on the laptop. Fresh general AI execution remains pending until that test passes.

This is the unchanged pilot, with 104 training patches and 2 selection-validation patches. The separate four-patch/two-event test subset achieved IoU 0.3194, with substantial event variation. No domain compatibility, Himalayan accuracy, calibrated flood probability, HAND screen, debris detection, Sentinel-2 confirmation or improved routing is added. A successful general run demonstrates execution, not model quality. Candidate intersections never establish confirmed damage or safe routes.
