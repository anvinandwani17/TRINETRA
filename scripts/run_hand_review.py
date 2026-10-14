"""Generate optional HAND variants without modifying existing detector outputs."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-dir',required=True)
    p.add_argument('--source-case-dir')
    args=p.parse_args()
    try:
        import os
        if args.source_case_dir:os.environ['TRINETRA_HAND_SOURCE_CASE_DIR']=str(Path(args.source_case_dir).resolve())
        from trinetra.terrain.pipeline import ensure_variants, sensitivity, source_case, read
        results=ensure_variants(args.case_dir)
        folder=Path(args.case_dir).resolve()
        comparison=sensitivity(folder,read(folder/'terrain_hand/hand_manifest.json'),source_case(folder,read(folder/'processing_manifest.json')))
        print('Threshold sensitivity:',folder/'terrain_hand/sensitivity.json')
        print(json.dumps({name:record['terrain_screen'] for name,record in results.items()},indent=2))
        print('Original baseline and AI outputs retained. HAND variants are experimental, not validated flood accuracy.')
    except (OSError,ValueError,KeyError) as e:p.exit(1,'HAND review stopped: '+str(e)+'\n')
