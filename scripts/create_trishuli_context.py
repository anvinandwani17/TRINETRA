"""Overlay actual AI candidates with CEMS landslide impacts; no flood accuracy."""
import argparse,json,sqlite3,hashlib
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom
from shapely import from_wkb
from shapely.geometry import shape,mapping,box
from shapely.ops import unary_union

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def read_geometry(blob):
    if blob[:2]!=b'GP':raise ValueError('Not GeoPackage geometry')
    envelope=(blob[3]>>1)&7
    size={0:0,1:32,2:48,3:48,4:64}.get(envelope)
    if size is None:raise ValueError('Unsupported GeoPackage envelope')
    return from_wkb(blob[8+size:])

def overlay(mask_path,reference_path,output):
    mask_path=Path(mask_path).resolve();reference_path=Path(reference_path).resolve()
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    input_hashes={'ai_candidates':digest(mask_path),'cems_reference':digest(reference_path)}
    with rasterio.open(mask_path) as src:
        if src.count!=1 or src.nodata!=255 or src.crs.to_epsg()!=32645 or not np.allclose(src.res,[10,10]):
            raise ValueError('Expected Trishuli UTM45N 10m candidate mask')
        data=src.read(1);transform=src.transform;crs=src.crs;bounds=src.bounds;tags=src.tags()
        if not np.isin(data,[0,1,255]).all():raise ValueError('Unexpected mask codes')
    c=sqlite3.connect(reference_path.as_uri()+'?mode=ro',uri=True)
    if c.execute('pragma quick_check').fetchone()[0]!='ok':raise ValueError('GeoPackage integrity failed')
    for name in ['observedEventA_v1','areaOfInterestA_v1','imageFootprintA_v1','notAnalysedA_v1']:
        if c.execute('SELECT srs_id FROM gpkg_geometry_columns WHERE table_name=?',(name,)).fetchone()[0]!=4326:
            raise ValueError('Unexpected reference CRS')
    c.row_factory=sqlite3.Row
    events=list(c.execute('SELECT * FROM observedEventA_v1'))
    if not events or any(r['event_type']!='6-Mass Movement' or r['obj_desc']!='Landslide' for r in events):
        raise ValueError('This context overlay requires the audited landslide-only package')
    sources={r['src_id']:dict(r) for r in c.execute('SELECT * FROM source_v1')}
    ids={r['dmg_src_id'] for r in events}
    if any(sources[i]['eventphase']!='Post-event' for i in ids):raise ValueError('Impact source is not post-event')
    def projected(blob):
        g=read_geometry(blob)
        if not g.is_valid:raise ValueError('Reference geometry invalid')
        return shape(transform_geom('EPSG:4326',crs,mapping(g)))
    aoi=unary_union([projected(r['geom']) for r in c.execute('SELECT geom FROM areaOfInterestA_v1')])
    footprint=unary_union([projected(r['geom']) for r in c.execute('SELECT geom,or_src_id FROM imageFootprintA_v1') if r['or_src_id'] in ids])
    unknown=unary_union([projected(r['geom']) for r in c.execute('SELECT geom FROM notAnalysedA_v1')])
    impact=unary_union([projected(r['geom']) for r in events])
    grid_extent=box(bounds.left,bounds.bottom,bounds.right,bounds.top)
    covered=aoi.intersection(footprint).intersection(grid_extent)
    analysed=covered.difference(unknown)
    if analysed.is_empty:raise ValueError('No mapped reference overlap')
    def burn(g):
        if g.is_empty:return np.zeros(data.shape,dtype=bool)
        return rasterize([(mapping(g),1)],out_shape=data.shape,transform=transform,fill=0,dtype='uint8',all_touched=False).astype(bool)
    covered_pixels=burn(covered);analysed_pixels=burn(analysed);impact_pixels=burn(impact.intersection(analysed))
    context=np.full(data.shape,255,dtype='uint8');context[covered_pixels]=2;context[analysed_pixels]=0;context[impact_pixels]=1
    target=output/'cems_landslide_context.tif'
    with rasterio.open(target,'w',driver='GTiff',height=data.shape[0],width=data.shape[1],count=1,dtype='uint8',nodata=255,crs=crs,transform=transform,compress='deflate') as dst:
        dst.write(context,1);dst.set_band_description(1,'0_no_mapped_landslide_1_landslide_2_not_analysed_255_outside_reference')
        dst.update_tags(reference_class='landslide, not flood water',use='visual context only; not flood ground truth')
    with rasterio.open(target) as src:
        if not np.array_equal(src.read(1),context) or src.transform!=transform:raise ValueError('Context GeoTIFF roundtrip failed')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    fig,axes=plt.subplots(1,2,figsize=(13,8))
    rgb=np.full((*data.shape,3),.9,dtype='float32');rgb[data==255]=[.63,.63,.63];rgb[data==1]=[.05,.48,.95]
    extent=[bounds.left/1000,bounds.right/1000,bounds.bottom/1000,bounds.top/1000]
    def outlines(ax,g,color,width=1,style='-'):
        if g.is_empty:return
        polygons=list(g.geoms) if hasattr(g,'geoms') else [g]
        for p in polygons:
            if p.geom_type!='Polygon':continue
            for ring in [p.exterior,*p.interiors]:
                xy=np.array(ring.coords)/1000;ax.plot(xy[:,0],xy[:,1],color=color,lw=width,ls=style)
    for ax in axes:
        ax.imshow(rgb,extent=extent,origin='upper',interpolation='nearest')
        outlines(ax,impact.intersection(grid_extent),'#e85d04',1.3)
        outlines(ax,analysed,'#9b00b5',1,'--')
        ax.set_xlabel('UTM easting (km)');ax.set_ylabel('UTM northing (km)');ax.set_aspect('equal')
    axes[0].set_title('Full AI raster: candidates and mapped landslide outlines')
    left,bottom,right,top=covered.bounds;pad=300
    axes[1].set_xlim(max(bounds.left,left-pad)/1000,min(bounds.right,right+pad)/1000)
    axes[1].set_ylim(max(bounds.bottom,bottom-pad)/1000,min(bounds.top,top+pad)/1000)
    axes[1].set_title('Detail around Copernicus mapped coverage')
    fig.suptitle('TRINETRA / Bidur: visual context comparison\nAI flood candidates and CEMS landslide impacts are different classes',fontsize=14)
    legend=[Patch(facecolor='#0d7af2',label='AI flood candidate'),Patch(facecolor='#a1a1a1',label='AI unknown / outside AOI'),Line2D([0],[0],color='#e85d04',label='CEMS landslide outline'),Line2D([0],[0],color='#9b00b5',ls='--',label='CEMS analysed coverage')]
    fig.legend(handles=legend,loc='lower center',ncol=2,fontsize=10)
    fig.tight_layout(rect=(0,.1,1,.91));png=output/'trishuli_context_overlay.png';fig.savefig(png,dpi=180);plt.close(fig)
    locality=dict(c.execute('SELECT emsr_id,area_id,locality,map_type FROM areaOfInterestA_v1').fetchone());c.close()
    report={'status':'complete_context_only','case':'trishuli','reference':locality,'reference_event_type':'6-Mass Movement','reference_object':'Landslide','reference_feature_count':len(events),'flood_reference_feature_count':0,
            'reference_sources':[sources[i] for i in sorted(ids)],'ai_checkpoint_sha256':tags.get('checkpoint_sha256'),
            'ai_threshold':tags.get('threshold'),'ai_candidate_pixels':int((data==1).sum()),'ai_candidate_area_km2':float((data==1).sum()*100/1e6),
            'reference_analysed_overlap_with_raster_extent_km2':analysed.area/1e6,'input_sha256':input_hashes,
            'outputs':['trishuli_context_overlay.png','cems_landslide_context.tif'],
            'flood_accuracy_computed':False,'reason':'Reference polygons are landslides; flood IoU/precision/recall would compare different target classes',
            'limitations':['Blank reference polygons do not establish absence of flood','Reference covers only part of the satellite AOI','Unknown/outside-AOI AI pixels share code 255 and cannot be separated from this mask alone','10m rasterization is display alignment, not finer reference accuracy','Context raster code 0 means no mapped landslide, not dry land','Source imagery is 2026-08-28 08:29 UTC; AI post-event acquisition is about 12:21 UTC']}
    if digest(mask_path)!=input_hashes['ai_candidates'] or digest(reference_path)!=input_hashes['cems_reference']:raise ValueError('Input changed during overlay')
    (output/'context_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mask',default='data/processed/trishuli/ai_flood_experimental/flood_candidates.tif')
    p.add_argument('--reference',default='data/reference/EMSR927/EMSR927_AOI03_GRA_MONIT01_v1.gpkg')
    p.add_argument('--output',default='data/processed/trishuli/reference_context')
    a=p.parse_args()
    overlay(a.mask,a.reference,a.output)
