"""Run physical Sentinel-1 preprocessing only; never train/classify flooding."""
import argparse
import json
from pathlib import Path
from trinetra.preprocessing.pipeline import preprocess_case


def main():
    parser = argparse.ArgumentParser(description='Trishuli: physical VV/VH preprocessing on a common 20 m grid')
    parser.add_argument('--manifest',default='data/runs/trishuli/download_manifest.json')
    parser.add_argument('--gpt',help='ESA SNAP bin/gpt.exe; defaults to SNAP_GPT environment variable')
    parser.add_argument('--output',default='data/processed/trishuli')
    parser.add_argument('--dem',help='Optional WGS84 geographic DEM with ellipsoidal height in meters (not unconverted EGM2008/EGM96)')
    parser.add_argument('--dem-nodata',type=float,default=-9999)
    parser.add_argument('--prepare-only',action='store_true',help='Prepare graphs; does not claim processing or produce previews')
    parser.add_argument('--cache-mb',type=int,default=2048)
    parser.add_argument('--threads',type=int,default=2)
    args = parser.parse_args()
    if not 256 <= args.cache_mb <= 8192 or not 1 <= args.threads <= 8:
        parser.error('Use cache 256..8192 MiB and threads 1..8')
    try:
        folder = Path(args.output)
        folder.mkdir(parents=True,exist_ok=True)
        lock = folder/'.preprocess.lock'
        try:
            handle = lock.open('x')
        except FileExistsError:
            raise RuntimeError('Preprocessing is already locked. After a crash, confirm the old run stopped before removing .preprocess.lock.') from None
        try:
            with handle:
                import os
                handle.write(str(os.getpid()))
            report = preprocess_case(args.manifest,gpt=args.gpt,dem=args.dem,dem_nodata=args.dem_nodata,
                                     output=args.output,prepare_only=args.prepare_only,cache_mb=args.cache_mb,threads=args.threads)
        finally:
            lock.unlink(missing_ok=True)
        print(json.dumps({k:report.get(k) for k in ['status','outputs','previews','observable_fraction']},indent=2))
        return 0
    except (RuntimeError,ValueError,OSError,ImportError) as exc:
        print(f'Preprocessing stopped: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
