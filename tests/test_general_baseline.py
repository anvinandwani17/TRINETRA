"""Explicit tiny synthetic fixtures; these are not Terai or Trishuli results."""
from pathlib import Path

import numpy as np
import pytest
import rasterio

from trinetra.core.cases import make_case
from trinetra.downloads.sentinel1 import atomic_json, _sha256, DownloadError
from trinetra.preprocessing.io import common_grid
from trinetra.flood_detection.pipeline import load_inputs, run_baseline
from trinetra.flood_detection.verify import verify_baseline


def fixture(tmp_path, *, southern=False, changed=True):
    bbox = [-70,-28,-69.996,-27.996] if southern else [-122.40,37.70,-122.396,37.704]
    case = make_case(bbox, '2026-09-10')
    grid, inside, _ = common_grid(case.aoi)
    shape = (grid['height'],grid['width'])
    obs = np.where(inside,1,255).astype(np.uint8)
    obs[8:11,8:11] = 0
    before = np.stack([np.full(shape,-10),np.full(shape,-17)]).astype(np.float32)
    after = before - 10 if changed else before.copy()
    before[:,obs!=1] = np.nan
    after[:,obs!=1] = np.nan
    pre_mask = np.where(obs==0,2,0).astype(np.uint8)
    post_mask = np.zeros(shape,dtype=np.uint8)
    pre_mask[obs==255] = post_mask[obs==255] = 255
    arrays = {'before_gamma0_db':before, 'after_gamma0_db':after, 'change_db':after-before,
              'observability':obs, 'quality_reasons':np.where(obs==0,16,np.where(obs==255,128,0)).astype(np.uint16),
              'pre_layover_shadow':pre_mask, 'post_layover_shadow':post_mask}
    pair = {'pre_id':'S1D_IW_GRDH_1SDV_UNIT_PRE','post_id':'S1D_IW_GRDH_1SDV_UNIT_POST',
            'pre_datetime':'2026-08-31T00:00:00Z','pre_end_datetime':'2026-08-31T00:01:00Z',
            'post_datetime':'2026-09-12T00:00:00Z','post_end_datetime':'2026-09-12T00:01:00Z',
            'event_start_utc':'2026-09-10T00:00:00Z','event_end_utc':'2026-09-11T00:00:00Z',
            'date_policy':'whole_utc_day_excluded','relative_orbit':121,'orbit_direction':'descending',
            'search_days':30,'coverage_pre':1.0,'coverage_post':1.0,'coverage_common':1.0,
            'source':'https://stac.dataspace.copernicus.eu/v1','collection':'sentinel-1-grd',
            'instrument_mode':'IW','product_type':'IW_GRDH_1S','polarizations':['VV','VH'],
            'explicit_test_fixture':True}
    root = tmp_path/'processed'/case.case_id
    root.mkdir(parents=True)
    report = {'case':case.case_id,'status':'complete','flood_date':case.flood_date,
              'aoi':case.aoi,'request_sha256':case.fingerprint,'source_pair':pair,
              'grid':{**{k:v for k,v in grid.items() if k!='transform'},'transform':list(grid['transform'])[:6]},
              'checks':{'shared_crs_transform_shape':'passed','geotiff_roundtrip':'passed',
                        'unknown_change_is_nan':True,'shadow_layover_never_observable':True},
              'outputs':{},'output_sha256':{}}
    for key, array in arrays.items():
        values = array if array.ndim==3 else array[None]
        path = root/(key+'.tif')
        with rasterio.open(path,'w',driver='GTiff',**grid,count=len(values),dtype=values.dtype) as dst:
            dst.write(values)
            if len(values)==2:
                dst.descriptions=('VV_dB','VH_dB')
                dst.update_tags(band_order='VV,VH')
        report['outputs'][key]=str(path)
        report['output_sha256'][key]=_sha256(path)
    for role in ['pre','post']:
        folder=root/'intermediate'/role
        folder.mkdir(parents=True)
        dem=folder/'elevation.tif'
        with rasterio.open(dem,'w',driver='GTiff',**grid,count=1,dtype='float32') as dst:
            dst.write(np.full((1,*shape),100,dtype=np.float32))
        atomic_json(folder/'radiometry.verified.json',{'bands':{'elevation':str(dem)},'files':{str(dem):_sha256(dem)}})
    path=root/'processing_manifest.json'
    atomic_json(path,report)
    return path, report, obs


@pytest.mark.parametrize('southern,changed', [(False,True),(True,True),(False,False)])
def test_generic_pipeline_raster_vector_checks_and_unknowns(tmp_path,southern,changed):
    path, processing, obs = fixture(tmp_path,southern=southern,changed=changed)
    report=run_baseline(path)
    assert report['case']==processing['case']
    assert report['status']=='complete'
    assert report['grid']==processing['grid']
    assert report['config']['max_slope_deg']==10
    assert report['config']['min_area_m2']==2000
    assert report['score_is_probability'] is False
    summary=verify_baseline(path.parent/'baseline/baseline_manifest.json')
    with rasterio.open(report['outputs']['flood_mask']) as src:
        mask=src.read(1)
        assert np.all(mask[obs!=1]==255)
        assert src.crs.to_string()==processing['grid']['crs']
    assert summary['unknown_aoi_pixels']>0
    assert summary['candidate_pixels']>0 if changed else summary['candidate_pixels']==0


@pytest.mark.parametrize('field,value', [('request_sha256','wrong'),('flood_date','2026-09-11'),('status','failed')])
def test_processing_identity_or_status_changes_refused(tmp_path,field,value):
    path, report, _ = fixture(tmp_path)
    report[field]=value
    atomic_json(path,report)
    with pytest.raises((DownloadError,ValueError)):
        load_inputs(path)


def test_changed_processing_raster_refused(tmp_path):
    path, report, _=fixture(tmp_path)
    Path(report['outputs']['change_db']).write_bytes(b'corrupted fixture')
    with pytest.raises(DownloadError,match='checksum'):
        load_inputs(path)


def test_false_observability_check_refused(tmp_path):
    path, report, _=fixture(tmp_path)
    report['checks']['shadow_layover_never_observable']=False
    atomic_json(path,report)
    with pytest.raises(DownloadError,match='quality checks'):
        load_inputs(path)


def test_wrong_case_output_folder_refused(tmp_path):
    path, _, _=fixture(tmp_path)
    with pytest.raises(DownloadError,match='own case'):
        run_baseline(path, output=tmp_path/'processed/trishuli/baseline')


def test_corrupted_baseline_output_refused(tmp_path):
    path, _, _=fixture(tmp_path)
    report=run_baseline(path)
    Path(report['outputs']['flood_mask']).write_bytes(b'corrupted fixture')
    with pytest.raises(ValueError,match='checksum'):
        verify_baseline(path.parent/'baseline/baseline_manifest.json')
