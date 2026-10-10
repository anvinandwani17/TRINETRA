"""Small transport/topology fixtures and optional actual Trishuli checks.

Fixtures here are unit tests only; they are never exposed as product results.
"""
import asyncio
import io
import json
from pathlib import Path
import shutil
import zipfile

import geopandas as gpd
import networkx as nx
import pytest
import requests
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box, mapping

from api.main import app
from api.satellite import ROOT, case_aoi, load_satellite
from api import integrated_jobs
from network.observed_access import analyze, connectivity, screen
from network.verified_osm import fetch_infrastructure, empty, FILTERS


def metadata():
    return {'extractRegion': {'temporalExtent': {'toTimestamp': '2026-07-27T09:00Z'}}}


class Response:
    status_code = 200
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


def test_empty_osm_never_synthesizes_features(monkeypatch, tmp_path):
    monkeypatch.setenv('TRINETRA_OSM_PROVIDER', 'legacy_v1')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response(metadata()))
    monkeypatch.setattr(requests, 'post', lambda *a, **k: Response({'type': 'FeatureCollection', 'features': []}))
    frames, provenance, _ = fetch_infrastructure(mapping(box(85.1, 27.9, 85.2, 28.0)), '2026-08-26', tmp_path)
    assert all(frame.empty for frame in frames.values())
    assert all(value['status'] == 'empty' for value in provenance['layers'].values())
    assert provenance['snapshot_requested'] == '2026-07-27T09:00:00Z'


def test_osm_failure_is_unknown_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv('TRINETRA_OSM_PROVIDER', 'legacy_v1')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response(metadata()))
    def fail(*a, **k): raise requests.ConnectionError('unit transport failure')
    monkeypatch.setattr(requests, 'post', fail)
    frames, provenance, warnings = fetch_infrastructure(mapping(box(85.1, 27.9, 85.2, 28.0)), '2026-08-26', tmp_path)
    assert all(frame.empty for frame in frames.values())
    assert all(value['status'] == 'unavailable' for value in provenance['layers'].values())
    assert warnings and not list(tmp_path.glob('*.json'))


def test_unknown_edges_do_not_certify_access():
    graph = nx.Graph()
    graph.add_edge((0, 0), (1, 0), geometry=LineString([(0, 0), (1, 0)]), length_m=1)
    graph.add_edge((1, 0), (2, 0), geometry=LineString([(1, 0), (2, 0)]), length_m=1)
    screened = screen(graph, Point().buffer(0), box(.9, -.1, 1.1, .1))
    assert connectivity(graph, screened, (0, 0), [(2, 0)])[0] == 'unknown'
    screened = screen(graph, box(.9, -.1, 1.1, .1), Point().buffer(0))
    assert connectivity(graph, screened, (0, 0), [(2, 0)])[0] == 'potential_cut_off'
    assert connectivity(graph, screened, None, [(2, 0)])[0] == 'unknown'
    assert connectivity(graph, screened, (0, 0), [])[0] == 'unknown'


def test_v2_missing_key_never_requests_or_invents(monkeypatch, tmp_path):
    monkeypatch.delenv('OHSOME_API_KEY', raising=False)
    monkeypatch.delenv('TRINETRA_OSM_PROVIDER', raising=False)
    def unexpected(*args, **kwargs): raise AssertionError('Missing key must not call extraction')
    monkeypatch.setattr(requests, 'post', unexpected)
    frames, provenance, warnings = fetch_infrastructure(mapping(box(85.1, 27.9, 85.2, 28.0)), '2026-08-26', tmp_path)
    assert all(frame.empty for frame in frames.values())
    assert all(value['status'] == 'unavailable' for value in provenance['layers'].values())
    assert warnings and provenance['snapshot_requested'] is None


def test_v2_reads_source_ids_tags_and_reuses_verified_cache(monkeypatch, tmp_path):
    monkeypatch.delenv('TRINETRA_OSM_PROVIDER', raising=False)
    monkeypatch.setenv('OHSOME_API_KEY', 'unit-test-placeholder')
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: Response({'temporalExtent': {'end': '2026-10-09T07:25:28Z'}}))
    source = gpd.GeoDataFrame([{'osm_id': 123, 'osm_type': 'way', 'tags': {'highway': 'residential'},
                               'geom': LineString([(85.12, 27.95), (85.18, 27.95)])}], geometry='geom', crs=4326)
    buffer = io.BytesIO(); source.to_parquet(buffer)
    class ParquetResponse:
        def raise_for_status(self): pass
        def iter_content(self, size): yield buffer.getvalue()
        def close(self): pass
    calls = []
    def post(*args, **kwargs): calls.append(kwargs); return ParquetResponse()
    monkeypatch.setattr(requests, 'post', post)
    for _ in range(2):
        frames, provenance, _ = fetch_infrastructure(mapping(box(85.1, 27.9, 85.2, 28.0)), '2026-08-26', tmp_path)
        assert frames['roads']['osm_id'].tolist() == ['way/123']
        assert frames['roads']['highway'].tolist() == ['residential']
        assert provenance['snapshot_requested'] == '2026-08-25T00:00:00Z'
    assert len(calls) == 4
    assert calls[0]['json']['clip'] is False
    assert all(value['cache_hit'] for value in provenance['layers'].values())


def test_unknown_hospital_is_not_usable_target():
    aoi = box(85.1, 27.9, 85.2, 28.0)
    roads = gpd.GeoDataFrame([{'osm_id': 'unit-road', 'highway': 'residential', 'geometry': LineString([(85.12, 27.95), (85.18, 27.95)])}], crs=4326)
    settlements = gpd.GeoDataFrame([{'osm_id': 'unit-settlement', 'geometry': Point(85.12, 27.95)}], crs=4326)
    hospitals = gpd.GeoDataFrame([{'osm_id': 'unit-hospital', 'geometry': Point(85.18, 27.95)}], crs=4326)
    unknown = gpd.GeoDataFrame([{'geometry': box(85.175, 27.94, 85.185, 27.96)}], crs=4326)
    output = analyze(roads, empty(), settlements, hospitals, empty(), unknown, aoi)
    assert output['settlements'][0]['road_status'] == 'unknown'


@pytest.fixture
def local_case():
    folder = ROOT / 'data/processed/trishuli'
    if not (folder / 'processing_manifest.json').exists():
        pytest.skip('Actual processed case is local data and is not committed to Git')
    return folder


def test_real_masks_and_method_separation(local_case):
    baseline = load_satellite(case_aoi(), '2026-08-26', 'baseline')
    ai = load_satellite(case_aoi(), '2026-08-26', 'experimental_ai')
    assert baseline['summary']['candidate_pixels'] == 151
    assert baseline['summary']['candidate_area_km2'] == pytest.approx(.0604)
    assert baseline['summary']['assessable_fraction'] < .12
    assert ai['summary']['resolution_m'] == 10
    assert ai['summary']['candidate_pixels'] > baseline['summary']['candidate_pixels']
    assert ai['provenance']['probability_calibrated'] is False
    assert baseline['hazard'].geometry.union_all().intersection(baseline['unknown'].geometry.union_all()).area < 1e-10


def test_modified_mask_fails_before_use(local_case, monkeypatch, tmp_path):
    shutil.copytree(local_case, tmp_path / 'case')
    mask = tmp_path / 'case/baseline/flood_mask.tif'
    with mask.open('ab') as stream: stream.write(b'unit corruption')
    monkeypatch.setenv('TRINETRA_CASE_DIR', str(tmp_path / 'case'))
    with pytest.raises(ValueError, match='hash'):
        load_satellite(case_aoi(), '2026-08-26', 'baseline')


def test_reject_unsupported_date_and_aoi_before_osm(local_case):
    with TestClient(app) as client:
        assert client.get('/health').json()['service'] == 'trinetra-api'
        assert client.get('/api/v1/cases/trishuli').status_code == 200
        assert client.post('/api/v1/jobs', json={'aoi': case_aoi(), 'flood_date': '2026-08-25'}).status_code == 422
        assert client.post('/api/v1/jobs', json={'aoi': mapping(box(84, 27, 85, 28)), 'flood_date': '2026-08-26'}).status_code == 422


def test_real_satellite_job_api_report_export_with_unavailable_osm(local_case, monkeypatch):
    def unavailable(*args):
        return {name: empty() for name in FILTERS}, {'snapshot_requested': None, 'layers': {name: {'status': 'unavailable'} for name in FILTERS}}, ['OSM unavailable in unit test']
    monkeypatch.setattr(integrated_jobs, 'fetch_infrastructure', unavailable)
    job = integrated_jobs.create_job('2026-08-26', case_aoi(), 'baseline')
    asyncio.run(integrated_jobs.run_job(job['id']))
    assert job['state'] == 'COMPLETE'
    assert job['summary']['damaged_road_segments'] is None
    assert job['summary']['settlements_cut_off'] is None
    assert not job['layers']['settlements']['features']
    with TestClient(app) as client:
        state = client.get('/api/v1/jobs/' + job['id'])
        assert state.status_code == 200 and 'baseline_road_graph' not in state.json()
        assert client.get('/api/v1/jobs/' + job['id'] + '/layers').status_code == 200
        report = client.get('/api/v1/jobs/' + job['id'] + '/report')
        assert 'candidate' in report.text.lower() and 'Unknown / unavailable' in report.text
        exported = client.get('/api/v1/jobs/' + job['id'] + '/export')
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert {'summary.json', 'provenance.json', 'SITREP.md', 'unobservable.geojson'} <= set(archive.namelist())
            assert json.loads(archive.read('summary.json'))['candidate_pixels'] == 151
