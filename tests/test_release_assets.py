"""Asset transfer rejects corruption/conflicts before changing case files."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('release_assets', Path(__file__).parents[1]/'scripts/release_assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def bundle(tmp_path, path='data/processed/trishuli/example.bin'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z: z.writestr(path, b'fixture bytes')
    data = stream.getvalue()
    manifest = {'schema_version': 1, 'bundle_size_bytes': len(data),
                'bundle_sha256': hashlib.sha256(data).hexdigest(),
                'files': [{'path': path, 'size_bytes': 13,
                           'sha256': hashlib.sha256(b'fixture bytes').hexdigest()}]}
    file = tmp_path/'manifest.json'; file.write_text(json.dumps(manifest))
    repo = tmp_path/'repo'; repo.mkdir(); (repo/'pyproject.toml').write_text('# fixture')
    return file, data, manifest, repo


def test_verified_asset_fetch_and_same_hash_reuse(tmp_path, monkeypatch):
    manifest, data, _, repo = bundle(tmp_path)
    monkeypatch.setattr(assets.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(data))
    assets.fetch(manifest, 'https://release.example/bundle.zip', repo)
    target = repo/'data/processed/trishuli/example.bin'
    assert target.read_bytes() == b'fixture bytes'
    before = target.stat().st_mtime_ns
    assets.fetch(manifest, 'https://release.example/bundle.zip', repo)
    assert target.stat().st_mtime_ns == before


def test_local_bundle_uses_same_hash_checks_without_network(tmp_path, monkeypatch):
    manifest, data, _, repo = bundle(tmp_path)
    local = tmp_path/'assets.zip'; local.write_bytes(data)
    monkeypatch.setattr(assets.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Local restore must not request network'))
    assets.fetch(manifest, None, repo, bundle_file=local)
    target = repo/'data/processed/trishuli/example.bin'
    before = target.stat().st_mtime_ns
    assets.fetch(manifest, None, repo, bundle_file=local)
    assert target.read_bytes() == b'fixture bytes' and target.stat().st_mtime_ns == before


def test_corrupt_local_bundle_changes_nothing(tmp_path):
    manifest, data, _, repo = bundle(tmp_path)
    local = tmp_path/'assets.zip'; local.write_bytes(b'x'*len(data))
    with pytest.raises(ValueError, match='hash mismatch'):
        assets.fetch(manifest, None, repo, bundle_file=local)
    assert not (repo/'data').exists()


def test_conflicting_local_asset_is_preserved(tmp_path):
    manifest, data, _, repo = bundle(tmp_path)
    local = tmp_path/'assets.zip'; local.write_bytes(data)
    target = repo/'data/processed/trishuli/example.bin'
    target.parent.mkdir(parents=True); target.write_bytes(b'user changes')
    with pytest.raises(ValueError, match='Existing asset differs'):
        assets.fetch(manifest, None, repo, bundle_file=local)
    assert target.read_bytes() == b'user changes'


def test_corrupt_bundle_does_not_install_assets(tmp_path, monkeypatch):
    manifest, data, _, repo = bundle(tmp_path)
    monkeypatch.setattr(assets.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'x'*len(data)))
    with pytest.raises(ValueError, match='hash mismatch'): assets.fetch(manifest, 'https://release.example/bundle.zip', repo)
    assert not (repo/'data').exists()


def test_conflicting_asset_is_preserved_without_network(tmp_path, monkeypatch):
    manifest, _, _, repo = bundle(tmp_path)
    target = repo/'data/processed/trishuli/example.bin'
    target.parent.mkdir(parents=True);target.write_bytes(b'user changes')
    monkeypatch.setattr(assets.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Conflict must stop before download'))
    with pytest.raises(ValueError, match='Existing asset differs'): assets.fetch(manifest, 'https://release.example/bundle.zip', repo)
    assert target.read_bytes() == b'user changes'


@pytest.mark.parametrize('path', ['data/../../escape', 'data/processed/../../../../escape', 'C:/escape', 'data\\escape'])
def test_manifest_path_escape_rejected(tmp_path, path):
    manifest, _, _, repo = bundle(tmp_path, path)
    with pytest.raises(ValueError, match='Unsafe asset path'): assets.fetch(manifest, 'https://release.example/bundle.zip', repo)
