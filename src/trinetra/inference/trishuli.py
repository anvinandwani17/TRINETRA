"""Bounded, masked CPU inference with a frozen Kuro Siwo checkpoint."""
import json
from pathlib import Path
import numpy as np
from trinetra.core.files import atomic_json, _sha256


def normalize_linear(pre, post, obs):
    values=np.concatenate([pre,post],axis=0).astype('float32')
    if values.shape[0]!=4 or obs.shape!=values.shape[1:]:
        raise ValueError('Expected aligned pre VV/VH and post VV/VH')
    valid=(obs==1)&np.all(np.isfinite(values)&(values>0),axis=0)
    x=np.zeros_like(values)
    x[:,valid]=(np.clip(10*np.log10(values[:,valid]),-35,5)+15)/20
    return x,valid


def tile_starts(size,tile,stride):
    if size<=tile:return [0]
    return sorted(set(list(range(0,size-tile+1,stride))+[size-tile]))


def tiled_scores(model,x,valid,tile=224,stride=112):
    import torch
    if tile<8 or not 0<stride<=tile:raise ValueError('Invalid tiling settings')
    height,width=valid.shape
    scores=np.zeros(valid.shape,dtype='float32');counts=np.zeros(valid.shape,dtype='uint16')
    model.eval()
    with torch.inference_mode():
        for y in tile_starts(height,tile,stride):
            for z in tile_starts(width,tile,stride):
                h=min(tile,height-y);w=min(tile,width-z)
                if not valid[y:y+h,z:z+w].any():continue
                patch=np.zeros((4,tile,tile),dtype='float32');patch[:,:h,:w]=x[:,y:y+h,z:z+w]
                prediction=torch.sigmoid(model(torch.from_numpy(patch[None])))[0,0].cpu().numpy()[:h,:w]
                if not np.isfinite(prediction).all():raise ValueError('Non-finite model scores')
                scores[y:y+h,z:z+w]+=prediction;counts[y:y+h,z:z+w]+=1
    if np.any(valid&(counts==0)):raise ValueError('Some observable pixels were not inferred')
    np.divide(scores,counts,out=scores,where=counts>0)
    scores[~valid]=np.nan
    return scores


def run_case(manifest_path,checkpoint_path,config_path,output):
    import rasterio
    import torch
    from trinetra.models.checkpoint import inspect_checkpoint
    from trinetra.models.siamese_unet import SiameseUNet
    from trinetra.preprocessing.ai_sigma0 import write_raster
    manifest_path=Path(manifest_path).resolve();checkpoint_path=Path(checkpoint_path).resolve();config_path=Path(config_path).resolve()
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    parent=json.loads(manifest_path.read_text(encoding='utf-8'))
    if parent.get('case')!='trishuli' or parent.get('status')!='complete_experimental' or parent.get('resolution_m')!=10:
        raise ValueError('Require completed experimental Trishuli 10m Sigma0 preprocessing')
    if not parent.get('radiometry','').startswith('linear Sigma0;'):
        raise ValueError('Only linear Sigma0 inputs are supported; never log dB inputs again')
    check=inspect_checkpoint(checkpoint_path,config_path)
    signature={'version':1,'manifest':_sha256(manifest_path),'checkpoint':check['sha256'],'config':_sha256(config_path),'tile':224,'stride':112,'threshold':0.5}
    marker=output/'inference_manifest.json'
    scenes={};grid=None
    for key,count in [('before_sigma0_linear',2),('after_sigma0_linear',2),('observability',1)]:
        path=Path(parent['outputs'][key])
        if _sha256(path)!=parent['output_sha256'][key]:raise ValueError('Preprocessing output checksum changed: '+key)
        with rasterio.open(path) as src:
            current={'crs':src.crs,'transform':src.transform,'height':src.height,'width':src.width}
            if grid is not None and current!=grid:raise ValueError('Input geographic grids differ')
            grid=current
            if src.count!=count or src.crs is None or not np.allclose(src.res,[10,10]):raise ValueError('Invalid input bands/grid')
            if key!='observability' and src.descriptions!=('VV_linear','VH_linear'):raise ValueError('VV/VH order is not documented')
            scenes[key]=src.read() if count==2 else src.read(1)
    if marker.exists():
        cached=json.loads(marker.read_text(encoding='utf-8'))
        if cached.get('status')=='complete_experimental' and cached.get('signature')==signature and all(Path(p).is_file() and _sha256(p)==cached.get('output_sha256',{}).get(k) for k,p in cached.get('outputs',{}).items()) and cached.get('outputs'):
            print('Verified inference cache reused',flush=True);return cached
    if grid['height']*grid['width']>4_000_000:raise ValueError('Only bounded Trishuli inference is supported')
    obs=scenes['observability']
    if not np.isin(obs,[0,1,255]).all():raise ValueError('Invalid observability codes')
    x,valid=normalize_linear(scenes['before_sigma0_linear'],scenes['after_sigma0_linear'],obs)
    if not valid.any():raise ValueError('No observable pixels')
    saved=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
    model=SiameseUNet(saved['config']['base_channels']);model.load_state_dict(saved['model_state'],strict=True)
    torch.set_num_threads(2)
    print('Running frozen checkpoint on CPU; 224px tiles, 112px stride',flush=True)
    scores=tiled_scores(model,x,valid)
    mask=np.full(valid.shape,255,dtype='uint8');mask[valid]=(scores[valid]>=0.5).astype('uint8')
    outputs={}
    outputs['score']=write_raster(output/'flood_score.tif',scores,grid,['uncalibrated_model_score'],np.nan)
    outputs['mask']=write_raster(output/'flood_candidates.tif',mask,grid,['experimental_candidate_0_1_unknown_255'],255)
    # Correct generic writer tags: this is a model output, not Sigma0 radiometry.
    for path in outputs.values():
        with rasterio.open(path,'r+') as dst:
            dst.update_tags(radiometry='not applicable: model output',threshold='0.5',checkpoint_sha256=check['sha256'],calibrated_probability='false')
    from rasterio.features import shapes
    from rasterio.warp import transform_geom
    features=[]
    for geometry,value in shapes(mask,mask=(mask==1),transform=grid['transform']):
        if value==1:
            features.append({'type':'Feature','properties':{'classification':'experimental_flood_candidate','threshold':0.5,'validated_on_trishuli':False},'geometry':transform_geom(grid['crs'],'EPSG:4326',geometry,precision=7)})
    geo=output/'flood_candidates.geojson';atomic_json(geo,{'type':'FeatureCollection','features':features});outputs['geojson']=str(geo)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,6))
    cmap=plt.get_cmap('viridis').copy();cmap.set_bad('#888888')
    image=axes[0].imshow(scores,cmap=cmap,vmin=0,vmax=1);axes[0].set_title('Experimental model score (uncalibrated)');fig.colorbar(image,ax=axes[0],shrink=.6)
    preview=np.full((*mask.shape,3),.55,dtype='float32');preview[mask==0]=[.9,.9,.9];preview[mask==1]=[0,.55,1];preview[obs==255]=1
    axes[1].imshow(preview);axes[1].set_title('Candidates: blue; unknown: grey; outside: white')
    for ax in axes:ax.axis('off')
    fig.tight_layout();png=output/'preview.png';fig.savefig(png,dpi=150);plt.close(fig);outputs['preview']=str(png)
    candidate_pixels=int((mask==1).sum())
    report={'case':'trishuli','status':'complete_experimental','signature':signature,'outputs':outputs,
            'output_sha256':{k:_sha256(p) for k,p in outputs.items()},'checkpoint_mode':check['mode'],
            'source_pair':parent['source_pair'],'threshold':0.5,'threshold_tuned':False,'training_performed':False,
            'observable_pixels':int(valid.sum()),'unknown_inside_aoi_pixels':int(((obs!=255)&~valid).sum()),
            'candidate_pixels':candidate_pixels,'candidate_area_m2':candidate_pixels*100,'candidate_polygons':len(features),
            'domain_compatibility_verified':False,'probability_calibrated':False,
            'limitations':parent['limitations']+['Pilot predictions are not validated Trishuli flood extents','Sigmoid scores are not calibrated flood probabilities','Overlap averaging reduces seams but tiling and GroupNorm can affect outputs'],
            'operational_fallback':'existing deterministic detector'}
    atomic_json(marker,report);return report
