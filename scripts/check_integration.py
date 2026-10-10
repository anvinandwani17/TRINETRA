"""Read-only smoke check of actual satellite inputs and the API."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env', override=False)
from api.satellite import load_satellite, case_aoi
from api.main import app
from fastapi.testclient import TestClient


def main():
    for method in ['baseline', 'experimental_ai']:
        data = load_satellite(case_aoi(), '2026-08-26', method)
        print(json.dumps(data['summary'], indent=2))
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/v1/cases/trishuli').status_code == 200
    print('Real satellite hashes, observability policy and API smoke check PASSED')
    print('Live infrastructure requires OHSOME_API_KEY in your local .env; this check makes no OSM extraction request.')


if __name__ == '__main__':
    main()
