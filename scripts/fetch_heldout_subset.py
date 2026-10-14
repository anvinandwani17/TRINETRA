"""Bounded resumable official validation/test subset fetch; never train or tune."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trinetra.datasets.kuro_siwo import REPO, verify_splits, ingest_prefix, validate_sample
from trinetra.datasets.kuro_training_fetch import RangeBudget, candidates, sample_extent, INFO_OFFSET
from trinetra.core.files import atomic_json


def allowed_events(config, report, split):
    if split not in {'validation', 'test'}: raise ValueError('Only held-out splits are allowed')
    data = config['data']
    if config['config']['mode'] not in {'fit', 'pilot'} or config['threshold'] != .5:
        raise ValueError('Use the trained checkpoint run_config and fixed threshold')
    if data.get('test_used') is not False or set(data.get('excluded', [])) != {'Trishuli', 'EMSR927'}:
        raise ValueError('Checkpoint holdout declarations are missing')
    if data['split_sha256'] != report['split_sha256']:
        raise ValueError('Official partition hashes differ from checkpoint')
    targets = {str(e) for e, s in report['assignments'].items() if s == split}
    train = set(map(str, data['train_events']))
    selection = set(map(str, data['validation_events']))
    if targets & train: raise ValueError('Held-out partition overlaps training')
    if split == 'test' and targets & selection: raise ValueError('Test partition overlaps checkpoint selection')
    if not targets or '927' in targets: raise ValueError('Invalid held-out targets')
    return targets


def probe_start(seed, index, total):
    positions = max(1, (total - 65536) // 512)
    value = int.from_bytes(hashlib.sha256(f'{seed}:{index}'.encode()).digest()[:8], 'big')
    return value % positions * 512


def source_folders(split):
    if split == 'test':
        return ['test_GRD', 'train_GRD']
    if split == 'validation':
        return ['train_GRD']
    raise ValueError('Only held-out splits are allowed')


def list_labeled_shards(revision, split):
    shards = []
    for folder in source_folders(split):
        api = f'https://huggingface.co/api/datasets/{REPO}/tree/{revision}/{folder}'
        with urlopen(api, timeout=60) as response:
            files = json.load(response)
        found = sorted((row['path'], row['size']) for row in files
                       if row['path'].startswith(folder + '/') and row['path'].endswith('.tar'))
        if not found:
            raise ValueError('No official labeled GRD archives found in ' + folder)
        shards.extend(found)
    return shards


def fetch(root, config_path, split, per_event, max_mib, probes, seed):
    root = Path(root).resolve()
    if not 1 <= per_event <= 8 or not 1 <= max_mib <= 256 or not 1 <= probes <= 2048:
        raise ValueError('Use bounded quotas (1..8), MiB (1..256), and probes (1..2048)')
    config_bytes = Path(config_path).read_bytes()
    config = json.loads(config_bytes.decode('utf-8-sig'))
    report = verify_splits(root)
    targets = allowed_events(config, report, split)
    local_path = root / 'samples/local_manifest.json'
    original = json.loads(local_path.read_text())['samples']
    # All non-target rows must remain byte-equivalent in the manifest.
    protected = [r for r in original if r['split'] != split]
    def counts():
        rows = json.loads(local_path.read_text())['samples']
        return {event: sum(r['split'] == split and r['event_id'] == event for r in rows) for event in targets}
    snapshot = json.loads((root/'metadata/source_snapshot.json').read_text())
    revision = snapshot['hf_revision']
    signature = {'revision': revision, 'split': split, 'seed': seed, 'source_folders': source_folders(split),
                 'run_config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                 'split_sha256': report['split_sha256']}
    # Preserve the old train-only cursor; the new shard sequence needs its own cursor.
    state_path = root / 'reports' / f'heldout_{split}_cursor_sources_v2.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'signature': signature, 'next_probe': 0, 'rejected': []}
    if state['signature'] != signature: raise ValueError('Fetch cursor belongs to another config/seed/partition')
    catalog = {(str(r['event_id']), str(r['grid_id'])) for r in
               (json.loads(line) for line in (root/'splits'/f'{split}.jsonl').read_text().splitlines())}
    lock = root/'samples/.heldout_fetch.lock'
    with lock.open('x') as f: f.write(str(__import__('os').getpid()))
    budget = RangeBudget(root/'cache/heldout_ranges', max_mib*1024**2)
    errors = []; visited = set(); current = counts(); rejected = {tuple(x) for x in state.get('rejected', [])}
    start_probe = state['next_probe']
    try:
        shards = list_labeled_shards(revision, split)
        print('Searching labeled folders: ' + ', '.join(source_folders(split)), flush=True)
        for step in range(probes):
            if all(current[e] >= per_event for e in targets): break
            index = start_probe + step
            shard, total = shards[index % len(shards)]
            start = probe_start(seed, index, total)
            url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{shard}'
            window = budget.get(url, start, 65536)
            for key, offset in candidates(window, start):
                identity = (shard, offset)
                if identity in visited or identity in rejected: continue
                visited.add(identity)
                info, size = sample_extent(key, offset, budget.get(url, offset+INFO_OFFSET, 8704))
                event, grid = str(info['actid']), str(info['grid_id'])
                if event not in targets or current[event] >= per_event: continue
                if (event, grid) not in catalog: raise ValueError('Candidate is absent from official held-out index')
                rows = json.loads(local_path.read_text())['samples']
                if any(r['event_id'] == event and r['grid_id'] == grid for r in rows): continue
                path = root/'cache/heldout_selected.tar.part'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(budget.get(url, offset, size))
                try:
                    ingest_prefix(root, path, {'repo': REPO, 'revision': revision, 'shard': shard,
                                              'byte_offset': offset, 'received_sample_bytes': size}, limit=1)
                except ValueError as exc:
                    if str(exc) not in {'Invalid label is marked valid', 'Unexpected label/validity codes'}: raise
                    rejected.add(identity)
                    print(f'Rejected event {event} grid {grid}: {exc}', flush=True)
                finally:
                    path.unlink(missing_ok=True)
                current = counts()
                print(f'Accepted {split} sample counts: {sum(current.values())}; {sum(v>0 for v in current.values())}/{len(targets)} events', flush=True)
            state.update(next_probe=index+1, rejected=[list(x) for x in sorted(rejected)])
            atomic_json(state_path, state)
            if step % 20 == 0:
                print(f'Probe {step+1}/{probes}; range bytes received: {budget.received/1024**2:.2f} MiB', flush=True)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        print('Bounded fetch stopped:', exc, flush=True)
    finally:
        lock.unlink(missing_ok=True)
    after = json.loads(local_path.read_text())['samples']
    if [r for r in after if r['split'] != split] != protected:
        raise ValueError('Non-target sample manifest changed unexpectedly')
    for row in after:
        if row['split'] == split: validate_sample(root, row)
    current = counts()
    result = {'split': split, 'source_folders': source_folders(split), 'samples': sum(current.values()), 'events': sorted(e for e,v in current.items() if v),
              'counts_by_event': current, 'missing_events': sorted(e for e,v in current.items() if not v),
              'target_per_event': per_event, 'all_event_quotas_met': all(v>=per_event for v in current.values()),
              'network_mib_this_run': budget.received/1024**2, 'range_budget_mib': max_mib,
              'next_probe': state['next_probe'], 'rejected_records': len(rejected), 'errors': errors,
              'sampling': 'bounded deterministic archive probes; not random representative/full-event coverage',
              'model_modified': False, 'training_performed': False, 'threshold_tuned': False,
              'note': 'Evaluate accepted samples separately; report subset coverage and rejected-record bias. Never tune against test results.'}
    atomic_json(root/'reports'/f'heldout_{split}_fetch.json', result)
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--run-config', required=True)
    p.add_argument('--split', choices=['validation', 'test'], default='test')
    p.add_argument('--per-event', type=int, default=2)
    p.add_argument('--max-mib', type=int, default=128)
    p.add_argument('--probes', type=int, default=512)
    p.add_argument('--seed', type=int, default=37)
    a = p.parse_args()
    try:
        r = fetch(a.root, a.run_config, a.split, a.per_event, a.max_mib, a.probes, a.seed)
        return 0 if r['samples'] and not r['errors'] else 1
    except (OSError, ValueError, KeyError) as exc:
        p.exit(1, 'Held-out fetch stopped: '+str(exc)+'\n')


if __name__ == '__main__': raise SystemExit(main())
