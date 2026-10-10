"""Look for one additional validation event with a strict small download budget."""
from pathlib import Path
import sys,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.datasets.discover_validation import discover_validation
if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='data/kuro_siwo')
    p.add_argument('--audit',required=True)
    a=p.parse_args()
    try:
        result=discover_validation(a.root,a.audit)
        if result['errors']:p.exit(1,'Stopped on the reported error; cached data preserved.\n')
    except (OSError,ValueError) as exc:p.exit(1,f'Stopped: {exc}\n')
