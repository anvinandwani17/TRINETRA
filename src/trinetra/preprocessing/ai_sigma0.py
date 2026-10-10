"""Separate reference-inspired Sigma0 branch using already successful SNAP graphs."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
from trinetra.core.files import atomic_json, _sha256

REFERENCE = 'https://github.com/Orion-AI-Lab/KuroSiwo/blob/4347ed173c4e48f5a9d578bb5fe8453706b08e5e/configs/grd_preprocessing.xml'


def sigma_graph(original, target):
    root = ET.fromstring(original)
    nodes = root.findall('node')
    operators = [n.findtext('operator') for n in nodes]
    allowed = {'Read','Apply-Orbit-File','Subset','ThermalNoiseRemoval','Remove-GRD-Border-Noise',
               'Calibration','Multilook','Terrain-Flattening','Terrain-Correction','Write'}
    if any(op not in allowed for op in operators) or any(operators.count(op)!=1 for op in ['Read','Calibration','Terrain-Correction','Write']):
        raise ValueError('Unexpected original graph; require the successful linear radiometry graph')
    previous = None
    def set_value(node, name, value):
        params = node.find('parameters')
        child = params.find(name)
        if child is None: child=ET.SubElement(params,name)
        child.text = str(value).lower() if isinstance(value,bool) else str(value)
    for node in list(nodes):
        operator=node.findtext('operator')
        if operator in ['Multilook','Terrain-Flattening']:
            root.remove(node); continue
        sources=node.find('sources')
        if sources is None:sources=ET.SubElement(node,'sources')
        if len(sources)>1:raise ValueError('Only a linear single-source SNAP graph is supported')
        sources.clear()
        if previous:ET.SubElement(sources,'sourceProduct',refid=previous)
        if operator=='Calibration':
            for key,value in {'outputSigmaBand':True,'outputBetaBand':False,'outputGammaBand':False,
                              'createGammaBand':False,'createBetaBand':False,'outputImageScaleInDb':False,
                              'selectedPolarisations':'VV,VH'}.items():set_value(node,key,value)
            filter_node=ET.Element('node',id='trinetra_ai_lee_sigma')
            ET.SubElement(filter_node,'operator').text='Speckle-Filter'
            ET.SubElement(ET.SubElement(filter_node,'sources'),'sourceProduct',refid=node.attrib['id'])
            params=ET.SubElement(filter_node,'parameters')
            for key,value in {'sourceBands':'Sigma0_VV,Sigma0_VH','filter':'Lee Sigma',
                              'filterSizeX':3,'filterSizeY':3,'numLooksStr':'1','windowSize':'7x7',
                              'targetWindowSizeStr':'3x3','sigmaStr':'0.9'}.items():
                ET.SubElement(params,key).text=str(value)
            root.insert(list(root).index(node)+1,filter_node)
            previous=filter_node.attrib['id'];continue
        if operator=='Terrain-Correction':
            for key,value in {'sourceBands':'Sigma0_VV,Sigma0_VH','pixelSpacingInMeter':10,
                              'pixelSpacingInDegree':8.983152841195215e-5,
                              'demResamplingMethod':'BILINEAR_INTERPOLATION',
                              'imgResamplingMethod':'BILINEAR_INTERPOLATION','applyRadiometricNormalization':False,
                              'saveSelectedSourceBand':True,'saveDEM':False,'saveLocalIncidenceAngle':False,
                              'saveSigmaNought':False,'saveGammaNought':False,'saveBetaNought':False}.items():set_value(node,key,value)
        if operator=='Write':
            set_value(node,'file',str(Path(target).resolve()));set_value(node,'formatName','BEAM-DIMAP')
        previous=node.attrib['id']
    root.attrib['id']='TRINETRA-AI-Sigma0-10m-Experimental'
    ET.indent(root);return ET.tostring(root,encoding='unicode')


def aligned_sigma(bands, reference_mask):
    import rasterio
    from rasterio.warp import reproject,Resampling
    from affine import Affine
    with rasterio.open(reference_mask) as src:
        if src.count!=1 or not src.crs or src.crs.is_geographic or not np.allclose(src.res,[20,20]):
            raise ValueError('Need the verified projected 20 m pair-observability mask')
        old=src.read(1)
        if not np.isin(old,[0,1,255]).all():raise ValueError('Unexpected observability codes')
        grid={'crs':src.crs,'transform':src.transform*Affine.scale(.5,.5),
              'width':src.width*2,'height':src.height*2}
        if grid['width']*grid['height']>4_000_000:raise ValueError('AOI exceeds bounded experimental grid')
        mask=np.full((grid['height'],grid['width']),255,dtype=np.uint8)
        reproject(old,mask,src_transform=src.transform,src_crs=src.crs,src_nodata=255,
                  dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=255,resampling=Resampling.nearest)
    arrays=[]
    for name in ['Sigma0_VV','Sigma0_VH']:
        if name not in bands:raise ValueError('Missing calibrated band '+name)
        with rasterio.open(bands[name]) as src:
            if not src.crs or src.count!=1:raise ValueError('Sigma0 band lacks physical georeferencing')
            data=src.read(1,masked=True).astype('float32').filled(np.nan)
            data[~np.isfinite(data)|(data<=0)]=np.nan
            target=np.full(mask.shape,np.nan,dtype=np.float32)
            reproject(data,target,src_transform=src.transform,src_crs=src.crs,src_nodata=np.nan,
                      dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=np.nan,resampling=Resampling.bilinear)
            arrays.append(target)
    return np.stack(arrays),mask,grid


def write_raster(path,data,grid,descriptions,nodata):
    import rasterio
    data=data[None] if data.ndim==2 else data
    with rasterio.open(path,'w',driver='GTiff',width=grid['width'],height=grid['height'],count=len(data),
                       crs=grid['crs'],transform=grid['transform'],dtype=str(data.dtype),nodata=nodata,
                       compress='deflate') as dst:
        dst.write(data)
        for i,name in enumerate(descriptions,1):dst.set_band_description(i,name)
        dst.update_tags(status='experimental',radiometry='Sigma0',resolution_m='10',
                        reference=REFERENCE,mask_source='inherited 20m conservative mask; nearest neighbor to 10m')
    with rasterio.open(path) as src:
        if src.count!=len(data) or src.shape!=data.shape[-2:]:raise ValueError('GeoTIFF roundtrip failed')
        if src.crs!=grid['crs'] or src.transform!=grid['transform']:
            raise ValueError('GeoTIFF geographic grid changed')
        if not np.array_equal(src.read(),data,equal_nan=True):
            raise ValueError('GeoTIFF pixel roundtrip failed')
    return str(Path(path).resolve())


def preprocess_ai_sigma0(parent_path='data/processed/trishuli/processing_manifest.json',prepare_only=False):
    import rasterio
    parent_path=Path(parent_path).resolve();base=parent_path.parent
    parent=json.loads(parent_path.read_text(encoding='utf-8'))
    if parent.get('case')!='trishuli' or parent.get('status')!='complete' or parent.get('resolution_m')!=20:
        raise ValueError('Need a complete real Trishuli 20m preprocessing manifest')
    if not parent.get('checks',{}).get('shadow_layover_never_observable'):
        raise ValueError('Physical shadow/layover exclusion must already be verified')
    out=base/'ai_sigma0_10m';out.mkdir(exist_ok=True)
    gpt=parent.get('snap_gpt');jobs={}
    for role in ['pre','post']:
        source_graph=base/'intermediate'/role/'radiometry.graph.xml'
        original=source_graph.read_text(encoding='utf-8')
        read=next(n for n in ET.fromstring(original).findall('node') if n.findtext('operator')=='Read')
        source=Path(read.findtext('parameters/file'))
        if not source.is_file():raise ValueError('Prepared SNAP source missing: '+str(source))
        folder=out/'intermediate'/role;folder.mkdir(parents=True,exist_ok=True)
        target=folder/'sigma0.dim';graph=sigma_graph(original,target)
        target.with_suffix('.graph.xml').write_text(graph,encoding='utf-8')
        signature=hashlib.sha256((graph+_sha256(source)+_sha256(parent_path)).encode()).hexdigest()
        jobs[role]={'target':target,'graph':graph,'signature':signature}
    report={'case':'trishuli','status':'graphs_prepared_only' if prepare_only else 'running',
            'source_pair':parent['source_pair'],'raw_sha256':parent['raw_sha256'],'reference':REFERENCE,
            'radiometry':'linear Sigma0; Lee Sigma 7x7 window, 3x3 target, sigma 0.9; terrain correction',
            'resolution_m':10,'dem':parent['dem'],'outputs':{},
            'training_performed':False,'neural_inference_performed':False,'compatibility_verified':False,
            'limitations':['Retains existing Copernicus DEM instead of the reference SRTM DEM',
                           'Shadow/AOI mask retains 20m information; no new 10m shadow accuracy claimed',
                           'S1D input differs from S1A/B training sensors',
                           'UTM grid differs from the published preprocessing projection',
                           'Matching selected processing settings does not validate model accuracy']}
    marker=out/('prepared_graphs_manifest.json' if prepare_only else 'ai_processing_manifest.json')
    atomic_json(marker,report)
    if prepare_only:return report
    from trinetra.preprocessing.ai_snap_runner import run_graph
    if not gpt or not Path(gpt).is_file():raise ValueError('Recorded SNAP GPT executable missing')
    mask=Path(parent['outputs']['observability'])
    if _sha256(mask)!=parent['output_sha256']['observability']:raise ValueError('Original observability checksum changed')
    try:
        scenes={}
        for role,job in jobs.items():
            bands=run_graph(gpt,job['graph'],job['target'],job['signature'],cache_mb=2048,threads=2)
            scenes[role]=aligned_sigma(bands,mask)
        pre,obs,grid=scenes['pre'];post,post_obs,post_grid=scenes['post']
        if grid!=post_grid or not np.array_equal(obs,post_obs):raise ValueError('Pre/post grids or masks differ')
        usable=(obs==1)&np.all(np.isfinite(pre)&(pre>0)&np.isfinite(post)&(post>0),axis=0)
        if not usable.any():raise ValueError('No jointly observable Sigma0 pixels remain')
        obs=np.where(obs==255,255,np.where(usable,1,0)).astype(np.uint8)
        pre[:,~usable]=np.nan;post[:,~usable]=np.nan
        for label,data in [('before',pre),('after',post)]:
            key=label+'_sigma0_linear';report['outputs'][key]=write_raster(out/(key+'.tif'),data,grid,['VV_linear','VH_linear'],np.nan)
        report['outputs']['observability']=write_raster(out/'observability.tif',obs,grid,['pair_observability'],255)
        report['grid']={'crs':str(grid['crs']),'width':grid['width'],'height':grid['height'],'transform':list(grid['transform'])[:6]}
        report['observable_pixels']=int(usable.sum());report['unknown_pixels']=int((obs==0).sum())
        report['output_sha256']={k:_sha256(p) for k,p in report['outputs'].items()}
        report['status']='complete_experimental'
        if _sha256(mask)!=parent['output_sha256']['observability']:raise ValueError('Parent mask changed during processing')
        atomic_json(marker,report);return report
    except Exception as exc:
        report['status']='failed';report['error']=str(exc);atomic_json(marker,report);raise
