# TRINETRA

Sentinel-1 flood evidence, terrain observability, mapped infrastructure and conditional settlement access for the Multimodal AI Hackathon 2026 Track B.

> **Candidate evidence only.** Outputs are suspected-flood candidates and conditional access scenarios, not confirmed damage, safe routes or landing zones. **Unknown terrain is not dry terrain.**

Anvi owns the satellite/flood-AI Python package in `src/trinetra`. Arnav owns the React/API/infrastructure side in `web`, `api`, `network` and `reports`. The active integrated API is **`api.main:app`**.

## What it does

Pipeline: discovery → selected-scene download/cache → physical SNAP preprocessing → deterministic baseline and/or experimental AI → optional HAND screening → historical OSM exposure → conditional access → interactive map → exportable situation report.

- AOIs are bounded to **300 km²** and need a suitable same-orbit Sentinel-1 pre/post pair.
- Downloads are **off by default**. Cached scenes are hash-verified and reused; missing scenes need the explicit download option.
- The pipeline never trains a model.
- The saved **Trishuli / 26 August 2026** case and one separate custom area/date run were completed and exported. Broader place/date coverage and accuracy are unvalidated.

## Choose your path

| Goal | What you need | Time |
|---|---|---|
| **A. Inspect the saved Trishuli case** (recommended for reviewers) | Python, Node, `OHSOME_API_KEY`, the saved case asset bundle | ~10 min |
| **B. Run a new AOI/date** | Everything in A, plus Copernicus Data Space credentials and ESA SNAP | 30+ min, depends on downloads |

> **Important:** large case assets (raw scenes, rasters, model weights) are **not in Git**. A source clone alone cannot load the saved case. See [Case assets](#case-assets) before step 4.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 | backend (all paths) |
| Node.js | 20.19+ or 22.12+ | frontend (Vite) |
| ESA SNAP (GPT) | current release | Path B only (new physical preprocessing) |
| ohsome API key | free, <https://account.heigit.org/> | infrastructure extraction |
| Copernicus Data Space account | free | Path B only (new downloads) |

Displaying existing outputs runs on CPU. No GPU and no new SNAP run are needed for Path A.

## Quick start (Windows PowerShell)

### 1. Clone and create an environment

```powershell
git clone https://github.com/anvinandwani17/TRINETRA.git
cd TRINETRA
git checkout feature/general-pipeline   # branch used for the submission
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-integration.txt
```

### 2. Configure credentials

```powershell
Copy-Item configs\integration.env.example .env
```

Edit `.env` (keep it private, never commit it):

```
OHSOME_API_KEY=your_key_here
# Path B only:
# Copernicus Data Space credentials and optional CDSE S3 keys,
# using the variable names given in configs/integration.env.example
```

A missing key or a source failure yields *unavailable/unknown* infrastructure. Roads and settlements are never invented.

### 3. Set the SNAP path (Path B only)

```powershell
$env:SNAP_GPT = "C:\Program Files\esa-snap\bin\gpt.exe"
```

This applies to the current terminal only. Set it again in each new session, or add it to `.env` if your configuration reads it from there.

### 4. Case assets

Place the saved Trishuli asset bundle in the locations described in [docs/integration.md](docs/integration.md) (and [docs/reproducibility.md](docs/reproducibility.md) for hash-checked restoration).

> TODO before submission: add the public download link/release tag for the asset bundle here, plus the expected SHA-256 of the archive. Public large-asset delivery is currently unresolved.

### 5. Verify the install

```powershell
python -m pytest tests -q --tb=short
```

If tests that depend on case assets fail on a fresh clone, check step 4 first.

### 6. Start the backend (terminal 1, venv active)

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Sanity check: <http://127.0.0.1:8000/docs> shows FastAPI's interactive docs (default behaviour).

### 7. Start the frontend (terminal 2, from the repository root)

```powershell
cd web
npm ci
npm run build
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

`--strictPort` makes Vite fail instead of silently switching ports. If 5173 is busy, free it or pick another port consistently.

### 8. Use the app

Open <http://127.0.0.1:5173>.

1. Select the saved **Trishuli** case and click **Load selected saved area**.
2. Choose the verified method (**experimental AI + HAND** for the submitted export; baseline is the conservative default).
3. Leave **scene downloads unchecked**.
4. Inspect acquisition dates, unknown terrain, the situation report and the results ZIP.

For a new area (Path B): draw a Polygon, choose a historical UTC day, and tick the download option only if scenes are missing. Run stages, elapsed times, source availability, preview links and exports are shown in the UI.

**Command-line alternative:**

```powershell
python scripts/run_general_pipeline.py --aoi examples/terai_discovery.geojson --date 2026-09-10
# add --allow-downloads only for missing raw scenes
```

`scripts/run_integrated_case.py` runs saved cases and accepts all four saved-case methods.

## macOS / Linux

Commands are equivalent; only activation and environment variables differ:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-integration.txt
cp configs/integration.env.example .env
export SNAP_GPT=/path/to/esa-snap/bin/gpt      # Path B only
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
# second terminal
cd web && npm ci && npm run build && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

The submission was verified on Windows. Other platforms are untested.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Saved case missing from the list or fails to load | Case assets are not in Git. Restore the bundle (step 4) and check hashes. |
| Infrastructure layers empty or "unavailable" | `OHSOME_API_KEY` missing or invalid in `.env`. This is the designed fallback, not a crash. |
| Download refused or "scene not cached" | Downloads are off by default. Tick the download option and provide Copernicus credentials. |
| SNAP/GPT not found | `SNAP_GPT` not set in this terminal, or it points to the wrong path. |
| `npm ci` or Vite fails | Node is older than 20.19 / 22.12. Upgrade Node. |
| Vite exits with "port in use" | Another process holds 5173. Stop it, or choose another port. |
| Frontend loads but shows API errors | Backend not running on `127.0.0.1:8000`. Check terminal 1. |
| Experimental AI unavailable | Needs the frozen checkpoint and run config. General-case AI also needs separate Sigma0 preprocessing. |
| AI preview missing | Intentional: an unavailable preview is omitted, never replaced by baseline imagery. |

## Interpretation and limits

- Results are candidate detections. Intersections with mapped roads, bridges and buildings prioritise inspection; they do not establish damage.
- Unknown or unsupported pixels remain unknown. In the Trishuli export only 6.464% of the requested area is assessable by the selected method.
- Medical-facility access is a bounded graph proxy, not absolute isolation or a safe route.
- Scores are uncalibrated. HAND thresholds are provisional, and candidate reduction under HAND is not validated accuracy.
- No debris/landslide/glacier-collapse classifier, Sentinel-2 confirmation, landing-zone recommendation or short-term collapse prediction is provided.
- EMSR927 comparison is **incomplete**. Bidur reference labels describe landslide/mass-movement context and are not water-flood ground truth or training/tuning input.

## Evidence and reproducibility

Read [methodology](docs/methodology.md), [evaluation status](docs/evaluation_status.md), [source attribution](docs/ATTRIBUTION.md) and [reproducibility setup](docs/reproducibility.md).

**Model comparison (same 13 held-out patches from 6 events, fixed threshold 0.5, no threshold tuning):**

| Measure | Frozen pilot | New candidate |
|---|---|---|
| Pixel IoU | 18.87% | 25.05% |
| Pixel F1 | 31.75% | 40.07% |
| Precision / recall | 21.62% / 59.75% | 29.29% / 63.37% |
| Macro-event IoU | 10.92% | 12.40% |
| False-positive pixels | 21,144 | 14,932 |

The candidate was trained on 173 patches / 27 events and validated on 12 patches / 6 events (validation IoU is not independent test performance). The test sample is 13 of 15,896 recorded test patches (0.0818%). This supports further evaluation only. It is not full-benchmark accuracy and does not validate Nepal.

**Delivery boundary:** the verified dashboard and the Trishuli export use the existing pilot checkpoint. The new candidate was trained and evaluated separately, and its integration into the cached demo has not been verified.

Exports include satellite IDs, timestamps, hashes, method, historical OSM snapshot, source availability, attribution and limitations. ZIP exports include a standalone HTML report; reports print cleanly from the browser.

Keep raw scenes, model weights, raster outputs and private keys **outside Git**.

## Experimental terrain review

Optional baseline/AI HAND variants preserve existing methods and outputs. Provisional thresholds strongly reduce coverage. See [terrain review](docs/terrain_hand.md) for cached-input commands, assumptions and measured tradeoffs.

## Retired prototype entry points

`api/jobs.py` and `scripts/run_case.py` stop with migration errors. The legacy OSM helper, uncalibrated Monte Carlo/landing-zone prototypes and the default-filled report generator are retired. Use `api.main:app` or `scripts/run_integrated_case.py`. Existing prototype cache files are preserved but must not be treated as verified evidence. See [clean-checkout verification](docs/clean_install.md) for Windows setup and remaining acceptance checks.

## Further documentation

- [docs/integration.md](docs/integration.md): data contract, exact setup, validation, limitations
- [docs/general_pipeline_step5.md](docs/general_pipeline_step5.md): current general workflow
- [docs/general_ai.md](docs/general_ai.md): general-case experimental AI

## License and attribution

Project code is MIT-licensed (see `LICENSE`). Third-party satellite/OSM data, datasets, model assets and dependencies keep their own licenses; the code license does not authorize redistributing them.

- Contains Copernicus Sentinel data (2026), via Copernicus Data Space Ecosystem. Terrain: Copernicus DEM.
- © OpenStreetMap contributors, ODbL. Historical extracts via HeiGIT ohsome.
- Model training data: Kuro Siwo (Bountos et al., NeurIPS 2024), Kuro-Siwo-Webdataset, CC BY 4.0, pinned revision `e8e61b7b1254b04bfa2b5e26d43db90cb86b5004`.
- Reference maps: Copernicus EMS activation EMSR927 (original EU/Copernicus credits retained).
