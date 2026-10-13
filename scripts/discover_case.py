"""General AOI/date discovery only; no raw download or SNAP processing."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trinetra.core.cases import make_case, case_paths
from trinetra.discovery import select_s1_pair


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def discover(case, data_root, *, prepare_only=False):
    paths = case_paths(case, data_root)
    run = paths["run"]
    request_path = run / "case_request.json"
    stage_path = run / "discovery_manifest.json"
    pair_path = run / "s1_pair.json"
    expected = case.to_dict()
    if request_path.exists():
        prior = read_json(request_path)
        if any(prior.get(k) != expected[k] for k in
               ("schema", "case_id", "request_sha256", "aoi", "flood_date", "date_policy")):
            raise ValueError("Existing case identity differs; refusing to overwrite its files")
    elif stage_path.exists() or pair_path.exists():
        raise ValueError("Case request is missing but discovery files exist; refusing overwrite")
    if stage_path.exists():
        stage = read_json(stage_path)
        if stage.get("status") == "discovered":
            if stage.get("request_sha256") != case.fingerprint or not pair_path.is_file():
                raise ValueError("Pinned discovery identity or pair file is missing")
            if digest(pair_path) != stage.get("pair_sha256"):
                raise ValueError("Pinned pair hash mismatch; refusing to silently replace it")
            print("Reusing the pinned pair for this case. No catalogue request.")
            return {"case": expected, "paths": paths, "pair": read_json(pair_path)}
    atomic_json(request_path, expected)
    if prepare_only:
        print("Case prepared only. No catalogue request and no scene download.")
        return {"case": expected, "paths": paths, "pair": None}
    stamp = lambda: datetime.now(timezone.utc).isoformat()
    stage = {"schema": 1, "case_id": case.case_id, "request_sha256": case.fingerprint,
             "status": "discovering", "started_at": stamp(),
             "search_days": 30, "minimum_coverage": 0.99,
             "scope": "metadata_only; no downloads, preprocessing or detection"}
    atomic_json(stage_path, stage)
    try:
        pair = select_s1_pair(case.aoi, case.flood_date,
                              cache_dir=paths["metadata_cache"]).to_dict()
        atomic_json(pair_path, pair)
        stage.update(status="discovered", completed_at=stamp(),
                     pair_sha256=digest(pair_path))
        atomic_json(stage_path, stage)
        return {"case": expected, "paths": paths, "pair": pair}
    except (RuntimeError, ValueError, OSError) as exc:
        stage.update(status="failed", failed_at=stamp(), error=str(exc))
        atomic_json(stage_path, stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--date", required=True, help="UTC event day: YYYY-MM-DD")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        case = make_case(read_json(args.aoi), args.date)
        result = discover(case, args.data_dir, prepare_only=args.prepare_only)
        print("Case:", case.case_id)
        print("AOI area (km2):", round(case.area_km2, 3))
        print("Run folder:", result["paths"]["run"])
        print("Future processed folder:", result["paths"]["processed"])
        if result["pair"]:
            print(json.dumps(result["pair"], indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        print("Discovery stopped: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
