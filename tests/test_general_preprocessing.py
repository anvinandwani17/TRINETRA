"""Tiny geometry/manifest fixtures; no real SNAP result is fabricated."""
import copy
from pathlib import Path
from unittest.mock import patch
import zipfile

import numpy as np
import pytest

from trinetra.core.cases import make_case
from trinetra.downloads.sentinel1 import DownloadError, _sha256, atomic_json
from trinetra.preprocessing.io import common_grid
from trinetra.preprocessing.pipeline import checked_pair, preprocess_case


@pytest.mark.parametrize('bbox,crs', [([85.2,26.85,85.25,26.9], 'EPSG:32645'),
                                   ([-122.42,37.7,-122.38,37.74], 'EPSG:32610'),
                                   ([-70.02,-28.02,-69.98,-27.98], 'EPSG:32719'),
                                   ([5.98,29.98,6.02,30.02], 'EPSG:32632')])
def test_grid_uses_centroid_zone_and_hemisphere(bbox, crs):
    case = make_case(bbox, '2026-09-10')
    grid, inside, context = common_grid(case.aoi)
    assert grid['crs'] == crs
    assert grid['transform'].a == 20 and grid['transform'].e == -20
    assert grid['transform'].c % 20 == grid['transform'].f % 20 == 0
    assert inside.dtype == bool and inside.any()
    assert 'POLYGON' in context


@pytest.mark.parametrize('spacing', [0, -20, float('nan'), float('inf'), True])
def test_invalid_grid_spacing(spacing):
    case = make_case([85.2,26.85,85.25,26.9], '2026-09-10')
    with pytest.raises(DownloadError, match='spacing'):
        common_grid(case.aoi, resolution=spacing)


def fixture_manifest(tmp_path):
    case = make_case([-122.42,37.7,-122.38,37.74], '2026-09-10')
    pair = {'pre_id': 'S1D_IW_GRDH_1SDV_TEST_PRE', 'post_id': 'S1D_IW_GRDH_1SDV_TEST_POST',
            'pre_datetime': '2026-08-31T00:00:00Z', 'pre_end_datetime': '2026-08-31T00:01:00Z',
            'post_datetime': '2026-09-12T00:00:00Z', 'post_end_datetime': '2026-09-12T00:01:00Z',
            'event_start_utc': '2026-09-10T00:00:00Z', 'event_end_utc': '2026-09-11T00:00:00Z',
            'date_policy': 'whole_utc_day_excluded', 'relative_orbit': 121,
            'orbit_direction': 'descending', 'search_days': 30,
            'coverage_pre': 1.0, 'coverage_post': 1.0, 'coverage_common': 1.0,
            'source': 'https://stac.dataspace.copernicus.eu/v1', 'collection': 'sentinel-1-grd',
            'instrument_mode': 'IW', 'product_type': 'IW_GRDH_1S', 'polarizations': ['VV','VH'],
            'explicit_test_fixture': True}
    manifest = {'case': case.case_id, 'flood_date': case.flood_date, 'aoi': case.aoi,
                'request_sha256': case.fingerprint, 'aoi_fingerprint': case.fingerprint,
                'status': 'complete', 'pair': pair, 'products': {}, 'resolved_metadata': {}}
    for role in ['pre','post']:
        name = pair[role + '_id'] + '.SAFE'
        raw = tmp_path / 'raw' / (name + '.zip')
        raw.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr(name + '/manifest.safe', '<root><software name="Sentinel-1 IPF" version="004.03"/></root>')
        # Deliberately miniature test input: no claim of SAFE/raster validity.
        manifest['products'][role] = {'product_uuid': role, 'name': name, 'path': str(raw),
            'verification': {'status': 'valid', 'bytes': raw.stat().st_size, 'sha256': _sha256(raw)}}
        manifest['resolved_metadata'][role] = {'Id': role, 'Name': name}
    path = tmp_path / 'runs' / case.case_id / 'download_manifest.json'
    atomic_json(path, manifest)
    return case, manifest, path


def test_generic_identity_and_actual_file_hash_enforced(tmp_path):
    case, manifest, path = fixture_manifest(tmp_path)
    assert len(checked_pair(manifest)) == 2
    bad = copy.deepcopy(manifest)
    bad['request_sha256'] = 'wrong'
    with pytest.raises(DownloadError, match='identity'):
        checked_pair(bad)
    Path(manifest['products']['post']['path']).write_bytes(b'tampered fixture')
    with pytest.raises(DownloadError, match='changed'):
        checked_pair(manifest)


@pytest.mark.parametrize('field,value', [('coverage_common', 0.98),
                                        ('event_start_utc', '2026-09-09T00:00:00Z'),
                                        ('pre_end_datetime', '2026-09-10T00:00:00Z'),
                                        ('post_datetime', '2026-09-10T23:00:00Z'),
                                        ('source', 'https://example.invalid')])
def test_unsuitable_pair_refused(tmp_path, field, value):
    _, manifest, _ = fixture_manifest(tmp_path)
    manifest['pair'][field] = value
    with pytest.raises(DownloadError):
        checked_pair(manifest)


def test_prepare_only_writes_real_graphs_without_gpt_or_rasters(tmp_path):
    case, _, path = fixture_manifest(tmp_path)
    with patch('trinetra.preprocessing.pipeline.subprocess.run', side_effect=AssertionError('GPT forbidden')):
        report = preprocess_case(path, prepare_only=True)
    output = tmp_path / 'processed' / case.case_id
    assert report['case'] == case.case_id
    assert report['status'] == 'graphs_prepared_only'
    assert report['grid']['crs'] == 'EPSG:32610'
    assert report['outputs'] == {}
    assert len(list(output.rglob('*.graph.xml'))) == 4
    assert not list(output.rglob('*.tif'))
    assert not (tmp_path / 'processed/trishuli').exists()


def test_wrong_output_location_refused(tmp_path):
    _, _, path = fixture_manifest(tmp_path)
    with pytest.raises(DownloadError, match='own'):
        preprocess_case(path, output=tmp_path / 'processed/trishuli', prepare_only=True)


def test_complete_manifest_not_downgraded_by_prepare_only(tmp_path):
    case, manifest, path = fixture_manifest(tmp_path)
    output = tmp_path / 'processed' / case.case_id
    record = output / 'processing_manifest.json'
    atomic_json(record, {'case': case.case_id, 'source_pair': manifest['pair'],
                         'request_sha256': case.fingerprint, 'status': 'complete'})
    before = record.read_bytes()
    with pytest.raises(DownloadError, match='already processed'):
        preprocess_case(path, output=output, prepare_only=True)
    assert record.read_bytes() == before
