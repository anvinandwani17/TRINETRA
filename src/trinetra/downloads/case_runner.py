"""Plan or download exactly a general case's hash-pinned Sentinel-1 pair."""
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import UUID

from trinetra.core.cases import case_paths
from .sentinel1 import DownloadError, atomic_json, download_product, now, resolve_product

GIB = 1024 ** 3


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metadata_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def pinned_discovery(case, data_root):
    paths = case_paths(case, data_root)
    request_path = paths["run"] / "case_request.json"
    pair_path = paths["run"] / "s1_pair.json"
    stage_path = paths["run"] / "discovery_manifest.json"
    if not all(p.is_file() for p in [request_path, pair_path, stage_path]):
        raise DownloadError("Run scripts/discover_case.py for this AOI/date first")
    request = read_json(request_path)
    expected = case.to_dict()
    if any(request.get(k) != expected[k] for k in
           ("schema", "case_id", "request_sha256", "aoi", "flood_date", "date_policy")):
        raise DownloadError("Discovery request differs from the requested AOI/date")
    stage = read_json(stage_path)
    if stage.get("status") != "discovered" or stage.get("request_sha256") != case.fingerprint:
        raise DownloadError("Successful discovery is required before product resolution")
    pair_sha = digest(pair_path)
    if pair_sha != stage.get("pair_sha256"):
        raise DownloadError("Pinned discovery pair hash mismatch")
    pair = read_json(pair_path)
    if (pair.get("event_start_utc") != case.flood_date + "T00:00:00Z"
            or pair.get("date_policy") != "whole_utc_day_excluded"):
        raise DownloadError("Pair event date/policy differs from the case request")
    for role in ["pre", "post"]:
        identity = pair.get(role + "_id", "")
        if not isinstance(identity, str) or not re.fullmatch(r"S1[ABCD]_IW_GRDH_1SDV_[A-Za-z0-9_.-]+", identity):
            raise DownloadError("Pinned pair has an unsupported product identity")
    if pair["pre_id"] == pair["post_id"]:
        raise DownloadError("Before and after must be distinct products")
    return paths, pair, pair_sha


def checked_products(products, pair, max_bytes):
    if not isinstance(products, dict) or set(products) != {"pre", "post"}:
        raise DownloadError("Exactly pre and post product metadata are required")
    clean = {}
    for role in ["pre", "post"]:
        p = products[role]
        identity = pair[role + "_id"]
        name = identity if identity.endswith(".SAFE") else identity + ".SAFE"
        if not isinstance(p, dict) or p.get("Name") != name or p.get("Online") is not True:
            raise DownloadError("Selected product identity/online status is unsuitable: " + role)
        try:
            uuid = str(UUID(p["Id"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise DownloadError("Invalid selected product UUID") from exc
        size = p.get("ContentLength")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise DownloadError("Missing/invalid catalogue size estimate: " + role)
        if size > max_bytes:
            raise DownloadError("Catalogue size exceeds the per-product limit: " + role)
        allowed = ("Id", "Name", "Online", "ContentLength", "S3Path", "Checksum",
                   "ContentDate", "PublicationDate", "ModificationDate", "Footprint", "GeoFootprint")
        clean[role] = {key: p[key] for key in allowed if key in p}
        clean[role]["Id"] = uuid
    if clean["pre"]["Id"] == clean["post"]["Id"]:
        raise DownloadError("Selected names resolve to the same product UUID")
    return clean


def plan_download(case, data_root, *, max_product_gib=3.0, resolver=None):
    if isinstance(max_product_gib, bool) or not isinstance(max_product_gib, (int, float)) or not math.isfinite(max_product_gib) or not 0 < max_product_gib <= 6:
        raise DownloadError("--max-product-gib must be finite, >0 and <=6")
    max_bytes = int(max_product_gib * GIB)
    paths, pair, pair_sha = pinned_discovery(case, data_root)
    path = paths["run"] / "download_plan.json"
    if path.exists():
        prior = read_json(path)
        if prior.get("request_sha256") != case.fingerprint or prior.get("pair_sha256") != pair_sha:
            raise DownloadError("Existing download plan identity differs; refusing overwrite")
        if metadata_digest(prior.get("resolved_metadata")) != prior.get("product_metadata_sha256"):
            raise DownloadError("Cached product metadata hash mismatch")
        products = checked_products(prior.get("resolved_metadata"), pair, max_bytes)
        fetched_at = prior.get("metadata_fetched_at")
        metadata_reused = True
    else:
        resolve = resolver or resolve_product
        products = checked_products({role: resolve(pair[role + "_id"]) for role in ["pre", "post"]}, pair, max_bytes)
        fetched_at, metadata_reused = now(), False
    planned = {"schema": 1, "case": case.case_id, "flood_date": case.flood_date,
               "request_sha256": case.fingerprint, "aoi_fingerprint": case.fingerprint,
               "aoi": case.aoi, "pair": pair, "pair_sha256": pair_sha,
               "status": "planned_only", "resolved_metadata": products,
               "product_metadata_sha256": metadata_digest(products),
               "metadata_fetched_at": fetched_at, "product_metadata_reused": metadata_reused,
               "catalogue_estimated_total_bytes": sum(p["ContentLength"] for p in products.values()),
               "max_product_bytes": max_bytes, "planned_at": now(),
               "raw_root": str(paths["raw"]), "size_policy": "Catalogue sizes are estimates; the transfer also enforces an actual byte limit"}
    atomic_json(path, planned)
    return planned, paths


def download_planned(plan, paths, *, tokens=None, transfer=None,
                     transport="odata", restart_partial=False):
    """Reuse product-level integrity/cache/resume implementation; no preprocessing."""
    if transport not in {"odata", "s3"}:
        raise DownloadError("Choose an official odata or s3 transport")
    if transport == "s3" and transfer is None:
        raise DownloadError("S3 download requires the existing S3 transport")
    report_path = paths["run"] / "download_manifest.json"
    if report_path.exists():
        prior = read_json(report_path)
        if (prior.get("request_sha256") != plan["request_sha256"]
                or prior.get("pair_sha256") != plan["pair_sha256"]
                or prior.get("product_metadata_sha256") != plan["product_metadata_sha256"]):
            raise DownloadError("Existing download manifest belongs to another request/pair")
    lock = paths["run"] / ".case-download.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise DownloadError("This case is already downloading; do not run it concurrently") from exc
    try:
        with handle:
            import os
            handle.write(str(os.getpid()))
        report = {**plan, "status": "running", "started_at": now(),
                  "transport": transport, "products": {}}
        atomic_json(report_path, report)
        try:
            for role in ["pre", "post"]:
                product = plan["resolved_metadata"][role]
                print(role + ": " + product["Name"], flush=True)
                result = download_product(product, paths["raw"], tokens,
                                          max_bytes=plan["max_product_bytes"],
                                          restart_partial=restart_partial, transfer=transfer)
                if (result.get("product_uuid") != product["Id"] or result.get("name") != product["Name"]
                        or result.get("verification", {}).get("status") != "valid"):
                    raise DownloadError("Product verification did not match the selected identity")
                report["products"][role] = result
                atomic_json(report_path, report)
                if transport == "s3" and hasattr(transfer, "cleanup"):
                    transfer.cleanup(product, paths["raw"])
                print(role + ": " + ("verified cache reused" if result["cache_hit"] else "downloaded and verified"), flush=True)
            report.update(status="complete", completed_at=now())
            atomic_json(report_path, report)
            return report
        except (RuntimeError, ValueError, OSError, ImportError) as exc:
            report.update(status="failed", failed_at=now(), error=str(exc))
            atomic_json(report_path, report)
            raise
    finally:
        lock.unlink(missing_ok=True)
