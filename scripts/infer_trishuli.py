"""Frozen experimental model inference; no training or threshold tuning."""
from pathlib import Path
import sys,argparse,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',default='data/processed/trishuli/ai_sigma0_10m/ai_processing_manifest.json')
    p.add_argument('--checkpoint',default='artifacts/models/siamese_v1/pilot/best.pt')
    p.add_argument('--run-config',default='artifacts/models/siamese_v1/pilot/run_config.json')
    p.add_argument('--output',default='data/processed/trishuli/ai_flood_experimental')
    a=p.parse_args()
    try:
        from trinetra.inference.trishuli import run_case
        print(json.dumps(run_case(a.manifest,a.checkpoint,a.run_config,a.output),indent=2))
    except Exception as exc:p.exit(1,f'Experimental inference stopped: {exc}\n')
