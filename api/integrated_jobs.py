"""Connect Anvi's verified local outputs to Arnav's infrastructure and API."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import uuid

from api.schemas import JobState
from api.satellite import load_satellite
from network.verified_osm import fetch_infrastructure
from network.normalize import normalize_roads, normalize_buildings, normalize_targets
from network.observed_access import analyze

_JOBS = {}


def create_job(flood_date, aoi, config='default'):
    job = {'id': uuid.uuid4().hex[:12], 'state': JobState.QUEUED,
           'created_at': datetime.now(timezone.utc), 'flood_date': flood_date,
           'aoi': aoi, 'config': config, 'layers': {}, 'summary': {},
           'message': 'Queued: verifying local satellite inputs.', 'cache_hit': False,
           'provenance': {}, 'warnings': []}
    _JOBS[job['id']] = job
    return job


def get_job(job_id):
    return _JOBS.get(job_id)


def public_job(job):
    # NetworkX graph handles stay in memory for analysis; never JSON-encode them.
    return {key: value for key, value in job.items() if key not in {
        'baseline_road_graph', 'hazard_road_graph', 'settlement_road_nodes', 'hospital_road_nodes'}}


async def run_job(job_id):
    job = _JOBS[job_id]
    try:
        job.update(state=JobState.PROCESSING, message='Verifying flood rasters, metadata hashes and requested AOI...')
        satellite = await asyncio.to_thread(load_satellite, job['aoi'], job['flood_date'], job['config'])
        job.update(state=JobState.DISCOVERING, message='Querying verified pre-event OSM history...')
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
        job.update(state=JobState.NETWORK_ANALYSIS, message='Screening infrastructure intersections and conditional road access...')
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
                      'cache_hit': any(value.get('cache_hit', False) for value in osm['layers'].values()),
                      'data_source': 'Copernicus Data Space Sentinel-1 + historical OpenStreetMap / ohsome',
                      'note': summary['interpretation'], 'warnings': warnings}
        job.update(layers=layers, summary=summary, provenance=provenance, warnings=warnings,
                   cache_hit=provenance['cache_hit'], **impact['handles'])
        job['cache_key'] = hashlib.sha256(json.dumps({'aoi': job['aoi'], 'date': job['flood_date'],
            'config': job['config'], 'satellite': satellite['provenance'], 'osm': osm}, sort_keys=True).encode()).hexdigest()
        runs = Path(__file__).resolve().parents[1] / 'data/runs/integrated' / job_id
        runs.mkdir(parents=True, exist_ok=True)
        for name, layer in layers.items():
            (runs / f'{name}.geojson').write_text(json.dumps(layer), encoding='utf-8')
        (runs / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        (runs / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
        job['output_dir'] = str(runs)
        job.update(state=JobState.COMPLETE, message='Real satellite layers loaded. Review candidate impacts, unknown areas and data availability.')
    except Exception as exc:
        job.update(state=JobState.FAILED, message=f'Analysis stopped: {exc}')
