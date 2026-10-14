import numpy as np
import pytest
from trinetra.terrain.hand import drainage_hand
from trinetra.flood_detection.baseline import detect_flood, detect_filtered_flood, BaselineConfig


def test_flowpath_height_not_euclidean_distance():
    dem=np.array([[9,8,7,8,9],[8,7,6,7,8],[7,6,5,6,7],[6,5,4,5,6],[5,4,3,4,5]],float)
    hand,_,r=drainage_hand(dem,20,.0008)
    assert hand[0,0]==2 and hand[2,2]==0
    assert np.isnan(hand[-1]).all()
    assert r['hand_known_cells']==18


def test_zero_hole_and_non_draining_cells_remain_unknown():
    dem=np.full((5,5),10.);dem[2,2]=0
    hand,_,r=drainage_hand(dem,20,100)
    assert np.isnan(hand).all()
    assert r['valid_cells']==24


def test_depression_conditioning_is_reported_not_negative_hand():
    dem=np.array([[8,8,8,8,8],[8,7,7,7,8],[8,7,1,7,8],[8,7,7,7,8],[8,8,8,8,8]],float)
    hand,_,r=drainage_hand(dem,20,.0008)
    assert r['depression_raised_cells']>0
    assert np.all(hand[np.isfinite(hand)]>=0)


def test_invalid_drainage_threshold_is_rejected():
    with pytest.raises(ValueError):drainage_hand(np.ones((3,3)),20,0)


def test_hand_replaces_slope_cap_preserves_unknown_and_filtered_rules():
    shape=(9,9);before=np.full((2,*shape),-10.,dtype='float32')
    after=np.empty_like(before);after[0]=-20;after[1]=-27
    slope=np.full(shape,35.,dtype='float32');obs=np.ones(shape,dtype='uint8')
    cfg=BaselineConfig(min_area_m2=400,min_filter_neighbors=1)
    raw=detect_flood(before,after,after-before,obs,slope,config=cfg)
    assert (raw['flood_mask']==255).all()
    allowed=np.ones(shape,dtype=bool);allowed[4,4]=False
    hand=detect_flood(before,after,after-before,obs,slope,config=cfg,terrain_allowed=allowed)
    assert hand['flood_mask'][4,4]==255 and hand['candidate_pixels']==80
    reused=detect_filtered_flood(hand['filtered_before_db'],hand['filtered_after_db'],obs,slope,config=cfg,terrain_allowed=allowed)
    assert np.array_equal(hand['flood_mask'],reused['flood_mask'])


def test_bad_terrain_eligibility_never_broadcasts():
    a=np.full((2,5,5),-10.,dtype='float32');obs=np.ones((5,5),dtype='uint8');slope=np.zeros((5,5))
    with pytest.raises(ValueError):detect_flood(a,a,a-a,obs,slope,terrain_allowed=np.ones((1,5),bool))


def test_ai_variant_rejection_is_unknown_cache_and_original_are_preserved(tmp_path):
    import json
    from affine import Affine
    from trinetra.downloads.sentinel1 import atomic_json, _sha256
    from trinetra.preprocessing.io import write_tif
    from trinetra.terrain.pipeline import generate, PARAMETERS
    import rasterio
    folder=tmp_path/'trishuli';rawfolder=folder/'ai_flood_experimental';rawfolder.mkdir(parents=True)
    grid={'crs':'EPSG:32645','transform':Affine(10,0,300000,0,-10,3100000),'width':24,'height':24}
    obsgrid={**grid,'transform':grid['transform']*Affine.scale(2,2),'width':12,'height':12}
    obs=write_tif(folder/'observability.tif',np.ones((12,12),dtype='uint8'),obsgrid,descriptions=['observable'],nodata=255)
    mask=write_tif(rawfolder/'mask.tif',np.ones((24,24),dtype='uint8'),grid,descriptions=['candidate'],nodata=255)
    score=write_tif(rawfolder/'score.tif',np.full((24,24),.7,dtype='float32'),grid,descriptions=['score'])
    physical={'case':'trishuli','source_pair':{'pre':'a','post':'b'},'output_sha256':{'observability':_sha256(obs)}}
    atomic_json(folder/'processing_manifest.json',physical)
    raw={'case':'trishuli','status':'complete_experimental','source_pair':physical['source_pair'],
         'outputs':{'mask':str(mask),'score':str(score)},'output_sha256':{'mask':_sha256(mask),'score':_sha256(score)},
         'signature':{'checkpoint':'known-model'},'limitations':[]}
    atomic_json(rawfolder/'inference_manifest.json',raw)
    terrain=folder/'terrain_hand';terrain.mkdir()
    hgrid={**grid,'transform':grid['transform']*Affine.scale(6,6),'width':4,'height':4}
    heights=np.tile(np.array([0,9,11,np.nan],dtype='float32'),(4,1))
    heightpath=write_tif(terrain/'hand.tif',heights,hgrid,descriptions=['HAND'])
    cache={'outputs':{'1.0':{'path':str(heightpath),'sha256':_sha256(heightpath)}}}
    atomic_json(terrain/'hand_manifest.json',cache)
    original_hash=_sha256(mask)
    result=generate(folder,'experimental_ai',cache,folder)
    with rasterio.open(result['outputs']['mask']) as src:m=src.read(1)
    with rasterio.open(result['outputs']['score']) as src:p=src.read(1)
    assert np.all(m[:,:12]==1) and np.all(m[:,12:]==255)
    assert np.isnan(p[:,12:]).all() and _sha256(mask)==original_hash
    assert result['signature']['checkpoint']=='known-model'
    assert result['candidate_area_m2']==28800 and result['unknown_inside_aoi_pixels']==288
    assert result['method']=='experimental_ai_hand'
    assert generate(folder,'experimental_ai',cache,folder)['hand_signature']==result['hand_signature']
    with open(result['outputs']['mask'],'ab') as stream:stream.write(b'corruption')
    with pytest.raises(ValueError,match='output changed'):generate(folder,'experimental_ai',cache,folder)


def test_hand_lock_cleanup_and_existing_lock_refusal(tmp_path,monkeypatch):
    from trinetra.terrain import pipeline
    from trinetra.downloads.sentinel1 import atomic_json
    atomic_json(tmp_path/'processing_manifest.json',{'status':'complete'})
    monkeypatch.setattr(pipeline,'source_case',lambda folder,physical:folder)
    monkeypatch.setattr(pipeline,'terrain',lambda *args:{})
    monkeypatch.setattr(pipeline,'generate',lambda *args:{'done':True})
    assert pipeline.ensure_variants(tmp_path,methods=('baseline',))['baseline']['done']
    assert not (tmp_path/'.hand.lock').exists()
    def fail(*args):raise ValueError('deliberate failure')
    monkeypatch.setattr(pipeline,'generate',fail)
    with pytest.raises(ValueError,match='deliberate'):pipeline.ensure_variants(tmp_path)
    assert not (tmp_path/'.hand.lock').exists()
    (tmp_path/'.hand.lock').write_text('active')
    with pytest.raises(ValueError,match='lock exists'):pipeline.ensure_variants(tmp_path)
    assert (tmp_path/'.hand.lock').read_text()=='active'
