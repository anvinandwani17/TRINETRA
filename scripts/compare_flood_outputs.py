"""Compare candidate masks on common support; agreement is not accuracy."""
from pathlib import Path
import argparse,hashlib,json
import numpy as np

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def comparison(ai,baseline):
    if ai.shape!=baseline.shape or not np.isin(ai,[0,1,255]).all() or not np.isin(baseline,[0,1,255]).all():
        raise ValueError('Masks must share a shape and use codes 0/1/255')
    valid=(ai!=255)&(baseline!=255)
    labels=np.full(ai.shape,255,dtype='uint8')
    labels[valid]=0
    labels[valid&(ai==1)&(baseline==1)]=1
    labels[valid&(ai==1)&(baseline==0)]=2
    labels[valid&(ai==0)&(baseline==1)]=3
    counts={key:int((labels==value).sum()) for key,value in [('neither_candidate',0),('both_candidate',1),('ai_only',2),('baseline_only',3)]}
    union=counts['both_candidate']+counts['ai_only']+counts['baseline_only']
    return labels,{'jointly_assessable_pixels':int(valid.sum()),'excluded_pixels':int((~valid).sum()),**counts,
                   'candidate_agreement_iou':counts['both_candidate']/union if union else None}

def main(ai_manifest,baseline_manifest,output):
    import rasterio
    from rasterio.warp import reproject,Resampling
    ai_report=json.loads(Path(ai_manifest).read_text(encoding='utf-8'))
    base_report=json.loads(Path(baseline_manifest).read_text(encoding='utf-8'))
    if ai_report.get('status')!='complete_experimental' or base_report.get('status')!='complete':
        raise ValueError('Both runs must be completed')
    if ai_report.get('case')!='trishuli' or base_report.get('case')!='trishuli':
        raise ValueError('This comparison is only for Trishuli')
    ai_path=Path(ai_report['outputs']['mask']);base_path=Path(base_report['outputs']['flood_mask'])
    for path,report,key in [(ai_path,ai_report,'mask'),(base_path,base_report,'flood_mask')]:
        if sha(path)!=report['output_sha256'][key]:raise ValueError('Recorded mask checksum changed: '+str(path))
    with rasterio.open(ai_path) as src:
        if src.count!=1 or not src.crs or src.crs.to_epsg()!=32645 or not np.allclose(src.res,[10,10]):
            raise ValueError('Expected Trishuli 10m UTM45N AI mask')
        ai=src.read(1);grid={'crs':src.crs,'transform':src.transform,'height':src.height,'width':src.width}
        if ai.size>4_000_000:raise ValueError('Comparison exceeds bounded case grid')
    aligned=np.full(ai.shape,255,dtype='uint8')
    with rasterio.open(base_path) as src:
        if src.count!=1 or not src.crs or src.nodata!=255:raise ValueError('Baseline lacks documented unknown codes/grid')
        baseline=src.read(1)
        if not np.isin(baseline,[0,1,255]).all():raise ValueError('Unexpected baseline codes')
        source_resolution=list(src.res)
        reproject(baseline,aligned,src_transform=src.transform,src_crs=src.crs,src_nodata=255,
                  dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=255,resampling=Resampling.nearest)
    labels,stats=comparison(ai,aligned)
    if not stats['jointly_assessable_pixels']:raise ValueError('No mutually assessable pixels; no meaningful agreement calculation')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    target=output/'agreement.tif'
    with rasterio.open(target,'w',driver='GTiff',count=1,dtype='uint8',nodata=255,compress='deflate',**grid) as dst:
        dst.write(labels,1);dst.set_band_description(1,'0_neither_1_both_2_ai_only_3_baseline_only_255_excluded')
        dst.update_tags(purpose='candidate agreement, not accuracy',baseline_resampling='nearest neighbour; preserves 20m information')
    with rasterio.open(target) as src:
        if src.transform!=grid['transform'] or src.crs!=grid['crs'] or not np.array_equal(src.read(1),labels):
            raise ValueError('Comparison GeoTIFF roundtrip failed')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap,BoundaryNorm
    from matplotlib.patches import Patch
    colors=['#eeeeee','#7b2cbf','#168aff','#ff9e00','#888888']
    display=np.where(labels==255,4,labels)
    fig,axes=plt.subplots(1,3,figsize=(13,6))
    for ax,mask,title in [(axes[0],ai,'AI candidates'),(axes[1],aligned,'Deterministic candidates (20m information)')]:
        rgb=np.full((*mask.shape,3),.55,dtype='float32');rgb[mask==0]=.93;rgb[mask==1]=[.08,.54,1]
        ax.imshow(rgb);ax.set_title(title)
    axes[2].imshow(display,cmap=ListedColormap(colors),norm=BoundaryNorm(np.arange(-.5,5.5,1),5))
    axes[2].set_title('Agreement on jointly assessable areas')
    for ax in axes:ax.axis('off')
    names=['Neither candidate','Both candidate','AI only','Baseline only','Excluded / unknown']
    fig.legend(handles=[Patch(facecolor=c,label=n) for c,n in zip(colors,names)],loc='lower center',ncol=5)
    fig.suptitle('TRINETRA: experimental candidate comparison, not reference accuracy')
    fig.tight_layout(rect=(0,.08,1,.95));preview=output/'comparison.png';fig.savefig(preview,dpi=150);plt.close(fig)
    area=abs(grid['transform'].a*grid['transform'].e-grid['transform'].b*grid['transform'].d)
    report={'case':'trishuli','status':'complete','comparison_grid_m':10,'baseline_native_resolution_m':source_resolution,
            'alignment':'baseline nearest-neighbour to AI grid; no increased baseline accuracy',
            'input_sha256':{'ai':sha(ai_path),'baseline':sha(base_path)},'statistics':stats,
            'areas_m2':{k:v*area for k,v in stats.items() if k!='candidate_agreement_iou'},
            'outputs':{'raster':str(target),'preview':str(preview)},
            'legend':dict(zip(['0','1','2','3','255'],names)),
            'reference_accuracy_evaluated':False,'thresholds_changed':False,
            'limitations':['Only jointly assessable pixels enter agreement statistics',
                           'Excluded pixels may be supported by one detector but unknown to the other',
                           'Agreement cannot establish which detector is correct',
                           'Neither candidate does not prove dry land']}
    (output/'comparison_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ai-manifest',default='data/processed/trishuli/ai_flood_experimental/inference_manifest.json')
    p.add_argument('--baseline-manifest',default='data/processed/trishuli/baseline/baseline_manifest.json')
    p.add_argument('--output',default='data/processed/trishuli/comparison')
    a=p.parse_args()
    try:main(a.ai_manifest,a.baseline_manifest,a.output)
    except Exception as exc:p.exit(1,f'Comparison stopped: {exc}\n')
