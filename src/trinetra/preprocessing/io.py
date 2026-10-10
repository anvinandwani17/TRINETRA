"""Common grid, safe DIMAP access, conservative resampling and GeoTIFF export."""
from pathlib import Path
import math
import xml.etree.ElementTree as ET
import numpy as np

from trinetra.downloads.sentinel1 import DownloadError


def bands_from_dim(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise DownloadError('SNAP did not create its DIMAP metadata')
    root = ET.parse(path).getroot()
    bands = {}
    for element in root.iter():
        if element.tag.split('}')[-1] == 'DATA_FILE_PATH':
            href = element.attrib.get('href')
            if not href:
                continue
            candidate = (path.parent / href).resolve()
            if not candidate.is_relative_to(path.parent) or not candidate.is_file():
                raise DownloadError('DIMAP band path is missing or escapes the processing folder')
            # BEAM-DIMAP references ENVI headers. GDAL/Rasterio must open the
            # companion data file; it discovers the header automatically.
            if candidate.suffix.lower() == '.hdr':
                candidate = candidate.with_suffix('.img').resolve()
                if not candidate.is_relative_to(path.parent) or not candidate.is_file():
                    raise DownloadError('DIMAP ENVI header has no safe companion .img raster')
            if candidate.stem in bands and bands[candidate.stem] != candidate:
                raise DownloadError('DIMAP contains ambiguous raster band paths')
            bands[candidate.stem] = candidate
    if not bands:
        raise DownloadError('DIMAP has no physical raster bands')
    return bands


def physical_mask_band(bands):
    """Select one explicit physical SNAP mask, rejecting missing/ambiguous masks."""
    allowed = {'layover_shadow_mask', 'layover_shadow_mask_VV', 'layover_shadow_mask_VH'}
    names = [name for name in bands if name in allowed]
    if len(names) != 1:
        raise DownloadError('Missing or ambiguous physical radar shadow/layover mask; refusing observable fallback')
    return names[0]


def common_grid(geometry, resolution=20):
    from shapely.geometry import shape
    from shapely.ops import transform
    from pyproj import Transformer
    from rasterio.transform import from_origin
    polygon = shape(geometry)
    if polygon.is_empty or not polygon.is_valid or polygon.geom_type not in {'Polygon','MultiPolygon'}:
        raise DownloadError('AOI must be a valid Polygon/MultiPolygon')
    center = polygon.centroid
    if not (84.5 <= polygon.bounds[0] < polygon.bounds[2] <= 85.6 and 27.6 <= polygon.bounds[1] < polygon.bounds[3] <= 28.8):
        raise DownloadError('First preprocessing runner accepts only local Trishuli AOIs')
    zone = int((center.x + 180) // 6) + 1
    crs = f'EPSG:{32600 + zone}'
    project = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    projected = transform(project.transform, polygon)
    left, bottom, right, top = projected.bounds
    left, bottom = math.floor(left/resolution)*resolution, math.floor(bottom/resolution)*resolution
    right, top = math.ceil(right/resolution)*resolution, math.ceil(top/resolution)*resolution
    width, height = int(round((right-left)/resolution)), int(round((top-bottom)/resolution))
    if width * height > 4_000_000 or width <= 0 or height <= 0:
        raise DownloadError('AOI exceeds bounded first-version processing grid')
    profile = {'crs': crs, 'transform': from_origin(left, top, resolution, resolution), 'width': width, 'height': height}
    from rasterio.features import geometry_mask
    inside = geometry_mask([projected.__geo_interface__], (height,width), profile['transform'], invert=True)
    # Twenty km terrain context; this is a bounded regional crop, not a global request.
    context = projected.buffer(20_000).envelope
    unproject = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
    context_geo = transform(unproject.transform, context)
    return profile, inside, context_geo.wkt


def _warp(path, grid, categorical=False, array=None):
    import rasterio
    from rasterio.warp import reproject, Resampling
    with rasterio.open(path) as src:
        if not src.crs:
            raise DownloadError('Intermediate raster has no CRS')
        # Mask code zero means observable, so never use zero as category nodata.
        data = array if array is not None else (src.read(1).astype(np.float32) if categorical else src.read(1, masked=True).filled(np.nan).astype(np.float32))
        nodata = 255 if categorical else np.nan
        target = np.full((grid['height'],grid['width']), nodata, dtype=np.float32)
        reproject(data, target, src_transform=src.transform, src_crs=src.crs,
                  src_nodata=nodata, dst_transform=grid['transform'], dst_crs=grid['crs'], dst_nodata=nodata,
                  resampling=Resampling.nearest if categorical else Resampling.bilinear)
    return target


def aligned_scene(radiometry, geometry, grid):
    import rasterio
    from rasterio.warp import reproject, Resampling
    for name in ['Gamma0_VV','Gamma0_VH','elevation','localIncidenceAngle']:
        if name not in radiometry:
            raise DownloadError(f'Missing terrain-corrected band: {name}')
    mask_path = geometry[physical_mask_band(geometry)]
    mask = _warp(mask_path, grid, True)
    dem = _warp(radiometry['elevation'], grid)
    incidence = _warp(radiometry['localIncidenceAngle'], grid)
    geometry_valid = np.isfinite(dem) & np.isfinite(incidence) & (incidence > 0) & (incidence < 90)
    arrays = []
    for band in ['Gamma0_VV','Gamma0_VH']:
        with rasterio.open(radiometry[band]) as src, rasterio.open(mask_path) as flags:
            local_mask = np.full((src.height,src.width),255,dtype=np.float32)
            reproject(flags.read(1).astype(np.float32), local_mask,
                      src_transform=flags.transform, src_crs=flags.crs, src_nodata=255,
                      dst_transform=src.transform,dst_crs=src.crs,dst_nodata=255,
                      resampling=Resampling.nearest)
            data = src.read(1,masked=True).filled(np.nan).astype(np.float32)
            # Invalid terrain radiometry cannot contaminate bilinear interpolation.
            data[(local_mask != 0) | ~np.isfinite(data) | (data <= 0)] = np.nan
        arrays.append(_warp(radiometry[band],grid,array=data))
    return np.stack(arrays), mask, geometry_valid


def write_tif(path, array, grid, *, descriptions, tags=None, nodata=np.nan):
    import rasterio
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    data = array if array.ndim == 3 else array[None,:,:]
    temporary = path.with_suffix('.tmp.tif')
    with rasterio.open(temporary,'w',driver='GTiff',count=data.shape[0],dtype=data.dtype,
                       **grid,nodata=nodata,tiled=True,blockxsize=256,blockysize=256,
                       compress='deflate',BIGTIFF='IF_SAFER') as dst:
        dst.write(data)
        dst.descriptions = tuple(descriptions)
        dst.update_tags(**(tags or {}))
        factors = [factor for factor in [2,4,8,16] if min(data.shape[1:])//factor >= 1]
        if factors:
            from rasterio.enums import Resampling
            dst.build_overviews(factors,Resampling.nearest if data.dtype.kind in 'ui' else Resampling.average)
    temporary.replace(path)
    with rasterio.open(path) as src:
        if src.crs.to_string() != grid['crs'] or src.transform != grid['transform'] or src.count != data.shape[0]:
            raise DownloadError('Written output failed grid verification')
        check = src.read()
        if not np.array_equal(check,data,equal_nan=True):
            raise DownloadError('Written output failed pixel round-trip verification')
    return path
