"""Fixed SAR transform and training data provenance guards; no terrain model input."""
import json
import re
from datetime import date
from pathlib import Path
import torch
from trinetra.datasets.kuro_siwo import KuroSiwoDataset, REPO
from trinetra.core.files import _sha256

TRANSFORM = {'channel_order': ['pre_VV', 'pre_VH', 'post_VV', 'post_VH'],
             'source': 'Kuro Siwo GRD linear backscatter', 'db_clip': [-35.0, 5.0],
             'scale': '(clip(db,-35,5)+15)/20', 'invalid_input': 0,
             'statistics_fitted': False, 'dem_input': False}


def sar_transform(linear, valid):
    db = 10*torch.log10(linear.clamp_min(1e-10))
    scaled = (db.clamp(-35, 5)+15)/20
    return torch.where(valid[:, None].bool(), scaled, torch.zeros_like(scaled))


def guard_record(row, info, assignments):
    text = re.sub(r'[^a-z0-9]', '', json.dumps([row, info]).lower())
    if 'trishuli' in text or 'trishul' in text or 'emsr927' in text or str(row['event_id']) == '927':
        raise ValueError('Forbidden holdout case: Trishuli / EMSR927')
    if assignments.get(str(row['event_id'])) != row['split']:
        raise ValueError('Event is not in its official partition')
    flood_date = date.fromisoformat(str(info['flood_date'])[:10])
    if not date(2015, 1, 1) <= flood_date < date(2023, 1, 1):
        raise ValueError('Flood event must be within 2015–2022')
    for source in info['sources'].values():
        source_date = date.fromisoformat(str(source['source_date'])[:10])
        if not date(2014, 1, 1) <= source_date < date(2023, 1, 1):
            raise ValueError('Satellite source date outside supported range')
    if row['source']['repo'] != REPO:
        raise ValueError('Training accepts only the official labeled Kuro Siwo source')


class TrainingDataset(KuroSiwoDataset):
    def __init__(self, root, split):
        super().__init__(root, split)
        root = Path(root)
        snapshot = json.loads((root/'metadata/source_snapshot.json').read_text())
        catalog = {r['grid_id']: r for r in (json.loads(line) for line in
                   (root/'splits'/(split+'.jsonl')).read_text().splitlines())}
        for row in self.rows:
            # KuroSiwoDataset validates all field hashes during indexing/loading.
            info = json.loads((root/row['path']/'info.json').read_text())
            guard_record(row, info, self.report['assignments'])
            if row['grid_id'] not in catalog or catalog[row['grid_id']]['event_id'] != row['event_id']:
                raise ValueError('Downloaded patch is absent from the official partition')
            if row['source']['revision'] != snapshot['hf_revision']:
                raise ValueError('Downloaded source revision differs from pinned release')
        self.fingerprint = _sha256(root/'samples/local_manifest.json')
        self.event_ids = sorted({r['event_id'] for r in self.rows})
