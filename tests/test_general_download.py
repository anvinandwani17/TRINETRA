"""Offline stage tests; stub product metadata/bytes are never real scene outputs."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from trinetra.core.cases import make_case, case_paths
from trinetra.downloads import case_runner as stage
from trinetra.downloads import sentinel1 as storage

AOI = [85.2,26.85,85.25,26.9]
NAMES = {"pre": "S1D_IW_GRDH_1SDV_TEST_PRE", "post": "S1D_IW_GRDH_1SDV_TEST_POST"}
UUIDS = {"pre": "00000000-0000-0000-0000-000000000001", "post": "00000000-0000-0000-0000-000000000002"}


def setup_case(tmp_path, day="2026-09-10"):
    case = make_case(AOI, day)
    paths = case_paths(case, tmp_path)
    run = paths["run"]
    stage.atomic_json(run / "case_request.json", case.to_dict())
    pair = {"pre_id": NAMES["pre"], "post_id": NAMES["post"],
            "event_start_utc": day + "T00:00:00Z", "date_policy": "whole_utc_day_excluded",
            "explicit_test_fixture": True}
    stage.atomic_json(run / "s1_pair.json", pair)
    stage.atomic_json(run / "discovery_manifest.json", {
        "status": "discovered", "request_sha256": case.fingerprint,
        "pair_sha256": stage.digest(run / "s1_pair.json")})
    return case


def product(identity):
    role = next(k for k,v in NAMES.items() if v == identity)
    return {"Id": UUIDS[role], "Name": identity + ".SAFE", "Online": True,
            "ContentLength": 1024, "S3Path": "/eodata/test-fixture/" + role}


def test_requires_completed_discovery(tmp_path):
    with patch.object(stage, "resolve_product", side_effect=AssertionError("no network")):
        with pytest.raises(storage.DownloadError, match="discover_case"):
            stage.plan_download(make_case(AOI, "2026-09-10"), tmp_path)


def test_plan_resolves_only_two_products_and_never_downloads(tmp_path):
    case = setup_case(tmp_path)
    with patch.object(stage, "download_product", side_effect=AssertionError("no download")):
        with patch.object(stage, "resolve_product", side_effect=product) as resolver:
            plan, paths = stage.plan_download(case, tmp_path)
    assert [c.args[0] for c in resolver.call_args_list] == [NAMES["pre"], NAMES["post"]]
    assert plan["status"] == "planned_only"
    assert plan["catalogue_estimated_total_bytes"] == 2048
    assert not paths["raw"].exists()
    assert not paths["processed"].exists()


def test_plan_metadata_reuse_and_hash_guard(tmp_path):
    case = setup_case(tmp_path)
    stage.plan_download(case, tmp_path, resolver=product)
    with patch.object(stage, "resolve_product", side_effect=AssertionError("no network")):
        plan, paths = stage.plan_download(case, tmp_path)
    assert plan["product_metadata_reused"] is True
    path = paths["run"] / "download_plan.json"
    plan["resolved_metadata"]["pre"]["Id"] = UUIDS["post"]
    stage.atomic_json(path, plan)
    with pytest.raises(storage.DownloadError, match="metadata hash"):
        stage.plan_download(case, tmp_path)


@pytest.mark.parametrize("limit", [0, -1, 7, float("nan"), float("inf"), True])
def test_invalid_limits_rejected_before_network(tmp_path, limit):
    with pytest.raises(storage.DownloadError, match="max-product"):
        stage.plan_download(make_case(AOI, "2026-09-10"), tmp_path, max_product_gib=limit)


@pytest.mark.parametrize("field,value", [("Online", False), ("Name", "wrong.SAFE"),
                                         ("ContentLength", None), ("ContentLength", True),
                                         ("ContentLength", 4 * 1024 ** 3)])
def test_unsuitable_product_rejected(tmp_path, field, value):
    case = setup_case(tmp_path)
    def unsuitable(identity):
        p = product(identity)
        p[field] = value
        return p
    with pytest.raises(storage.DownloadError):
        stage.plan_download(case, tmp_path, resolver=unsuitable)
    assert not case_paths(case, tmp_path)["raw"].exists()


def test_pair_corruption_prevents_product_lookup(tmp_path):
    case = setup_case(tmp_path)
    (case_paths(case, tmp_path)["run"] / "s1_pair.json").write_text("{}")
    with patch.object(stage, "resolve_product", side_effect=AssertionError("no network")):
        with pytest.raises(storage.DownloadError, match="pair hash"):
            stage.plan_download(case, tmp_path)


def test_two_cases_share_actual_product_cache_without_retransfers(tmp_path):
    first = setup_case(tmp_path)
    second = setup_case(tmp_path, "2026-09-11")
    calls = []
    def transfer(p, partial, state_path, tokens, max_bytes, restart=False):
        calls.append(p["Id"])
        partial.write_bytes(b"explicit offline test bytes")
    def validate(path, expected_name):
        return {"status": "valid", "bytes": Path(path).stat().st_size,
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    with patch.object(storage, "validate_safe", side_effect=validate):
        plan, paths = stage.plan_download(first, tmp_path, resolver=product)
        a = stage.download_planned(plan, paths, transfer=transfer)
        plan, paths = stage.plan_download(second, tmp_path, resolver=product)
        b = stage.download_planned(plan, paths, transfer=transfer)
    assert len(calls) == 2
    assert a["status"] == b["status"] == "complete"
    assert all(p["cache_hit"] for p in b["products"].values())
    assert a["case"] != b["case"]
    assert not paths["processed"].exists()


def test_interrupted_post_records_pre_and_can_resume(tmp_path):
    case = setup_case(tmp_path)
    plan, paths = stage.plan_download(case, tmp_path, resolver=product)
    def result(p):
        return {"product_uuid": p["Id"], "name": p["Name"], "cache_hit": True,
                "verification": {"status": "valid"}}
    def interrupted(p, *args, **kwargs):
        if p["Id"] == UUIDS["post"]:
            raise storage.DownloadError("offline test interruption")
        return result(p)
    with patch.object(stage, "download_product", side_effect=interrupted):
        with pytest.raises(storage.DownloadError, match="interruption"):
            stage.download_planned(plan, paths)
    report = stage.read_json(paths["run"] / "download_manifest.json")
    assert report["status"] == "failed"
    assert set(report["products"]) == {"pre"}
    assert not (paths["run"] / ".case-download.lock").exists()
    with patch.object(stage, "download_product", side_effect=lambda p,*a,**kw: result(p)):
        resumed = stage.download_planned(plan, paths)
    assert resumed["status"] == "complete"


def test_concurrent_case_lock_is_preserved(tmp_path):
    case = setup_case(tmp_path)
    plan, paths = stage.plan_download(case, tmp_path, resolver=product)
    lock = paths["run"] / ".case-download.lock"
    lock.write_text("other process")
    with patch.object(stage, "download_product", side_effect=AssertionError("no download")):
        with pytest.raises(storage.DownloadError, match="concurrently"):
            stage.download_planned(plan, paths)
    assert lock.read_text() == "other process"
