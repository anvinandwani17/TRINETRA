"""Offline dependency and integrated API smoke check; no scene/key/model access."""
import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(require_ai=False):
    modules = ['numpy', 'scipy', 'geopandas', 'rasterio', 'shapely', 'pyproj',
               'pyarrow', 'networkx', 'requests', 'dotenv', 'fastapi', 'httpx', 'uvicorn', 'trinetra']
    if require_ai:
        modules.append('torch')
    for name in modules:
        importlib.import_module(name)
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as client:
        health = client.get('/health')
        if health.status_code != 200 or health.json() != {'status': 'ok', 'service': 'trinetra-api'}:
            raise RuntimeError('Integrated /health contract failed')
        routes = set(client.get('/openapi.json').json()['paths'])
        if not {'/api/v1/jobs', '/api/v1/pipeline-jobs', '/api/v1/jobs/{job_id}/export'} <= routes:
            raise RuntimeError('Integrated analysis/export routes are missing')
    return {'status': 'passed', 'imports': modules, 'api': 'api.main:app',
            'health': '/health', 'transport': 'in-process TestClient',
            'ai_import_checked': require_ai,
            'limitations': ['No scene processing, asset restore or numerical inference',
                            'No live OSM request, bound server port or accuracy evaluation']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-ai', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.require_ai)
    except Exception as exc:
        result = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
    result['generated_at'] = datetime.now(timezone.utc).isoformat()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
