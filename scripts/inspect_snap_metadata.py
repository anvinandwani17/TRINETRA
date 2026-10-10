"""Inspect existing SNAP processing identifiers without rerunning or downloading."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.preprocessing.snap import snap_processing_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dim', default='data/processed/trishuli/intermediate/pre/radiometry.dim')
    args = parser.parse_args()
    path = Path(args.dim)
    if not path.is_file():
        parser.error(f'SNAP metadata does not exist: {path}')
    values = snap_processing_metadata(path)
    print(json.dumps({'dim': str(path), 'snap_processing_identifiers': values}, indent=2))
    if not values:
        print('No processing identifiers found; calibration remains unverified.')


if __name__ == '__main__':
    main()
