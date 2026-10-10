"""Independent output checks; validate unknown semantics and vector/raster agreement."""
from pathlib import Path
import json
import numpy as np
from trinetra.downloads.sentinel1 import _sha256


def verify_baseline(manifest,allow_pending=False):
    import rasterio
    from rasterio.features import rasterize
    from rasterio.warp import transform_geom
    from PIL import Image
    report = json.loads(Path(manifest).read_text())
    states = ['complete','outputs_written'] if allow_pending else ['complete']
    if report.get('status') not in states or report.get('score_is_probability') is not False:
        raise ValueError('Baseline report is not complete or mislabels score as probability')
    for key,source in report['inputs'].items():
        if _sha256(source['path'])!=source['sha256']:
            raise ValueError('Baseline input checksum changed: '+key)
    data,reference = {},None
    for key,path in report['outputs'].items():
        if _sha256(path)!=report['output_sha256'][key]:
            raise ValueError('Baseline checksum changed: '+key)
        if key=='flood_geojson':
            continue
        with rasterio.open(path) as src:
            current = (src.crs,src.transform,src.width,src.height)
            if reference is not None and current!=reference or src.res!=(20,20) or src.crs.to_epsg()!=32645:
                raise ValueError('Baseline geographic grids differ')
            reference = current
            data[key] = src.read() if src.count>1 else src.read(1)
            if key=='flood_mask' and src.nodata!=255:
                raise ValueError('Flood mask must encode unknown as nodata 255')
    mask,score,slope = data['flood_mask'],data['evidence_score'],data['slope_deg']
    if not np.isin(mask,[0,1,255]).all():
        raise ValueError('Invalid flood mask codes')
    candidate,unknown = mask==1,mask==255
    if not np.isnan(score[unknown]).all() or not np.isfinite(score[~unknown]).all() or np.any((score[~unknown]<0)|(score[~unknown]>1)):
        raise ValueError('Evidence-score bounds or unknown semantics violated')
    with rasterio.open(report['inputs']['observability']['path']) as src:
        obs = src.read(1)
    if np.any(mask[obs!=1]!=255):
        raise ValueError('Unobservable pixels became assessed/dry/flood')
    cfg = report['config']
    if np.any(~np.isfinite(slope[candidate])) or np.any(slope[candidate]>cfg['max_slope_deg']):
        raise ValueError('Flood candidates violate terrain exclusions')
    change,pre,post = [data[k] for k in ['filtered_change_db','filtered_before_db','filtered_after_db']]
    np.testing.assert_allclose(change,post-pre,atol=1e-5,rtol=1e-6,equal_nan=True)
    if (np.any(change[0,candidate]>-cfg['vv_drop_db']+1e-5) or
        np.any(change[1,candidate]>-cfg['vh_drop_db']+1e-5) or
        np.any(post[0,candidate]>cfg['post_vv_max_db']+1e-5) or
        np.any(post[1,candidate]>cfg['post_vh_max_db']+1e-5) or
        np.any((pre[0,candidate]<=cfg['pre_low_vv_db']) & (pre[1,candidate]<=cfg['pre_low_vh_db']))):
        raise ValueError('Candidate flood violates stated backscatter rules')
    if np.any(data['reasons'][candidate]!=0):
        raise ValueError('Flood candidate carries exclusion/rejection flags')
    geo = json.loads(Path(report['outputs']['flood_geojson']).read_text())
    if geo['type']!='FeatureCollection' or len(geo['features'])!=report['summary']['component_count']:
        raise ValueError('Invalid candidate vector count')
    projected = []
    for feature in geo['features']:
        properties = feature['properties']
        if properties['area_m2']<cfg['min_area_m2'] or properties['score_is_probability'] is not False:
            raise ValueError('Vector area/score semantics incorrect')
        projected.append((transform_geom('EPSG:4326',reference[0],feature['geometry']),1))
    reconstructed = rasterize(projected,out_shape=mask.shape,transform=reference[1],fill=0,dtype='uint8') if projected else np.zeros(mask.shape,dtype=np.uint8)
    if not np.array_equal(reconstructed.astype(bool),candidate):
        raise ValueError('GeoJSON polygons disagree with raster candidate pixels')
    count = int(candidate.sum())
    if count!=report['summary']['candidate_pixels'] or count*400!=report['summary']['candidate_area_m2']:
        raise ValueError('Candidate area summary differs from raster')
    if sum(f['properties']['area_m2'] for f in geo['features'])!=count*400:
        raise ValueError('Vector areas disagree with raster')
    for key,path in report['previews'].items():
        if _sha256(path)!=report['preview_sha256'][key]:
            raise ValueError('Preview checksum changed')
        with Image.open(path) as image:
            image.verify()
    return report['summary']
