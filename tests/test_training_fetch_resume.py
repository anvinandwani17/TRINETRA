"""Training fetch rejects invalid labels, preserves test rows and resumes its cursor."""
import io
import json
import tarfile

import pytest

from trinetra.datasets import kuro_training_fetch as fetcher


def fixture(tmp_path, monkeypatch, error='Invalid label is marked valid'):
    root=tmp_path/'data'
    for folder in ['metadata','samples','splits','cache']:
        (root/folder).mkdir(parents=True,exist_ok=True)
    (root/'metadata/source_snapshot.json').write_text('{"hf_revision":"fixture"}')
    protected={'event_id':'3','grid_id':'test-fixture','split':'test'}
    manifest=root/'samples/local_manifest.json'
    manifest.write_text(json.dumps({'samples':[protected]}))
    for split,event in [('train','1'),('validation','2')]:
        (root/'splits'/f'{split}.jsonl').write_text(json.dumps({'event_id':event,'grid_id':'grid-'+event})+'\n')
    report={'assignments':{'1':'train','2':'validation','3':'test'},'split_sha256':{'train':'a','validation':'b','test':'c'}}
    monkeypatch.setattr(fetcher,'verify_splits',lambda root:report)
    monkeypatch.setattr(fetcher,'urlopen',lambda *a,**k:io.BytesIO(b'[{"path":"train_GRD/shard.tar","size":10000000}]'))
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as z:
        for field in fetcher.BEFORE_INFO+['info.json','mask.npy','valid_mask.npy']:
            data=b'{}' if field=='info.json' else bytes(200832)
            info=tarfile.TarInfo('000000.'+field);info.size=len(data);z.addfile(info,io.BytesIO(data))
    raw=stream.getvalue()
    class Budget:
        def __init__(self,cache,maximum):self.received=0;self.max_bytes=maximum
        def get(self,url,start,count):
            if count==8704:return str(start-fetcher.INFO_OFFSET).encode()
            return raw if count==len(raw) else b'window'
    monkeypatch.setattr(fetcher,'RangeBudget',Budget)
    monkeypatch.setattr(fetcher,'candidates',lambda *a:[('000000',0),('000000',3000000),('000000',6000000)])
    def extent(key,start,data):
        event='2' if start==6000000 else '1'
        grid='bad-grid' if start==0 else 'grid-'+event
        return {'actid':event,'grid_id':grid},len(raw)
    # Bad record must exist in the official training index before validation rejects it.
    with (root/'splits/train.jsonl').open('a') as file:
        file.write(json.dumps({'event_id':'1','grid_id':'bad-grid'})+'\n')
    monkeypatch.setattr(fetcher,'sample_extent',extent)
    calls=[]
    def ingest(root,path,source,limit):
        calls.append(source['byte_offset'])
        if source['byte_offset']==0:raise ValueError(error)
        event='2' if source['byte_offset']==6000000 else '1'
        local=json.loads(manifest.read_text())
        local['samples'].append({'event_id':event,'grid_id':'grid-'+event,'split':report['assignments'][event]})
        manifest.write_text(json.dumps(local))
    monkeypatch.setattr(fetcher,'ingest_prefix',ingest)
    return root,manifest,protected,calls


def test_known_invalid_sample_is_rejected_once_and_cursor_resumes(tmp_path,monkeypatch):
    root,manifest,protected,calls=fixture(tmp_path,monkeypatch)
    for expected_probe in [1,2]:
        with pytest.raises(ValueError,match='Bounded fetch incomplete'):
            fetcher.fetch_training(root,train_quota=2,val_quota=1,max_mib=1,probes=1)
        result=json.loads((root/'reports/training_download.json').read_text())
        assert result['next_probe']==expected_probe
        assert result['errors']==[] and result['rejected_records']==1
        assert [r for r in json.loads(manifest.read_text())['samples'] if r['split']=='test']==[protected]
        assert not (root/'samples/.heldout_fetch.lock').exists()
        assert not list((root/'cache').glob('*.tar.part'))
    assert calls.count(0)==1
    state=json.loads((root/'reports/training_fetch_cursor_v2.json').read_text())
    assert state['rejected'][0]['reason']=='Invalid label is marked valid'


def test_other_validation_errors_still_stop_the_fetch(tmp_path,monkeypatch):
    root,manifest,protected,calls=fixture(tmp_path,monkeypatch,error='Sample field/checksum changed')
    with pytest.raises(ValueError,match='Bounded fetch incomplete'):
        fetcher.fetch_training(root,1,1,1,1)
    result=json.loads((root/'reports/training_download.json').read_text())
    assert result['errors']==['Sample field/checksum changed']
    assert result['rejected_records']==0
    assert json.loads(manifest.read_text())['samples']==[protected]
    assert not (root/'samples/.heldout_fetch.lock').exists()


def test_existing_dataset_fetch_lock_is_preserved(tmp_path,monkeypatch):
    root,_,_,calls=fixture(tmp_path,monkeypatch)
    lock=root/'samples/.heldout_fetch.lock';lock.write_text('another process')
    with pytest.raises(FileExistsError):fetcher.fetch_training(root,1,1,1,1)
    assert lock.read_text()=='another process' and calls==[]
