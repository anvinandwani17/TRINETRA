"""Stable, bounded AOI/date identities for the general pipeline."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from trinetra.discovery.sentinel1 import _Coverage


@dataclass(frozen=True)
class CaseRequest:
    case_id: str
    fingerprint: str
    aoi: dict
    flood_date: str
    area_km2: float

    def to_dict(self):
        return {"schema": 1, "case_id": self.case_id,
                "request_sha256": self.fingerprint, "aoi": self.aoi,
                "flood_date": self.flood_date, "area_km2": self.area_km2,
                "date_policy": "whole_utc_day_excluded"}


def make_case(aoi, flood_date):
    """Validate a local RFC7946 AOI and UTC event day; never fetch imagery."""
    if not isinstance(flood_date, str):
        raise ValueError("Use a flood date in YYYY-MM-DD format")
    try:
        day = date.fromisoformat(flood_date)
    except ValueError as exc:
        raise ValueError("Use a valid flood date in YYYY-MM-DD format") from exc
    if day.isoformat() != flood_date:
        raise ValueError("Use a flood date in YYYY-MM-DD format")
    if day > datetime.now(timezone.utc).date():
        raise ValueError("Future event dates cannot have an observed pre/post pair")
    if isinstance(aoi, dict) and aoi.get("crs"):
        raise ValueError("AOI must be RFC7946 WGS84, without a custom CRS")
    engine = _Coverage(aoi)
    if engine.area / 1e6 > 300:
        raise ValueError("Pilot AOI limit is 300 km2; choose a smaller area")
    # GEOS normalization makes ring start, direction and part order stable.
    from shapely.geometry import mapping
    geometry = json.loads(json.dumps(mapping(engine.aoi.normalize())))
    identity = {"schema": 1, "aoi": geometry, "flood_date": flood_date}
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")
    fingerprint = hashlib.sha256(serialized).hexdigest()
    return CaseRequest("case-" + fingerprint[:16], fingerprint, geometry,
                       flood_date, engine.area / 1e6)


def case_paths(case, data_root):
    root = Path(data_root).resolve()
    return {"run": root / "runs" / case.case_id,
            "processed": root / "processed" / case.case_id,
            "raw": root / "raw" / "sentinel1",
            "metadata_cache": root / "cache" / "metadata"}
