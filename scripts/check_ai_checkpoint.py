"""Verify local best.pt and its input contract; no training or satellite inference."""
from pathlib import Path
import sys,argparse,json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',default='artifacts/models/siamese_v1/pilot/best.pt')
    p.add_argument('--run-config',default='artifacts/models/siamese_v1/pilot/run_config.json')
    p.add_argument('--output',default='artifacts/reports/ai_checkpoint_check.json')
    a=p.parse_args()
    try:
        from trinetra.models.checkpoint import inspect_checkpoint
        report=inspect_checkpoint(a.checkpoint,a.run_config)
        out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2))
        print('Checkpoint check saved:',out)
    except (ImportError,OSError,ValueError,KeyError,RuntimeError) as exc:
        p.exit(1,f'Checkpoint check stopped: {exc}\n')
