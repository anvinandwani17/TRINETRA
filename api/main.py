"""TRINETRA API: verified Trishuli outputs and conditional infrastructure impact."""
import asyncio
import html
import io
import json
import os
import zipfile
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from api.schemas import AnalyzeRequest, JobState, RestoreEdgeRequest
from api.integrated_jobs import create_job, get_job, public_job, run_job
from api.satellite import case_aoi, load_satellite
from reports.grounded import report

load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)

app = FastAPI(title='TRINETRA API', version='0.2.0')
app.add_middleware(CORSMiddleware,
    allow_origins=os.getenv('TRINETRA_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(','),
    allow_credentials=False, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
_TASKS = set()


@app.get('/health')
def health_check():
    return {'status': 'ok', 'service': 'trinetra-api'}


@app.get('/api/v1/cases/trishuli')
def trishuli_case():
    try:
        return {'case': 'trishuli', 'flood_date': '2026-08-26', 'aoi': case_aoi(),
                'methods': ['baseline', 'experimental_ai'], 'scope': 'Existing processed case only; other AOIs/dates require satellite processing first'}
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/v1/jobs')
async def start_job(payload: AnalyzeRequest):
    try:
        await asyncio.to_thread(load_satellite, payload.aoi.model_dump(), payload.flood_date, payload.config)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    job = create_job(payload.flood_date, payload.aoi.model_dump(), payload.config)
    task = asyncio.create_task(run_job(job['id']))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return public_job(job)


def require_job(job_id, complete=False):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    if complete and job['state'] != JobState.COMPLETE:
        raise HTTPException(409, 'Analysis is not complete')
    return job


@app.get('/api/v1/jobs/{job_id}')
def read_job_status(job_id: str):
    return public_job(require_job(job_id))


@app.get('/api/v1/jobs/{job_id}/summary')
def get_job_summary(job_id: str):
    return {'summary': require_job(job_id).get('summary', {})}


@app.get('/api/v1/jobs/{job_id}/layers')
def get_job_layers(job_id: str):
    return require_job(job_id).get('layers', {})


@app.get('/api/v1/jobs/{job_id}/provenance')
def get_job_provenance(job_id: str):
    return require_job(job_id).get('provenance', {})


@app.get('/api/v1/jobs/{job_id}/report', response_class=HTMLResponse)
def get_job_report(job_id: str, lang: str = 'en'):
    if lang != 'en':
        raise HTTPException(422, 'This grounded report currently supports English only')
    text = html.escape(report(require_job(job_id, True)))
    return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><title>TRINETRA situation report</title>'
        '<style>body{max-width:1000px;margin:32px auto;padding:20px;font-family:system-ui}pre{white-space:pre-wrap;line-height:1.6}'
        '@media print{button{display:none}}</style><button onclick="window.print()">Print / PDF</button><pre>' + text + '</pre></html>')


@app.get('/api/v1/jobs/{job_id}/export')
def export_job_bundle(job_id: str):
    job = require_job(job_id, True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('summary.json', json.dumps(job['summary'], indent=2))
        archive.writestr('provenance.json', json.dumps(job['provenance'], indent=2))
        archive.writestr('SITREP.md', report(job))
        for name, layer in job['layers'].items():
            archive.writestr(name + '.geojson', json.dumps(layer))
    return Response(buffer.getvalue(), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename=trinetra_{job_id}.zip'})


@app.post('/api/v1/jobs/{job_id}/simulate-restoration')
def simulate_restoration(job_id: str, payload: RestoreEdgeRequest):
    require_job(job_id, True)
    raise HTTPException(501, 'Restoration simulation needs observability-aware validation; candidate exposure does not establish a physically damaged edge')
