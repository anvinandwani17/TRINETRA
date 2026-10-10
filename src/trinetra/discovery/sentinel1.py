"""Bounded Sentinel-1 STAC metadata search; never downloads raster assets."""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CATALOG = "https://stac.dataspace.copernicus.eu/v1"
COLLECTION = "sentinel-1-grd"
UTC = timezone.utc


class CatalogError(RuntimeError):
    """Catalog unavailable, invalid response, or incomplete bounded search."""


class NoSuitablePair(RuntimeError):
    """No scene pair passes all suitability constraints."""


def _iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing ISO acquisition timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Acquisition/event timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _event_window(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Flood timestamp must include a timezone")
        instant = value.astimezone(UTC)
        return instant, instant, "instant"
    if isinstance(value, date):
        day = value
    elif isinstance(value, str) and len(value) == 10:
        day = date.fromisoformat(value)
    elif isinstance(value, str):
        instant = _timestamp(value)
        return instant, instant, "instant"
    else:
        raise ValueError("flood_date must be an ISO date or timezone-aware timestamp")
    start = datetime.combine(day, datetime.min.time(), UTC)
    return start, start + timedelta(days=1), "whole_utc_day_excluded"


@dataclass(frozen=True)
class Scene:
    product_id: str
    start: datetime
    end: datetime
    relative_orbit: int
    orbit_direction: str
    platform: str
    coverage: float
    geometry: dict


@dataclass(frozen=True)
class S1Pair:
    pre_id: str
    post_id: str
    pre_datetime: str
    post_datetime: str
    pre_end_datetime: str
    post_end_datetime: str
    relative_orbit: int
    orbit_direction: str
    platform_pre: str
    platform_post: str
    delta_days: float
    pre_age_days: float
    post_delay_days: float
    coverage_pre: float
    coverage_post: float
    coverage_common: float
    aoi_area_km2: float
    event_start_utc: str
    event_end_utc: str
    date_policy: str
    search_days: int
    minimum_coverage: float
    cache_hit: bool
    metadata_fetched_at: str
    candidate_count: int
    rejected: dict
    source: str = CATALOG
    collection: str = COLLECTION
    instrument_mode: str = "IW"
    product_type: str = "IW_GRDH_1S"
    polarizations: tuple = ("VV", "VH")

    def to_dict(self):
        """JSON-compatible result; coverage fractions range from zero to one."""
        return asdict(self)


class _Coverage:
    """Validate WGS84 geometry and measure intersections in local equal-area CRS."""

    def __init__(self, aoi):
        try:
            from pyproj import CRS, Transformer
            from shapely.geometry import box, mapping, shape
            from shapely.ops import transform
        except ImportError as exc:
            raise RuntimeError("Install discovery dependencies: python -m pip install -e .[discovery]") from exc
        if isinstance(aoi, (list, tuple)) and len(aoi) == 4:
            west, south, east, north = aoi
            if west >= east or south >= north:
                raise ValueError("AOI bbox must be [west, south, east, north]")
            aoi = mapping(box(west, south, east, north))
        if not isinstance(aoi, dict):
            raise ValueError("AOI must be GeoJSON Polygon/MultiPolygon/Feature or a bbox")
        if aoi.get("type") == "Feature":
            aoi = aoi.get("geometry")
        if not isinstance(aoi, dict) or aoi.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError("AOI must be Polygon or MultiPolygon")
        if aoi.get("crs"):
            raise ValueError("Provide RFC 7946 GeoJSON in WGS84 longitude/latitude")
        # Reject non-finite or out-of-range coordinates before GEOS operations.
        self._validate_coordinates(aoi["coordinates"])
        self.aoi = shape(aoi)
        if self.aoi.is_empty or not self.aoi.is_valid or self.aoi.area <= 0:
            raise ValueError("AOI must have positive area and valid, non-self-intersecting geometry")
        west, south, east, north = self.aoi.bounds
        if east - west > 2 or north - south > 2 or abs(south) > 80 or abs(north) > 80:
            raise ValueError("Use a local AOI <=2 degrees per axis, within +/-80 latitude; dateline AOIs unsupported")
        center = self.aoi.centroid
        crs = CRS.from_proj4(f"+proj=laea +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs")
        self.project = Transformer.from_crs(4326, crs, always_xy=True).transform
        self.transform = transform
        self.shape = shape
        self.area = self._area(self.aoi)
        if not math.isfinite(self.area) or self.area <= 0 or self.area > 1e10:
            raise ValueError("AOI must have positive area and be <=10,000 km2")
        self.geojson = mapping(self.aoi)

    @staticmethod
    def _validate_coordinates(coordinates):
        count = 0
        def visit(values):
            nonlocal count
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError("Invalid GeoJSON coordinates")
            if isinstance(values[0], (int, float)):
                if len(values) != 2 or not all(math.isfinite(v) for v in values):
                    raise ValueError("Use finite two-dimensional longitude/latitude coordinates")
                if not (-180 <= values[0] <= 180 and -90 <= values[1] <= 90):
                    raise ValueError("AOI coordinates outside WGS84 ranges")
                count += 1
                if count > 5000:
                    raise ValueError("AOI has more than 5,000 vertices; simplify it first")
            else:
                for child in values:
                    visit(child)
        visit(coordinates)

    def _area(self, geometry):
        # Densify before projection so straight lon/lat edges remain represented.
        return self.transform(self.project, geometry.segmentize(0.01)).area

    def _footprint(self, geometry):
        try:
            footprint = self.shape(geometry)
        except Exception as exc:
            raise ValueError("Missing or invalid scene footprint") from exc
        if footprint.geom_type not in {"Polygon", "MultiPolygon"} or not footprint.is_valid or footprint.is_empty:
            raise ValueError("Missing or invalid scene footprint")
        return footprint

    def coverage(self, geometry):
        clipped = self.aoi.intersection(self._footprint(geometry))
        return min(1.0, max(0.0, self._area(clipped) / self.area))

    def common(self, pre, post):
        clipped = self.aoi.intersection(self._footprint(pre.geometry)).intersection(self._footprint(post.geometry))
        return min(1.0, max(0.0, self._area(clipped) / self.area))


def _scene(item, coverage):
    """Do not guess missing orbit metadata from names or absolute-orbit formulas."""
    if not isinstance(item, dict):
        raise ValueError("invalid_catalog_item")
    if item.get("collection") != COLLECTION or not isinstance(item.get("id"), str) or not item["id"]:
        raise ValueError("wrong_collection_or_missing_id")
    p = item.get("properties", {})
    if not isinstance(p, dict):
        raise ValueError("invalid_catalog_properties")
    if p.get("product:type") != "IW_GRDH_1S" or p.get("sar:instrument_mode") != "IW":
        raise ValueError("not_iw_grdh")
    polarizations = p.get("sar:polarizations", [])
    if isinstance(polarizations, str):
        polarizations = polarizations.replace("&", ",").split(",")
    if set(polarizations) != {"VV", "VH"}:
        raise ValueError("not_vv_vh")
    orbit = p.get("sat:relative_orbit")
    if isinstance(orbit, bool) or not isinstance(orbit, int) or not 1 <= orbit <= 175:
        raise ValueError("missing_or_invalid_relative_orbit")
    direction = p.get("sat:orbit_state")
    platform = p.get("platform")
    if direction not in {"ascending", "descending"}:
        raise ValueError("missing_or_invalid_orbit_direction")
    if platform not in {"sentinel-1a", "sentinel-1b", "sentinel-1c", "sentinel-1d"}:
        raise ValueError("missing_or_invalid_platform")
    start = _timestamp(p.get("start_datetime") or p.get("datetime"))
    # Require acquisition end to reject scenes that straddle the event boundary.
    end = _timestamp(p.get("end_datetime"))
    if end < start or end - start > timedelta(minutes=10):
        raise ValueError("invalid_acquisition_interval")
    geometry = item.get("geometry")
    return Scene(item["id"], start, end, orbit, direction, platform, coverage(geometry), geometry)


def _choose_pair(scenes, event_start, event_end, mode, search_days, min_coverage, common):
    lower, upper = event_start - timedelta(days=search_days), event_end + timedelta(days=search_days)
    pre = [s for s in scenes if lower <= s.start and s.end < event_start and s.coverage >= min_coverage]
    post = [s for s in scenes if event_end <= s.start <= upper and s.coverage >= min_coverage
            and (mode != "instant" or s.start > event_end)]
    choices = []
    for before in pre:
        for after in post:
            if before.relative_orbit != after.relative_orbit or before.orbit_direction != after.orbit_direction:
                continue
            shared = common(before, after)
            if shared < min_coverage:
                continue
            pre_age = (event_start - before.start).total_seconds() / 86400
            delay = (after.start - event_end).total_seconds() / 86400
            # Prefer same platform; then the earliest post-event observation,
            # then the latest suitable pre-event baseline. IDs only break ties.
            score = (before.platform != after.platform, delay, pre_age, -shared,
                     before.product_id, after.product_id)
            choices.append((score, before, after, shared))
    if not choices:
        raise NoSuitablePair(f"No suitable same-orbit/direction IW GRDH VV/VH pair: "
                             f"{len(pre)} eligible before, {len(post)} after, "
                             f"coverage >= {min_coverage:.1%}, window +/-{search_days} days. "
                             "Try a smaller AOI or a wider explicit window; no unsafe fallback is returned.")
    _, before, after, shared = min(choices, key=lambda choice: choice[0])
    return before, after, shared


def _trusted_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "stac.dataspace.copernicus.eu" or p.port not in {None, 443} or p.username or p.password or not p.path.startswith("/v1/"):
        raise CatalogError("Catalog pagination/redirect must stay on the official HTTPS STAC metadata host")
    return url


class _MetadataRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request_json(url, method, body, timeout):
    _trusted_url(url)
    headers = {"Accept": "application/geo+json, application/json", "User-Agent": "TRINETRA/0.1 discovery"}
    encoded = json.dumps(body).encode() if body is not None else None
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    # Public STAC metadata needs no credentials. Never send download credentials here.
    for attempt in range(3):
        try:
            request = Request(url, data=encoded, headers=headers, method=method)
            with build_opener(_MetadataRedirects()).open(request, timeout=timeout) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise CatalogError("Catalog metadata page exceeded 5 MB")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise CatalogError(f"Copernicus STAC HTTP {exc.code}. Check catalog/network access; "
                               "no satellite data or credentials were downloaded/sent.") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise CatalogError("Copernicus STAC request failed after retries; check network access") from exc
        except (ValueError, UnicodeError) as exc:
            raise CatalogError("Copernicus STAC returned invalid JSON metadata") from exc


def _fetch_metadata(query, timeout=30, max_pages=10):
    url, method, body = CATALOG + "/search", "POST", query
    items, seen = {}, set()
    for _ in range(max_pages):
        signature = json.dumps([url, method, body], sort_keys=True)
        if signature in seen:
            raise CatalogError("Repeated pagination link; refusing an incomplete search")
        seen.add(signature)
        page = _request_json(url, method, body, timeout)
        if not isinstance(page, dict) or page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise CatalogError("Expected a STAC FeatureCollection")
        for item in page["features"]:
            if not isinstance(item, dict):
                raise CatalogError("Invalid STAC item")
            # Assets are not needed. Do not cache signed asset URLs or asset data.
            clean = {k: item.get(k) for k in ("id", "type", "collection", "geometry", "properties")}
            items[str(item.get("id"))] = clean
        next_links = [link for link in page.get("links", []) if link.get("rel") == "next"]
        if not next_links:
            return list(items.values())
        if len(next_links) != 1:
            raise CatalogError("Ambiguous next-page metadata link")
        link = next_links[0]
        url = _trusted_url(urljoin(url, link["href"]))
        method = link.get("method", "GET").upper()
        if method not in {"GET", "POST"}:
            raise CatalogError("Unsupported catalog pagination method")
        if method == "POST":
            next_body = link.get("body", {})
            if not isinstance(next_body, dict):
                raise CatalogError("Invalid next-page request body")
            body = {**(body or query), **next_body} if link.get("merge", False) else next_body
        else:
            body = None
    raise CatalogError(f"Catalog search exceeded {max_pages} pages; reduce AOI/window. No partial pair returned.")


def _cached_metadata(query, cache_dir, ttl_hours, refresh, fetch):
    # GeoJSON mappings may contain tuples; cache JSON contains lists.
    query = json.loads(json.dumps(query))
    key = hashlib.sha256(json.dumps({"schema": 1, "source": CATALOG, "query": query}, sort_keys=True).encode()).hexdigest()
    path = Path(cache_dir) / f"s1-{key}.json"
    now = datetime.now(UTC)
    if not refresh and path.exists():
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            age = (now - _timestamp(envelope["fetched_at"])).total_seconds()
            if envelope["query"] == query and envelope["source"] == CATALOG and 0 <= age <= ttl_hours * 3600 and isinstance(envelope["items"], list):
                return envelope["items"], envelope["fetched_at"], True
        except (ValueError, KeyError, TypeError):
            pass  # Corrupt/stale caches are refreshed, never represented as success.
    items = fetch(query)
    fetched_at = _iso(now)
    envelope = {"schema": 1, "source": CATALOG, "query": query, "fetched_at": fetched_at, "items": items}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(envelope, file)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return items, fetched_at, False


def select_s1_pair(aoi, flood_date, *, search_days=30, min_coverage=0.99,
                   cache_dir=None, cache_ttl_hours=24, refresh=False):
    """Select a real catalog pair from AOI/date; no manually supplied product IDs.

    Date-only input excludes that entire UTC day. A timezone-aware timestamp
    specifies an exact event instant. Search uses a local AOI and +/-30 days.
    Same relative orbit AND orbit direction are mandatory; no cross-orbit fallback.
    """
    if isinstance(search_days, bool) or not isinstance(search_days, int) or not 1 <= search_days <= 90:
        raise ValueError("search_days must be an integer from 1 to 90")
    if not math.isfinite(min_coverage) or not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")
    if not math.isfinite(cache_ttl_hours) or not 0 <= cache_ttl_hours <= 168:
        raise ValueError("cache_ttl_hours must be in [0, 168]")
    start, end, mode = _event_window(flood_date)
    if start > datetime.now(UTC):
        raise ValueError("Flood date is in the future; before/after discovery is not yet possible")
    engine = _Coverage(aoi)
    query = {"collections": [COLLECTION], "intersects": engine.geojson,
             "datetime": f"{_iso(start - timedelta(days=search_days))}/{_iso(end + timedelta(days=search_days))}",
             "query": {"product:type": {"eq": "IW_GRDH_1S"}, "sar:instrument_mode": {"eq": "IW"}},
             "limit": 100, "fields": {"exclude": ["assets"]}}
    if cache_dir is None:
        from trinetra.core.config import Settings
        root = Settings().metadata_cache_dir
    else:
        root = cache_dir
    items, fetched_at, cache_hit = _cached_metadata(query, root, cache_ttl_hours, refresh, _fetch_metadata)
    scenes, rejected = [], Counter()
    for item in items:
        try:
            scene = _scene(item, engine.coverage)
            if scene.coverage < min_coverage:
                rejected["insufficient_aoi_coverage"] += 1
            scenes.append(scene)
        except (ValueError, TypeError, KeyError) as exc:
            rejected[str(exc)] += 1
    try:
        pre, post, shared = _choose_pair(scenes, start, end, mode, search_days, min_coverage, engine.common)
    except NoSuitablePair as exc:
        raise NoSuitablePair(f"{exc} Catalog returned {len(items)} items; metadata rejections: {dict(rejected)}") from exc
    return S1Pair(
        pre_id=pre.product_id, post_id=post.product_id,
        pre_datetime=_iso(pre.start), post_datetime=_iso(post.start),
        pre_end_datetime=_iso(pre.end), post_end_datetime=_iso(post.end),
        relative_orbit=pre.relative_orbit, orbit_direction=pre.orbit_direction,
        platform_pre=pre.platform, platform_post=post.platform,
        delta_days=(post.start - pre.start).total_seconds() / 86400,
        pre_age_days=(start - pre.start).total_seconds() / 86400,
        post_delay_days=(post.start - end).total_seconds() / 86400,
        coverage_pre=pre.coverage, coverage_post=post.coverage, coverage_common=shared,
        aoi_area_km2=engine.area / 1e6, event_start_utc=_iso(start), event_end_utc=_iso(end),
        date_policy=mode, search_days=search_days, minimum_coverage=min_coverage,
        cache_hit=cache_hit, metadata_fetched_at=fetched_at,
        candidate_count=len(items), rejected=dict(rejected),
    )
