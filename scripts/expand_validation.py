"""Expand cached validation events without training or a random archive search."""
from pathlib import Path
import sys
import argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.datasets.expand_validation import expand_validation
if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='data/kuro_siwo')
    p.add_argument('--audit',required=True)
    p.add_argument('--per-event',type=int,default=8)
    p.add_argument('--max-mib',type=int,default=64)
    a=p.parse_args()
    try:
        result=expand_validation(a.root,a.audit,a.per_event,a.max_mib)
        if result['errors']:p.exit(1,'Expansion stopped on a reported error; existing samples are preserved.\n')
    except (OSError,ValueError) as exc:p.exit(1,f'Expansion stopped: {exc}\n')
