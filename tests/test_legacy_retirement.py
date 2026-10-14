"""Retired runners fail before reading caches, fetching data or writing outputs."""
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_retired_cli_never_creates_outputs(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/run_case.py'), '--aoi', 'missing.json',
         '--date', '2026-08-26', '--output-dir', 'results'],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert 'retired' in result.stderr
    assert 'run_integrated_case.py' in result.stderr
    assert not list(tmp_path.iterdir())


def test_retired_job_api_cannot_reuse_unverified_cache(tmp_path, monkeypatch):
    cache = tmp_path / 'data/cache'
    cache.mkdir(parents=True)
    old = cache / 'prototype.json'
    old.write_text('{"summary":{"damaged_road_segments":99}}')
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location('retired_jobs', ROOT / 'api/jobs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for fn in [module.create_job, module.get_job, module.compute_cache_key]:
        with pytest.raises(RuntimeError, match='retired'):
            fn('prototype')
    with pytest.raises(RuntimeError, match='retired'):
        asyncio.run(module.run_job('prototype'))
    assert old.read_text() == '{"summary":{"damaged_road_segments":99}}'
    assert list(cache.iterdir()) == [old]
