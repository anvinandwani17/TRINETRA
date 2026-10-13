"""General-case deterministic fallback with unchanged conservative defaults."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from trinetra.core.cases import make_case, case_paths
from trinetra.downloads.case_runner import read_json, pinned_discovery
from trinetra.downloads.sentinel1 import DownloadError
from trinetra.flood_detection.baseline import BaselineConfig
from trinetra.flood_detection.pipeline import run_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aoi', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    args = parser.parse_args()
    try:
        case = make_case(read_json(args.aoi), args.date)
        paths, pair, _ = pinned_discovery(case, args.data_dir)
        manifest = paths['processed']/'processing_manifest.json'
        processing = read_json(manifest)
        if (processing.get('case') != case.case_id or processing.get('request_sha256') != case.fingerprint
                or processing.get('source_pair') != pair or processing.get('status') != 'complete'):
            raise DownloadError('Processing differs from the pinned case or is incomplete')
        report = run_baseline(manifest, paths['processed']/'baseline', BaselineConfig())
        print(json.dumps({key: report.get(key) for key in ['case','status','config','summary','checks','outputs','previews']}, indent=2))
        print('Candidate open-water change, not confirmed flooding or debris damage.')
        print('No neural model, training or new scene downloads were used.')
        return 0
    except (RuntimeError, ValueError, OSError, ImportError, KeyError, AssertionError) as exc:
        print('General baseline stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
