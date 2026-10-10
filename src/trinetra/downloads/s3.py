"""Bounded, resumable downloads of selected CDSE SAFE objects; no global listing."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import time
import zipfile

from .sentinel1 import DownloadError, _retry_wait, _sha256, atomic_json, now

ENDPOINT = "https://eodata.dataspace.copernicus.eu"


def _error_code(exc):
    """Streaming errors may have response=None rather than a ClientError dict."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) and code else None


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, OSError):
        raise DownloadError("S3 transfer metadata is unreadable; preserve the files for inspection") from None


def _location(product):
    path = product.get("S3Path", "")
    if not isinstance(path, str) or len(path) > 2048:
        raise DownloadError("Missing or invalid catalog S3Path")
    parts = path.lstrip("/").split("/", 1)
    if len(parts) != 2 or parts[0] != "eodata":
        raise DownloadError("Only the official eodata bucket is permitted")
    prefix = parts[1].rstrip("/")
    if not prefix.startswith("Sentinel-1/SAR/") or prefix.split("/")[-1] != product["Name"]:
        raise DownloadError("Catalog S3Path does not identify this selected SAFE product")
    _safe_member(prefix)
    return parts[0], prefix + "/"


def _safe_member(name):
    parts = PurePosixPath(name).parts
    if (not name or name.startswith("/") or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name)
            or any(p in {"", ".", ".."} or p.endswith((" ", ".")) for p in name.split("/"))
            or not parts):
        raise DownloadError("Unsafe S3 object path")


class S3Transfer:
    """Callable transport for download_product; uses its lock and final validation."""

    def __init__(self, client=None):
        self.client = client

    def _client(self):
        if self.client is None:
            try:
                import boto3
                from botocore.config import Config
                from trinetra.core.config import Settings
                settings = Settings()
                access = settings.cdse_s3_access_key.get_secret_value()
                secret = settings.cdse_s3_secret_key.get_secret_value()
                if not access or not secret:
                    raise DownloadError("Set CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY in your local .env")
                self.client = boto3.client(
                    "s3", endpoint_url=ENDPOINT, region_name="default",
                    aws_access_key_id=access, aws_secret_access_key=secret,
                    config=Config(signature_version="s3v4", connect_timeout=30,
                                  read_timeout=60, retries={"max_attempts": 2, "mode": "standard"},
                                  s3={"addressing_style": "path"}),
                )
            except ImportError:
                raise DownloadError('Install S3 support: python -m pip install -e ".[s3,download]"') from None
        return self.client

    def _objects(self, bucket, prefix, max_bytes):
        objects, token, seen = [], None, set()
        total = 0
        for _ in range(5):
            args = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 100}
            if token:
                args["ContinuationToken"] = token
            try:
                page = self._client().list_objects_v2(**args)
            except Exception as exc:
                code = _error_code(exc) or type(exc).__name__
                raise DownloadError(f"CDSE S3 object listing failed ({code}); credentials are not logged") from None
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise DownloadError("S3 listing escaped the selected product prefix")
                if key.endswith("/") and obj.get("Size") == 0:
                    continue
                relative = key[len(prefix):]
                _safe_member(relative)
                if key in seen:
                    raise DownloadError("Duplicate S3 object in listing")
                seen.add(key)
                size, etag = obj.get("Size"), obj.get("ETag")
                if not isinstance(size, int) or isinstance(size, bool) or size < 0 or not isinstance(etag, str) or not etag:
                    raise DownloadError("S3 object lacks a size or stable ETag")
                total += size
                if total > max_bytes:
                    raise DownloadError("Selected S3 product exceeds the per-product size limit")
                objects.append({"key": key, "relative": relative, "size": size, "etag": etag})
                if len(objects) > 500:
                    raise DownloadError("Selected product exceeded the 500-object listing limit")
            if not page.get("IsTruncated"):
                break
            next_token = page.get("NextContinuationToken")
            if not next_token or next_token == token:
                raise DownloadError("Invalid S3 pagination token")
            token = next_token
        else:
            raise DownloadError("Selected product exceeded the 500-object listing limit")
        if not objects or total <= 0:
            raise DownloadError("No data objects found for the selected product")
        return sorted(objects, key=lambda o: o["key"]), total

    def _object(self, bucket, obj, stage, restart=False):
        stem = hashlib.sha256(obj["key"].encode()).hexdigest()
        final = stage / (stem + ".bin")
        partial = stage / (stem + ".part")
        marker = stage / (stem + ".json")
        state = _read_json(marker)
        identity = {"bucket": bucket, "key": obj["key"], "size": obj["size"], "etag": obj["etag"]}
        if final.exists():
            if (any(state.get(k) != v for k, v in identity.items()) or
                    final.stat().st_size != obj["size"] or state.get("sha256") != _sha256(final)):
                raise DownloadError("Cached S3 object changed or failed its local hash; refusing silent replacement")
            return final, True
        if restart:
            partial.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            state = {}
        if partial.exists() and any(state.get(k) != v for k, v in identity.items()):
            raise DownloadError("S3 partial identity changed; preserve it or use --restart-partial explicitly")
        atomic_json(marker, identity)
        for attempt in range(5):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > obj["size"]:
                raise DownloadError("S3 partial exceeds the object size")
            # All expected bytes already persisted before interruption: no GET needed.
            if partial.exists() and offset == obj["size"]:
                break
            args = {"Bucket": bucket, "Key": obj["key"], "IfMatch": obj["etag"]}
            if offset:
                args["Range"] = f"bytes={offset}-"
            body = None
            try:
                response = self._client().get_object(**args)
                body = response["Body"]
                status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status != (206 if offset else 200) or response.get("ETag") != obj["etag"]:
                    raise DownloadError("S3 object changed or range was ignored; refusing unsafe append")
                if response.get("ContentLength") != obj["size"] - offset:
                    raise DownloadError("S3 response length mismatch")
                if offset and response.get("ContentRange") != f"bytes {offset}-{obj['size']-1}/{obj['size']}":
                    raise DownloadError("S3 range offsets do not match the partial file")
                written = offset
                last_progress = time.monotonic()
                with partial.open("ab" if offset else "wb") as file:
                    while True:
                        block = body.read(1024 * 1024)
                        if not block:
                            break
                        if written + len(block) > obj["size"]:
                            raise DownloadError("S3 object exceeded its advertised size")
                        file.write(block)
                        written += len(block)
                        if time.monotonic() - last_progress >= 8:
                            print(f"S3 object transfer: {written / 1024**2:.0f} MiB received", flush=True)
                            last_progress = time.monotonic()
                if written != obj["size"]:
                    raise ConnectionError("Incomplete S3 object")
                break
            except DownloadError:
                raise
            except (ConnectionError, TimeoutError):
                if attempt == 4:
                    raise DownloadError("S3 connection interrupted after five attempts; rerun to resume") from None
                _retry_wait(attempt, "CDSE S3 connection interrupted")
            except OSError:
                raise DownloadError("S3 transfer encountered a local disk error; partial bytes retained") from None
            except Exception as exc:
                code = _error_code(exc)
                if code and code not in {"500", "502", "503", "504", "SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout"}:
                    raise DownloadError(f"S3 object request failed ({code}); partial retained") from None
                if attempt == 4:
                    raise DownloadError("S3 object transfer interrupted after five attempts; rerun to resume") from None
                _retry_wait(attempt, "CDSE S3 transfer interrupted")
            finally:
                if body is not None:
                    body.close()
        digest = _sha256(partial)
        # Marker precedes rename so a crash between them remains recoverable.
        atomic_json(marker, {**identity, "sha256": digest, "completed_at": now()})
        partial.replace(final)
        return final, False

    def __call__(self, product, partial, state_path, tokens, max_bytes, restart=False):
        bucket, prefix = _location(product)
        source = ENDPOINT + "/" + bucket + "/" + prefix
        state = _read_json(state_path)
        if partial.exists():
            if not restart:
                if (state.get("source") == source and state.get("product_uuid") == product["Id"]
                        and state.get("stream_complete") and state.get("bytes") == partial.stat().st_size
                        and state.get("sha256") == _sha256(partial)):
                    return state
                raise DownloadError("Existing archive partial is from another/incomplete packaging run; use --restart-partial. Cached S3 objects are retained.")
            partial.unlink()
        stage = partial.parent / "s3_objects"
        stage.mkdir(exist_ok=True)
        objects, total = self._objects(bucket, prefix, max_bytes)
        # ZIP_STORED prevents costly recompression; bound its headers/names too.
        overhead = sum(256 + 2 * len((product["Name"] + "/" + obj["relative"]).encode()) for obj in objects) + 1024
        if total + overhead > max_bytes:
            raise DownloadError("S3 product plus ZIP headers exceeds the configured archive bound")
        cached = sum(p.stat().st_size for p in stage.glob("*.bin")) + sum(p.stat().st_size for p in stage.glob("*.part"))
        needed = max(0, total - cached) + total + overhead + 256 * 1024**2
        if shutil.disk_usage(stage).free < needed:
            raise DownloadError("Insufficient disk space for S3 objects plus temporary ZIP (about twice the scene size)")
        print(f"S3: {len(objects)} objects, {total / 1024**2:.0f} MiB; selected product only", flush=True)
        paths = []
        for index, obj in enumerate(objects, 1):
            path, hit = self._object(bucket, obj, stage, restart)
            paths.append(path)
            print(f"S3 object {index}/{len(objects)}: {'cache reused' if hit else 'received'} ({obj['size']} bytes)", flush=True)
        # Re-list metadata to reject products modified while downloading.
        current, _ = self._objects(bucket, prefix, max_bytes)
        if current != objects:
            raise DownloadError("S3 product object list changed during transfer; no successful archive claimed")
        state = {"source": source, "transport": "s3", "product_uuid": product["Id"],
                 "stream_complete": False, "objects": objects}
        atomic_json(state_path, state)
        print("Packaging raw SAFE objects without modifying imagery...", flush=True)
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for obj, path in zip(objects, paths):
                archive.write(path, product["Name"] + "/" + obj["relative"])
        if partial.stat().st_size > max_bytes:
            raise DownloadError("Packaged S3 archive exceeds the configured size bound")
        state.update({"stream_complete": True, "bytes": partial.stat().st_size,
                      "sha256": _sha256(partial), "completed_at": now()})
        atomic_json(state_path, state)
        return state

    @staticmethod
    def cleanup(product, raw_root):
        """Called only after final archive verification; incomplete stages are retained."""
        from uuid import UUID
        stage = Path(raw_root) / str(UUID(product["Id"])) / "s3_objects"
        if stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError:
                print("Verified archive saved; S3 object staging could not be fully cleaned up.", flush=True)
