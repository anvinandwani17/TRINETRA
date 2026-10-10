"""Trishuli-only discovery + two authenticated raw downloads; no preprocessing."""

import argparse
import getpass
import hashlib
import json
from pathlib import Path
import sys

from trinetra.discovery import select_s1_pair
from trinetra.downloads.sentinel1 import DownloadError, TokenProvider, atomic_json, download_product, now, resolve_product


def credentials():
    from trinetra.core.config import Settings
    settings = Settings()
    return settings.cdse_username.get_secret_value(), settings.cdse_password.get_secret_value()


def main():
    parser = argparse.ArgumentParser(description="Trishuli: discover and store exactly two raw Sentinel-1 scenes")
    parser.add_argument("--aoi", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--transport", choices=("odata", "s3"), default="odata", help="Official CDSE download route; s3 bypasses the archive service")
    parser.add_argument("--two-factor", action="store_true", help="Prompt privately for a current CDSE one-time code when authenticating")
    parser.add_argument("--restart-partial", action="store_true", help="Explicitly restart incomplete files if safe range resume is unavailable")
    parser.add_argument("--max-product-gib", type=float, default=3.0, help="Maximum archive size per scene (default 3 GiB)")
    args = parser.parse_args()
    report_path = Path("data/runs/trishuli/download_manifest.json")
    manifest_writable = not report_path.exists()
    report = {"case": "trishuli", "flood_date": args.date, "started_at": now(), "status": "running", "products": {}}
    try:
        if args.date != "2026-08-26":
            raise DownloadError("This download runner supports only the Trishuli test date 2026-08-26")
        if not 0 < args.max_product_gib <= 6:
            raise DownloadError("--max-product-gib must be >0 and <=6")
        aoi = json.loads(args.aoi.read_text(encoding="utf-8-sig"))
        # Only a local Trishuli-area AOI is allowed in this first download stage.
        from shapely.geometry import shape
        geometry = aoi.get("geometry") if aoi.get("type") == "Feature" else aoi
        west, south, east, north = shape(geometry).bounds
        if not (84.5 <= west < east <= 85.6 and 27.6 <= south < north <= 28.8):
            raise DownloadError("This runner accepts only local Trishuli-area AOIs")
        fingerprint = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()
        prior = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        if prior.get("aoi_fingerprint") and prior["aoi_fingerprint"] != fingerprint:
            raise DownloadError("Existing Trishuli run uses a different AOI; preserve/rename its run folder before starting a new case")
        if prior.get("aoi_fingerprint") and prior.get("flood_date") != args.date:
            raise DownloadError("Existing run records a different event date; it will not be overwritten")
        manifest_writable = True
        if prior.get("aoi_fingerprint") == fingerprint and prior.get("pair"):
            pair = prior["pair"]
            print("Reusing the pinned pair for this AOI/date.", flush=True)
        else:
            pair = select_s1_pair(aoi, args.date).to_dict()
        report.update({"aoi_fingerprint": fingerprint, "aoi": geometry, "pair": pair})
        atomic_json("data/runs/trishuli/s1_pair.json", pair)
        # Resolve only these two names; no new scene search or global data.
        products = prior.get("resolved_metadata") if prior.get("aoi_fingerprint") == fingerprint else None
        if not products:
            products = {"pre": resolve_product(pair["pre_id"]), "post": resolve_product(pair["post_id"])}
        if products["pre"]["Id"] == products["post"]["Id"]:
            raise DownloadError("Before and after unexpectedly resolved to the same product")
        report["resolved_metadata"] = products
        atomic_json(report_path, report)
        transfer = None
        tokens = None
        if args.transport == "s3":
            from trinetra.downloads.s3 import S3Transfer
            transfer = S3Transfer()
        else:
            tokens = TokenProvider(credentials, (lambda: getpass.getpass("Current CDSE 2FA code: ")) if args.two_factor else None)
        report["transport"] = args.transport
        # Raw downloads are immutable inputs; processed/ is never written here.
        for role, product in products.items():
            print(f"{role}: {product['Name']} (catalog size estimate: {product.get('ContentLength', 'unknown')} bytes)", flush=True)
            result = download_product(product, "data/raw/sentinel1", tokens,
                                      max_bytes=int(args.max_product_gib * 1024 ** 3), restart_partial=args.restart_partial,
                                      transfer=transfer)
            report["products"][role] = result
            atomic_json(report_path, report)
            if transfer is not None:
                transfer.cleanup(product, "data/raw/sentinel1")
            print(f"{role}: {'cache reused' if result['cache_hit'] else 'downloaded'}; SAFE and raster validation passed", flush=True)
        report.update({"status": "complete", "completed_at": now()})
        atomic_json(report_path, report)
        print(f"Two raw scenes verified. Manifest: {report_path}")
        return 0
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        report.update({"status": "failed", "failed_at": now(), "error": str(exc)})
        if manifest_writable:
            atomic_json(report_path, report)
        print(f"Case failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
