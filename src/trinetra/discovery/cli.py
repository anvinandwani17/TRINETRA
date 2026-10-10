"""CLI for catalog-only before/after Sentinel-1 discovery."""

import argparse
import json
from pathlib import Path
import sys

from .sentinel1 import CatalogError, NoSuitablePair, select_s1_pair


def main():
    parser = argparse.ArgumentParser(description="Find real Sentinel-1 acquisitions; metadata only, no imagery downloads")
    parser.add_argument("--aoi", required=True, type=Path, help="WGS84 GeoJSON Polygon/MultiPolygon/Feature file")
    parser.add_argument("--date", required=True, help="ISO date (whole UTC day excluded) or timezone-aware event timestamp")
    parser.add_argument("--search-days", type=int, default=30)
    parser.add_argument("--min-coverage", type=float, default=0.99)
    parser.add_argument("--refresh", action="store_true", help="Force live catalog query instead of a fresh cache")
    parser.add_argument("--output", type=Path, help="Save a successful pair as JSON")
    args = parser.parse_args()
    try:
        aoi = json.loads(args.aoi.read_text(encoding="utf-8-sig"))
        pair = select_s1_pair(aoi, args.date, search_days=args.search_days,
                              min_coverage=args.min_coverage, refresh=args.refresh)
        output = json.dumps(pair.to_dict(), indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        print(output, end="")
        return 0
    except (CatalogError, NoSuitablePair, ValueError, RuntimeError, OSError) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
