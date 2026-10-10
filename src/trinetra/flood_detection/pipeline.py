"""Verified Trishuli preprocessing to deterministic raster/vector/preview outputs."""
from dataclasses import asdict
from pathlib import Path
import json
import numpy as np
from trinetra.downloads.sentinel1 import atomic_json, _sha256, now, DownloadError
from trinetra.preprocessing.io import _warp, write_tif
from .baseline import BaselineConfig, detect_flood, terrain_slope, REASONS


def validate_band_order(count, descriptions, tags=None):
    """Accept our explicit polarization labels with or without their dB suffix."""
    labels = tuple((name or '').strip().upper() for name in descriptions)
    allowed = ({'VV','VV_DB'}, {'VH','VH_DB'})
    if count!=2 or len(labels)!=2 or any(label not in choices for label,choices in zip(labels,allowed)):
        raise DownloadError(f'Radiometry must contain VV then VH (VV/VH or VV_dB/VH_dB); got {descriptions!r}')
    order = (tags or {}).get('band_order')
    if order is not None and order.replace(' ','').upper()!='VV,VH':
        raise DownloadError('Radiometry band-order tag contradicts VV/VH descriptions')


def load_inputs(manifest_path):
    import rasterio
    manifest_path = Path(manifest_path).resolve()
    report = json.loads(manifest_path.read_text())
    if report.get('status') != 'complete' or report.get('case') != 'trishuli':
        raise DownloadError('Baseline requires complete, verified Trishuli preprocessing')
    arrays, hashes, grid = {}, {}, None
    keys = ['before_gamma0_db','after_gamma0_db','change_db','observability','quality_reasons',
            'pre_layover_shadow','post_layover_shadow']
    for key in keys:
        path = Path(report['outputs'][key])
        digest = _sha256(path)
        if digest != report['output_sha256'][key]:
            raise DownloadError('Preprocessing output checksum changed: '+key)
        hashes[key] = {'path':str(path),'sha256':digest}
        with rasterio.open(path) as src:
            actual = {'crs':src.crs.to_string(),'transform':src.transform,'width':src.width,'height':src.height}
            if src.crs.to_epsg()!=32645 or src.res!=(20,20) or src.transform.b!=0 or src.transform.d!=0:
                raise DownloadError('First baseline requires the north-up Trishuli UTM 45N 20 m grid')
            if grid is not None and actual != grid:
                raise DownloadError('Preprocessing grids differ')
            grid = actual
            if src.width*src.height>4_000_000:
                raise DownloadError('Baseline AOI exceeds bounded first-version grid')
            if key in keys[:3]:
                validate_band_order(src.count,src.descriptions,src.tags())
            if key not in keys[:3] and src.count!=1:
                raise DownloadError('Quality/mask raster must contain one band')
            arrays[key] = src.read() if src.count==2 else src.read(1)
    obs = arrays['observability']
    if not np.isin(obs,[0,1,255]).all():
        raise DownloadError('Unexpected observability codes')
    usable = obs==1
    if not usable.any() or np.any(arrays['quality_reasons'][usable]!=0):
        raise DownloadError('No observations or contradictory preprocessing quality flags')
    for key in keys[:3]:
        if not np.isfinite(arrays[key][:,usable]).all() or not np.isnan(arrays[key][:,~usable]).all():
            raise DownloadError('Preprocessed unknown radiometry policy violated')
    for key in keys[-2:]:
        if not np.isin(arrays[key],[0,1,2,3,255]).all() or np.any(arrays[key][usable]!=0):
            raise DownloadError('Shadow/layover was marked observable')
    # Reuse the already processed DEM; never request a new/global dataset.
    dems = []
    for role in ['pre','post']:
        marker_path = manifest_path.parent/'intermediate'/role/'radiometry.verified.json'
        marker = json.loads(marker_path.read_text())
        dem_path = Path(marker['bands']['elevation'])
        digest = _sha256(dem_path)
        if marker.get('files',{}).get(str(dem_path)) != digest:
            raise DownloadError('SNAP terrain cache checksum changed')
        hashes[role+'_dem'] = {'path':str(dem_path),'sha256':digest}
        dems.append(_warp(dem_path,grid))
    slopes = [terrain_slope(dem) for dem in dems]
    slope = np.maximum(slopes[0],slopes[1])
    # A mismatched/missing DEM is unknown, never automatically non-flooded.
    slope[~np.isfinite(dems[0]) | ~np.isfinite(dems[1]) | (np.abs(dems[0]-dems[1])>20)] = np.nan
    hashes['processing_manifest'] = {'path':str(manifest_path),'sha256':_sha256(manifest_path)}
    return arrays,slope,grid,hashes,report


def polygonize(result,grid):
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    labels = result['labels']
    counts = np.bincount(labels.ravel())
    sums = np.bincount(labels.ravel(),weights=np.nan_to_num(result['evidence_score']).ravel())
    features = []
    for geometry,label in shapes(labels,mask=labels>0,transform=grid['transform'],connectivity=4):
        label = int(label)
        features.append({'type':'Feature','id':label,
            'geometry':transform_geom(grid['crs'],'EPSG:4326',geometry),
            'properties':{'classification':'candidate_flood','method':'deterministic_s1_change_v1',
                          'pixels':int(counts[label]),'area_m2':float(counts[label]*400),
                          'mean_evidence_score':float(sums[label]/counts[label]),
                          'score_is_probability':False}})
    if len(features)>10000:
        raise DownloadError('Vector output exceeds bounded 10,000-feature limit')
    return {'type':'FeatureCollection','features':features,
            'description':'Candidate newly inundated open-water areas; unknown pixels excluded. Coordinates WGS84.'}


def create_previews(folder,result,grid):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    folder.mkdir(parents=True,exist_ok=True)
    t = grid['transform']
    extent = [t.c,t.c+grid['width']*20,t.f-grid['height']*20,t.f]
    mask = result['flood_mask']
    display = np.where(mask==255,2,mask)
    fig,ax = plt.subplots(figsize=(7,7))
    ax.imshow(display,cmap=ListedColormap(['#e8eadf','#167ac6','#929292']),vmin=0,vmax=2,extent=extent,interpolation='nearest')
    ax.set_title('Deterministic candidate flood — not ground truth')
    ax.legend(handles=[Patch(color='#e8eadf',label='No candidate flood evidence'),
                       Patch(color='#167ac6',label='Candidate new inundation'),
                       Patch(color='#929292',label='Unknown / unsupported terrain')],loc='upper left',fontsize=8)
    ax.set_xlabel('UTM easting (m)'); ax.set_ylabel('UTM northing (m)')
    fig.tight_layout(); fig.savefig(folder/'flood_mask.png',dpi=150); plt.close(fig)
    for key,title,limits,cmap in [('evidence_score','Heuristic flood evidence — not probability',(0,1),'viridis'),
                                  ('slope_deg','DEM-derived slope (degrees)',(0,30),'terrain')]:
        fig,ax = plt.subplots(figsize=(7,7))
        colors = plt.get_cmap(cmap).copy(); colors.set_bad('#929292')
        im = ax.imshow(np.ma.masked_invalid(result[key]),cmap=colors,vmin=limits[0],vmax=limits[1],extent=extent,interpolation='nearest')
        ax.set_title(title); fig.colorbar(im,ax=ax)
        ax.set_xlabel('UTM easting (m)'); ax.set_ylabel('UTM northing (m)')
        fig.tight_layout(); fig.savefig(folder/(key+'.png'),dpi=150); plt.close(fig)
    return {name:str((folder/(name+'.png')).resolve()) for name in ['flood_mask','evidence_score','slope_deg']}


def run_baseline(manifest='data/processed/trishuli/processing_manifest.json',
                 output='data/processed/trishuli/baseline',config=None):
    config = config or BaselineConfig(); config.validate()
    folder = Path(output).resolve(); folder.mkdir(parents=True,exist_ok=True)
    source = Path(manifest).resolve().parent
    raw_folder = Path('data/raw').resolve()
    if folder==source or source.is_relative_to(folder) or folder.is_relative_to(raw_folder) or raw_folder.is_relative_to(folder):
        raise DownloadError('Baseline output must be separate from preprocessing outputs and raw data')
    lock = folder/'.baseline.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        raise DownloadError('Baseline output is locked by another run; do not run concurrently') from None
    report = {'status':'running','method':'deterministic_s1_change_v1','case':'trishuli',
              'started_at':now(),'config':asdict(config),'score_is_probability':False,
              'legend':{'0':'no candidate flood evidence; does not certify dry land',
                        '1':'candidate new open-water inundation','255':'unknown or unsupported'},
              'reason_bits':REASONS,
              'limitations':['Thresholds are initial conservative engineering defaults, not locally calibrated accuracy',
                'Single reference cannot establish permanent water; pre-existing low backscatter is only a proxy',
                'May miss flooded vegetation, urban double bounce, narrow channels and steep valley inundation',
                'Wind, sediment, agriculture and registration errors can affect backscatter',
                '20 m grid and DEM slope do not imply 20 m independent terrain resolution']}
    report_path = folder/'baseline_manifest.json'
    try:
        with handle:
            import os
            handle.write(str(os.getpid()))
        atomic_json(report_path,report)
        arrays,slope,grid,hashes,preprocessing = load_inputs(manifest)
        report['inputs'] = hashes
        report['pair'] = preprocessing.get('source_pair',preprocessing.get('pair',{}))
        result = detect_flood(arrays['before_gamma0_db'],arrays['after_gamma0_db'],arrays['change_db'],
                              arrays['observability'],slope,config=config)
        outputs = {}
        for key,description,nodata in [('flood_mask','candidate_flood',255),('evidence_score','uncalibrated_evidence_score',np.nan),
                                      ('reasons','exclusion_and_rule_bits',None),('slope_deg','terrain_slope_degrees',np.nan),
                                      ('filtered_before_db','VV,VH',np.nan),('filtered_after_db','VV,VH',np.nan),
                                      ('filtered_change_db','VV,VH',np.nan)]:
            descriptions = ['VV','VH'] if key.startswith('filtered_') else [description]
            path = write_tif(folder/(key+'.tif'),result[key],grid,descriptions=descriptions,
                             nodata=nodata,tags={'method':report['method'],'score_is_probability':'false',
                             'unknown_policy':'255/NaN unknown; zero is no candidate evidence, not confirmed dry'})
            outputs[key] = str(path.resolve())
        geojson = folder/'flood_candidates.geojson'
        atomic_json(geojson,polygonize(result,grid)); outputs['flood_geojson'] = str(geojson)
        previews = create_previews(folder/'previews',result,grid)
        report.update(outputs=outputs,previews=previews,
                      output_sha256={k:_sha256(p) for k,p in outputs.items()},
                      preview_sha256={k:_sha256(p) for k,p in previews.items()},
                      summary={k:result[k] for k in ['component_count','candidate_pixels','assessable_pixels','unknown_pixels']})
        report['summary']['candidate_area_m2'] = result['candidate_pixels']*400
        report['summary']['candidate_fraction_of_assessable'] = result['candidate_pixels']/max(1,result['assessable_pixels'])
        if result['assessable_pixels']==0:
            raise DownloadError('No terrain-supported observable pixels; refusing a successful flood assessment')
        # Persist a non-complete state until independent raster/vector checks pass.
        report['status'] = 'outputs_written'
        atomic_json(report_path,report)
        from .verify import verify_baseline
        verify_baseline(report_path,allow_pending=True)
        report.update(status='complete',completed_at=now(),checks='raster/vector/unknown-policy validation passed')
        atomic_json(report_path,report)
        return report
    except Exception as exc:
        report.update(status='failed',error=str(exc)); atomic_json(report_path,report)
        raise
    finally:
        lock.unlink(missing_ok=True)
