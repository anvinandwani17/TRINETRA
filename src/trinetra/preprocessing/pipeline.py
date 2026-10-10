"""Trishuli-only physical preprocessing of the verified, automatically selected pair."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

from trinetra.downloads.sentinel1 import DownloadError, atomic_json, now, _sha256
from .io import common_grid, aligned_scene, write_tif, physical_mask_band
from .quality import pair_quality, db_change, REASONS
from .snap import build_graph, run_graph, gpt_path, border_noise_policy
from .metadata import prepare_source, assert_ipf_identifier
from .previews import previews


def checked_pair(manifest):
    """Fail closed: selected IDs must match recorded, readable raw products."""
    if manifest.get('case') != 'trishuli' or manifest.get('flood_date') != '2026-08-26':
        raise DownloadError('First preprocessing runner supports only the pinned Trishuli case')
    if manifest.get('status') != 'complete':
        raise DownloadError('Finish and verify both raw downloads before preprocessing')
    pair = manifest.get('pair', {})
    products = manifest.get('products', {})
    resolved = manifest.get('resolved_metadata', {})
    paths = {}
    for role in ['pre','post']:
        product = products.get(role,{})
        metadata = resolved.get(role,{})
        identity = pair.get(role+'_id','')
        identity = identity if identity.endswith('.SAFE') else identity+'.SAFE'
        if product.get('name') != identity or metadata.get('Name') != identity or metadata.get('Id') != product.get('product_uuid'):
            raise DownloadError('Raw product identity does not match the automatically selected pair')
        path = Path(product.get('path',''))
        verification = product.get('verification',{})
        if not path.is_file() or verification.get('status') != 'valid':
            raise DownloadError('Missing or unverified raw scene')
        if path.stat().st_size != verification.get('bytes') or _sha256(path) != verification.get('sha256'):
            raise DownloadError('Raw archive changed since download verification')
        paths[role] = path.resolve()
    if paths['pre'] == paths['post']:
        raise DownloadError('Before and after must be distinct archives')
    return paths


def preprocess_case(manifest_path='data/runs/trishuli/download_manifest.json', *, gpt=None, dem=None,
                    dem_nodata=-9999, output='data/processed/trishuli', prepare_only=False,
                    cache_mb=2048, threads=2):
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise DownloadError('No download manifest found; finish run_case.py --transport s3 first')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    paths = checked_pair(manifest)
    grid, inside, context = common_grid(manifest['aoi'])
    output = Path(output)
    # Keep this first stage local and separate from every raw/cache input.
    raw = Path('data/raw').resolve()
    if output.resolve().is_relative_to(raw) or raw.is_relative_to(output.resolve()):
        raise DownloadError('Processed output folder must remain separate from raw inputs')
    output.mkdir(parents=True,exist_ok=True)
    report_path = output/'processing_manifest.json'
    report = {'case':'trishuli','status':'running','started_at':now(),'source_manifest':str(manifest_path.resolve()),
              'raw_sha256':{role:manifest['products'][role]['verification']['sha256'] for role in ['pre','post']},
              'source_pair':manifest['pair'], 'resolution_m':20,
              'radiometry':'terrain-flattened Gamma0; linear resampling; dB export',
              'alignment':'independent range-Doppler geocoding + identical snapped UTM grid; no empirical shift applied',
              'shadow_method':'SNAP SAR-Simulation DEM/orbit based layover and shadow, nearest-neighbour geocoding',
              'terrain_context_buffer_m':20000, 'quality_buffer_pixels':1, 'mask_legend':{'1':'observable in both dates and VV/VH','0':'unknown/unobservable; never dry','255':'outside AOI'},
              'reason_bits':REASONS, 'dem':str(Path(dem).resolve()) if dem else 'SNAP Copernicus 30m Global DEM (cached auto download)',
              'limitations':['20 m is output grid spacing, not verified effective resolution or alignment accuracy',
                  'DEM-based mask is an estimate; 30 m DEM cannot resolve all subpixel terrain/building shadows',
                  'Post-minus-pre backscatter change is not a flood classification',
                  'Observability is a terrain/data-quality screen, not a guarantee of flood detectability',
                  'Buffered AOI does not guarantee all distant terrain occluders are represented'],
              'grid':{**{k:v for k,v in grid.items() if k!='transform'},'transform':list(grid['transform'])[:6]},'outputs':{}}
    try:
        if dem:
            import rasterio
            dem = Path(dem)
            if not dem.is_file():
                raise DownloadError('External DEM is missing')
            with rasterio.open(dem) as source:
                from shapely import wkt
                if source.crs is None or source.crs.to_epsg() != 4326 or source.count != 1:
                    raise DownloadError('External DEM must be single-band WGS84 geographic, ellipsoidal height in meters')
                if source.nodata != dem_nodata:
                    raise DownloadError('External DEM nodata does not match --dem-nodata')
                west,south,east,north = wkt.loads(context).bounds
                b = source.bounds
                if not (b.left <= west and b.bottom <= south and b.right >= east and b.top >= north):
                    raise DownloadError('External DEM must cover the AOI plus twenty-km terrain context')
        plan = {}
        border_policies = {role:border_noise_policy(source) for role,source in paths.items()}
        report['border_noise_policy'] = border_policies
        for role, source in paths.items():
            policy = border_policies[role]
            print(f"{role}: IPF {policy['ipf_version']}; {policy['reason']}",flush=True)
            folder = output/'intermediate'/role
            folder.mkdir(parents=True,exist_ok=True)
            plan[role] = {}
            for kind, mask in [('radiometry',False),('geometry',True)]:
                target = folder/(kind+'.dim')
                prepared = folder/'source.dim'
                graph = build_graph(prepared,target,context,grid['crs'],dem=dem,dem_nodata=dem_nodata,mask=mask,
                                    border_noise_required=policy['remove_grd_border_noise'],prepared_source=True)
                target.with_suffix('.graph.xml').write_text(graph,encoding='utf-8')
                plan[role][kind] = {'target':target,'graph':graph}
        if prepare_only:
            report['status'] = 'graphs_prepared_only'
            report['note'] = 'No physical processing executed and no imagery/preview success claimed'
            atomic_json(report_path,report)
            return report
        gpt = gpt_path(gpt)
        # Save installed-operator help and require the actual shadow-mask parameter.
        help_path = output/'snap_operator_help.txt'
        help_parts = []
        for operator in ['Calibration','Terrain-Flattening','SAR-Simulation','Terrain-Correction','Multilook']:
            result = subprocess.run([gpt,operator,'-h'],capture_output=True,text=True,errors='replace',timeout=120)
            help_parts.append(f'OPERATOR: {operator}\n{result.stdout}\n{result.stderr}')
            if result.returncode or (operator == 'SAR-Simulation' and 'saveLayoverShadowMask' not in result.stdout+result.stderr):
                raise DownloadError('Required SNAP operator unavailable; install/update Microwave Toolbox; no mask fallback is allowed')
        help_path.write_text('\n'.join(help_parts),encoding='utf-8')
        report['snap_gpt'] = gpt
        report['snap_operator_help'] = str(help_path.resolve())
        software = _sha256(help_path)
        # Small bounded AOI, but two branches and SNAP intermediates need working space.
        if shutil.disk_usage(output).free < 2 * 1024**3:
            raise DownloadError('Allow at least 2 GiB free working space for preprocessing intermediates')
        atomic_json(report_path,report)
        scenes = {}
        report['snap_reader_compatibility'] = {}
        report['geometry_mask_bands'] = {}
        for role in ['pre','post']:
            prepared = output/'intermediate'/role/'source.dim'
            correction = prepare_source(gpt,paths[role],prepared,context,
                manifest['products'][role]['verification']['sha256'],cache_mb=cache_mb,threads=threads)
            report['snap_reader_compatibility'][role] = correction
            atomic_json(report_path,report)
            bands = {}
            for kind in ['radiometry','geometry']:
                job = plan[role][kind]
                payload = {'graph':job['graph'], 'raw_sha256':manifest['products'][role]['verification']['sha256'],
                           'prepared_dim_sha256':_sha256(prepared),
                           'dem_sha256':_sha256(dem) if dem else 'SNAP cached built-in DEM',
                           'operators_sha256':software}
                signature = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                bands[kind] = run_graph(gpt,job['graph'],job['target'],signature,cache_mb=cache_mb,threads=threads)
                assert_ipf_identifier(job['target'],border_policies[role]['ipf_version'])
            report['geometry_mask_bands'][role] = physical_mask_band(bands['geometry'])
            scenes[role] = aligned_scene(bands['radiometry'],bands['geometry'],grid)
        pre,pre_mask,pre_geometry = scenes['pre']
        post,post_mask,post_geometry = scenes['post']
        observable,reasons,usable = pair_quality(pre,post,pre_mask,post_mask,pre_geometry & post_geometry,inside)
        if not np.any(usable):
            raise DownloadError('No jointly observable pixels remain; inspect geometry/radiometry logs instead of claiming a dry AOI')
        before,after,change = db_change(pre,post,usable)
        tags = {'case':'trishuli','radiometry':'Gamma0 RTC','resolution_m':'20',
                'unknown_policy':'NaN never means dry; use observability and quality_reasons',
                'band_order':'VV,VH','change_definition':'post_dB - pre_dB'}
        outputs = {}
        for name,data in [('before_gamma0_db',before),('after_gamma0_db',after),('change_db',change)]:
            path = write_tif(output/(name+'.tif'),data,grid,descriptions=['VV_dB','VH_dB'],tags=tags)
            outputs[name] = str(path.resolve())
        outputs['observability'] = str(write_tif(output/'observability.tif',observable,grid,
                     descriptions=['pair_observability'],tags={'legend':json.dumps(report['mask_legend'])},nodata=255).resolve())
        outputs['quality_reasons'] = str(write_tif(output/'quality_reasons.tif',reasons,grid,
                     descriptions=['bit_flags'],tags={'bits':json.dumps(REASONS)},nodata=None).resolve())
        for role,mask in [('pre',pre_mask),('post',post_mask)]:
            categorical = np.where(np.isin(mask,[0,1,2,3]),mask,255).astype(np.uint8)
            categorical[~inside] = 255
            outputs[role+'_layover_shadow'] = str(write_tif(output/(role+'_layover_shadow.tif'),categorical,grid,
                         descriptions=['layover_shadow'],tags={'legend':'0=no predicted shadow/layover,1=layover,2=shadow,3=both,255=unknown'},nodata=255).resolve())
        pngs = previews(output/'previews',before,after,change,observable,reasons,grid)
        report.update(status='complete',completed_at=now(),outputs=outputs,
                      output_sha256={name:_sha256(path) for name,path in outputs.items()},
                      previews={p.stem:str(p.resolve()) for p in pngs},
                      aoi_pixels=int(inside.sum()),observable_pixels=int(usable.sum()),
                      observable_fraction=float(usable.sum()/inside.sum()),
                      checks={'shared_crs_transform_shape':'passed','geotiff_roundtrip':'passed',
                              'unknown_change_is_nan':bool(np.all(np.isnan(change[:,~usable]))),
                              'shadow_layover_never_observable':bool(np.all(~usable[(reasons & (4|8|16|32)) != 0]))})
        atomic_json(report_path,report)
        return report
    except Exception as exc:
        report.update(status='failed',failed_at=now(),error=str(exc))
        atomic_json(report_path,report)
        raise
