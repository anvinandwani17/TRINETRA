"""TRINETRA API: verified Trishuli outputs and conditional infrastructure impact."""
import asyncio
import io
import json
import os
import zipfile
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse

from api.schemas import AnalyzeRequest, JobState, RestoreEdgeRequest, PipelineRequest
from api.integrated_jobs import create_job, get_job, public_job, run_job, run_pipeline_job, save_job, _JOBS
from api.satellite import case_aoi, load_satellite, available_cases, data_root, ROOT
from reports.grounded import report
from reports.html_report import report_html
from reports.one_page import one_page_pdf
from reports.credits import ATTRIBUTIONS
from typing import Literal

load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)

app = FastAPI(title='TRINETRA API', version='0.3.0')
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


@app.get('/api/v1/cases')
def list_cases():
    return {'cases': available_cases()}


def preview_folder(case_id):
    import re
    if case_id == 'trishuli':
        folder = Path(os.getenv('TRINETRA_CASE_DIR', str(ROOT/'data/processed/trishuli'))).resolve()
    elif re.fullmatch(r'case-[a-f0-9]{16}', case_id):
        folder = data_root()/'processed'/case_id
    else:
        raise HTTPException(404, 'Unknown case')
    return folder


@app.get('/api/v1/cases/{case_id}/previews')
def list_previews(case_id: str, method: Literal['baseline', 'experimental_ai', 'baseline_hand', 'experimental_ai_hand'] = 'baseline'):
    folder = preview_folder(case_id)
    choices = {'before':'previews/before.png','after':'previews/after.png',
               'change':'previews/change.png','observability':'previews/observability.png',
               'baseline':'baseline/previews/flood_mask.png'}
    if method == 'experimental_ai':
        choices.pop('baseline')
        choices['experimental_ai'] = 'ai_flood_experimental/preview.png'
    if method.endswith('_hand'):
        choices.pop('baseline',None)
        choices[method] = ('baseline_hand' if method=='baseline_hand' else 'ai_flood_hand') + '/hand_candidates.png'
    return {'method': method, 'previews': {key:f'/api/v1/cases/{case_id}/previews/{key}' for key,relative in choices.items() if (folder/relative).is_file()}}


@app.get('/api/v1/cases/{case_id}/previews/{kind}')
def get_preview(case_id: str, kind: str):
    choices = {'before':'previews/before.png','after':'previews/after.png',
               'change':'previews/change.png','observability':'previews/observability.png',
               'baseline':'baseline/previews/flood_mask.png',
               'experimental_ai':'ai_flood_experimental/preview.png',
               'baseline_hand':'baseline_hand/hand_candidates.png',
               'experimental_ai_hand':'ai_flood_hand/hand_candidates.png'}
    if kind not in choices:
        raise HTTPException(404, 'Unknown preview type')
    folder = preview_folder(case_id)
    path = (folder/choices[kind]).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise HTTPException(404, 'Preview file is unavailable')
    return FileResponse(path, media_type='image/png')


@app.post('/api/v1/pipeline-jobs', status_code=202)
async def start_pipeline_job(payload: PipelineRequest):
    from trinetra.core.cases import make_case
    try:
        make_case(payload.aoi.model_dump(), payload.flood_date)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    active = sum(1 for job in _JOBS.values() if job.get('pipeline') and job['state'] not in {JobState.COMPLETE, JobState.FAILED})
    if active >= 2:
        raise HTTPException(429, 'One pipeline job is running and another is queued. Wait for them to finish.')
    job = create_job(payload.flood_date, payload.aoi.model_dump(), payload.config)
    job['pipeline'] = True
    job['message'] = 'Queued: ' + payload.config + ' pipeline. Downloads are ' + ('enabled for the selected two scenes.' if payload.allow_downloads else 'disabled; existing cached data only.')
    save_job(job)
    task = asyncio.create_task(run_pipeline_job(job['id'], payload.allow_downloads, payload.transport, payload.max_product_gib))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return public_job(job)


@app.post('/api/v1/jobs')
async def start_job(payload: AnalyzeRequest):
    try:
        await asyncio.to_thread(load_satellite, payload.aoi.model_dump(), payload.flood_date, payload.config)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
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
    return HTMLResponse(report_html(require_job(job_id, True)))


def render_one_page(job):
    try:
        return one_page_pdf(job)
    except ImportError as exc:
        raise HTTPException(503, 'Install requirements-onepage.txt to generate the compact PDF') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/v1/jobs/{job_id}/sitrep.pdf')
def get_job_one_page(job_id: str, lang: str = 'en'):
    if lang != 'en':
        raise HTTPException(422, 'This grounded report currently supports English only')
    job = require_job(job_id, True)
    return Response(render_one_page(job), media_type='application/pdf',
        headers={'Content-Disposition': 'inline; filename=TRINETRA_one_page_situation_report.pdf'})


@app.get('/api/v1/jobs/{job_id}/export')
def export_job_bundle(job_id: str):
    job = require_job(job_id, True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('summary.json', json.dumps(job['summary'], indent=2))
        archive.writestr('provenance.json', json.dumps(job['provenance'], indent=2))
        archive.writestr('SITREP.md', report(job))
        archive.writestr('SITREP.html', report_html(job))
        archive.writestr('SITREP_ONE_PAGE.pdf', render_one_page(job))
        archive.writestr('ATTRIBUTION.md', ATTRIBUTIONS)
        archive.writestr('EXPORT_README.txt',
            'Candidate evidence and conditional impacts; no confirmed damage or safe routes.\n'
            'SITREP_ONE_PAGE.pdf is the compact system-generated A4 report; SITREP.html and SITREP.md retain the full report.\n'
            'candidate_affected_roads.geojson replaces the legacy damaged_roads export name.\n'
            'API layer identifiers and existing job summaries retain their old keys for compatibility.\n'
            'Empty layers mean no exported features, not necessarily available source data.\n'
            'Check summary.json infrastructure_status and provenance.json limitations.\n'
            'Raster/checkpoint hashes identify source files, which are not included in this result ZIP.\n')
        for name, layer in job['layers'].items():
            filename = 'candidate_affected_roads' if name == 'damaged_roads' else name
            archive.writestr(filename + '.geojson', json.dumps(layer))
    return Response(buffer.getvalue(), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename=trinetra_{job_id}.zip'})


@app.post('/api/v1/jobs/{job_id}/simulate-restoration')
def simulate_restoration(job_id: str, payload: RestoreEdgeRequest):
    require_job(job_id, True)
    raise HTTPException(501, 'Restoration simulation needs observability-aware validation; candidate exposure does not establish a physically damaged edge')

