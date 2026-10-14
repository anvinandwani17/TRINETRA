"""Bounded HTTP-range sample selection; no full shards or test imagery."""
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import io
import json
import re
import tarfile
import tempfile
from trinetra.datasets.kuro_siwo import FIELDS, REPO, verify_splits, ingest_prefix
from trinetra.core.files import atomic_json

ARRAY_STRIDE = 201728  # Header + padded float32 [1,224,224] NPY, verified below.
BEFORE_INFO = FIELDS[:7]
INFO_OFFSET = 7*ARRAY_STRIDE


class RangeBudget:
    def __init__(self, cache, max_bytes):
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes; self.received = 0; self.requests = 0

    def get(self, url, start, count):
        if start < 0 or not 1 <= count <= 2_000_000: raise ValueError('Unsafe byte range')
        key = hashlib.sha256(f'{url}:{start}:{count}'.encode()).hexdigest()
        path = self.cache/(key+'.range')
        if path.exists() and path.stat().st_size == count: return path.read_bytes()
        if self.received+count > self.max_bytes: raise ValueError('Download budget reached; rerun to reuse cached ranges')
        req = Request(url, headers={'Range': f'bytes={start}-{start+count-1}'})
        with urlopen(req, timeout=60) as response:
            if response.status != 206: raise ValueError('Server must support exact HTTP ranges')
            content_range = response.headers.get('Content-Range', '')
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', content_range)
            if not match or (int(match[1]), int(match[2])) != (start, start+count-1):
                raise ValueError('Unexpected HTTP range')
            data = response.read(count+1)
        self.received += len(data); self.requests += 1
        if len(data) != count: raise ValueError('Truncated/oversized range')
        temp = path.with_suffix('.tmp'); temp.write_bytes(data); temp.replace(path)
        return data


def header(buf):
    try:
        m = tarfile.TarInfo.frombuf(buf, 'utf-8', 'strict')
        if m.isfile() and m.size <= 1_000_000 and re.fullmatch(r'\d+\.[a-z0-9_]+\.(npy|json)', m.name): return m
    except (tarfile.HeaderError, ValueError, UnicodeError): pass
    return None


def candidates(window, window_offset):
    """Infer sample starts only from checksum-validated known tar headers."""
    found = set()
    for i in range(0, len(window)-511, 512):
        m = header(window[i:i+512])
        if m:
            key, field = m.name.split('.', 1)
            if field in BEFORE_INFO and m.size == 200832:
                start = window_offset+i-BEFORE_INFO.index(field)*ARRAY_STRIDE
                if start >= 0 and start not in found:
                    found.add(start); yield key, start


def sample_extent(key, start, info_bytes):
    m = header(info_bytes[:512])
    if not m or m.name != key+'.info.json' or m.size > 8000:
        raise ValueError('Shard layout differs from verified GRD layout')
    info = json.loads(info_bytes[512:512+m.size])
    size = 9*ARRAY_STRIDE+512+((m.size+511)//512)*512
    return info, size


def fetch_training(root, train_quota=16, val_quota=8, max_mib=1024, probes=4096, seed=17):
    root = Path(root); report = verify_splits(root)
    snapshot = json.loads((root/'metadata/source_snapshot.json').read_text())
    revision = snapshot['hf_revision']
    if not 1 <= train_quota <= 512 or not 1 <= val_quota <= 512:
        raise ValueError('Event quotas must be 1..512')
    if not 1 <= max_mib <= 8192 or not 1 <= probes <= 50000: raise ValueError('Unsafe budget/probe count')
    api = f'https://huggingface.co/api/datasets/{REPO}/tree/{revision}/train_GRD'
    with urlopen(api, timeout=60) as response: files = json.load(response)
    shards = sorted((x['path'], x['size']) for x in files if x['path'].endswith('.tar'))
    if not shards: raise ValueError('No official labeled GRD train shards')
    quotas = {e: train_quota if s == 'train' else val_quota
              for e, s in report['assignments'].items() if s in ['train', 'validation']}
    manifest = root/'samples/local_manifest.json'
    original = json.loads(manifest.read_text())['samples']
    protected = [r for r in original if r['split'] not in ['train', 'validation']]
    def counts():
        rows = json.loads((root/'samples/local_manifest.json').read_text())['samples']
        return {e: sum(r['event_id'] == e for r in rows) for e in quotas}
    current = counts(); budget = RangeBudget(root/'cache/training_ranges', max_mib*1024**2)
    signature = {'revision': revision, 'split_sha256': report['split_sha256'],
                 'seed': seed, 'shards': shards, 'schedule': 'sha256-probes-v1'}
    # JSON turns tuples into lists; compare the canonical serialized structure.
    signature = json.loads(json.dumps(signature))
    state_path = root/'reports/training_fetch_cursor_v2.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'signature': signature, 'next_probe': 0, 'rejected': []}
    if state['signature'] != signature:
        raise ValueError('Training fetch cursor belongs to another source/partition/seed')
    rejected = {(r['shard'], r['byte_offset']) for r in state['rejected']}
    visited = set(); errors = []; catalog = {}
    for split in ['train', 'validation']:
        for line in (root/'splits'/(split+'.jsonl')).read_text().splitlines():
            row = json.loads(line); catalog[(row['event_id'], row['grid_id'])] = row
    lock = root/'samples/.heldout_fetch.lock'
    with lock.open('x') as stream:
        stream.write(str(__import__('os').getpid()))
    try:
        first_probe = state['next_probe']
        for step in range(probes):
            if all(current[e] >= quotas[e] for e in quotas): break
            probe = first_probe + step
            shard, total = shards[probe % len(shards)]
            url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{shard}'
            positions = max(1, (total-65536)//512)
            start = int.from_bytes(hashlib.sha256(f'{seed}:{probe}'.encode()).digest()[:8], 'big') % positions * 512
            window = budget.get(url, start, 65536)
            for key, sample_start in candidates(window, start):
                identity = (shard, sample_start)
                if identity in visited or identity in rejected: continue
                visited.add(identity)
                info_bytes = budget.get(url, sample_start+INFO_OFFSET, 8704)
                info, size = sample_extent(key, sample_start, info_bytes)
                event, grid = str(info['actid']), str(info['grid_id'])
                if event not in quotas or current[event] >= quotas[event]: continue
                if (event, grid) not in catalog: raise ValueError('Candidate absent from official index')
                local = json.loads((root/'samples/local_manifest.json').read_text())['samples']
                if any(r['event_id'] == event and r['grid_id'] == grid for r in local): continue
                raw = budget.get(url, sample_start, size)
                # Verify all record names and array sizes before passing it to the safe ingester.
                with tarfile.open(fileobj=io.BytesIO(raw), mode='r|') as archive:
                    members = list(archive)
                if [m.name for m in members] != [key+'.'+f for f in BEFORE_INFO+['info.json','mask.npy','valid_mask.npy']]:
                    raise ValueError('Unexpected selected sample field order')
                if any(m.size != 200832 for m in members if m.name.endswith('.npy')):
                    raise ValueError('Unexpected selected sample array size')
                with tempfile.NamedTemporaryFile(dir=root/'cache', suffix='.tar.part', delete=False) as stream:
                    stream.write(raw); path = Path(stream.name)
                try:
                    ingest_prefix(root, path, {'repo': REPO, 'revision': revision, 'shard': shard,
                                            'byte_offset': sample_start, 'received_sample_bytes': size}, limit=1)
                except ValueError as exc:
                    # Reject known label inconsistencies, never relax the sample validator.
                    if str(exc) not in {'Invalid label is marked valid', 'Unexpected label/validity codes'}:
                        raise
                    rejected.add(identity)
                    state['rejected'].append({'shard': shard, 'byte_offset': sample_start,
                                              'event_id': event, 'grid_id': grid, 'reason': str(exc)})
                    atomic_json(state_path, state)
                    print(f'Rejected invalid sample: event {event}, grid {grid}: {exc}', flush=True)
                    continue
                finally:
                    path.unlink(missing_ok=True)
                current = counts()
                print(f'Cached event {event}: {current[event]}/{quotas[event]} ({report["assignments"][event]})', flush=True)
            state['next_probe'] = probe + 1
            atomic_json(state_path, state)
            if step % 20 == 0:
                print(f'Probe {step+1}/{probes}; received {budget.received/1024**2:.2f} MiB; rejected {len(rejected)}', flush=True)
    except (ValueError, OSError) as exc: errors.append(str(exc))
    finally:
        lock.unlink(missing_ok=True)
    after = json.loads(manifest.read_text())['samples']
    if [r for r in after if r['split'] not in ['train', 'validation']] != protected:
        raise ValueError('Protected test sample manifest changed unexpectedly')
    current = counts()
    result = {'events': {e: {'split': report['assignments'][e], 'samples': current[e], 'target': quotas[e]} for e in quotas},
              'all_event_quotas_met': all(current[e] >= quotas[e] for e in quotas),
              'network_bytes_this_run': budget.received, 'network_budget_bytes': budget.max_bytes,
              'test_downloaded': False, 'errors': errors, 'next_probe': state['next_probe'],
              'rejected_records': len(rejected), 'selection_bias': 'Bounded archive probes with invalid-label rejection; not representative full-event coverage',
              'note': 'Subset counts only; not a download of the full metadata-indexed release. Invalid samples are not accepted into the local manifest.'}
    atomic_json(root/'reports/training_download.json', result)
    if not result['all_event_quotas_met']:
        raise ValueError('Bounded fetch incomplete; see reports/training_download.json. No proper training started.')
    return result
