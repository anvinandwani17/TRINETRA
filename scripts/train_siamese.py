"""Train the first four-channel Siamese U-Net without using held-out cases."""
from pathlib import Path
import sys
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['sanity', 'fit'], required=True)
    p.add_argument('--root', default='data/kuro_siwo')
    p.add_argument('--out', default='artifacts/models/siamese_v1')
    p.add_argument('--steps', type=int, default=40)
    p.add_argument('--epochs', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=8)
    p.add_argument('--base-channels', type=int)
    p.add_argument('--seed', type=int, default=17)
    p.add_argument('--sanity-report', default='artifacts/models/siamese_v1/sanity_report.json')
    p.add_argument('--resume')
    a = p.parse_args()
    try:
        from trinetra.training.runner import sanity, fit
        if a.mode == 'sanity':
            sanity(a.root, a.out, a.steps, a.seed, a.base_channels or 8)
        else:
            fit(a.root, a.out, a.sanity_report, a.epochs, a.batch_size,
                a.base_channels or 16, a.seed, a.resume)
    except (ValueError, OSError, ImportError) as exc:
        p.exit(1, f'Training stopped: {exc}\n')


if __name__ == '__main__': main()
