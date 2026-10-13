"""API/orchestration fixtures only; no downloaded or fabricated product results."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api import integrated_jobs as jobs
from api.satellite import load_satellite
from network.verified_osm import empty, FILTERS
from trinetra.core import general_pipeline as pipeline
from trinetra.core.cases import make_case, case_paths
from trinetra.downloads.sentinel1 import atomic_json, DownloadError
from trinetra.flood_detection.pipeline import run_baseline

spec = importlib.util.spec_from_file_location('baseline_test_helper', Path(__file__).with_name('test_general_baseline.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def saved_case(tmp_path, monkeypatch):
    root = tmp_path/'data'
    path, processing, _ = helper.fixture(root, changed=False)
    run_baseline(path)
    monkeypatch.setenv('TRINETRA_DATA_DIR', str(root))
    return root, path, processing


def unavailable(*args):
    return ({name:empty() for name in FILTERS},
            {'snapshot_requested':None, 'layers':{name:{'status':'unavailable'} for name in FILTERS}},
            ['Explicit offline OSM test fixture'])


def wait_complete(client, job_id):
    for _ in range(100):
        data = client.get('/api/v1/jobs/'+job_id).json()
        if data['state'] in {'COMPLETE','FAILED'}:
            return data
        time.sleep(.02)
    raise AssertionError('Small unit-test job did not finish')


def test_saved_general_case_api_map_report_export_and_restart(saved_case, monkeypatch):
    root, path, processing = saved_case
    def forbidden(*a, **k):
        raise AssertionError('Saved results must not trigger raw processing')
    monkeypatch.setattr(pipeline, 'ensure_baseline', forbidden)
    monkeypatch.setattr(jobs, 'fetch_infrastructure', unavailable)
    with TestClient(app) as client:
        listed=client.get('/api/v1/cases').json()['cases']
        assert any(item['case']==processing['case'] for item in listed)
        created=client.post('/api/v1/pipeline-jobs', json={'aoi':processing['aoi'],'flood_date':processing['flood_date']})
        assert created.status_code==202
        state=wait_complete(client,created.json()['id'])
        assert state['state']=='COMPLETE',state['message']
        assert state['summary']['case']==processing['case']
        assert state['summary']['candidate_pixels']==0
        assert state['summary']['settlements_cut_off'] is None
        assert state['cache_hit'] is True
        assert 'REPORTING' in [s['stage'] for s in state['stage_history']]
        job_id=state['id']
        assert client.get('/api/v1/jobs/'+job_id+'/layers').status_code==200
        assert 'Unknown / unavailable' in client.get('/api/v1/jobs/'+job_id+'/report').text
        bundle=client.get('/api/v1/jobs/'+job_id+'/export')
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            assert {'summary.json','provenance.json','SITREP.md'} <= set(archive.namelist())
        images=client.get('/api/v1/cases/'+processing['case']+'/previews').json()['previews']
        assert 'baseline' in images
        assert client.get(images['baseline']).status_code==200
        assert client.get('/api/v1/cases/'+processing['case']+'/previews/credentials').status_code==404
        jobs._JOBS.pop(job_id)
        restored=client.get('/api/v1/jobs/'+job_id).json()
        assert restored['state']=='COMPLETE'
        assert 'baseline_road_graph' not in restored
        assert (root/'runs/integrated'/job_id/'SITREP.md').is_file()


def test_modified_saved_mask_fails_without_reprocessing(saved_case, monkeypatch):
    _, path, processing=saved_case
    (path.parent/'baseline/flood_mask.tif').write_bytes(b'corrupt fixture')
    monkeypatch.setattr(pipeline,'ensure_baseline',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No repair fallback')))
    with TestClient(app) as client:
        response=client.post('/api/v1/pipeline-jobs',json={'aoi':processing['aoi'],'flood_date':processing['flood_date']})
        state=wait_complete(client,response.json()['id'])
    assert state['state']=='FAILED'
    assert 'hash' in state['message']


@pytest.mark.parametrize('change', [{'flood_date':'2999-01-01'}, {'config':'unsupported'},
                                  {'max_product_gib':4}, {'allow_downloads':'yes'},
                                  {'aoi':{'type':'Polygon','coordinates':[[[84,27],[85,27],[85,28],[84,28],[84,27]]]}}])
def test_invalid_live_requests_rejected_before_jobs(change, monkeypatch, tmp_path):
    monkeypatch.setenv('TRINETRA_DATA_DIR',str(tmp_path/'data'))
    body={'aoi':{'type':'Polygon','coordinates':[[[85.2,26.85],[85.25,26.85],[85.25,26.9],[85.2,26.9],[85.2,26.85]]]},'flood_date':'2026-09-10'}
    body.update(change)
    with TestClient(app) as client:
        assert client.post('/api/v1/pipeline-jobs',json=body).status_code==422


def test_missing_outputs_delegate_to_pipeline_then_real_loader(saved_case, monkeypatch):
    root, _, processing=saved_case
    real_loader=jobs.load_satellite
    calls=[]
    def loader(*args):
        calls.append(args)
        if len(calls)==1:
            raise FileNotFoundError('Explicit absent-cache test fixture')
        return real_loader(*args)
    observed={}
    def ensure(*args, **kwargs):
        observed.update(kwargs)
        kwargs['progress']('DISCOVERING','Unit stage fixture',{})
        kwargs['progress']('DETECTING','Unit stage fixture',{'source_pair':processing['source_pair']})
        return {'trace':{'explicit_test_fixture':True,'status':'satellite_ready'}}
    monkeypatch.setattr(jobs,'load_satellite',loader)
    monkeypatch.setattr(pipeline,'ensure_baseline',ensure)
    monkeypatch.setattr(jobs,'fetch_infrastructure',unavailable)
    with TestClient(app) as client:
        created=client.post('/api/v1/pipeline-jobs',json={'aoi':processing['aoi'],'flood_date':processing['flood_date']})
        state=wait_complete(client,created.json()['id'])
    assert state['state']=='COMPLETE',state['message']
    assert observed['allow_downloads'] is False
    assert observed['transport']=='s3'
    assert len(calls)==2
    assert 'DISCOVERING' in [s['stage'] for s in state['stage_history']]


def test_queue_limit_and_health_stay_available(tmp_path, monkeypatch):
    import api.main as main
    monkeypatch.setenv('TRINETRA_DATA_DIR',str(tmp_path/'data'))
    previous=dict(jobs._JOBS)
    jobs._JOBS.clear()
    async def hold(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(main,'run_pipeline_job',hold)
    body={'aoi':{'type':'Polygon','coordinates':[[[85.2,26.85],[85.25,26.85],[85.25,26.9],[85.2,26.9],[85.2,26.85]]]},'flood_date':'2026-09-10'}
    try:
        with TestClient(app) as client:
            assert client.post('/api/v1/pipeline-jobs',json=body).status_code==202
            assert client.post('/api/v1/pipeline-jobs',json=body).status_code==202
            assert client.post('/api/v1/pipeline-jobs',json=body).status_code==429
            assert client.get('/health').status_code==200
    finally:
        jobs._JOBS.clear()
        jobs._JOBS.update(previous)


def test_pipeline_missing_cache_never_transfers_when_disabled(tmp_path, monkeypatch):
    case=make_case([85.2,26.85,85.25,26.9],'2026-09-10')
    paths=case_paths(case,tmp_path)
    monkeypatch.setattr(pipeline,'_discover',lambda *a:{'pair':{'explicit_test_fixture':True}})
    plan={'catalogue_estimated_total_bytes':123,'pair':{},'explicit_test_fixture':True}
    monkeypatch.setattr(pipeline,'plan_download',lambda *a,**k:(plan,paths))
    def attempt(*a,**kwargs):
        kwargs['transfer']()
    monkeypatch.setattr(pipeline,'download_planned',attempt)
    monkeypatch.setattr(pipeline,'preprocess_case',lambda *a,**k: (_ for _ in ()).throw(AssertionError('SNAP forbidden')))
    with pytest.raises(DownloadError,match='not cached'):
        pipeline.ensure_baseline(case.aoi,case.flood_date,tmp_path,allow_downloads=False)
    record=json.loads((paths['run']/'pipeline_manifest.json').read_text())
    assert record['status']=='failed'
    assert record['stages'][-1]['stage']=='DOWNLOADING'
    assert not (paths['run']/'.pipeline.lock').exists()


def test_pipeline_stages_snapshot_and_failure_recording(tmp_path, monkeypatch):
    case=make_case([85.2,26.85,85.25,26.9],'2026-09-10')
    paths=case_paths(case,tmp_path)
    plan={'catalogue_estimated_total_bytes':123,'pair':{},'explicit_test_fixture':True}
    monkeypatch.setattr(pipeline,'_discover',lambda *a:{'pair':{'explicit_test_fixture':True}})
    monkeypatch.setattr(pipeline,'plan_download',lambda *a,**k:(plan,paths))
    def raw(*a,**k):
        atomic_json(paths['run']/'download_manifest.json',{'explicit_test_fixture':True})
    monkeypatch.setattr(pipeline,'download_planned',raw)
    monkeypatch.setattr('trinetra.preprocessing.snap.gpt_path',lambda:'offline-test-gpt')
    snapshots=[]
    def physical(source,**kwargs):
        snapshots.append(Path(source))
        assert Path(source).name.startswith('download-input-')
        return {'explicit_test_fixture':True}
    monkeypatch.setattr(pipeline,'preprocess_case',physical)
    monkeypatch.setattr(pipeline,'run_baseline',lambda *a,**k:{'explicit_test_fixture':True})
    phases=[]
    result=pipeline.ensure_baseline(case.aoi,case.flood_date,tmp_path,progress=lambda name,*a:phases.append(name))
    assert result['trace']['status']=='satellite_ready'
    assert [s['stage'] for s in result['trace']['stages']]==['DISCOVERING','PLANNING','DOWNLOADING','PREPROCESSING','DETECTING']
    assert all(s['elapsed_seconds']>=0 for s in result['trace']['stages'])
    assert len(snapshots)==1 and snapshots[0].is_file()
    assert not (paths['processed']/'.preprocess.lock').exists()
