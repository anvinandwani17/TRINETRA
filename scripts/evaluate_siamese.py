"""Evaluate a frozen proper-training checkpoint; never tune its threshold."""
from pathlib import Path
import sys
import argparse
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='data/kuro_siwo')
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--split', choices=['validation','test'], required=True)
    p.add_argument('--output', default='artifacts/reports/siamese_evaluation.json')
    a = p.parse_args()
    from trinetra.training.runner import evaluate_checkpoint
    try: print(json.dumps(evaluate_checkpoint(a.root, a.checkpoint, a.split, a.output), indent=2))
    except (OSError, ValueError) as exc: p.exit(1, str(exc)+'\n')
