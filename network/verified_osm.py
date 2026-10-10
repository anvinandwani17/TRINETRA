"""Bounded historical OSM queries with honest missing-data states and caching."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import shape

URL = 'https://api.ohsome.org/v1/elements/geometry'
FILTERS = {
    'roads': 'highway=* and type:way',
    'buildings': 'building=* and building!=no and (type:way or type:relation)',
    'settlements': 'place in (city,town,village,hamlet,isolated_dwelling) and type:node',
    'hospitals': 'amenity in (hospital,clinic,doctors,health_post)',
}


def empty():
    return gpd.GeoDataFrame({'geometry': []}, geometry='geometry', crs=4326)


def fetch_legacy_infrastructure(aoi, flood_date, cache_dir=None):
    default = datetime.fromisoformat(flood_date).replace(tzinfo=timezone.utc) - timedelta(days=1)
    try:
        metadata_response = requests.get('https://api.ohsome.org/v1/metadata', timeout=(10, 20))
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        latest = metadata['extractRegion']['temporalExtent']['toTimestamp']
        latest_dt = datetime.fromisoformat(latest.replace('Z', '+00:00'))
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        return {name: empty() for name in FILTERS}, {'source': 'ohsome API v1 / OpenStreetMap', 'snapshot_requested': None,
            'layers': {name: {'status': 'unavailable', 'error': str(exc)[:300]} for name in FILTERS}}, ['Historical OSM coverage could not be verified; infrastructure results are unknown']
    timestamp = os.getenv('TRINETRA_OSM_SNAPSHOT', min(default, latest_dt).isoformat().replace('+00:00', 'Z'))
    parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed >= datetime.fromisoformat(flood_date).replace(tzinfo=timezone.utc) or parsed > latest_dt:
        raise ValueError('OSM snapshot must be a timezone-aware date before the flood event')
    cache_dir = Path(cache_dir or Path(__file__).resolve().parents[1] / 'data/cache/verified_osm')
    cache_dir.mkdir(parents=True, exist_ok=True)
    boundary = shape(aoi)
    frames, provenance, warnings = {}, {}, []
    for name, query in FILTERS.items():
        params = {'bpolys': json.dumps({'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {}, 'geometry': aoi}]}),
                  'filter': query, 'time': timestamp, 'properties': 'tags,metadata', 'clipGeometry': 'false'}
        key = hashlib.sha256(json.dumps({'endpoint': URL, 'params': params, 'version': 1}, sort_keys=True).encode()).hexdigest()
        path = cache_dir / (key + '.json')
        try:
            hit = path.exists()
            if hit:
                content = json.loads(path.read_text(encoding='utf-8'))
            else:
                response = requests.post(URL, data=params, timeout=(10, 45))
                response.raise_for_status()
                content = response.json()
            if content.get('type') != 'FeatureCollection' or not isinstance(content.get('features'), list):
                raise ValueError('OSM service returned an invalid GeoJSON response')
            records = []
            for feature in content['features']:
                geometry = shape(feature['geometry'])
                if geometry.is_empty or not geometry.is_valid or not geometry.intersects(boundary):
                    continue
                props = feature.get('properties', {})
                osm_id = props.get('@osmId') or props.get('osm_id')
                if not osm_id:
                    raise ValueError('OSM response is missing source element IDs')
                records.append({**props, **props.get('tags', {}), 'osm_id': str(osm_id), 'geometry': geometry})
            frames[name] = gpd.GeoDataFrame(records, geometry='geometry', crs=4326) if records else empty()
            if not hit:
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(content), encoding='utf-8')
                tmp.replace(path)
            provenance[name] = {'status': 'available' if records else 'empty', 'snapshot_requested': timestamp,
                                'features': len(records), 'cache_hit': hit, 'endpoint': URL,
                                'response_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'api_version': content.get('apiVersion'), 'attribution': content.get('attribution')}
        except (requests.RequestException, ValueError, KeyError, OSError, TypeError) as exc:
            frames[name] = empty()
            # Failed queries are never written to the cache or turned into features.
            provenance[name] = {'status': 'unavailable', 'snapshot_requested': timestamp, 'error': str(exc)[:300], 'endpoint': URL}
            warnings.append(f'{name}: historical OSM data unavailable; results for this infrastructure type are unknown')
    return frames, {'snapshot_requested': timestamp, 'latest_available': latest, 'source': 'ohsome API v1 / OpenStreetMap', 'layers': provenance}, warnings


def fetch_infrastructure(aoi, flood_date, cache_dir=None):
    """Official v2 GeoParquet extraction; legacy v1 is an explicit opt-in only."""
    if os.getenv('TRINETRA_OSM_PROVIDER') == 'legacy_v1':
        return fetch_legacy_infrastructure(aoi, flood_date, cache_dir)
    from io import BytesIO
    base = os.getenv('OHSOME_API_URL', 'https://api.heigit.org/ohsome-api/v2-rc').rstrip('/')
    # Do not send a user's API key to an arbitrary host from configuration.
    if base not in {'https://api.heigit.org/ohsome-api/v2-rc', 'https://api.heigit.org/ohsome-api/v2'}:
        raise ValueError('OHSOME_API_URL must be an official HeiGIT ohsome v2 endpoint')
    key = os.getenv('OHSOME_API_KEY', '').strip()
    missing = {name: empty() for name in FILTERS}
    if not key:
        return missing, {'source': 'ohsome API v2 / OpenStreetMap', 'snapshot_requested': None,
            'layers': {name: {'status': 'unavailable', 'error': 'OHSOME_API_KEY is not configured'} for name in FILTERS}}, ['Add a free OHSOME_API_KEY locally to load real historical infrastructure; no infrastructure has been invented']
    default = datetime.fromisoformat(flood_date).replace(tzinfo=timezone.utc) - timedelta(days=1)
    cache_dir = Path(cache_dir or Path(__file__).resolve().parents[1] / 'data/cache/verified_osm_v2')
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = cache_dir / 'metadata.json'
    try:
        response = requests.get(base + '/metadata', timeout=(10, 20))
        response.raise_for_status()
        metadata = response.json()
        latest = metadata['temporalExtent']['end']
        latest_dt = datetime.fromisoformat(latest.replace('Z', '+00:00'))
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        metadata_cached = False
    except (requests.RequestException, KeyError, ValueError, TypeError, OSError):
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            latest = metadata['temporalExtent']['end']
            latest_dt = datetime.fromisoformat(latest.replace('Z', '+00:00'))
            metadata_cached = True
        except (OSError, ValueError, KeyError, TypeError):
            return missing, {'source': 'ohsome API v2 / OpenStreetMap', 'snapshot_requested': None,
                'layers': {name: {'status': 'unavailable', 'error': 'Historical OSM temporal coverage could not be verified'} for name in FILTERS}}, ['Historical OSM coverage could not be verified; infrastructure results are unknown']
    timestamp = os.getenv('TRINETRA_OSM_SNAPSHOT', min(default, latest_dt).isoformat().replace('+00:00', 'Z'))
    parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed >= datetime.fromisoformat(flood_date).replace(tzinfo=timezone.utc) or parsed > latest_dt:
        raise ValueError('OSM snapshot must precede the flood and be within verified OSM history coverage')
    filters = {'roads': 'highway=* and geometry:line', 'buildings': 'building=* and building!=no and geometry:polygon',
               'settlements': 'place in (city,town,village,hamlet,isolated_dwelling) and geometry:point',
               'hospitals': 'amenity in (hospital,clinic,doctors,health_post)'}
    boundary = shape(aoi)
    frames, provenance, warnings = {}, {}, []
    if metadata_cached:
        warnings.append('OSM temporal coverage was loaded from previously verified local metadata')
    for name, query in filters.items():
        params = {'aoi': aoi, 'filter': query, 'time': timestamp, 'clip': False}
        cache_key = hashlib.sha256(json.dumps({'url': base, 'params': params, 'version': 1}, sort_keys=True).encode()).hexdigest()
        path = cache_dir / (cache_key + '.parquet')
        marker = path.with_suffix('.verified.json')
        try:
            hit = path.exists()
            if hit:
                raw = path.read_bytes()
                record = json.loads(marker.read_text(encoding='utf-8'))
                if record.get('sha256') != hashlib.sha256(raw).hexdigest() or record.get('cache_key') != cache_key:
                    raise ValueError('Cached infrastructure bytes differ from their verified identity')
            else:
                # Stream with a hard bound: never fetch a global or unbounded extract.
                response = requests.post(base + '/extraction/features.parquet', json=params,
                    headers={'Authorization': key}, timeout=(10, 90), stream=True)
                try:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_content(1024 * 1024):
                        raw.extend(chunk)
                        if len(raw) > 50 * 1024 * 1024:
                            raise ValueError('Bounded infrastructure extract exceeded 50 MiB')
                    raw = bytes(raw)
                finally:
                    response.close()
            source = gpd.read_parquet(BytesIO(raw), to_pandas_kwargs={'maps_as_pydicts': 'strict'})
            if source.crs is None:
                raise ValueError('OSM extract lacks a CRS')
            source = source.to_crs(4326)
            records = []
            for _, row in source.iterrows():
                geometry = row[source.geometry.name]
                if geometry is None or geometry.is_empty or not geometry.is_valid or not geometry.intersects(boundary):
                    continue
                if 'osm_id' not in row or 'osm_type' not in row:
                    raise ValueError('OSM extract lacks source element IDs')
                tags = dict(row.get('tags') or {})
                records.append({**tags, 'osm_id': str(row['osm_type']) + '/' + str(row['osm_id']), 'geometry': geometry})
            frames[name] = gpd.GeoDataFrame(records, geometry='geometry', crs=4326) if records else empty()
            if not hit:
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(raw)
                temporary.replace(path)
                marker.write_text(json.dumps({'cache_key': cache_key, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'snapshot_requested': timestamp, 'endpoint': base + '/extraction/features.parquet'}), encoding='utf-8')
            provenance[name] = {'status': 'available' if records else 'empty', 'snapshot_requested': timestamp,
                'features': len(records), 'cache_hit': hit, 'endpoint': base + '/extraction/features.parquet',
                'response_sha256': hashlib.sha256(raw).hexdigest(), 'attribution': metadata.get('attribution')}
        except Exception as exc:
            # Exception text deliberately excludes HTTP headers and credentials.
            if isinstance(exc, requests.HTTPError):
                error = f'Historical OSM service returned HTTP {exc.response.status_code}'
            elif isinstance(exc, requests.RequestException):
                error = 'Historical OSM request failed or timed out'
            else:
                error = 'Historical OSM extract could not be read or validated (' + type(exc).__name__ + ')'
            frames[name] = empty()
            provenance[name] = {'status': 'unavailable', 'snapshot_requested': timestamp, 'error': error}
            warnings.append(name + ': ' + error + '; impact results are unknown')
    return frames, {'snapshot_requested': timestamp, 'latest_available': latest, 'metadata_cache_hit': metadata_cached,
        'source': 'ohsome API v2 / OpenStreetMap', 'layers': provenance}, warnings
