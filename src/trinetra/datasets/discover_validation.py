"""Small deterministic archive scan for one additional held-out event."""
from pathlib import Path
from urllib.request import urlopen
import json
from trinetra.datasets.kuro_siwo import verify_splits, ingest_prefix, REPO
from trinetra.datasets.kuro_training_fetch import RangeBudget, candidates, sample_extent, INFO_OFFSET
from trinetra.datasets.expand_validation import expand_validation
from trinetra.core.files import atomic_json


def discover_validation(root, audit, probes=30):
    root, audit = Path(root), Path(audit)
    if not 1 <= probes <= 30: raise ValueError('This diagnostic scan is limited to 30 probes')
    audit.mkdir(parents=True, exist_ok=True)
    manifest = root/'samples/local_manifest.json'
    before = audit/'manifest_before_new_event.json'
    if not before.exists(): before.write_bytes(manifest.read_bytes())
    report = verify_splits(root)
    rows = json.loads(manifest.read_text())['samples']
    existing = {r['event_id'] for r in rows if r['split'] == 'validation'}
    missing = {e for e, s in report['assignments'].items() if s == 'validation'}-existing
    snapshot = json.loads((root/'metadata/source_snapshot.json').read_text())
    revision = snapshot['hf_revision']
    catalog = {(r['event_id'], r['grid_id']) for r in
               (json.loads(line) for line in (root/'splits/validation.jsonl').read_text().splitlines())}
    result = {'validation_events_before': sorted(existing), 'events_still_missing_before': sorted(missing),
              'new_event': None, 'errors': [], 'model_modified': False,
              'note': 'One additional event, fixed checkpoint/threshold; not complete validation coverage.'}
    budget = RangeBudget(root/'cache/training_ranges', 32*1024**2)
    try:
        if not missing: return result
        api = f'https://huggingface.co/api/datasets/{REPO}/tree/{revision}/train_GRD'
        with urlopen(api, timeout=60) as response: files = json.load(response)
        shards = sorted((f['path'], f['size']) for f in files if f['path'].endswith('.tar'))
        if not shards: raise ValueError('No official labeled GRD shards found')
        rounds = (probes+len(shards)-1)//len(shards)
        visited = set()
        for probe in range(probes):
            shard, total = shards[probe % len(shards)]
            fraction = (probe//len(shards)+1)/(rounds+1)
            start = int((total-524288)*fraction)//512*512
            print(f'New validation event search: probe {probe+1}/{probes}', flush=True)
            url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{shard}'
            window = budget.get(url, start, 524288)
            for key, offset in candidates(window, start):
                if (shard, offset) in visited: continue
                visited.add((shard, offset))
                meta = budget.get(url, offset+INFO_OFFSET, 8704)
                info, size = sample_extent(key, offset, meta)
                event, grid = str(info['actid']), str(info['grid_id'])
                if event not in missing: continue
                if (event, grid) not in catalog: raise ValueError('Candidate absent from official validation index')
                path = root/'cache/new_validation_event.tar.part'
                path.write_bytes(budget.get(url, offset, size))
                ingest_prefix(root, path, {'repo': REPO, 'revision': revision, 'shard': shard,
                                          'byte_offset': offset, 'received_sample_bytes': size}, limit=1)
                path.unlink(missing_ok=True)
                result['new_event'] = event
                print(f'Found additional held-out validation event {event}.', flush=True)
                break
            if result['new_event']: break
    except (OSError, ValueError) as exc:
        result['errors'].append(str(exc)); print('Discovery stopped:', exc, flush=True)
    result['discovery_network_mib'] = budget.received/1024**2
    if result['new_event']:
        # Only follow the new event's neighbors: existing events already have 8.
        result['expansion'] = expand_validation(root, audit/'neighbor_expansion', per_event=4, max_mib=16, max_neighbors=8)
    atomic_json(audit/'new_validation_event_report.json', result)
    print(json.dumps(result, indent=2)); return result
