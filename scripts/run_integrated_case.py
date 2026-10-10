"""Run the connected case from existing satellite outputs, without new training."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env', override=False)
from api.integrated_jobs import create_job, run_job
from api.satellite import case_aoi, load_satellite
from reports.grounded import report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--aoi', type=Path)
    parser.add_argument('--date', default='2026-08-26')
    parser.add_argument('--method', choices=['baseline', 'experimental_ai'], default='baseline')
    args = parser.parse_args()
    if args.aoi:
        data = json.loads(args.aoi.read_text(encoding='utf-8-sig'))
        aoi = data.get('geometry', data)
    else:
        aoi = case_aoi()
    load_satellite(aoi, args.date, args.method)
    job = create_job(args.date, aoi, args.method)
    asyncio.run(run_job(job['id']))
    if job['state'] != 'COMPLETE':
        raise SystemExit(job['message'])
    output = Path(job['output_dir'])
    (output / 'SITREP.md').write_text(report(job), encoding='utf-8')
    print(json.dumps(job['summary'], indent=2))
    print('Outputs:', output)
    for warning in job['warnings']:
        print('NOTE:', warning)


if __name__ == '__main__':
    main()
