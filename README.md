# TRINETRA

Sentinel-1 flood evidence, terrain observability, mapped infrastructure and conditional settlement access for the Multimodal AI Hackathon 2026 Track B.

Anvi owns the satellite/flood-AI Python package in `src/trinetra`. Arnav owns the React/API/infrastructure side in `web`, `api`, `network` and `reports`. The active integrated API is **`api.main:app`**.

The baseline supports saved cases and a general local AOI/date pipeline: discovery → selected-scene download/cache → physical SNAP preprocessing → deterministic detection → historical OSM → conditional access → report. AOIs are bounded to 300 km² and must have a suitable same-orbit Sentinel-1 pair. Downloads are disabled by default and must be enabled for missing scene data. The saved **Trishuli / 26 August 2026** baseline and experimental AI demo remain available. General-case AI inference is still pending.

## Run locally on Windows

Use Python 3.12 and your active virtual environment. Vite requires Node.js 20.19+ or 22.12+ (check the package's engine requirements for other supported major versions).

```powershell
python -m pip install -r requirements-integration.txt
python -m pytest tests -q --tb=short
$env:SNAP_GPT = "C:\Program Files\esa-snap\bin\gpt.exe"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repository root:

```powershell
cd web
npm ci
npm run build
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173, select a saved case and click **Load selected saved area**, or draw a local Polygon and choose a historical UTC day. Keep baseline selected for general cases. Existing case files are not included in Git. Cached satellite results are hash-verified and reused; missing scenes require the explicit download option. The pipeline never trains a model. Run stages, actual elapsed times, source availability, preview links and report/export results are displayed.

## Credentials

Official ohsome v2 infrastructure extraction needs a **free API key** from https://account.heigit.org/. Put `OHSOME_API_KEY` in your private root `.env`; the blank template is `configs/integration.env.example`. Missing keys or source failures produce unavailable/unknown infrastructure, never invented roads or settlements. Other existing CDSE/SNAP settings can stay in the same private file.

New satellite downloads require Copernicus Data Space credentials and optionally CDSE S3 keys. New physical preprocessing requires SNAP/GPT. Displaying existing outputs runs on CPU and requires neither a GPU nor a new SNAP run.

## Interpretation

Results are candidate flood detections and conditional road-access scenarios. They do not establish confirmed damage, safe routes or safe landing zones. Unknown/unsupported pixels remain unknown. The AI pilot has unverified Trishuli accuracy and uncalibrated scores. Mapped medical-facility access inside this AOI is a bounded proxy, not absolute isolation. No optical-scene availability or calibrated road-closure probability is claimed. EMSR927 landslides are not water-flood ground truth or training/tuning inputs.

Exports include satellite IDs, timestamps, hashes, method, historical OSM snapshot, source availability and limitations. Keep raw scenes, model weights, raster outputs and private keys outside Git.

See [docs/integration.md](docs/integration.md) for the data contract, exact setup, validation and limitations. Use `scripts/run_general_pipeline.py --aoi examples/terai_discovery.geojson --date 2026-09-10` for the single-command baseline workflow; add `--allow-downloads` only for missing raw scenes. See [docs/general_pipeline_step5.md](docs/general_pipeline_step5.md) for the current workflow and remaining limitations. Existing separate scripts remain available for diagnostics. Earlier demo runner modules are not used by the active integrated flow.


## Evidence and reproducibility

Read [methodology](docs/methodology.md), [source attribution](docs/ATTRIBUTION.md) and [reproducibility setup](docs/reproducibility.md). The supplied AI pilot used 104 training samples from 15 events and 2 validation samples from 2 events; no generalization result is claimed. Frozen pilot evaluation reports the exact locally cached coverage. HAND/debris/S2 and general-case AI remain pending.

Reports now render headings and tables for browser printing. ZIP exports include a standalone HTML report, attribution and export semantics. The AI preview is selected by method; an unavailable preview is omitted rather than replaced by baseline imagery.

Project code licensing is not yet declared. Source attribution and third-party data terms do not grant a license to this project's code. Do not represent the release as complete until assets are published, hash-checked and accessible to another machine.
