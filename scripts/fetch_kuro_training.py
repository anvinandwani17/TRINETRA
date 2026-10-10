"""Fetch only a bounded, event-balanced Kuro Siwo training/validation subset."""
from pathlib import Path
import sys
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='data/kuro_siwo')
    p.add_argument('--train-per-event', type=int, default=16)
    p.add_argument('--val-per-event', type=int, default=8)
    p.add_argument('--max-mib', type=int, default=1024)
    p.add_argument('--probes', type=int, default=4096)
    a = p.parse_args()
    from trinetra.datasets.kuro_training_fetch import fetch_training
    try: fetch_training(a.root, a.train_per_event, a.val_per_event, a.max_mib, a.probes)
    except (OSError, ValueError) as exc: p.exit(1, f'Data preparation stopped: {exc}\n')
