"""Build a separate Trishuli 10m Sigma0 branch; no model training/inference."""
from pathlib import Path
import sys,argparse,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',default='data/processed/trishuli/processing_manifest.json')
    p.add_argument('--prepare-only',action='store_true')
    a=p.parse_args()
    try:
        from trinetra.preprocessing.ai_sigma0 import preprocess_ai_sigma0
        print(json.dumps(preprocess_ai_sigma0(a.manifest,a.prepare_only),indent=2))
    except Exception as exc:p.exit(1,f'AI preprocessing stopped: {exc}\n')
