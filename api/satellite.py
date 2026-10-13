"""Read verified local satellite outputs; never synthesize satellite evidence."""
import hashlib
import json
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]


def data_root():
    return Path(os.getenv('TRINETRA_DATA_DIR', str(ROOT / 'data'))).resolve()


def resolve_case(aoi, flood_date, method='baseline'):
    from trinetra.core.cases import make_case
    request = make_case(aoi, flood_date)
    requested = shape(request.aoi)
    legacy = Path(os.getenv('TRINETRA_CASE_DIR', str(ROOT / 'data/processed/trishuli'))).resolve()
    legacy_detection = legacy / ('baseline/baseline_manifest.json' if method == 'baseline' else 'ai_flood_experimental/inference_manifest.json')
    if flood_date == '2026-08-26' and shape(case_aoi()).buffer(1e-8).covers(requested):
        if (legacy / 'processing_manifest.json').is_file() and legacy_detection.is_file():
            return 'trishuli', legacy, request
    if method != 'baseline':
        raise ValueError('Experimental AI is currently supported only for the saved Trishuli case; choose baseline for general cases')
    return request.case_id, data_root() / 'processed' / request.case_id, request


def available_cases():
    result = [{'case':'trishuli', 'flood_date':'2026-08-26', 'aoi':case_aoi(),
               'methods':['baseline','experimental_ai'], 'label':'Trishuli saved demo'}]
    for folder in sorted((data_root() / 'processed').glob('case-*'))[:200]:
        try:
            processing = read_json(folder / 'processing_manifest.json')
            detection = read_json(folder / 'baseline/baseline_manifest.json')
            if processing.get('status') != 'complete' or detection.get('status') != 'complete':
                continue
            from trinetra.core.cases import make_case
            request = make_case(processing['aoi'], processing['flood_date'])
            if request.case_id != folder.name or request.fingerprint != processing.get('request_sha256'):
                continue
            result.append({'case':request.case_id, 'flood_date':request.flood_date,
                           'aoi':request.aoi, 'methods':['baseline'],
                           'label':request.case_id + ' — ' + request.flood_date})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def case_aoi():
    data = read_json(ROOT / 'examples/trishuli_satellite.geojson')
    geometry = data.get('geometry', data)
    polygon = shape(geometry)
    if not polygon.is_valid or polygon.is_empty or polygon.geom_type != 'Polygon':
        raise ValueError('Trishuli case AOI must be a valid Polygon')
    return geometry


def empty_frame():
    return gpd.GeoDataFrame({'geometry': []}, geometry='geometry', crs='EPSG:4326')


def to_frame(geometry, properties=None):
    if geometry.is_empty:
        return empty_frame()
    return gpd.GeoDataFrame([{**(properties or {}), 'geometry': geometry}], crs='EPSG:4326')


def load_satellite(aoi, flood_date, config='default'):
    method = {'default': 'baseline', 'baseline': 'baseline', 'experimental_ai': 'experimental_ai'}.get(config)
    if not method:
        raise ValueError('Choose baseline or experimental_ai')
    case_id, folder, request = resolve_case(aoi, flood_date, method)
    requested = shape(request.aoi)
    if not requested.is_valid or requested.is_empty or requested.geom_type != 'Polygon':
        raise ValueError('AOI must be a valid nonempty Polygon')
    processing = read_json(folder / 'processing_manifest.json')
    if processing.get('status') != 'complete' or processing.get('case') != case_id:
        raise ValueError('Satellite preprocessing is not complete for the requested case')
    if case_id != 'trishuli':
        from trinetra.flood_detection.pipeline import general_processing_grid
        general_processing_grid(processing, folder)
    if method == 'baseline':
        sub = folder / 'baseline'
        manifest = read_json(sub / 'baseline_manifest.json')
        keys = {'mask': 'flood_mask', 'geojson': 'flood_geojson', 'score': 'evidence_score'}
        if manifest.get('status') != 'complete':
            raise ValueError('Deterministic baseline is not complete')
        if case_id != 'trishuli' and (manifest.get('case') != case_id or manifest.get('request_sha256') != request.fingerprint):
            raise ValueError('Baseline identity differs from the requested case')
        pair = manifest.get('pair', {})
    else:
        sub = folder / 'ai_flood_experimental'
        manifest = read_json(sub / 'inference_manifest.json')
        keys = {'mask': 'mask', 'geojson': 'geojson', 'score': 'score'}
        if manifest.get('status') != 'complete_experimental':
            raise ValueError('Experimental AI inference is not complete')
        pair = manifest.get('source_pair', {})
    if pair != processing.get('source_pair'):
        raise ValueError('Detection and preprocessing refer to different satellite pairs')
    if not str(pair.get('event_start_utc', '')).startswith(flood_date):
        raise ValueError('Satellite source event date differs from the requested date')
    files = {}
    for role, key in keys.items():
        filename = str(manifest['outputs'][key]).replace('\\', '/').split('/')[-1]
        path = sub / filename
        expected = manifest.get('output_sha256', {}).get(key)
        if not expected or not path.is_file() or digest(path) != expected:
            raise ValueError(f'Satellite {role} file is missing or its recorded hash does not match')
        files[role] = path
    obs_path = folder / 'observability.tif'
    if digest(obs_path) != processing['output_sha256']['observability']:
        raise ValueError('Observability hash differs from preprocessing provenance')
    with rasterio.open(obs_path) as obs:
        observed = obs.read(1)
        if not set(np.unique(observed)).issubset({0, 1, 255}):
            raise ValueError('Invalid observability codes')
        project = Transformer.from_crs(4326, obs.crs, always_xy=True).transform
        inside_obs = rasterize([(mapping(transform(project, requested)), 1)], out_shape=observed.shape, transform=obs.transform).astype(bool)
        if not inside_obs.any() or np.any(inside_obs & (observed == 255)):
            raise ValueError('Requested AOI is outside the recorded satellite coverage')
        observable_fraction = float(np.count_nonzero(inside_obs & (observed == 1)) / inside_obs.sum())
        observation_grid = (obs.transform, obs.crs)
    with rasterio.open(files['mask']) as src, rasterio.open(files['score']) as score:
        data = src.read(1)
        if src.count != 1 or src.nodata != 255 or not set(np.unique(data)).issubset({0, 1, 255}):
            raise ValueError('Flood mask must contain 0, 1 and 255 with 255 as unknown')
        if not src.crs or not src.crs.is_projected or src.crs.linear_units != 'metre':
            raise ValueError('Flood mask requires a metric projected CRS')
        if (src.crs, src.transform, src.shape) != (score.crs, score.transform, score.shape):
            raise ValueError('Mask and score grids differ')
        project = Transformer.from_crs(4326, src.crs, always_xy=True).transform
        unproject = Transformer.from_crs(src.crs, 4326, always_xy=True).transform
        inside = rasterize([(mapping(transform(project, requested)), 1)], out_shape=data.shape, transform=src.transform).astype(bool)
        if not inside.any():
            raise ValueError('AOI has no pixels at the operational resolution')
        from rasterio.warp import reproject, Resampling
        observed_on_grid = np.full(data.shape, 255, dtype='uint8')
        reproject(observed, observed_on_grid, src_transform=observation_grid[0], src_crs=observation_grid[1],
                  src_nodata=255, dst_transform=src.transform, dst_crs=src.crs, dst_nodata=255, resampling=Resampling.nearest)
        if np.any(inside & (data != 255) & (observed_on_grid != 1)):
            raise ValueError('Flood mask treats an unobservable pixel as assessable')
        pixel_area = abs(src.transform.a * src.transform.e - src.transform.b * src.transform.d)
        def polygonize(code):
            mask = inside & (data == code)
            polygons = [shape(g) for g, _ in shapes(mask.astype('uint8'), mask=mask, transform=src.transform)]
            return transform(unproject, unary_union(polygons)).intersection(requested) if polygons else shape({'type': 'GeometryCollection', 'geometries': []})
        flood = polygonize(1)
        unknown = polygonize(255)
        scores = score.read(1)
        if np.any(~np.isfinite(scores[inside & (data != 255)])):
            raise ValueError('Assessable pixels have missing scores')
        candidates = int(np.count_nonzero(inside & (data == 1)))
        unsupported = int(np.count_nonzero(inside & (data == 255)))
        resolution = abs(float(src.transform.a))
    # Vector export is checked for valid WGS84 geometries; the map polygons are
    # regenerated from the verified raster so raster and map decisions agree.
    vector = read_json(files['geojson'])
    if vector.get('type') != 'FeatureCollection':
        raise ValueError('Invalid satellite vector export')
    for feature in vector.get('features', []):
        geometry = shape(feature['geometry'])
        if not geometry.is_valid or geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
            raise ValueError('Invalid satellite candidate polygon')
    provenance = {
        'method': method, 'case': case_id, 'flood_date': flood_date,
        'source_pair': pair, 'raw_sha256': processing.get('raw_sha256'),
        'output_sha256': {role: digest(path) for role, path in files.items()},
        'processing_manifest_sha256': digest(folder / 'processing_manifest.json'),
        'detection_manifest_sha256': digest(sub / ('baseline_manifest.json' if method == 'baseline' else 'inference_manifest.json')),
        'resolution_m': resolution, 'probability_calibrated': False,
        'limitations': manifest.get('limitations', []) + processing.get('limitations', []),
        'checkpoint_sha256': manifest.get('signature', {}).get('checkpoint'),
        'aoi_policy': 'Raster decisions are clipped to the requested AOI; resampled edge cells outside it are excluded',
        'full_export_candidate_pixels': manifest.get('candidate_pixels', manifest.get('summary', {}).get('candidate_pixels')),
    }
    summary = {
        'case': case_id,
        'method': method, 'candidate_pixels': candidates,
        'candidate_area_km2': candidates * pixel_area / 1e6,
        'unknown_area_km2': unsupported * pixel_area / 1e6,
        'assessable_fraction': (int(inside.sum()) - unsupported) / int(inside.sum()),
        'observable_fraction': observable_fraction, 'resolution_m': resolution,
        'flood_date': flood_date, 'probability_calibrated': False,
    }
    return {'hazard': to_frame(flood, {'classification': 'candidate_flood', 'method': method}),
            'unknown': to_frame(unknown, {'classification': 'unknown_or_unsupported', 'method': method}),
            'summary': summary, 'provenance': provenance, 'aoi': requested}
