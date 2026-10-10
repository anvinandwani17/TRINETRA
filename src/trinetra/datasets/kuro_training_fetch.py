"""Bounded HTTP-range sample selection; no full shards or test imagery."""
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import io
import json
import random
import re
import tarfile
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
    def counts():
        rows = json.loads((root/'samples/local_manifest.json').read_text())['samples']
        return {e: sum(r['event_id'] == e for r in rows) for e in quotas}
    current = counts(); budget = RangeBudget(root/'cache/training_ranges', max_mib*1024**2)
    rng = random.Random(seed); visited = set(); errors = []; catalog = {}
    for split in ['train', 'validation']:
        for line in (root/'splits'/(split+'.jsonl')).read_text().splitlines():
            row = json.loads(line); catalog[(row['event_id'], row['grid_id'])] = row
    try:
        for probe in range(probes):
            if all(current[e] >= quotas[e] for e in quotas): break
            shard, total = shards[probe % len(shards)]
            url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{shard}'
            start = rng.randrange(max(1, (total-65536)//512))*512
            window = budget.get(url, start, 65536)
            for key, sample_start in candidates(window, start):
                identity = (shard, sample_start)
                if identity in visited: continue
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
                path = root/'cache/selected_sample.tar.part'; path.write_bytes(raw)
                ingest_prefix(root, path, {'repo': REPO, 'revision': revision, 'shard': shard,
                                        'byte_offset': sample_start, 'received_sample_bytes': size}, limit=1)
                path.unlink(missing_ok=True); current = counts()
                print(f'Cached event {event}: {current[event]}/{quotas[event]} ({report["assignments"][event]})', flush=True)
    except (ValueError, OSError) as exc: errors.append(str(exc))
    result = {'events': {e: {'split': report['assignments'][e], 'samples': current[e], 'target': quotas[e]} for e in quotas},
              'all_event_quotas_met': all(current[e] >= quotas[e] for e in quotas),
              'network_bytes_this_run': budget.received, 'network_budget_bytes': budget.max_bytes,
              'test_downloaded': False, 'errors': errors,
              'note': 'Subset counts only; not a download of the full metadata-indexed release.'}
    atomic_json(root/'reports/training_download.json', result)
    if not result['all_event_quotas_met']:
        raise ValueError('Bounded fetch incomplete; see reports/training_download.json. No proper training started.')
    return result
