# TRINETRA

Sentinel-1 flood evidence, terrain observability, mapped infrastructure and conditional settlement access for the Multimodal AI Hackathon 2026 Track B.

Anvi owns the satellite/flood-AI Python package in `src/trinetra`. Arnav owns the React/API/infrastructure side in `web`, `api`, `network` and `reports`. The active integrated API is **`api.main:app`**.

The current integration uses the existing **Trishuli / 26 August 2026** processed case. It keeps the conservative deterministic baseline and experimental Siamese U-Net pilot separate, validates real output hashes, and exposes candidate/unknown layers to the map and grounded report. Other dates and AOIs require satellite processing first.

## Run locally on Windows

Use Python 3.12 and your active virtual environment. Vite requires Node.js 20.19+ or 22.12+ (check the package's engine requirements for other supported major versions).

```powershell
python -m pip install -r requirements-integration.txt
python scripts/check_integration.py
python -m pytest tests -q --tb=short
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repository root:

```powershell
cd web
npm ci
npm run build
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173 and click **Load Trishuli test area**. Existing local case files must be in `data/processed/trishuli`; they are not included in Git. This integration does not download scenes or train a model.

## Credentials

Official ohsome v2 infrastructure extraction needs a **free API key** from https://account.heigit.org/. Put `OHSOME_API_KEY` in your private root `.env`; the blank template is `configs/integration.env.example`. Missing keys or source failures produce unavailable/unknown infrastructure, never invented roads or settlements. Other existing CDSE/SNAP settings can stay in the same private file.

New satellite downloads require Copernicus Data Space credentials and optionally CDSE S3 keys. New physical preprocessing requires SNAP/GPT. Displaying existing outputs runs on CPU and requires neither a GPU nor a new SNAP run.

## Interpretation

Results are candidate flood detections and conditional road-access scenarios. They do not establish confirmed damage, safe routes or safe landing zones. Unknown/unsupported pixels remain unknown. The AI pilot has unverified Trishuli accuracy and uncalibrated scores. Mapped medical-facility access inside this AOI is a bounded proxy, not absolute isolation. No optical-scene availability or calibrated road-closure probability is claimed. EMSR927 landslides are not water-flood ground truth or training/tuning inputs.

Exports include satellite IDs, timestamps, hashes, method, historical OSM snapshot, source availability and limitations. Keep raw scenes, model weights, raster outputs and private keys outside Git.

See [docs/integration.md](docs/integration.md) for the data contract, exact setup, validation and limitations. Use `scripts/run_integrated_case.py` for the connected CLI and `scripts/run_satellite_case.py` for Anvi's separate discovery/download pipeline. Earlier demo runner modules are not used by the active integrated flow.
