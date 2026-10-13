"""General AI case identity and inherited-mask contracts; no inference here."""
from pathlib import Path
import json
import numpy as np
from trinetra.core.files import _sha256


def general_identity(record):
    from trinetra.core.cases import make_case
    request = make_case(record['aoi'], record['flood_date'])
    if record.get('case') != request.case_id or record.get('request_sha256') != request.fingerprint:
        raise ValueError('AI case identity differs from its AOI/date')
    if not str(record.get('source_pair', {}).get('event_start_utc', '')).startswith(request.flood_date+'T'):
        raise ValueError('AI source event differs from the requested date')
    return request


def validate_ai_parent(record, manifest_path):
    request = general_identity(record)
    parent_path = Path(record['parent_processing_manifest']).resolve()
    folder = Path(manifest_path).resolve().parent
    if parent_path.parent.name != request.case_id or folder != parent_path.parent/'ai_sigma0_10m':
        raise ValueError('General AI preprocessing must stay inside its own case folder')
    if _sha256(parent_path) != record.get('parent_processing_manifest_sha256'):
        raise ValueError('Parent processing manifest checksum changed')
    physical = json.loads(parent_path.read_text(encoding='utf-8-sig'))
    from trinetra.flood_detection.pipeline import general_processing_grid
    expected = general_processing_grid(physical, parent_path.parent)
    if physical['source_pair'] != record['source_pair'] or physical['raw_sha256'] != record['raw_sha256']:
        raise ValueError('AI and physical preprocessing refer to different source pairs')
    return request, physical, expected


def clip_observability(obs, grid, aoi):
    from rasterio.features import rasterize
    from shapely.geometry import shape, mapping
    from shapely.ops import transform
    from pyproj import Transformer
    geometry = transform(Transformer.from_crs(4326, grid['crs'], always_xy=True).transform, shape(aoi))
    inside = rasterize([(mapping(geometry), 1)], out_shape=obs.shape, transform=grid['transform']).astype(bool)
    return np.where(inside, obs, 255).astype('uint8')
