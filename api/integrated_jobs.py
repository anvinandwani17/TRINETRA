"""Connect Anvi's verified local outputs to Arnav's infrastructure and API."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import uuid
import time
import re

from api.schemas import JobState
from api.satellite import load_satellite, data_root
from network.verified_osm import fetch_infrastructure
from network.normalize import normalize_roads, normalize_buildings, normalize_targets
from network.observed_access import analyze

_JOBS = {}
_PIPELINE_SEMAPHORE = asyncio.Semaphore(1)


def set_stage(job, state, message, details=None):
    history = [dict(item) for item in job.get('stage_history', [])]
    state = state.value if isinstance(state, JobState) else state
    if not history or history[-1]['stage'] != state:
        if history:
            history[-1].update(elapsed_seconds=time.perf_counter()-job['_stage_started'], status='complete', ended_at=datetime.now(timezone.utc).isoformat())
        history.append({'stage':state, 'started_at':datetime.now(timezone.utc).isoformat(), 'status':'running'})
        job['_stage_started'] = time.perf_counter()
    job.update(state=JobState(state), message=message, stage_history=history)
    if details:
        if details.get('source_pair'):
            job['selected_pair'] = details['source_pair']
        if details.get('estimated_bytes') is not None:
            job['estimated_download_bytes'] = details['estimated_bytes']


def save_job(job):
    from trinetra.downloads.sentinel1 import atomic_json
    folder = data_root() / 'runs/integrated' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder/'job.json', json.loads(json.dumps(public_job(job), default=str)))


def create_job(flood_date, aoi, config='default'):
    job = {'id': uuid.uuid4().hex[:12], 'state': JobState.QUEUED,
           'created_at': datetime.now(timezone.utc), 'flood_date': flood_date,
           'aoi': aoi, 'config': config, 'layers': {}, 'summary': {},
           'message': 'Queued: verifying local satellite inputs.', 'cache_hit': False,
           'provenance': {}, 'warnings': [], 'pipeline': False, 'stage_history': [],
           '_stage_started':time.perf_counter(), 'selected_pair':None,
           'estimated_download_bytes':None, 'pipeline_trace':None}
    _JOBS[job['id']] = job
    return job


def get_job(job_id):
    if job_id in _JOBS:
        return _JOBS[job_id]
    if not re.fullmatch(r'[a-f0-9]{12}', job_id):
        return None
    path = data_root() / 'runs/integrated' / job_id / 'job.json'
    if path.is_file():
        try:
            job = json.loads(path.read_text(encoding='utf-8-sig'))
            if job.get('id') != job_id:
                return None
            if job.get('state') not in {JobState.COMPLETE.value, JobState.FAILED.value}:
                job.update(state=JobState.FAILED, message='API restarted before this job completed. Rerun the same case; cached files are retained. Check old processes/locks before retrying.')
            _JOBS[job_id] = job
            return job
        except (OSError, ValueError):
            pass
    return None


def public_job(job):
    # NetworkX graph handles stay in memory for analysis; never JSON-encode them.
    result = {key: value for key, value in job.items() if key not in {
        'baseline_road_graph', 'hazard_road_graph', 'settlement_road_nodes', 'hospital_road_nodes', '_stage_started'}}
    result['stage_history'] = [dict(item) for item in job.get('stage_history', [])]
    if result['stage_history'] and result['stage_history'][-1]['status']=='running' and '_stage_started' in job:
        result['stage_history'][-1]['elapsed_seconds'] = time.perf_counter()-job['_stage_started']
    return result


async def run_pipeline_job(job_id, allow_downloads=False, transport='s3', max_product_gib=3.0):
    job = _JOBS[job_id]
    async with _PIPELINE_SEMAPHORE:
        try:
            set_stage(job, JobState.PROCESSING, 'Verifying any existing processed satellite case...')
            try:
                satellite = await asyncio.to_thread(load_satellite, job['aoi'], job['flood_date'], 'baseline')
                job['cache_hit'] = True
                job['selected_pair'] = satellite['provenance']['source_pair']
            except FileNotFoundError:
                from trinetra.core.general_pipeline import ensure_baseline
                output = await asyncio.to_thread(ensure_baseline, job['aoi'], job['flood_date'], data_root(),
                    allow_downloads=allow_downloads, transport=transport, max_product_gib=max_product_gib,
                    progress=lambda stage,message,details: set_stage(job, stage, message, details))
                job['pipeline_trace'] = output['trace']
                satellite = await asyncio.to_thread(load_satellite, job['aoi'], job['flood_date'], 'baseline')
            await run_job(job_id, satellite=satellite)
        except Exception as exc:
            fail_job(job, exc)


def fail_job(job, exc):
    history = [dict(item) for item in job.get('stage_history', [])]
    if history and history[-1].get('status')=='running':
        history[-1].update(status='failed', ended_at=datetime.now(timezone.utc).isoformat(),
                           elapsed_seconds=time.perf_counter()-job['_stage_started'])
    job.update(state=JobState.FAILED, message=f'Analysis stopped: {exc}', stage_history=history)
    save_job(job)


async def run_job(job_id, satellite=None):
    job = _JOBS[job_id]
    try:
        if satellite is None:
            set_stage(job, JobState.PROCESSING, 'Verifying flood rasters, metadata hashes and requested AOI...')
            satellite = await asyncio.to_thread(load_satellite, job['aoi'], job['flood_date'], job['config'])
        set_stage(job, JobState.OSM_QUERYING, 'Querying verified pre-event OSM history...')
        infra, osm, warnings = await asyncio.to_thread(fetch_infrastructure, job['aoi'], job['flood_date'])
        roads = normalize_roads(infra['roads'])
        # Keep routing restrictions that the original normalizer does not retain.
        for column in ['access', 'motor_vehicle']:
            if column in infra['roads'] and not roads.empty:
                values = infra['roads'].set_index('osm_id')[column].to_dict()
                roads[column] = roads['osm_id'].map(values)
        buildings = normalize_buildings(infra['buildings'])
        settlements = normalize_targets(infra['settlements'])
        hospitals = normalize_targets(infra['hospitals'])
        set_stage(job, JobState.NETWORK_ANALYSIS, 'Screening infrastructure intersections and conditional road access...')
        impact = await asyncio.to_thread(analyze, roads, buildings, settlements, hospitals,
                                        satellite['hazard'], satellite['unknown'], satellite['aoi'])
        layers = {'flood_extent': json.loads(satellite['hazard'].to_json()),
                  'unobservable': json.loads(satellite['unknown'].to_json()), **impact['layers']}
        summary = {**satellite['summary'], **impact['summary'], 'osm_snapshot': osm['snapshot_requested'],
                   'infrastructure_status': {key: value['status'] for key, value in osm['layers'].items()},
                   's2_optical_available': None, 's2_optical_date': None, 's2_cloud_cover': 'Not queried',
                   'interpretation': 'Candidate flood evidence and conditional route impact; physical damage and passability are unverified'}
        statuses = summary['infrastructure_status']
        if statuses.get('roads') != 'available':
            for key in ['damaged_road_segments', 'affected_bridges', 'unknown_roads', 'settlements_cut_off', 'foot_cut_off']:
                summary[key] = None
        if statuses.get('buildings') != 'available':
            summary['total_buildings'] = summary['affected_buildings'] = None
        if statuses.get('settlements') != 'available':
            summary['total_settlements'] = summary['settlements_access_unknown'] = summary['settlements_cut_off'] = None
        if statuses.get('hospitals') != 'available':
            summary['hospitals_monitored'] = summary['settlements_cut_off'] = summary['foot_cut_off'] = None
        if any(value == 'empty' for value in statuses.values()):
            warnings.append('One or more infrastructure queries returned no mapped features; this does not prove absence on the ground')
        warnings += ['Unknown or unsupported pixels are never classified as dry or used to certify a safe route',
                     'Connectivity uses mapped nodes and a 500 m endpoint snapping limit; missing topology and routes beyond the AOI can change access results',
                     'Candidate-intersecting roads are removed only in a conditional scenario; field verification is required',
                     'Landing zones, Sentinel-2 availability and calibrated closure probabilities have not been assessed']
        if satellite['summary']['method'] == 'experimental_ai':
            warnings.append('Experimental pilot model: Trishuli accuracy and model scores are not validated or calibrated')
        provenance = {**satellite['provenance'], 'osm': osm, 'osm_snapshot': osm['snapshot_requested'],
                      'satellite_cache_hit':job['cache_hit'],
                      'cache_hit': any(value.get('cache_hit', False) for value in osm['layers'].values()),
                      'data_source': 'Copernicus Data Space Sentinel-1 + historical OpenStreetMap / ohsome',
                      'note': summary['interpretation'], 'warnings': warnings}
        job.update(layers=layers, summary=summary, provenance=provenance, warnings=warnings,
                   cache_hit=job['cache_hit'] or provenance['cache_hit'], **impact['handles'])
        provenance['pipeline_trace'] = job.get('pipeline_trace')
        job['cache_key'] = hashlib.sha256(json.dumps({'aoi': job['aoi'], 'date': job['flood_date'],
            'config': job['config'], 'satellite': satellite['provenance'], 'osm': osm}, sort_keys=True).encode()).hexdigest()
        set_stage(job, JobState.REPORTING, 'Writing grounded situation report and export layers...')
        runs = data_root() / 'runs/integrated' / job_id
        runs.mkdir(parents=True, exist_ok=True)
        for name, layer in layers.items():
            (runs / f'{name}.geojson').write_text(json.dumps(layer), encoding='utf-8')
        (runs / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        (runs / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
        from reports.grounded import report
        (runs / 'SITREP.md').write_text(report(job), encoding='utf-8')
        job['output_dir'] = str(runs)
        set_stage(job, JobState.COMPLETE, 'Real satellite layers loaded. Review candidate impacts, unknown areas and data availability.')
        job['stage_history'][-1]['status'] = 'complete'
        job['stage_history'][-1]['elapsed_seconds'] = 0.0
        save_job(job)
    except Exception as exc:
        fail_job(job, exc)
