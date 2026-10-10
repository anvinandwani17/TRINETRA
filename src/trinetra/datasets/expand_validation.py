"""Expand only already held-out events by following adjacent official tar records."""
from pathlib import Path
import json
from trinetra.datasets.kuro_siwo import verify_splits, ingest_prefix
from trinetra.datasets.kuro_training_fetch import RangeBudget, INFO_OFFSET, header, sample_extent, REPO
from trinetra.core.files import atomic_json


def expand_validation(root, audit, per_event=8, max_mib=64, max_neighbors=16):
    root, audit = Path(root), Path(audit)
    if not 1 <= per_event <= 32 or not 1 <= max_mib <= 128 or not 1 <= max_neighbors <= 64:
        raise ValueError('Use a small explicit validation expansion')
    audit.mkdir(parents=True, exist_ok=True)
    manifest = root/'samples/local_manifest.json'
    original = json.loads(manifest.read_text())['samples']
    before = audit/'manifest_before_validation_expansion.json'
    if not before.exists(): before.write_bytes(manifest.read_bytes())
    report = verify_splits(root)
    protected = {(r['event_id'], r['grid_id'], r['split']) for r in original if r['split'] != 'validation'}
    seeds = {}
    for row in original:
        if row['split'] == 'validation' and 'byte_offset' in row['source']:
            seeds.setdefault(row['event_id'], row)
    if not seeds: raise ValueError('No validation samples with archive offsets found')
    budget = RangeBudget(root/'cache/training_ranges', max_mib*1024**2)
    errors = []
    for event, seed in seeds.items():
        source = seed['source']
        if source['repo'] != REPO or report['assignments'][event] != 'validation':
            raise ValueError('Seed is not an official validation event')
        url = f'https://huggingface.co/datasets/{REPO}/resolve/{source["revision"]}/{source["shard"]}'
        offset = source['byte_offset']+source['received_sample_bytes']
        try:
            for attempt in range(max_neighbors):
                rows = json.loads(manifest.read_text())['samples']
                have = sum(r['event_id'] == event and r['split'] == 'validation' for r in rows)
                if have >= per_event: break
                print(f'Validation event {event}: {have}/{per_event}; inspecting neighbor {attempt+1}', flush=True)
                metadata = budget.get(url, offset+INFO_OFFSET, 8704)
                member = header(metadata[:512])
                if member is None: break
                key = member.name.split('.', 1)[0]
                info, size = sample_extent(key, offset, metadata)
                if str(info['actid']) != event:
                    print('Reached event boundary; stopping this event.', flush=True); break
                grid = str(info['grid_id'])
                if not any(r['event_id'] == event and r['grid_id'] == grid for r in rows):
                    raw = budget.get(url, offset, size)
                    path = root/'cache/validation_neighbor.tar.part'; path.write_bytes(raw)
                    ingest_prefix(root, path, {'repo': REPO, 'revision': source['revision'],
                                  'shard': source['shard'], 'byte_offset': offset,
                                  'received_sample_bytes': size}, limit=1)
                    path.unlink(missing_ok=True)
                offset += size
        except (OSError, ValueError) as exc:
            errors.append(f'{event}: {exc}'); print(errors[-1], flush=True)
    rows = json.loads(manifest.read_text())['samples']
    after = {(r['event_id'], r['grid_id'], r['split']) for r in rows if r['split'] != 'validation'}
    if protected != after: raise ValueError('Training/test manifest changed unexpectedly; stop other download processes')
    result = {'validation_samples': sum(r['split'] == 'validation' for r in rows),
              'validation_events': sorted({r['event_id'] for r in rows if r['split'] == 'validation'}),
              'counts': {e: sum(r['split'] == 'validation' and r['event_id'] == e for r in rows) for e in seeds},
              'target_per_event': per_event, 'network_mib': budget.received/1024**2,
              'errors': errors, 'model_modified': False,
              'sampling': 'adjacent patches in existing validation events; spatially correlated, not full-event coverage'}
    atomic_json(audit/'validation_expansion_report.json', result)
    print(json.dumps(result, indent=2)); return result
