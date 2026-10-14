"""Opt-in experimental HAND variants; originals and unknown semantics retained."""
import json
import math
import os
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling
from trinetra.downloads.sentinel1 import atomic_json, _sha256, now
from trinetra.flood_detection.baseline import detect_filtered_flood, BaselineConfig
from trinetra.preprocessing.io import write_tif
from .hand import drainage_hand

PARAMETERS = {'routing_resolution_m': 60, 'drainage_area_km2': 1.0, 'maximum_hand_m': 10.0,
              'dem_pair_max_difference_m': 20, 'algorithm': 'priority-flood-D8-original-elevation-HAND-v1'}
LIMITATIONS = [
    'Experimental HAND screen: 60 m routing grid derived from Copernicus DEM; it adds no terrain resolution',
    '1 km2 drainage initiation and 10 m HAND ceiling are provisional, not calibrated to flood labels',
    'HAND does not model flood depth, rainfall, pluvial floods or debris flows',
    'Bounded drainage context and missing DEM can truncate contributing areas and change channels',
    'Zero/nonpositive DEM values are unsupported in this positive-elevation pilot; this is not a sea-level terrain method',
    'HAND-rejected and unresolved pixels are unknown, not dry; lower candidate counts are not improved accuracy',
    'Baseline HAND retains radiometric rules and valid slope neighborhoods, replaces the 10 degree slope cap, and uses radiometric evidence margins without slope weighting',
]


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def resample(array, transform, crs, grid):
    target = np.full((grid['height'], grid['width']), np.nan, dtype='float32')
    reproject(array, target, src_transform=transform, src_crs=crs, src_nodata=np.nan,
              dst_transform=grid['transform'], dst_crs=grid['crs'], dst_nodata=np.nan,
              resampling=Resampling.nearest)
    return target


def source_case(folder, physical):
    if all((folder/'intermediate'/role/'radiometry.verified.json').is_file() for role in ['pre','post']):
        source = folder
    elif physical['case'] == 'trishuli' and os.getenv('TRINETRA_HAND_SOURCE_CASE_DIR'):
        source = Path(os.environ['TRINETRA_HAND_SOURCE_CASE_DIR']).resolve()
    else:
        raise ValueError('HAND needs verified cached terrain/radiometry. For saved Trishuli set TRINETRA_HAND_SOURCE_CASE_DIR to its original processed folder')
    original = read(source/'processing_manifest.json')
    for key in ['case','source_pair','raw_sha256']:
        if original.get(key) != physical.get(key):
            raise ValueError('HAND source physical case identity differs: '+key)
    if original.get('status') != 'complete' or original['output_sha256']['observability'] != physical['output_sha256']['observability']:
        raise ValueError('HAND source observation provenance differs')
    return source


def terrain(folder, physical, source):
    root = folder/'terrain_hand'
    marker = root/'hand_manifest.json'
    dem_paths = []; sources = {}
    for role in ['pre','post']:
        verification = source/'intermediate'/role/'radiometry.verified.json'
        record = read(verification); dem = Path(record['bands']['elevation']).resolve()
        # Cached source must remain inside the verified original case's role folder.
        if not dem.is_relative_to((source/'intermediate'/role).resolve()):
            raise ValueError('HAND DEM escapes verified source case')
        actual = _sha256(dem)
        if actual != record.get('files',{}).get(str(dem)):
            raise ValueError('HAND DEM checksum differs from verified terrain cache')
        sources[role] = {'path':str(dem),'sha256':actual,'verification_sha256':_sha256(verification)}
        dem_paths.append(dem)
    signature = {'physical_manifest_sha256':_sha256(folder/'processing_manifest.json'),
        'sources':sources,'parameters':PARAMETERS,'algorithm_sha256':_sha256(Path(__file__).with_name('hand.py'))}
    if marker.is_file():
        cache=read(marker)
        if cache.get('status') != 'complete_experimental' or cache.get('signature') != signature:
            raise ValueError('HAND cache provenance changed; preserve the old cache and use a separate review folder')
        for role, item in cache['outputs'].items():
            path=root/Path(item['path']).name
            if _sha256(path)!=item['sha256']:raise ValueError('HAND cached output changed: '+role)
        return cache
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):raise ValueError('Incomplete HAND cache exists; preserve/inspect it before retrying')
    with rasterio.open(dem_paths[0]) as s:
        metric = bool(s.crs and s.crs.is_projected and np.isclose(s.crs.linear_units_factor[1],1.0,atol=1e-9,rtol=0))
        if not metric or s.count!=1 or not np.isfinite(tuple(s.transform)[:6]).all() or abs(s.transform.determinant)<1e-12:
            raise ValueError(f'HAND needs a valid projected metre-unit single-band DEM: crs={s.crs}, units={s.crs.linear_units_factor if s.crs and s.crs.is_projected else None}, bands={s.count}, transform={s.transform}')
        # Native SNAP ENVI may name metre units differently or retain small shear.
        # Reprojection below handles the source affine; routing uses a new north-up grid.
        resolution=PARAMETERS['routing_resolution_m']
        grid={'transform':Affine(resolution,0,s.bounds.left,0,-resolution,s.bounds.top),'crs':s.crs.to_string(),
              'width':math.ceil((s.bounds.right-s.bounds.left)/resolution),'height':math.ceil((s.bounds.top-s.bounds.bottom)/resolution)}
    if grid['width']*grid['height']>2_000_000:raise ValueError('HAND context exceeds the bounded routing grid')
    arrays=[]
    for dem in dem_paths:
        with rasterio.open(dem) as s:
            a=s.read(1,masked=True).astype('float32').filled(np.nan);a[a<=0]=np.nan
            dest=np.full((grid['height'],grid['width']),np.nan,dtype='float32')
            reproject(a,dest,src_transform=s.transform,src_crs=s.crs,src_nodata=np.nan,
                dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=np.nan,resampling=Resampling.bilinear)
            arrays.append(dest)
    dem=(arrays[0]+arrays[1])/2;dem[np.abs(arrays[0]-arrays[1])>PARAMETERS['dem_pair_max_difference_m']]=np.nan
    outputs={};stats={}
    for drainage in [.5,1.0,2.0]:
        heights,_,detail=drainage_hand(dem,resolution,drainage)
        filename='hand_'+str(drainage)+'.tif'
        path=write_tif(root/filename,heights,grid,nodata=np.nan,descriptions=['experimental_height_above_nearest_drainage_m'])
        outputs[str(drainage)]={'path':str(path),'sha256':_sha256(path)};stats[str(drainage)]=detail
    cache={'status':'complete_experimental','case':physical['case'],'signature':signature,
        'outputs':outputs,'routing_statistics':stats,'limitations':LIMITATIONS}
    atomic_json(marker,cache)
    return cache


def checked_raster(folder, manifest, key):
    filename=str(manifest['outputs'][key]).replace('\\','/').split('/')[-1]
    path=folder/filename
    if _sha256(path)!=manifest.get('output_sha256',{}).get(key):raise ValueError('Original detector output changed: '+key)
    with rasterio.open(path) as s:
        if s.count!=1 or not s.crs or not s.crs.is_projected or s.crs.linear_units!='metre' or s.transform.b or s.transform.d:
            raise ValueError('Expected metric north-up detector raster')
        return s.read(1),{'crs':s.crs.to_string(),'transform':s.transform,'width':s.width,'height':s.height},path


def hand_on_grid(folder, cache, grid, drainage=1.0):
    path=folder/'terrain_hand'/Path(cache['outputs'][str(drainage)]['path']).name
    with rasterio.open(path) as s:return resample(s.read(1),s.transform,s.crs,grid)


def generate(folder, method, cache, source):
    physical=read(folder/'processing_manifest.json')
    ai=method=='experimental_ai';base=folder/('ai_flood_experimental' if ai else 'baseline')
    filename='inference_manifest.json' if ai else 'baseline_manifest.json';raw=read(base/filename)
    if raw.get('status')!=('complete_experimental' if ai else 'complete') or raw.get('case')!=physical['case']:
        raise ValueError('Original detector is incomplete or belongs to a different case')
    if raw.get('source_pair' if ai else 'pair')!=physical.get('source_pair'):
        raise ValueError('Original detector satellite pair differs')
    maskkey='mask' if ai else 'flood_mask';scorekey='score' if ai else 'evidence_score';geokey='geojson' if ai else 'flood_geojson'
    original,grid,maskpath=checked_raster(base,raw,maskkey)
    score,scoregrid,scorepath=checked_raster(base,raw,scorekey)
    if grid!=scoregrid or not np.isin(original,[0,1,255]).all():raise ValueError('Detector mask/score grid differs or classes invalid')
    if not np.isfinite(score[original!=255]).all():raise ValueError('Original assessable detector scores are missing')
    output=folder/('ai_flood_hand' if ai else 'baseline_hand');marker=output/filename
    signature={'raw_manifest_sha256':_sha256(base/filename),'hand_manifest_sha256':_sha256(folder/'terrain_hand/hand_manifest.json'),
        'variant':method+'_hand','parameters':PARAMETERS,'pipeline_sha256':_sha256(Path(__file__))}
    if marker.is_file():
        record=read(marker)
        if record.get('hand_signature')!=signature:raise ValueError('HAND variant signature differs; preserve old outputs before a separate rerun')
        for k,p in record['outputs'].items():
            if _sha256(output/Path(p).name)!=record['output_sha256'][k]:raise ValueError('HAND variant output changed: '+k)
        return record
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('Incomplete HAND variant folder exists; inspect it before retrying')
    heights=hand_on_grid(folder,cache,grid)
    eligible=np.isfinite(heights)&(heights<=PARAMETERS['maximum_hand_m'])
    if ai:
        mask=original.copy();mask[~eligible]=255
        score=score.copy();score[mask==255]=np.nan
        result={'flood_mask':mask,'evidence_score':score}
    else:
        filtered_folder=base
        if not all((base/str(raw['outputs'][k]).replace('\\','/').split('/')[-1]).is_file() for k in ['filtered_before_db','filtered_after_db','slope_deg']):
            filtered_folder=source/'baseline'
        filtered_manifest=read(filtered_folder/'baseline_manifest.json')
        if filtered_manifest.get('pair')!=physical['source_pair'] or filtered_manifest.get('config')!=raw.get('config'):
            raise ValueError('Cached filtered baseline pair or parameters differ')
        bands=[]
        for key in ['filtered_before_db','filtered_after_db']:
            path=filtered_folder/str(filtered_manifest['outputs'][key]).replace('\\','/').split('/')[-1]
            if _sha256(path)!=filtered_manifest['output_sha256'][key]:raise ValueError('Filtered radiometry checksum differs')
            with rasterio.open(path) as src:
                actual={'crs':src.crs.to_string(),'transform':src.transform,'width':src.width,'height':src.height}
                if src.count!=2 or actual!=grid:raise ValueError('Filtered baseline radiometry grid differs')
                bands.append(src.read())
        slope,slopegrid,_=checked_raster(filtered_folder,filtered_manifest,'slope_deg')
        if slopegrid!=grid:raise ValueError('Slope grid differs')
        with rasterio.open(folder/'observability.tif') as src:
            obs=src.read(1)
            if (src.crs.to_string(),src.transform,src.shape)!=(grid['crs'],grid['transform'],original.shape):
                raise ValueError('Baseline observability grid differs')
        result=detect_filtered_flood(bands[0],bands[1],obs,slope,
            config=BaselineConfig(**raw['config']),terrain_allowed=eligible)
        mask=result['flood_mask'];score=result['evidence_score']
    maskfile=write_tif(output/'flood_candidates.tif',mask,grid,nodata=255,descriptions=['experimental_HAND_candidate_0_1_unknown_255'])
    scorefile=write_tif(output/'evidence_score.tif',score,grid,nodata=np.nan,descriptions=['uncalibrated_experimental_HAND_evidence'])
    from scipy import ndimage
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    labels,_=ndimage.label(mask==1,structure=ndimage.generate_binary_structure(2,1))
    features=[]
    for geometry,label in shapes(labels.astype('int32'),mask=labels>0,transform=grid['transform'],connectivity=4):
        features.append({'type':'Feature','geometry':transform_geom(grid['crs'],'EPSG:4326',geometry),
            'properties':{'classification':'experimental_flood_candidate','method':method+'_hand','score_is_probability':False}})
        if len(features)>10000:raise ValueError('HAND vector export exceeds bounded feature count')
    geo=output/'flood_candidates.geojson';atomic_json(geo,{'type':'FeatureCollection','features':features})
    outputs={maskkey:str(maskfile),scorekey:str(scorefile),geokey:str(geo)}
    # Preview is produced below without changing the raw detector's preview.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap,BoundaryNorm
    image=output/'hand_candidates.png';fig,ax=plt.subplots(figsize=(7,7))
    show=np.where(mask==255,2,mask)
    ax.imshow(show,cmap=ListedColormap(['#eeeeee','#1676d2','#898989']),norm=BoundaryNorm([-.5,.5,1.5,2.5],3),interpolation='nearest')
    ax.set_title(method+' + experimental HAND\nBlue: candidate; grey: unknown');ax.axis('off');fig.tight_layout();fig.savefig(image,dpi=130);plt.close(fig)
    outputs['preview']=str(image)
    record={**raw,'outputs':outputs,'output_sha256':{k:_sha256(p) for k,p in outputs.items()},'hand_signature':signature,
        'parent_processing_manifest_sha256':_sha256(folder/'processing_manifest.json'),
        'candidate_pixels':int((mask==1).sum()),'previews':{'flood_mask':str(image)},
        'limitations':raw.get('limitations',[])+LIMITATIONS,'terrain_screen':{'parameters':PARAMETERS,
            'original_candidate_pixels':int((original==1).sum()),'screened_candidate_pixels':int((mask==1).sum()),
            'raw_manifest_sha256':signature['raw_manifest_sha256'],'hand_manifest_sha256':signature['hand_manifest_sha256']}}
    # Summary counts use physical AOI, not the rectangular raster extent.
    with rasterio.open(folder/'observability.tif') as s:
        obs=s.read(1).astype('float32');obs[obs==255]=np.nan
        inside=np.isfinite(resample(obs,s.transform,s.crs,grid))
    record['terrain_screen']['assessable_fraction']=float((inside&(mask!=255)).sum()/inside.sum())
    record['terrain_screen']['unknown_pixels']=int((inside&(mask==255)).sum())
    record['method']=method+'_hand'
    record['candidate_area_m2']=int((mask==1).sum())*abs(float(grid['transform'].a*grid['transform'].e))
    record['candidate_polygons']=len(features)
    record['unknown_inside_aoi_pixels']=record['terrain_screen']['unknown_pixels']
    record['created_at']=now()
    record['checks']={'raster_roundtrip':'passed','source_checksums':'passed','unsupported_is_unknown':True,'accuracy_validated':False}
    if not ai:
        record['source_detector_config']=raw['config']
        record['config']={**raw['config'],'max_slope_deg':None,'terrain_screen':PARAMETERS}
        record['summary']={'component_count':len(features),'candidate_pixels':record['candidate_pixels'],
            'assessable_pixels':int((mask!=255).sum()),'unknown_pixels':int((mask==255).sum()),
            'candidate_area_m2':record['candidate_area_m2'],
            'candidate_fraction_of_assessable':float((mask==1).sum()/max((mask!=255).sum(),1))}
    for name in ['started_at','completed_at']:
        if name in record:record['source_detector_'+name]=record.pop(name)
    atomic_json(marker,record)
    return record


def ensure_variants(folder, methods=('baseline','experimental_ai')):
    folder=Path(folder).resolve();physical=read(folder/'processing_manifest.json')
    if physical.get('status')!='complete':raise ValueError('HAND needs completed physical preprocessing')
    source=source_case(folder,physical)
    lock=folder/'.hand.lock'
    try:
        with lock.open('x') as s:s.write(str(os.getpid()))
    except FileExistsError:raise ValueError('HAND run lock exists; wait for the active run')
    try:
        cache=terrain(folder,physical,source)
        results={method:generate(folder,method,cache,source) for method in methods}
        return results
    finally:lock.unlink(missing_ok=True)


def sensitivity(folder, cache, source):
    """Descriptive threshold sweep on existing AI output, not label-based tuning."""
    folder=Path(folder);raw=read(folder/'ai_flood_experimental/inference_manifest.json')
    mask,grid,maskpath=checked_raster(folder/'ai_flood_experimental',raw,'mask')
    slope_folder=folder/'baseline'
    if not (slope_folder/'slope_deg.tif').is_file():slope_folder=source/'baseline'
    baseline=read(slope_folder/'baseline_manifest.json');slope,slopegrid,slopepath=checked_raster(slope_folder,baseline,'slope_deg')
    slope=resample(slope,slopegrid['transform'],slopegrid['crs'],grid)
    with rasterio.open(folder/'observability.tif') as src:
        obs=src.read(1).astype('float32');obs[obs==255]=np.nan
        inside=np.isfinite(resample(obs,src.transform,src.crs,grid))
    rows=[]
    for area in [.5,1.0,2.0]:
        heights=hand_on_grid(folder,cache,grid,area)
        for ceiling in [5,10,15]:
            supported=(mask!=255)&np.isfinite(heights)&(heights<=ceiling)
            kept=(mask==1)&supported;count=int(kept.sum());steep=int((kept&(slope>=30)).sum())
            rows.append({'drainage_area_km2':area,'maximum_hand_m':ceiling,
                'retained_candidate_pixels':count,'retained_candidates_at_least_30_deg':steep,
                'steep_fraction_of_retained_candidates':steep/count if count else None,
                'assessable_aoi_fraction':float((inside&supported).sum()/inside.sum())})
    report={'status':'complete_diagnostic_only','rows':rows,'selected_parameters':PARAMETERS,
        'parameters_tuned_to_labels':False,'flood_accuracy_computed':False,
        'original_candidate_pixels':int((mask==1).sum()),'input_sha256':{'ai_mask':_sha256(maskpath),'slope':_sha256(slopepath)},
        'limitations':LIMITATIONS}
    atomic_json(folder/'terrain_hand/sensitivity.json',report)
    return report
