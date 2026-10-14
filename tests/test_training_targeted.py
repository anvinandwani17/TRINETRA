"""Targeted archive search retains the checkpoint event split and held-out rows."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('targeted_fetch',Path(__file__).parents[1]/'scripts/fetch_training_targeted.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class OrderedArchive:
    def __init__(self,events):self.events=events;self.total=len(events)*1000000
    def exact(self,start):
        assert start%1000000==0
        return {'start':start,'end':start+1000000,'info':{'actid':self.events[start//1000000]}}
    def near(self,position):
        position=max(0,min(position,self.total-1))
        return self.exact(position//1000000*1000000)


@pytest.mark.parametrize('event,expected',[(1,0),(2,None),(3,1000000),(4,None),(5,3000000),(6,None)])
def test_search_returns_first_matching_event_or_absence(event,expected):
    assert module.first_event(OrderedArchive([1,3,3,5]),event)==expected


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'data'
    for name in ['metadata','splits','samples','cache']:(root/name).mkdir(parents=True)
    report={'assignments':{'1':'train','2':'validation','3':'test'},'split_sha256':{'train':'a','validation':'b','test':'c'}}
    monkeypatch.setattr(module,'verify_splits',lambda root:report)
    config=tmp_path/'config.json';config.write_text(json.dumps({'data':{'split_sha256':report['split_sha256']}}))
    (root/'metadata/source_snapshot.json').write_text('{"hf_revision":"fixture-pinned"}')
    for split,event in [('train','1'),('validation','2')]:
        (root/'splits'/f'{split}.jsonl').write_text(json.dumps({'event_id':event,'grid_id':'grid-'+event})+'\n')
    protected={'event_id':'3','grid_id':'untouched','split':'test','sha256':{'fixture':'unchanged'}}
    manifest=root/'samples/local_manifest.json';manifest.write_text(json.dumps({'samples':[protected]}))
    calls=[]
    def listing(url,**kwargs):
        calls.append(url);folder=url.rsplit('/',1)[1]
        return io.BytesIO(json.dumps([{'path':folder+'/shard.tar','size':2000000}]).encode())
    monkeypatch.setattr(module,'urlopen',listing)
    class Budget:
        def __init__(self,*args):self.requests=0;self.received=0
    monkeypatch.setattr(module,'RangeBudget',Budget)
    class Archive(OrderedArchive):
        def __init__(self,shard,*args):
            self.shard=shard;super().__init__([3,3] if shard.startswith('train_') else [1,2])
        def exact(self,start):
            row=super().exact(start);row['info']['grid_id']='grid-'+str(row['info']['actid']);return row
        def get(self,start,size):return b'fixture'
    monkeypatch.setattr(module,'Archive',Archive)
    ingested=[]
    def ingest(root,path,source,limit):
        assert source['shard'].startswith('test_GRD/')
        event='1' if source['byte_offset']==0 else '2'
        rows=json.loads(manifest.read_text());rows['samples'].append({'event_id':event,'grid_id':'grid-'+event,'split':report['assignments'][event]})
        manifest.write_text(json.dumps(rows));ingested.append(event)
    monkeypatch.setattr(module,'ingest_prefix',ingest)
    return root,config,manifest,protected,calls,ingested


def test_archive_folder_does_not_override_recorded_event_split(tmp_path,monkeypatch):
    root,config,manifest,protected,calls,ingested=fixture(tmp_path,monkeypatch)
    result=module.fetch(root,config,1,1,1,32)
    assert result['all_event_quotas_met'] and result['errors']==[]
    assert ingested==['1','2']
    assert len(calls)==2
    assert [r for r in json.loads(manifest.read_text())['samples'] if r['split']=='test']==[protected]
    assert not (root/'samples/.heldout_fetch.lock').exists()


def test_requesting_test_event_stops_before_source_requests(tmp_path,monkeypatch):
    root,config,manifest,protected,calls,ingested=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='declared training/validation'):
        module.fetch(root,config,1,1,1,32,events=['3'])
    assert calls==[] and ingested==[]
    assert json.loads(manifest.read_text())['samples']==[protected]


def test_partition_change_stops_before_source_requests(tmp_path,monkeypatch):
    root,config,_,_,calls,ingested=fixture(tmp_path,monkeypatch)
    config.write_text('{"data":{"split_sha256":{}}}')
    with pytest.raises(ValueError,match='partition differs'):
        module.fetch(root,config,1,1,1,32)
    assert calls==[] and ingested==[]
