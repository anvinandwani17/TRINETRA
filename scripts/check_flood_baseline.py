"""Check deterministic flood outputs independently of the processing runner."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/processed/trishuli/baseline/baseline_manifest.json')
    args = parser.parse_args()
    try:
        from trinetra.flood_detection.verify import verify_baseline
        summary = verify_baseline(args.manifest)
        print('PASS: shared grid, checksums, unknown policy, terrain/change rules, GeoJSON/raster agreement and readable previews')
        print(json.dumps(summary,indent=2))
        return 0
    except (RuntimeError,ValueError,OSError,ImportError,KeyError,AssertionError) as exc:
        print(f'Baseline verification failed: {exc}')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
