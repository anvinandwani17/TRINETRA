import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trinetra.core.cases import make_case, case_paths
from trinetra.discovery import NoSuitablePair

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("general_discovery_script", ROOT / "scripts/discover_case.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
AOI = {"type": "Polygon", "coordinates": [[[85.2,26.85],[85.25,26.85],[85.25,26.9],[85.2,26.9],[85.2,26.85]]]}


def test_stable_identity_ignores_feature_properties_ring_start_and_direction():
    first = make_case(AOI, "2026-09-10")
    points = list(reversed(AOI["coordinates"][0][:-1]))
    points = points[2:] + points[:2]
    equivalent = {"type": "Feature", "properties": {"name": "different"},
                  "geometry": {"type": "Polygon", "coordinates": [points + [points[0]]]}}
    assert make_case(equivalent, "2026-09-10").fingerprint == first.fingerprint


def test_distinct_dates_have_distinct_paths(tmp_path):
    a = make_case(AOI, "2026-09-10")
    b = make_case(AOI, "2026-09-11")
    assert case_paths(a, tmp_path)["run"] != case_paths(b, tmp_path)["run"]
    assert case_paths(a, tmp_path)["run"].name != "trishuli"


@pytest.mark.parametrize("day", ["20260910", "2026-02-30", "2026-09-10T00:00:00Z", "2999-01-01"])
def test_invalid_or_future_day(day):
    with pytest.raises(ValueError):
        make_case(AOI, day)


def test_size_cap():
    with pytest.raises(ValueError, match="300 km2"):
        make_case([85,27,85.3,27.3], "2026-09-10")


def test_prepare_only_makes_no_catalogue_or_raw_calls(tmp_path):
    with patch.object(runner, "select_s1_pair", side_effect=AssertionError("network forbidden")):
        result = runner.discover(make_case(AOI, "2026-09-10"), tmp_path, prepare_only=True)
    assert result["pair"] is None
    assert (result["paths"]["run"] / "case_request.json").is_file()
    assert not result["paths"]["raw"].exists()
    assert not result["paths"]["processed"].exists()


def test_failed_search_is_not_success(tmp_path):
    case = make_case(AOI, "2026-09-10")
    with patch.object(runner, "select_s1_pair", side_effect=NoSuitablePair("no pair")):
        with pytest.raises(NoSuitablePair):
            runner.discover(case, tmp_path)
    stage = runner.read_json(case_paths(case, tmp_path)["run"] / "discovery_manifest.json")
    assert stage["status"] == "failed"
    assert not (case_paths(case, tmp_path)["run"] / "s1_pair.json").exists()


def test_pinned_pair_reuse_and_corruption_guard(tmp_path):
    # Explicit unit-test stub, never installed as satellite output in the product.
    class Stub:
        def to_dict(self):
            return {"test_fixture_only": True}
    case = make_case(AOI, "2026-09-10")
    with patch.object(runner, "select_s1_pair", return_value=Stub()) as selector:
        runner.discover(case, tmp_path)
        runner.discover(case, tmp_path)
        assert selector.call_count == 1
    pair_path = case_paths(case, tmp_path)["run"] / "s1_pair.json"
    pair_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        runner.discover(case, tmp_path)


def test_identity_mismatch_preserves_existing_file(tmp_path):
    case = make_case(AOI, "2026-09-10")
    runner.discover(case, tmp_path, prepare_only=True)
    path = case_paths(case, tmp_path)["run"] / "case_request.json"
    altered = case.to_dict()
    altered["flood_date"] = "2026-09-11"
    path.write_text(json.dumps(altered), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="identity differs"):
        runner.discover(case, tmp_path, prepare_only=True)
    assert path.read_bytes() == before
