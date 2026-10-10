"""Run the deterministic Trishuli flood fallback, without downloads or neural models."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/processed/trishuli/processing_manifest.json')
    parser.add_argument('--output',default='data/processed/trishuli/baseline')
    parser.add_argument('--max-slope-deg',type=float,default=10)
    parser.add_argument('--min-area-m2',type=float,default=2000)
    args = parser.parse_args()
    try:
        from trinetra.flood_detection.baseline import BaselineConfig
        from trinetra.flood_detection.pipeline import run_baseline
        config = BaselineConfig(max_slope_deg=args.max_slope_deg,min_area_m2=args.min_area_m2)
        report = run_baseline(args.manifest,args.output,config)
        print(json.dumps({k:report[k] for k in ['status','summary','outputs','previews']},indent=2))
        return 0
    except (RuntimeError,ValueError,OSError,ImportError,KeyError,AssertionError) as exc:
        print(f'Flood baseline stopped: {exc}')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
