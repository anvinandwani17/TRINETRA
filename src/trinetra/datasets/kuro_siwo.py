"""Bounded official Kuro Siwo GRD preparation and event-disjoint sample loading."""
from pathlib import Path
from datetime import date
import gzip
import hashlib
import io
import json
import pickle
import re
import tarfile
import numpy as np
from trinetra.core.files import atomic_json, _sha256

REPO = 'orion-ai-lab/Kuro-Siwo-Webdataset'
FIELDS = ['flood_vv.npy','flood_vh.npy','sec1_vv.npy','sec1_vh.npy',
          'sec2_vv.npy','sec2_vh.npy','dem.npy','mask.npy','valid_mask.npy','info.json']
CHANNELS = ['pre_VV_linear','pre_VH_linear','post_VV_linear','post_VH_linear']


class PrimitiveMetadata(pickle.Unpickler):
    """Author metadata is literal containers; never import pickle globals."""
    def find_class(self,module,name):
        raise ValueError('Non-primitive pickle metadata is forbidden')


def read_metadata_index(path):
    with gzip.open(path,'rb') as stream:
        raw = stream.read(300_000_001)
    if len(raw)>300_000_000:
        raise ValueError('Metadata decompression exceeds its bound')
    result = PrimitiveMetadata(io.BytesIO(raw)).load()
    if not isinstance(result,dict):
        raise ValueError('Expected official GRD metadata dictionary')
    return result


def split_assignment(config, available_events):
    assignments = {}
    for split,key in [('train','train_acts'),('validation','val_acts'),('test','test_acts')]:
        for event in config[key]:
            event = str(event)
            if event in assignments:
                raise ValueError('Event leakage/duplicate assignment: '+event)
            assignments[event] = split
    missing = set(available_events)-set(assignments)
    if missing:
        raise ValueError('Unassigned released events: '+','.join(sorted(missing)))
    empty = sorted(set(assignments)-set(available_events))
    return {event:assignments[event] for event in available_events},empty


def build_splits(root):
    """Count/index the entire release using metadata only, without sample downloads."""
    root = Path(root)
    config = json.loads(re.sub(r'//[^\n]*','',(root/'metadata/data_config.json').read_text()))
    index = read_metadata_index(root/'metadata/KuroV2_grid_dict_test_0_100.gz')
    events = sorted({str(row['info']['actid']) for row in index.values()})
    assignment,empty = split_assignment(config,events)
    folder = root/'splits'; folder.mkdir(parents=True,exist_ok=True)
    counts = {s:{'events':0,'samples':0} for s in ['train','validation','test']}
    for event in events:
        counts[assignment[event]]['events']+=1
    streams = {s:(folder/(s+'.jsonl')).open('w',encoding='utf-8') for s in counts}
    seen = set()
    try:
        for key,row in index.items():
            info = row['info']; event = str(info['actid']); split = assignment[event]
            grid = str(info['grid_id'])
            identity = (event,grid)
            if identity in seen:
                raise ValueError('Duplicate event/grid sample in official index')
            seen.add(identity)
            record = {'sample_id':str(key),'grid_id':grid,'event_id':event,
                      'aoi_id':str(info['aoiid']),'split':split,
                      'flood_date':str(info['flood_date']),
                      'source_dates':{k:v['source_date'] for k,v in info['sources'].items()}}
            streams[split].write(json.dumps(record,sort_keys=True)+'\n')
            counts[split]['samples']+=1
    finally:
        for stream in streams.values(): stream.close()
    report = {'grouping':'actid; all AOIs/captures/patches from an activation stay together',
              'assignments':assignment,'counts':counts,'total_events':len(events),
              'total_samples':len(index),'configured_events_without_samples':empty,
              'metadata_sha256':_sha256(root/'metadata/KuroV2_grid_dict_test_0_100.gz'),
              'split_sha256':{s:_sha256(folder/(s+'.jsonl')) for s in counts}}
    atomic_json(folder/'events.json',report)
    verify_splits(root)
    return report


def verify_splits(root):
    root = Path(root); report = json.loads((root/'splits/events.json').read_text())
    identities,groups = set(),{}
    for split in ['train','validation','test']:
        path = root/'splits'/(split+'.jsonl')
        if _sha256(path)!=report['split_sha256'][split]:
            raise ValueError('Split manifest checksum changed')
        count = 0
        for line in path.read_text().splitlines():
            row = json.loads(line); event = row['event_id']; identity=(event,row['grid_id'])
            if row['split']!=split or report['assignments'][event]!=split or identity in identities:
                raise ValueError('Event leakage, wrong assignment or duplicate sample')
            if event in groups and groups[event]!=split:
                raise ValueError('An event appears across partitions')
            groups[event]=split; identities.add(identity); count+=1
        if count!=report['counts'][split]['samples']:
            raise ValueError('Split sample count differs from metadata')
    return report


def ingest_prefix(root,path,source,limit=2):
    """Read a bounded tar prefix; never extract arbitrary archive paths."""
    if not 1<=limit<=8:
        raise ValueError('Initial loading test accepts 1..8 samples only')
    root = Path(root); split_report = verify_splits(root)
    catalog = {}
    for split in ['train','validation','test']:
        for line in (root/'splits'/(split+'.jsonl')).read_text().splitlines():
            row = json.loads(line); catalog[(row['event_id'],row['grid_id'])]=row
    local_path = root/'samples/local_manifest.json'
    local = json.loads(local_path.read_text()) if local_path.exists() else {'samples':[]}
    stored = {(row['event_id'],row['grid_id']) for row in local['samples']}
    added = 0; complete_seen = 0; current = None; parts = {}
    try:
        with tarfile.open(path,'r|') as archive:
            for member in archive:
                name = member.name
                if '/' in name or not member.isfile() or member.size>1_000_000:
                    raise ValueError('Unexpected tar member or size')
                key,field = name.split('.',1)
                if field not in FIELDS:
                    raise ValueError('Unexpected GRD sample field: '+field)
                if current is not None and current!=key and parts:
                    raise ValueError('Incomplete/interleaved GRD sample')
                current = key
                if field in parts: raise ValueError('Duplicate sample member')
                parts[field] = archive.extractfile(member).read()
                if set(parts)==set(FIELDS):
                    complete_seen += 1
                    info = json.loads(parts['info.json']); event = str(info['actid']); grid = str(info['grid_id'])
                    if (event,grid) not in catalog:
                        raise ValueError('Downloaded sample absent from the verified GRD metadata index')
                    if (event,grid) not in stored:
                        if not re.fullmatch(r'[0-9a-f-]{32,36}',grid) or not event.isdigit():
                            raise ValueError('Unsafe sample identity')
                        folder = root/'samples'/('event_'+event)/grid
                        folder.mkdir(parents=True,exist_ok=True)
                        hashes = {}
                        for field,raw in parts.items():
                            (folder/field).write_bytes(raw); hashes[field]=_sha256(folder/field)
                        row = {'event_id':event,'grid_id':grid,'split':split_report['assignments'][event],
                               'path':str(folder.relative_to(root).as_posix()),'sha256':hashes,'source':source}
                        validate_sample(root,row)
                        local['samples'].append(row); stored.add((event,grid)); added+=1
                    parts={}; current=None
                    if complete_seen>=limit: break
    except tarfile.ReadError:
        # A prefix is intentionally not a full archive. Partial last records
        # are discarded, but complete validated records are kept.
        pass
    atomic_json(local_path,local)
    if not local['samples']:
        raise ValueError('No complete real GRD samples found within the download bound')
    return local


def validate_sample(root,row):
    root = Path(root).resolve(); folder=(root/row['path']).resolve()
    if not folder.is_relative_to(root): raise ValueError('Sample path escapes dataset root')
    if set(row['sha256'])!=set(FIELDS): raise ValueError('Missing sample checksums')
    for field,digest in row['sha256'].items():
        if field not in FIELDS or _sha256(folder/field)!=digest:
            raise ValueError('Sample field/checksum changed')
    info = json.loads((folder/'info.json').read_text())
    if str(info['actid'])!=row['event_id'] or str(info['grid_id'])!=row['grid_id']:
        raise ValueError('Sample metadata identity mismatch')
    arrays = {}
    for field in FIELDS[:-1]:
        a = np.load(folder/field,allow_pickle=False)
        if a.shape!=(1,224,224) or a.dtype!=np.float32:
            raise ValueError('Unexpected GRD array shape/dtype: '+field)
        arrays[field] = a[0]
    labels,valid = arrays['mask.npy'],arrays['valid_mask.npy']
    if not np.isin(valid,[0,1]).all() or not np.isin(labels,[0,1,2,3]).all():
        raise ValueError('Unexpected label/validity codes')
    if np.any((labels==3)&(valid==1)):
        raise ValueError('Invalid label is marked valid')
    return arrays,info


class KuroSiwoDataset:
    """Portable, NumPy-first dataset; no normalization fit and no augmentation yet."""
    def __init__(self,root='data/kuro_siwo',split='train'):
        self.root=Path(root); self.report=verify_splits(root)
        if split not in ['train','validation','test']: raise ValueError('Invalid split')
        local=json.loads((self.root/'samples/local_manifest.json').read_text())
        self.rows=[r for r in local['samples'] if r['split']==split]
        if any(self.report['assignments'][r['event_id']]!=split for r in self.rows):
            raise ValueError('Local samples violate event assignment')
        if not self.rows: raise ValueError('No downloaded samples for '+split)

    def __len__(self):return len(self.rows)

    def __getitem__(self,index):
        row=self.rows[index]; arrays,info=validate_sample(self.root,row)
        flood=date.fromisoformat(str(info['flood_date'])[:10])
        choices=[(date.fromisoformat(info['sources'][name]['source_date']),prefix)
                 for name,prefix in [('SL1','sec1'),('SL2','sec2')]]
        choices=[choice for choice in choices if choice[0]<flood]
        if not choices:raise ValueError('No genuinely pre-event capture')
        pre_date,prefix=max(choices)
        post_date=date.fromisoformat(info['sources']['MS1']['source_date'])
        if post_date<flood:raise ValueError('Post-event capture precedes flood')
        sar=np.stack([arrays[prefix+'_vv.npy'],arrays[prefix+'_vh.npy'],arrays['flood_vv.npy'],arrays['flood_vh.npy']])
        dem=arrays['dem.npy'][None]
        valid=(arrays['valid_mask.npy']==1)&(arrays['mask.npy']!=3)&np.all(np.isfinite(sar)&(sar>0),axis=0)&np.isfinite(dem[0])
        labels=np.where(valid,arrays['mask.npy'],255).astype(np.int64)
        binary=np.where(valid,labels==2,255).astype(np.int64)
        return {'sar_linear':np.where(valid[None],sar,0).astype(np.float32),
                'dem_m':np.where(valid[None],dem,0).astype(np.float32),
                'labels':labels,'flood_target':binary,'valid_mask':valid,
                'event_id':row['event_id'],'grid_id':row['grid_id'],
                'pre_date':str(pre_date),'post_date':str(post_date)}


def load_batch(dataset,batch_size=2):
    if batch_size<1 or len(dataset)<batch_size:raise ValueError('Not enough real samples for requested batch')
    samples=[dataset[i] for i in range(batch_size)]
    batch={k:np.stack([sample[k] for sample in samples]) for k in ['sar_linear','dem_m','labels','flood_target','valid_mask']}
    batch['event_ids']=[s['event_id'] for s in samples]
    return batch


def torch_loader(dataset,batch_size=2):
    """Torch converts the NumPy fields to CPU tensors; no model/training involved."""
    from torch.utils.data import DataLoader
    return DataLoader(dataset,batch_size=batch_size,shuffle=False,num_workers=0)
