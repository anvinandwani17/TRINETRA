"""General-AI request routing, identity, cache and unknown preservation fixtures."""
import asyncio
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
import pytest
import rasterio
from affine import Affine
from fastapi.testclient import TestClient
from api.main import app
from api import integrated_jobs as jobs
from api.satellite import load_satellite
from trinetra.core.ai_contract import general_identity, clip_observability
from trinetra.core.files import atomic_json, _sha256
from trinetra.flood_detection.pipeline import run_baseline
from trinetra.preprocessing.ai_sigma0 import preprocess_ai_sigma0
from network.verified_osm import empty, FILTERS

spec=importlib.util.spec_from_file_location('general_ai_fixture',Path(__file__).with_name('test_general_baseline.py'))
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


@pytest.fixture
def physical(tmp_path,monkeypatch):
    root=tmp_path/'data';path,parent,_=helper.fixture(root,changed=False)
    run_baseline(path)
    monkeypatch.setenv('TRINETRA_DATA_DIR',str(root))
    parent.update(resolution_m=20,raw_sha256={'pre':'test-only','post':'test-only'},snap_gpt='test-only-gpt',dem='synthetic fixture')
    atomic_json(path,parent)
    return root,path,parent


def fake_ai(parent_path,parent):
    folder=parent_path.parent/'ai_flood_experimental';folder.mkdir(exist_ok=True)
    with rasterio.open(parent['outputs']['observability']) as src:
        obs=np.repeat(np.repeat(src.read(1),2,0),2,1)
        grid={'crs':src.crs,'transform':src.transform*Affine.scale(.5,.5),'width':src.width*2,'height':src.height*2}
    obs=clip_observability(obs,grid,parent['aoi'])
    mask=np.where(obs==1,0,255).astype('uint8');score=np.where(obs==1,.1,np.nan).astype('float32')
    outputs={}
    for name,data in [('mask',mask),('score',score)]:
        target=folder/(name+'.tif')
        with rasterio.open(target,'w',driver='GTiff',count=1,dtype=data.dtype,nodata=255 if name=='mask' else np.nan,**grid) as dst:dst.write(data,1)
        outputs[name]=str(target)
    geo=folder/'flood_candidates.geojson';atomic_json(geo,{'type':'FeatureCollection','features':[]});outputs['geojson']=str(geo)
    atomic_json(folder/'inference_manifest.json',{'case':parent['case'],'request_sha256':parent['request_sha256'],
        'parent_processing_manifest_sha256':_sha256(parent_path),'status':'complete_experimental',
        'outputs':outputs,'output_sha256':{k:_sha256(v) for k,v in outputs.items()},
        'source_pair':parent['source_pair'],'signature':{'checkpoint':'fixture-only'},'limitations':['Synthetic test only']})


def test_general_ai_loader_keeps_method_and_identity_separate(physical):
    _,path,parent=physical;fake_ai(path,parent)
    result=load_satellite(parent['aoi'],parent['flood_date'],'experimental_ai')
    assert result['summary']['method']=='experimental_ai' and result['summary']['resolution_m']==10
    assert result['provenance']['case']==parent['case'] and result['summary']['candidate_pixels']==0
    marker=path.parent/'ai_flood_experimental/inference_manifest.json';record=json.loads(marker.read_text())
    record['request_sha256']='wrong';atomic_json(marker,record)
    with pytest.raises(ValueError,match='identity'):load_satellite(parent['aoi'],parent['flood_date'],'experimental_ai')


def test_general_sigma_prepare_only_preserves_parent_and_baseline(physical):
    _,path,parent=physical
    for role in ['pre','post']:
        folder=path.parent/'intermediate'/role;source=folder/'source.dim';source.write_text('test-only physical source')
        graph=f'<graph><node id="read"><operator>Read</operator><parameters><file>{source}</file></parameters></node><node id="cal"><operator>Calibration</operator><parameters/></node><node id="tc"><operator>Terrain-Correction</operator><parameters/></node><node id="write"><operator>Write</operator><parameters/></node></graph>'
        (folder/'radiometry.graph.xml').write_text(graph)
    before=_sha256(path);baseline=_sha256(path.parent/'baseline/flood_mask.tif')
    result=preprocess_ai_sigma0(path,prepare_only=True)
    assert result['case']==parent['case'] and result['status']=='graphs_prepared_only'
    assert result['parent_processing_manifest_sha256']==before
    assert _sha256(path)==before and _sha256(path.parent/'baseline/flood_mask.tif')==baseline
    assert not (path.parent/'ai_sigma0_10m/.ai_preprocess.lock').exists()


def test_bad_case_identity_is_rejected_before_ai_graphs(physical):
    _,path,parent=physical;parent['request_sha256']='wrong';atomic_json(path,parent)
    with pytest.raises(ValueError,match='identity'):preprocess_ai_sigma0(path,True)
    assert not (path.parent/'ai_sigma0_10m').exists()


def test_custom_ai_request_orchestrates_then_reports(physical,monkeypatch):
    root,path,parent=physical
    from trinetra.core import general_pipeline,general_ai
    observed={}
    monkeypatch.setattr(general_pipeline,'ensure_baseline',lambda *a,**k:{'trace':{'status':'satellite_ready','test_fixture':True}})
    def ensure(*a,**k):
        observed.update(k);k['progress']('AI_PREPROCESSING','Fixture preparation',{})
        fake_ai(path,parent);k['progress']('INFERENCE','Fixture inference',{})
        return {'trace':{'status':'satellite_ready','test_fixture':True}}
    monkeypatch.setattr(general_ai,'ensure_ai',ensure)
    monkeypatch.setattr(jobs,'fetch_infrastructure',lambda *a:({n:empty() for n in FILTERS},{'snapshot_requested':None,'layers':{n:{'status':'unavailable'} for n in FILTERS}},['Offline fixture']))
    with TestClient(app) as client:
        response=client.post('/api/v1/pipeline-jobs',json={'aoi':parent['aoi'],'flood_date':parent['flood_date'],'config':'experimental_ai','allow_downloads':False})
        assert response.status_code==202
        for _ in range(100):
            state=client.get('/api/v1/jobs/'+response.json()['id']).json()
            if state['state'] in {'COMPLETE','FAILED'}:break
            time.sleep(.02)
        assert state['state']=='COMPLETE',state['message']
        assert state['summary']['method']=='experimental_ai'
        assert {'AI_PREPROCESSING','INFERENCE'}<={x['stage'] for x in state['stage_history']}
        assert state['provenance']['pipeline_trace']['ai']['test_fixture'] is True
        assert client.get('/api/v1/jobs/'+state['id']+'/export').status_code==200


def test_completed_ai_input_cache_corruption_is_not_repaired(physical):
    _,path,parent=physical
    out=path.parent/'ai_sigma0_10m';out.mkdir()
    atomic_json(out/'ai_processing_manifest.json',{'status':'complete_experimental','parent_processing_manifest_sha256':_sha256(path),
        'outputs':{'observability':str(out/'missing.tif')},'output_sha256':{'observability':'wrong'}})
    with pytest.raises(ValueError,match='missing/corrupt'):preprocess_ai_sigma0(path)


def test_unknown_and_outside_pixels_survive_clipping():
    grid={'crs':'EPSG:4326','transform':Affine(.1,0,0,0,-.1,.2)}
    obs=np.array([[1,0],[255,1]],dtype='uint8')
    aoi={'type':'Polygon','coordinates':[[[0,0],[.1,0],[.1,.2],[0,.2],[0,0]]]}
    result=clip_observability(obs,grid,aoi)
    assert result.tolist()==[[1,255],[255,255]]


def test_general_frozen_inference_roundtrip_and_cache(physical):
    torch=pytest.importorskip('torch',reason='Numerical inference requires laptop PyTorch')
    from trinetra.models.siamese_unet import SiameseUNet
    from trinetra.models.checkpoint import EXPECTED_TRANSFORM
    from trinetra.preprocessing.ai_sigma0 import write_raster
    from trinetra.inference.trishuli import run_case
    root,path,parent=physical
    folder=path.parent/'ai_sigma0_10m';folder.mkdir()
    with rasterio.open(parent['outputs']['observability']) as src:
        obs=np.repeat(np.repeat(src.read(1),2,0),2,1)
        grid={'crs':src.crs,'transform':src.transform*Affine.scale(.5,.5),'width':src.width*2,'height':src.height*2}
    obs=clip_observability(obs,grid,parent['aoi'])
    array=np.full((2,*obs.shape),.02,dtype='float32');array[:,obs!=1]=np.nan
    outputs={}
    for key in ['before_sigma0_linear','after_sigma0_linear']:
        outputs[key]=write_raster(folder/(key+'.tif'),array,grid,['VV_linear','VH_linear'],np.nan)
    outputs['observability']=write_raster(folder/'observability.tif',obs,grid,['pair_observability'],255)
    sigma={'case':parent['case'],'aoi':parent['aoi'],'flood_date':parent['flood_date'],
        'request_sha256':parent['request_sha256'],'status':'complete_experimental','resolution_m':10,
        'radiometry':'linear Sigma0; synthetic fixture','source_pair':parent['source_pair'],'raw_sha256':parent['raw_sha256'],
        'parent_processing_manifest':str(path),'parent_processing_manifest_sha256':_sha256(path),
        'outputs':outputs,'output_sha256':{k:_sha256(v) for k,v in outputs.items()},'limitations':['Synthetic unit fixture only']}
    manifest=folder/'ai_processing_manifest.json';atomic_json(manifest,sigma)
    model=SiameseUNet(4)
    config={'mode':'pilot','base_channels':4}
    data={'train_events':['fixture_train'],'validation_events':['fixture_val'],'test_used':False,'excluded':['Trishuli','EMSR927']}
    saved={'schema_version':1,'model_state':model.state_dict(),'config':config,'data':data,'transform':EXPECTED_TRANSFORM,'threshold':.5,'epoch':0}
    cp=root/'fixture.pt';torch.save(saved,cp)
    rc=root/'run_config.json';atomic_json(rc,{k:saved[k] for k in ['config','data','transform','threshold']})
    output=path.parent/'ai_flood_experimental'
    report=run_case(manifest,cp,rc,output)
    assert report['case']==parent['case'] and report['request_sha256']==parent['request_sha256']
    assert report['training_performed'] is False and report['threshold_tuned'] is False
    with rasterio.open(report['outputs']['mask']) as src:assert np.all(src.read(1)[obs!=1]==255)
    with rasterio.open(report['outputs']['score']) as src:assert np.isnan(src.read(1)[obs!=1]).all()
    first=_sha256(report['outputs']['score'])
    assert run_case(manifest,cp,rc,output)['output_sha256']['score']==first
    with rasterio.open(outputs['before_sigma0_linear'],'r+') as dst:
        value=dst.read();value[:,obs==1]=.03;dst.write(value)
    with pytest.raises(ValueError,match='checksum changed'):run_case(manifest,cp,rc,output)
