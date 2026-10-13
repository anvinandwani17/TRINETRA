"""Regression checks for report safety, method previews and export semantics."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from api.main import app
from api import integrated_jobs
from reports.html_report import report_html


def example_job():
    return {'id': 'report-fixture', 'state': 'COMPLETE', 'flood_date': '2026-08-26',
            'summary': {'case': 'trishuli', 'method': 'experimental_ai', 'resolution_m': 10,
                        'candidate_area_km2': 2.7809, 'candidate_pixels': 27809,
                        'assessable_fraction': .905, 'unknown_area_km2': 10.3199,
                        'total_settlements': 12, 'settlements_cut_off': None,
                        'infrastructure_status': {'roads': 'unavailable'}},
            'provenance': {'source_pair': {'pre_id': '<script>alert(1)</script>'},
                           'checkpoint_sha256': 'a'*64},
            'warnings': ['<img src=x onerror=alert(1)>'],
            'layers': {'damaged_roads': {'type': 'FeatureCollection', 'features': []},
                       'unknown_roads': {'type': 'FeatureCollection', 'features': []}}}


def test_printable_report_renders_tables_and_escapes_service_text():
    html = report_html(example_job())
    assert '<table>' in html and '<thead>' in html and '<h1>TRINETRA' in html
    assert '| Measure |' not in html
    assert '<script>alert' not in html and '<img src=x' not in html
    assert '&lt;script&gt;' in html and 'Unknown / unavailable' in html
    assert 'report-fixture' in html and 'Checkpoint SHA256:' in html
    assert 'Copernicus' in html and 'ODbL' in html


def test_job_export_keeps_unknowns_and_relabels_candidate_roads(monkeypatch):
    job = example_job()
    monkeypatch.setitem(integrated_jobs._JOBS, job['id'], job)
    with TestClient(app) as client:
        result = client.get('/api/v1/jobs/report-fixture/export')
        assert result.status_code == 200
        with zipfile.ZipFile(io.BytesIO(result.content)) as z:
            assert {'ATTRIBUTION.md', 'SITREP.html', 'SITREP.md', 'EXPORT_README.txt',
                    'candidate_affected_roads.geojson', 'unknown_roads.geojson'} <= set(z.namelist())
            assert 'damaged_roads.geojson' not in z.namelist()
            assert json.loads(z.read('summary.json'))['settlements_cut_off'] is None
            assert 'experimental_ai' in z.read('SITREP.html').decode()
        assert client.get('/api/v1/jobs/report-fixture/report?lang=ne').status_code == 422


def test_ai_preview_never_falls_back_to_baseline(tmp_path, monkeypatch):
    import api.main as main
    (tmp_path/'baseline/previews').mkdir(parents=True)
    (tmp_path/'baseline/previews/flood_mask.png').write_bytes(b'baseline fixture')
    monkeypatch.setattr(main, 'preview_folder', lambda _: tmp_path)
    with TestClient(app) as client:
        assert 'baseline' in client.get('/api/v1/cases/trishuli/previews').json()['previews']
        assert client.get('/api/v1/cases/trishuli/previews?method=experimental_ai').json()['previews'] == {}
        (tmp_path/'ai_flood_experimental').mkdir()
        (tmp_path/'ai_flood_experimental/preview.png').write_bytes(b'AI fixture')
        images = client.get('/api/v1/cases/trishuli/previews?method=experimental_ai').json()['previews']
        assert set(images) == {'experimental_ai'}
        assert client.get(images['experimental_ai']).content == b'AI fixture'
        assert client.get('/api/v1/cases/trishuli/previews?method=invalid').status_code == 422
