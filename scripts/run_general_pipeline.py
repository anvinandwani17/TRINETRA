"""One general-case baseline chain including historical OSM/access/report."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aoi', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--allow-downloads', action='store_true')
    parser.add_argument('--transport', choices=('s3','odata'), default='s3')
    parser.add_argument('--max-product-gib', type=float, default=3.0)
    args = parser.parse_args()
    if not math.isfinite(args.max_product_gib) or not 0 < args.max_product_gib <= 3:
        parser.error('Live pipeline limit must be finite, >0 and <=3 GiB per product')
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env', override=False)
        from trinetra.core.cases import make_case
        from api.integrated_jobs import create_job, run_pipeline_job
        case = make_case(json.loads(args.aoi.read_text(encoding='utf-8-sig')), args.date)
        job = create_job(case.flood_date, case.aoi, 'baseline')
        job['pipeline'] = True
        asyncio.run(run_pipeline_job(job['id'], args.allow_downloads, args.transport, args.max_product_gib))
        print(json.dumps({key:job.get(key) for key in ['id','state','message','summary','stage_history','output_dir']}, indent=2, default=str))
        return 0 if job['state']=='COMPLETE' else 1
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        print('Pipeline stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
