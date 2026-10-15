"""Checks for genuinely single-page system reports, job guards and export parity."""
import copy
import io
import zipfile
from pathlib import Path
import pytest
from pypdf import PdfReader
from fastapi.testclient import TestClient
from api.main import app
from api import integrated_jobs
from reports.one_page import one_page_pdf


def job_fixture():
    return {'id':'compact-fixture','state':'COMPLETE','flood_date':'2026-08-26',
        'summary':{'case':'trishuli','method':'experimental_ai_hand','resolution_m':10,
            'candidate_area_km2':.6702,'candidate_pixels':6702,'assessable_fraction':.06464077758928981,
            'unknown_area_km2':102.0056,'damaged_road_segments':26,'affected_bridges':5,
            'affected_buildings':84,'total_settlements':12,'settlements_cut_off':0,
            'settlements_access_unknown':12,'infrastructure_status':{'roads':'available','buildings':'available','settlements':'available','hospitals':'available'},
            'osm_snapshot':'2026-08-25T00:00:00Z'},
        'provenance':{'source_pair':{'pre_id':'S1D_PRE','post_id':'S1D_POST',
            'pre_datetime':'2026-08-16T12:21:41Z','post_datetime':'2026-08-28T12:21:41Z',
            'relative_orbit':85,'orbit_direction':'ascending'},
            'processing_manifest_sha256':'b'*64,'detection_manifest_sha256':'c'*64,
            'checkpoint_sha256':'a'*64,'limitations':['Terrain remains uncertain.']},
        'warnings':['Check the reference coverage.'],
        'layers':{'damaged_roads':{'type':'FeatureCollection','features':[]}}}


def pdf_text(data):
    reader=PdfReader(io.BytesIO(data))
    assert len(reader.pages)==1
    return reader.pages[0].extract_text()


def test_pdf_is_one_a4_page_with_actual_counts_and_coverage():
    reader=PdfReader(io.BytesIO(one_page_pdf(job_fixture())))
    assert len(reader.pages)==1
    page=reader.pages[0]
    assert abs(float(page.mediabox.width)-595.28)<1
    assert abs(float(page.mediabox.height)-841.89)<1
    text=page.extract_text()
    for value in ['0.6702','6,702','6.46%','102.0056','26','84','12','EDUCATIONAL PROTOTYPE']:
        assert value in text
    assert 'Unknown terrain is not dry land' in text
    assert 'a'*64 in text


def test_missing_values_do_not_become_zero_or_synthetic_success():
    job=job_fixture()
    job['summary']['settlements_cut_off']=None
    job['summary']['infrastructure_status']={}
    job['summary']['affected_bridges']=None
    text=pdf_text(one_page_pdf(job))
    assert 'Unknown / unavailable' in text
    assert 'roads: Unknown / unavailable' in text
    assert job['summary']['settlements_cut_off'] is None


def test_renderer_does_not_modify_the_job_or_drop_full_warning_count():
    job=job_fixture();job['warnings']=['warning '+str(i) for i in range(100)]
    before=copy.deepcopy(job)
    text=pdf_text(one_page_pdf(job))
    assert job==before
    assert '101 unique job warnings' in text
    assert 'detailed report' in text


def test_markup_in_metadata_is_literal_text_not_active_content():
    job=job_fixture();job['summary']['case']='<script>alert(1)</script>'
    job['provenance']['source_pair']['pre_id']='<a href="https://example.com">bad</a>'
    data=one_page_pdf(job);text=pdf_text(data)
    assert '<script>alert(1)</script>' in text
    assert '<a href=' in text
    reader=PdfReader(io.BytesIO(data))
    assert not reader.pages[0].get('/Annots')


def test_numeric_invalids_are_explicitly_unknown():
    job=job_fixture();job['summary'].update(candidate_area_km2=float('nan'),assessable_fraction=1.5,unknown_area_km2=-1)
    text=pdf_text(one_page_pdf(job))
    assert ' '.join(text.split()).count('Unknown / unavailable')>=3


def test_endpoint_and_zip_use_the_same_renderer(monkeypatch):
    job=job_fixture();monkeypatch.setitem(integrated_jobs._JOBS,job['id'],job)
    with TestClient(app) as client:
        response=client.get('/api/v1/jobs/compact-fixture/sitrep.pdf')
        assert response.status_code==200
        assert response.headers['content-type']=='application/pdf'
        assert '0.6702' in pdf_text(response.content)
        result=client.get('/api/v1/jobs/compact-fixture/export')
        assert result.status_code==200
        with zipfile.ZipFile(io.BytesIO(result.content)) as z:
            assert {'SITREP_ONE_PAGE.pdf','SITREP.html','SITREP.md','provenance.json','ATTRIBUTION.md'}<=set(z.namelist())
            assert '0.6702' in pdf_text(z.read('SITREP_ONE_PAGE.pdf'))
        assert client.get('/api/v1/jobs/compact-fixture/sitrep.pdf?lang=ne').status_code==422


def test_incomplete_and_missing_jobs_cannot_generate_pdf(monkeypatch):
    job=job_fixture();job['state']='PROCESSING';monkeypatch.setitem(integrated_jobs._JOBS,job['id'],job)
    with TestClient(app) as client:
        assert client.get('/api/v1/jobs/compact-fixture/sitrep.pdf').status_code==409
        assert client.get('/api/v1/jobs/never-a-real-job/sitrep.pdf').status_code==404


def test_unbounded_metadata_refuses_instead_of_clipping():
    job=job_fixture();job['summary']['case']='long metadata '*2000
    with pytest.raises(ValueError,match='too long'):
        one_page_pdf(job)
