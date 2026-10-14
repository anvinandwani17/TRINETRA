"""Legacy product paths cannot fabricate infrastructure, probabilities or landing sites."""
import importlib
import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize('module,name', [
    ('network.ohsome', 'fetch_all_infrastructure'),
    ('network.ohsome', 'fetch_osm_snapshot'),
    ('network.monte_carlo', 'assign_edge_failure_probabilities'),
    ('network.monte_carlo', 'run_monte_carlo_simulation'),
    ('network.landing_zones', 'extract_viable_landing_zones'),
    ('network.landing_zones', 'match_settlements_to_landing_zones'),
    ('reports.generator', 'generate_situation_report'),
])
def test_retired_paths_fail_without_network_or_output(module, name, tmp_path, monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError('Retired prototypes must not call external services')
    monkeypatch.setattr(requests, 'get', forbidden)
    monkeypatch.setattr(requests, 'post', forbidden)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):
        getattr(importlib.import_module(module), name)({})
    assert not list(tmp_path.iterdir())


def test_clean_install_check_uses_integrated_health(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('clean_check', root/'scripts/check_clean_install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify()
    assert result['status'] == 'passed'
    assert result['api'] == 'api.main:app'
    assert result['health'] == '/health'
    assert result['ai_import_checked'] is False
