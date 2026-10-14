"""Bounded event-directed fetching under the frozen RandomEvents partition.

Archive train/test folder names are not this checkpoint's event assignments.
Only rows in the verified train/validation indexes can enter its sample manifest.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.core.files import atomic_json
from trinetra.datasets.kuro_siwo import REPO, verify_splits, ingest_prefix
from trinetra.datasets.kuro_training_fetch import RangeBudget, candidates, header, sample_extent, INFO_OFFSET

FOLDERS = ['test_GRD', 'train_GRD']
KNOWN_INVALID = {'Invalid label is marked valid', 'Unexpected label/validity codes'}


class Archive:
    def __init__(self, shard, total, revision, budget, max_requests):
        self.shard, self.total, self.budget = shard, total, budget
        self.max_requests = max_requests
        self.url = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{shard}'

    def get(self, start, count):
        if self.budget.requests >= self.max_requests:
            raise ValueError('Request budget reached; accepted samples/cache are preserved')
        return self.budget.get(self.url, start, count)

    def exact(self, start):
        raw = self.get(start+INFO_OFFSET, 8704)
        record = header(raw[:512])
        if not record or not record.name.endswith('.info.json'):
            raise ValueError('Archive sample boundary/layout differs')
        key = record.name.split('.', 1)[0]
        info, size = sample_extent(key, start, raw)
        if not start < start+size <= self.total:
            raise ValueError('Archive sample size exceeds its bounds')
        return {'start':start, 'end':start+size, 'key':key, 'info':info}

    def near(self, position):
        start = min(max(0, position//512*512), max(0, self.total-524288)//512*512)
        count = min(524288, self.total-start)
        raw = self.get(start,count)
        # An info header yields an exact boundary without downloading imagery.
        for offset in range(0,len(raw)-511,512):
            record = header(raw[offset:offset+512])
            if record and record.name.endswith('.info.json'):
                base = start+offset-INFO_OFFSET
                if base >= 0:
                    return self.exact(base)
        found = list(candidates(raw,start))
        if not found:
            raise ValueError('No supported sample headers in bounded archive window')
        return self.exact(found[0][1])


def first_event(archive,event):
    """Lower bound using event order; every candidate is still individually checked."""
    low, high = 0, archive.total
    for _ in range(64):
        if low >= high:
            break
        sample = archive.near((low+high)//2)
        if sample['start'] < low:
            sample = archive.exact(low)
        if not low <= sample['start'] < high:
            raise ValueError('Archive search did not progress; ordering/bounds differ')
        identity = int(sample['info']['actid'])
        if identity < event:
            low = sample['end']
        else:
            high = sample['start']
    else:
        raise ValueError('Event search exceeded its iteration bound')
    if low >= archive.total:
        return None
    sample = archive.exact(low)
    return sample['start'] if int(sample['info']['actid']) == event else None


def fetch(root, config_path, train_quota=4, val_quota=2, max_mib=512, max_requests=768, events=None):
    root=Path(root).resolve()
    if not 1 <= train_quota <= 32 or not 1 <= val_quota <= 16:
        raise ValueError('Use train quotas 1..32 and validation quotas 1..16')
    if not 1 <= max_mib <= 2048 or not 1 <= max_requests <= 2048:
        raise ValueError('Use bounded MiB/request limits (1..2048)')
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    report=verify_splits(root)
    if config['data']['split_sha256'] != report['split_sha256']:
        raise ValueError('Dataset partition differs from frozen model; no fetch started')
    revision=json.loads((root/'metadata/source_snapshot.json').read_text())['hf_revision']
    manifest=root/'samples/local_manifest.json'
    original=json.loads(manifest.read_text())['samples']
    protected=[r for r in original if r['split'] not in ['train','validation']]
    catalog={}
    for split in ['train','validation']:
        for line in (root/'splits'/f'{split}.jsonl').read_text().splitlines():
            row=json.loads(line);catalog[(str(row['event_id']),row['grid_id'])]=split
    quotas={str(e):train_quota if split=='train' else val_quota
            for e,split in report['assignments'].items() if split in ['train','validation']}
    targets=set(quotas) if events is None else set(map(str,events))
    if not targets or not targets <= set(quotas):
        raise ValueError('Requested events must be declared training/validation events')
    def counts():
        rows=json.loads(manifest.read_text())['samples']
        return {e:sum(str(r['event_id'])==e and r['split'] in ['train','validation'] for r in rows) for e in quotas}
    state_path=root/'reports/targeted_training_cursor.json'
    signature={'revision':revision,'split_sha256':report['split_sha256'],'folders':FOLDERS,'algorithm':'event-directed-v1'}
    state=json.loads(state_path.read_text()) if state_path.exists() else {
        'signature':signature,'bounds':{},'starts':{},'rejected':[]}
    if state['signature'] != signature:
        raise ValueError('Targeted cursor belongs to another source/partition')
    rejected={(r['shard'],r['byte_offset']) for r in state['rejected']}
    budget=RangeBudget(root/'cache/targeted_ranges',max_mib*1024**2)
    current=counts();errors=[];locations={}
    lock=root/'samples/.heldout_fetch.lock'
    with lock.open('x') as stream:stream.write(str(__import__('os').getpid()))
    try:
        shards=[]
        for folder in FOLDERS:
            with urlopen(f'https://huggingface.co/api/datasets/{REPO}/tree/{revision}/{folder}',timeout=60) as response:
                files=json.load(response)
            entries=sorted((f['path'],f['size']) for f in files
                           if f['path'].startswith(folder+'/') and f['path'].endswith('.tar'))
            if not entries:raise ValueError('No labeled archives found in '+folder)
            shards.extend(entries)
        for shard,total in shards:
            if all(current[e]>=quotas[e] for e in targets):break
            print('Inspecting archive: '+shard,flush=True)
            archive=Archive(shard,total,revision,budget,max_requests)
            bounds=state['bounds'].get(shard)
            if bounds is None:
                first,last=archive.exact(0),archive.near(total-524288)
                bounds=[int(first['info']['actid']),int(last['info']['actid'])]
                if bounds[0]>bounds[1]:raise ValueError('Archive event ordering differs')
                state['bounds'][shard]=bounds;atomic_json(state_path,state)
            eligible=sorted((e for e in targets if current[e]<quotas[e] and bounds[0]<=int(e)<=bounds[1]),key=int)
            for event in eligible:
                cache_key=shard+':'+event
                if cache_key not in state['starts']:
                    print(f'Locating event {event} within archive bounds {bounds}',flush=True)
                    state['starts'][cache_key]=0 if bounds[0]==int(event) else first_event(archive,int(event))
                    atomic_json(state_path,state)
                offset=state['starts'][cache_key]
                if offset is None:continue
                print(f'Located event {event} in {shard}; accepted {current[event]}/{quotas[event]}',flush=True)
                locations[event]=shard
                examined=0
                while current[event]<quotas[event] and offset<total and examined<256:
                    sample=archive.exact(offset);examined+=1
                    info=sample['info'];actual=str(info['actid'])
                    if actual!=event:break
                    identity=(shard,offset)
                    if (actual,info['grid_id']) not in catalog:
                        raise ValueError('Located sample is absent from verified train/validation catalog')
                    rows=json.loads(manifest.read_text())['samples']
                    already=any(str(r['event_id'])==actual and r['grid_id']==info['grid_id'] for r in rows)
                    if not already and identity not in rejected:
                        raw=archive.get(offset,sample['end']-offset)
                        with tempfile.NamedTemporaryFile(dir=root/'cache',suffix='.tar.part',delete=False) as stream:
                            stream.write(raw);path=Path(stream.name)
                        try:
                            ingest_prefix(root,path,{'repo':REPO,'revision':revision,'shard':shard,
                                                     'byte_offset':offset,'received_sample_bytes':len(raw)},limit=1)
                        except ValueError as exc:
                            if str(exc) not in KNOWN_INVALID:raise
                            rejected.add(identity)
                            state['rejected'].append({'shard':shard,'byte_offset':offset,'event_id':event,
                                                      'grid_id':info['grid_id'],'reason':str(exc)})
                            atomic_json(state_path,state)
                        finally:path.unlink(missing_ok=True)
                        current=counts()
                        print(f'Event {event}: {current[event]}/{quotas[event]}; received {budget.received/1024**2:.1f} MiB',flush=True)
                    offset=sample['end']
    except (OSError,ValueError,KeyError) as exc:
        errors.append(str(exc));print('Bounded targeted fetch stopped:',exc,flush=True)
    finally:lock.unlink(missing_ok=True)
    after=json.loads(manifest.read_text())['samples']
    if [r for r in after if r['split'] not in ['train','validation']]!=protected:
        raise ValueError('Protected held-out sample manifest changed')
    current=counts()
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'source_folders':FOLDERS,
            'partition_policy':'Pinned author RandomEvents event assignments; archive folder names are not this model split',
            'events':{e:{'split':report['assignments'][e],'samples':current[e],'target':quotas[e]} for e in sorted(quotas,key=int)},
            'requested_events':sorted(targets,key=int),'requested_quotas_met':all(current[e]>=quotas[e] for e in targets),
            'all_event_quotas_met':all(current[e]>=quotas[e] for e in quotas),'network_mib_this_run':budget.received/1024**2,
            'network_budget_mib':max_mib,'range_requests_this_run':budget.requests,'rejected_records':len(rejected),
            'locations_this_run':locations,'errors':errors,'heldout_samples_ingested':False,'training_performed':False,
            'limitations':['Event-directed contiguous samples are spatially correlated and not representative full-event coverage',
                           'Search requires ordered fixed-layout archives and stops on incompatible data',
                           'This is the recorded RandomEvents split, not the webdataset folder-defined benchmark split']}
    atomic_json(root/'reports/targeted_training_download.json',result)
    print(json.dumps(result,indent=2),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--run-config',required=True)
    parser.add_argument('--train-per-event',type=int,default=4)
    parser.add_argument('--val-per-event',type=int,default=2)
    parser.add_argument('--max-mib',type=int,default=512)
    parser.add_argument('--max-requests',type=int,default=768)
    parser.add_argument('--events',nargs='+')
    args=parser.parse_args()
    try:
        result=fetch(args.root,args.run_config,args.train_per_event,args.val_per_event,args.max_mib,args.max_requests,args.events)
        return 0 if result['requested_quotas_met'] and not result['errors'] else 1
    except (OSError,ValueError,KeyError) as exc:
        parser.exit(1,'Targeted fetch stopped: '+str(exc)+'\n')


if __name__=='__main__':raise SystemExit(main())
