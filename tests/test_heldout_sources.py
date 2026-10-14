"""Test coverage must search the released test shards with event-leakage guards."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('heldout_sources', Path(__file__).parents[1]/'scripts/fetch_heldout_subset.py')
heldout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(heldout)


def test_test_searches_pinned_test_and_train_folders(monkeypatch):
    calls = []
    def listing(url, **kwargs):
        calls.append(url)
        folder = url.rsplit('/', 1)[1]
        return io.BytesIO(json.dumps([{'path':folder+'/shard-000000.tar','size':3000000},
                                     {'path':'unlabelled/'+folder+'/ignored.tar','size':3000000}]).encode())
    monkeypatch.setattr(heldout, 'urlopen', listing)
    shards = heldout.list_labeled_shards('pinned-fixture-revision', 'test')
    assert [s[0] for s in shards] == ['test_GRD/shard-000000.tar', 'train_GRD/shard-000000.tar']
    assert all('/pinned-fixture-revision/' in url for url in calls)


def test_validation_does_not_list_test_folder(monkeypatch):
    calls = []
    def listing(url, **kwargs):
        calls.append(url)
        return io.BytesIO(b'[{"path":"train_GRD/shard-000000.tar","size":3000000}]')
    monkeypatch.setattr(heldout, 'urlopen', listing)
    heldout.list_labeled_shards('fixture', 'validation')
    assert len(calls) == 1 and calls[0].endswith('/train_GRD')


def test_missing_test_folder_is_not_silently_ignored(monkeypatch):
    monkeypatch.setattr(heldout, 'urlopen', lambda *a, **k: io.BytesIO(b'[]'))
    with pytest.raises(ValueError, match='test_GRD'):
        heldout.list_labeled_shards('fixture', 'test')


@pytest.mark.parametrize('split', ['train', 'unlabelled'])
def test_disallowed_splits_do_not_request_anything(split, monkeypatch):
    monkeypatch.setattr(heldout, 'urlopen', lambda *a, **k: pytest.fail('No request allowed'))
    with pytest.raises(ValueError, match='held-out'):
        heldout.list_labeled_shards('fixture', split)


@pytest.mark.parametrize('train,selection,match', [(['test-event'], [], 'training'),
                                                ([], ['test-event'], 'selection')])
def test_event_leakage_guards_remain_enabled(train, selection, match):
    hashes = {'train':'a','validation':'b','test':'c'}
    config = {'config':{'mode':'pilot'},'threshold':.5,
              'data':{'test_used':False,'excluded':['Trishuli','EMSR927'],
                      'split_sha256':hashes,'train_events':train,'validation_events':selection}}
    report = {'split_sha256':hashes,'assignments':{'test-event':'test'}}
    with pytest.raises(ValueError, match=match):
        heldout.allowed_events(config,report,'test')
