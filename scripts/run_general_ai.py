"""Run general-case AI from completed physical inputs; no scene downloads/train."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True)
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--run-config', required=True)
    args = p.parse_args()
    try:
        from trinetra.core.general_ai import ensure_ai
        manifest = Path(args.manifest).resolve()
        parent = json.loads(manifest.read_text(encoding='utf-8-sig'))
        print(json.dumps(ensure_ai(parent['aoi'], parent['flood_date'], manifest.parents[2],
            checkpoint=args.checkpoint, run_config=args.run_config,
            progress=lambda stage, message, _: print(stage+': '+message, flush=True)), indent=2))
    except Exception as exc: p.exit(1, 'General AI stopped: '+str(exc)+'\n')
