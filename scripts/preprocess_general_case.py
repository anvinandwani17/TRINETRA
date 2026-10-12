"""Run the existing physical SNAP workflow for a separately stored general case."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from trinetra.core.cases import make_case
from trinetra.downloads.case_runner import pinned_discovery, read_json
from trinetra.downloads.sentinel1 import DownloadError
from trinetra.preprocessing.pipeline import preprocess_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aoi', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--gpt', help='Installed SNAP bin/gpt.exe')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--cache-mb', type=int, default=2048)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if not 256 <= args.cache_mb <= 8192 or not 1 <= args.threads <= 8:
        parser.error('Use cache 256..8192 MiB and threads 1..8')
    try:
        case = make_case(read_json(args.aoi), args.date)
        paths, pair, pair_sha = pinned_discovery(case, args.data_dir)
        manifest_path = paths['run'] / 'download_manifest.json'
        manifest = read_json(manifest_path)
        if (manifest.get('request_sha256') != case.fingerprint or manifest.get('case') != case.case_id
                or manifest.get('pair') != pair or manifest.get('pair_sha256') != pair_sha):
            raise DownloadError('Downloads differ from the pinned case discovery')
        folder = paths['processed']
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder / '.preprocess.lock'
        try:
            handle = lock.open('x', encoding='utf-8')
        except FileExistsError as exc:
            raise DownloadError('This case is already locked; confirm any old process stopped before removing a crash lock') from exc
        try:
            with handle:
                handle.write(str(os.getpid()))
            report = preprocess_case(manifest_path, output=folder, gpt=args.gpt,
                                     prepare_only=args.prepare_only,
                                     cache_mb=args.cache_mb, threads=args.threads)
        finally:
            lock.unlink(missing_ok=True)
        print(json.dumps({key: report.get(key) for key in ['case', 'status', 'grid', 'outputs', 'previews', 'observable_fraction']}, indent=2))
        print('No flood classification or neural inference has run.')
        return 0
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        print('Preprocessing stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
