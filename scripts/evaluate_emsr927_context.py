"""Read-only EMSR927 spatial context evaluation; never water-flood or damage accuracy.

Requires numpy, rasterio, shapely, pyproj. Does not import training/inference code.
All reference footprints are clipped by source imagery, not-analysed areas and
the case AOI inherited from the physical observability grid. Pixel centres are
used throughout. The 10 m grid does not add reference or baseline accuracy.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.warp import reproject, Resampling
from shapely import wkb
from shapely.geometry import shape, mapping, GeometryCollection
from shapely.ops import transform, unary_union
from pyproj import Transformer, CRS

ENVELOPES={0:0,1:32,2:48,3:48,4:64}

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def read_gpkg(path):
    """Decode standard GPKG geometries via SQLite; no GDAL GPKG driver needed."""
    uri=Path(path).resolve().as_uri()+'?mode=ro'
    layers={}
    with sqlite3.connect(uri,uri=True) as con:
        con.row_factory=sqlite3.Row
        for meta in con.execute('select * from gpkg_contents').fetchall():
            name=meta['table_name']
            if meta['data_type']!='features':continue
            if not name.replace('_','').isalnum():raise ValueError('Unsafe layer identifier')
            geom_col=con.execute('select column_name from gpkg_geometry_columns where table_name=?',(name,)).fetchone()[0]
            features=[];total_rows=0;empty_geometry_grades=Counter()
            for row in con.execute('select * from "'+name+'"'):
                total_rows+=1
                props=dict(row);blob=props.pop(geom_col)
                if blob is None:
                    empty_geometry_grades[props.get('damage_gra','not_applicable')]+=1
                    continue
                if blob[:2]!=b'GP':raise ValueError('Not a GPKG geometry')
                env=(blob[3]>>1)&7
                if env not in ENVELOPES:raise ValueError('Unsupported geometry envelope')
                geom=wkb.loads(blob[8+ENVELOPES[env]:])
                if geom.is_empty:
                    empty_geometry_grades[props.get('damage_gra','not_applicable')]+=1
                    continue
                if not geom.is_valid:raise ValueError('Invalid reference geometry: '+name)
                features.append((props,geom))
            layers[name]={'srs':meta['srs_id'],'features':features,'total_rows':total_rows,
                          'empty_geometry_grades':dict(empty_geometry_grades)}
        sources=[dict(x) for x in con.execute('select * from source_v1')]
    return layers,sources

def warp_codes(path,grid):
    with rasterio.open(path) as ds:
        src=ds.read(1)
        if not set(np.unique(src)).issubset({0,1,255}):raise ValueError('Unexpected input codes')
        dst=np.full(grid['shape'],255,dtype='uint8')
        reproject(src,dst,src_transform=ds.transform,src_crs=ds.crs,
                  dst_transform=grid['transform'],dst_crs=grid['crs'],
                  src_nodata=255,dst_nodata=255,resampling=Resampling.nearest)
        return dst

def burn(geom,grid):
    if geom.is_empty:return np.zeros(grid['shape'],dtype=bool)
    return rasterize([(mapping(geom),1)],out_shape=grid['shape'],transform=grid['transform'],fill=0,all_touched=False,dtype='uint8').astype(bool)

def model_codes(mask,inside):
    if np.any((mask!=255)&~inside):raise ValueError('Prediction supported outside physical AOI')
    return np.where(~inside,255,np.where(mask==255,2,mask)).astype('uint8')

def accounting(ref,model,pixel_area):
    n=int(ref.sum()); counts={str(code):int((ref&(model==code)).sum()) for code in [0,1,2,255]}
    assert sum(counts.values())==n
    return {'reference_pixels':n,'reference_area_km2':n*pixel_area/1e6,
            'candidate_pixels':counts['1'],'non_candidate_pixels':counts['0'],
            'unknown_inside_case_pixels':counts['2'],'outside_case_pixels':counts['255'],
            'candidate_share':counts['1']/n if n else None,
            'unknown_share':counts['2']/n if n else None,
            'not_water_flood_accuracy':True}

def polygon_for(mask,grid):
    if not mask.any():return GeometryCollection()
    return unary_union([shape(g) for g,v in shapes(mask.astype('uint8'),mask=mask,transform=grid['transform']) if v==1])

def run(args):
    output=Path(args.out)
    if output.exists():raise ValueError('Output directory exists; choose a new path')
    inputs={'reference_gpkg':Path(args.reference),'ai_mask':Path(args.ai),
            'baseline_mask':Path(args.baseline),'physical_observability':Path(args.observability),
            'ai_manifest':Path(args.ai_manifest)}
    if args.hand_export:inputs['hand_export']=Path(args.hand_export)
    hashes={k:digest(v) for k,v in inputs.items()}
    manifest=json.loads(inputs['ai_manifest'].read_text(encoding='utf-8-sig'))
    if manifest['output_sha256']['mask']!=hashes['ai_mask']:raise ValueError('AI mask differs from its frozen manifest')
    with rasterio.open(args.ai) as ds:
        if ds.count!=1 or not ds.crs or not ds.crs.is_projected:raise ValueError('Projected single-band AI raster required')
        if not CRS(ds.crs).axis_info[0].unit_name.lower() in {'metre','meter'}:raise ValueError('Metric CRS required')
        ai=ds.read(1);grid={'shape':ds.shape,'transform':ds.transform,'crs':ds.crs}
        pixel_area=abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)
    if not set(np.unique(ai)).issubset({0,1,255}):raise ValueError('Unexpected AI mask codes')
    layers,sources=read_gpkg(args.reference)
    aoi_layer=layers['areaOfInterestA_v1']
    identity=aoi_layer['features'][0][0]
    if identity.get('emsr_id')!='EMSR927':raise ValueError('Wrong activation')
    convert={name:Transformer.from_crs(layer['srs'],grid['crs'],always_xy=True).transform for name,layer in layers.items()}
    def geoms(name):return [(props,transform(convert[name],g)) for props,g in layers[name]['features']]
    reference_aoi=unary_union([g for p,g in geoms('areaOfInterestA_v1')])
    footprints=defaultdict(list)
    for props,g in geoms('imageFootprintA_v1'):footprints[int(props['or_src_id'])].append(g)
    footprints={k:unary_union(v) for k,v in footprints.items()}
    not_analysed=unary_union([g for p,g in geoms('notAnalysedA_v1')])
    coverage_cache={}
    def coverage(src_ids):
        if any(i not in footprints for i in src_ids):raise ValueError('Missing imagery footprint')
        key=tuple(sorted(src_ids))
        if key not in coverage_cache:
            coverage_cache[key]=reference_aoi.intersection(unary_union([footprints[i] for i in src_ids])).difference(not_analysed)
        return coverage_cache[key]
    physical=warp_codes(args.observability,grid);inside=physical!=255
    models={'pilot_ai':model_codes(ai,inside),'baseline':model_codes(warp_codes(args.baseline,grid),inside)}
    hand_prov=None
    if args.hand_export:
        with zipfile.ZipFile(args.hand_export) as z:
            summary=json.loads(z.read('summary.json'));hand_prov=json.loads(z.read('provenance.json'))
            if summary['method']!='experimental_ai_hand':raise ValueError('Expected AI HAND export')
            if hand_prov['checkpoint_sha256']!=manifest['signature']['checkpoint']:raise ValueError('HAND checkpoint differs from pilot')
            if hand_prov['source_pair']['post_id']!=manifest['source_pair']['post_id']:raise ValueError('HAND scene pair mismatch')
            tr=Transformer.from_crs(4326,grid['crs'],always_xy=True).transform
            def export_geometry(name):
                return unary_union([transform(tr,shape(f['geometry'])) for f in json.loads(z.read(name))['features']])
            hand_candidate=burn(export_geometry('flood_extent.geojson'),grid)
            hand_unknown=burn(export_geometry('unobservable.geojson'),grid)
            if np.any(hand_candidate&hand_unknown):raise ValueError('HAND export candidate/unknown overlap')
            if int(hand_candidate.sum())!=summary['candidate_pixels']:raise ValueError('HAND vector rasterization candidate count differs from summary')
            models['pilot_ai_hand_export']=np.where(~inside,255,np.where(hand_unknown,2,np.where(hand_candidate,1,0))).astype('uint8')
    events=geoms('observedEventA_v1')
    classes=Counter((p['event_type'],p['obj_desc']) for p,g in events)
    if any(t!='6-Mass Movement' or d!='Landslide' for t,d in classes):raise ValueError('Unexpected hazard classes; this evaluator is context-only')
    event_sources=sorted({int(p['dmg_src_id']) for p,g in events})
    ref_coverage=burn(coverage(event_sources),grid)
    landslide=burn(unary_union([g for p,g in events]),grid)&ref_coverage
    common=landslide&inside
    polygon_results={name:accounting(common,m,pixel_area) for name,m in models.items()}
    # Original rectangle statistic retained only to explain the previous overlay.
    rectangle_results={name:accounting(landslide,m,pixel_area) for name,m in models.items()}
    coverage_result={'reference_analysed_inside_case_km2':int((ref_coverage&inside).sum())*pixel_area/1e6,
                     'reference_landslide_inside_case_km2':int(common.sum())*pixel_area/1e6,
                     'reference_landslide_outside_case_in_raster_km2':int((landslide&~inside).sum())*pixel_area/1e6}
    # Class polygons support exact line lengths within the pixel-centred raster domain.
    poly={name:{code:polygon_for((m==code)&inside,grid) for code in [0,1,2]} for name,m in models.items()}
    domain=polygon_for(inside,grid)
    to_wgs84=Transformer.from_crs(grid['crs'],4326,always_xy=True).transform
    groups={};feature_output=[]
    inv=~grid['transform'];height,width=grid['shape']
    for layer_name in ['transportationL_v1','transportationP_v1','builtUpP_v1']:
        groups[layer_name]={};outcomes={name:defaultdict(Counter) for name in models}
        line_lengths={name:defaultdict(lambda:defaultdict(float)) for name in models}
        grades=Counter();in_count=Counter();outside=Counter();outside_length=0.;partial=0
        seen=set();duplicates=0
        for props,geom in geoms(layer_name):
            grade=props['damage_gra'];grades[grade]+=1
            key=(geom.normalize().wkb,grade)
            if key in seen:duplicates+=1
            seen.add(key)
            src_id=int(props['dmg_src_id']);cover=coverage([src_id])
            selected=geom.intersection(cover)
            attrs={'reference_fid':props['fid'],'reference_layer':layer_name,'damage_grade':grade,'damage_source_id':src_id}
            if geom.geom_type=='Point':
                colf,rowf=inv*(geom.x,geom.y);r,c=int(np.floor(rowf)),int(np.floor(colf))
                valid=cover.covers(geom) and 0<=r<height and 0<=c<width and bool(inside[r,c])
                if valid:
                    in_count[grade]+=1
                    for name,m in models.items():
                        status={0:'non_candidate',1:'candidate',2:'unknown_inside_case'}[int(m[r,c])]
                        outcomes[name][grade][status]+=1;attrs[name]=status
                else:
                    outside[grade]+=1
                    for name in models:attrs[name]='outside_evaluation_coverage'
            else:
                # The union of 0/1/2 is the physical AOI on the evaluation grid.
                selected=selected.intersection(domain)
                outside_length+=max(0.,geom.length-selected.length)
                if selected.is_empty:
                    outside[grade]+=1
                    for name in models:attrs[name]='outside_evaluation_coverage'
                else:
                    in_count[grade]+=1
                    if selected.length < geom.length-0.01:partial+=1
                    for name in models:
                        lengths={code:selected.intersection(poly[name][code]).length for code in [0,1,2]}
                        if abs(sum(lengths.values())-selected.length)>max(.05,selected.length*1e-6):raise ValueError('Line-length partition inconsistency')
                        for code,label in [(0,'non_candidate_m'),(1,'candidate_m'),(2,'unknown_inside_case_m')]:
                            line_lengths[name][grade][label]+=lengths[code]
                        status='candidate_intersection' if lengths[1]>.01 else ('unknown_no_candidate' if lengths[2]>.01 else 'non_candidate')
                        outcomes[name][grade][status]+=1;attrs[name]=status
                        attrs[name+'_candidate_length_m']=round(lengths[1],3)
                        attrs[name+'_unknown_length_m']=round(lengths[2],3)
            feature_output.append({'type':'Feature','geometry':mapping(transform(to_wgs84,geom)), 'properties':attrs})
        groups[layer_name]={'source_features':layers[layer_name]['total_rows'],
            'nonempty_geometry_features':sum(grades.values()),
            'unlocated_empty_geometry_by_grade':layers[layer_name]['empty_geometry_grades'],
            'damage_grade_counts_nonempty_geometry':dict(grades),
            'inside_evaluation_coverage_by_grade':dict(in_count),'outside_evaluation_coverage_by_grade':dict(outside),
            'partially_covered_line_features':partial,'line_length_outside_evaluation_m':outside_length,
            'duplicate_geometry_and_grade_rows':duplicates,
            'model_feature_counts':{n:{g:dict(c) for g,c in gg.items()} for n,gg in outcomes.items()},
            'model_line_lengths_by_grade':{n:{g:dict(c) for g,c in gg.items()} for n,gg in line_lengths.items()},
            'point_policy':'Exact pixel containing reference point; no matching buffer or OSM identity match',
            'not_damage_classification_accuracy':True}
    report={'schema_version':1,'status':'complete_spatial_context_comparison','created_utc':datetime.now(timezone.utc).isoformat(),
        'reference':identity,'input_sha256':hashes,'checkpoint_sha256':manifest['signature']['checkpoint'],
        'reference_hazard_classes':[{'event_type':k[0],'object':k[1],'features':v} for k,v in classes.items()],
        'reference_sources':sources,'prediction_post_datetime':manifest['source_pair']['post_datetime'],
        'threshold':manifest['threshold'],'threshold_tuned':False,'training_performed':False,
        'grid':{'crs':str(grid['crs']),'shape':list(grid['shape']),'pixel_area_m2':pixel_area,'transform':list(grid['transform'])[:6]},
        'coverage':coverage_result,'landslide_footprint_inside_case':polygon_results,
        'landslide_footprint_in_raster_rectangle':rectangle_results,'infrastructure':groups,
        'osm_export_provenance':hand_prov.get('osm') if hand_prov else None,
        'flood_accuracy_computed':False,'damage_accuracy_computed':False,
        'limitations':['Reference polygons are landslides, predictions are water-flood candidates; overlap is not class-specific accuracy.',
          'No visible damage is not proof of dry land; graded infrastructure is not a flood-water reference.',
          'Physical AOI is inherited from the 20 m observability mask with nearest-neighbour alignment; no finer boundary accuracy is claimed.',
          'The baseline is resampled from 20 m to 10 m for display/accounting only; no added accuracy.',
          'HAND comparison reconstructs an existing AOI-clipped export, not a new raster inference.',
          'Reference image dates and sensors differ; some damage grades use 27 August imagery, others 28 August.',
          'Building points differ from OSM building polygons; point sampling is not object matching.',
          'No one-to-one reference-to-OSM identity matching or spatial-tolerance tuning was performed.',
          'Road pieces with a candidate intersection may also contain unknown terrain; line lengths preserve both.',
          'Results describe one frozen pilot, not the separately trained candidate.']}
    # Inputs must remain unchanged during this check-only operation.
    if hashes!={k:digest(v) for k,v in inputs.items()}:raise ValueError('Input changed during evaluation')
    output.mkdir(parents=True)
    (output/'emsr927_context_comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'reference_feature_comparison.geojson').write_text(json.dumps({'type':'FeatureCollection','features':feature_output})+'\n')
    np.savez_compressed(output/'comparison_arrays.npz',reference_coverage=ref_coverage,reference_landslide=landslide,inside_case=inside,**models)
    print(json.dumps({'status':report['status'],'coverage':coverage_result,'models':polygon_results},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',required=True);p.add_argument('--ai',required=True)
    p.add_argument('--baseline',required=True);p.add_argument('--observability',required=True)
    p.add_argument('--ai-manifest',required=True);p.add_argument('--hand-export');p.add_argument('--out',required=True)
    run(p.parse_args())
