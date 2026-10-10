"""Download exactly selected Sentinel-1 GRD SAFE/COG_SAFE products from CDSE."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID
import xml.etree.ElementTree as ET
import zipfile

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD = "https://download.dataspace.copernicus.eu/odata/v1/Products"
IDENTITY = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
DOWNLOAD_HOSTS = {"download.dataspace.copernicus.eu", "zipper.dataspace.copernicus.eu"}
GIB = 1024 ** 3
MAX_ATTEMPTS = 5


def _retry_wait(attempt, reason, headers=None):
    """Bounded backoff, including numeric/date Retry-After; never log URLs/tokens."""
    delay = min(60, 10 * 2 ** attempt)
    retry_after = (headers or {}).get("Retry-After")
    if retry_after:
        try:
            requested = int(retry_after)
        except (ValueError, TypeError):
            try:
                requested = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        delay = min(60, max(delay, requested))
    print(f"{reason}; retry {attempt + 2}/{MAX_ATTEMPTS} in {delay:.0f}s. Existing partial bytes are preserved.", flush=True)
    time.sleep(delay)


class DownloadError(RuntimeError):
    """Download or validation failed; no successful raw result is claimed."""


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as file:
            temporary = Path(file.name)
            json.dump(value, file, indent=2)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise DownloadError("Unexpected redirect from the authentication/catalog endpoint")


class _DownloadRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_download_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in DOWNLOAD_HOSTS or p.username or p.password or p.port not in {None, 443}:
        raise DownloadError("Refusing download/credential redirect outside official download hosts")


def resolve_product(stac_id):
    """Map selected STAC product name to its OData UUID; no broad scene search."""
    if not isinstance(stac_id, str) or not re.fullmatch(r"S1[ABCD]_IW_GRDH_1SDV_[A-Za-z0-9_.-]+", stac_id):
        raise DownloadError("Selected item is not an IW GRDH VV/VH SAFE or COG_SAFE product")
    name = stac_id if stac_id.endswith(".SAFE") else stac_id + ".SAFE"
    query = urlencode({"$filter": f"Collection/Name eq 'SENTINEL-1' and Name eq '{name}'", "$top": 2})
    request = Request(ODATA + "?" + query, headers={"Accept": "application/json"})
    try:
        with build_opener(_NoRedirects()).open(request, timeout=30) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise DownloadError("Selected product metadata exceeded its size limit")
            payload = json.loads(raw)
    except HTTPError as exc:
        raise DownloadError(f"Product metadata lookup returned HTTP {exc.code}") from exc
    except (URLError, ValueError, TimeoutError) as exc:
        raise DownloadError("Product metadata lookup failed; check CDSE/network access") from exc
    products = payload.get("value", []) if isinstance(payload, dict) else []
    if not isinstance(products, list) or len(products) != 1 or not isinstance(products[0], dict) or products[0].get("Name") != name:
        raise DownloadError("Selected STAC name did not resolve to exactly one matching OData product")
    product = products[0]
    try:
        product["Id"] = str(UUID(product["Id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise DownloadError("Invalid OData UUID") from exc
    if product.get("Online") is not True:
        raise DownloadError("Selected product is not online; no archive order or alternative scene is requested")
    return product


class TokenProvider:
    """Tokens exist only in memory; credentials are supplied by environment settings."""

    def __init__(self, credentials, totp=None):
        self.credentials = credentials
        self.totp = totp
        self._token = None
        self._expires = 0

    def token(self, refresh=False):
        if self._token and not refresh and time.monotonic() < self._expires:
            return self._token
        username, password = self.credentials()
        if not username or not password:
            raise DownloadError("Set CDSE_USERNAME and CDSE_PASSWORD in your local .env or environment")
        fields = {"client_id": "cdse-public", "grant_type": "password", "username": username, "password": password}
        if self.totp:
            fields["totp"] = self.totp()
        request = Request(IDENTITY, data=urlencode(fields).encode(), method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with build_opener(_NoRedirects()).open(request, timeout=30) as response:
                payload = json.loads(response.read(100_000))
            token = payload["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError("missing token")
            self._token = token
            self._expires = time.monotonic() + max(1, int(payload.get("expires_in", 300)) - 30)
            return token
        except HTTPError as exc:
            raise DownloadError(f"CDSE authentication returned HTTP {exc.code}; check login and 2FA. Credentials are not logged.") from None
        except (URLError, TimeoutError, KeyError, ValueError, TypeError) as exc:
            raise DownloadError("CDSE authentication failed; credentials/tokens are not logged") from None


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_safe(path, expected_name, raster_check=None):
    """Full ZIP CRC, SAFE identity/manifest references, and real VV/VH raster reads."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise DownloadError("Duplicate archive entries")
            for name in names:
                parts = PurePosixPath(name).parts
                if name.startswith("/") or ".." in parts or "\\" in name or not parts or parts[0] != expected_name:
                    raise DownloadError("Archive has unsafe paths or the wrong SAFE product root")
            manifest_name = expected_name + "/manifest.safe"
            if manifest_name not in names or archive.getinfo(manifest_name).file_size > 10_000_000:
                raise DownloadError("Missing or oversized SAFE manifest")
            bad = archive.testzip()
            if bad:
                raise DownloadError("Archive CRC validation failed")
            manifest = ET.fromstring(archive.read(manifest_name))
            references = []
            for node in manifest.iter():
                if node.tag.split("}")[-1] == "fileLocation":
                    href = node.attrib.get("href", "")
                    relative = PurePosixPath(href)
                    if not href or relative.is_absolute() or ".." in relative.parts or "\\" in href:
                        raise DownloadError("Invalid SAFE manifest file reference")
                    target = expected_name + "/" + str(relative)
                    if target not in names:
                        raise DownloadError("SAFE manifest references a missing file")
                    references.append(target)
            if not references:
                raise DownloadError("SAFE manifest has no data-object file references")
            measurements = {pol: [name for name in names if "/measurement/" in name
                                 and f"-{pol}-" in name.lower() and name.lower().endswith((".tif", ".tiff"))]
                            for pol in ("vv", "vh")}
            if any(len(files) != 1 for files in measurements.values()):
                raise DownloadError("Expected one IW GRD measurement TIFF for each of VV and VH")
            annotations = [name for name in names if "/annotation/" in name and name.endswith(".xml")]
            if not annotations:
                raise DownloadError("Missing SAR annotation XML")
            for annotation in annotations:
                if archive.getinfo(annotation).file_size > 20_000_000:
                    raise DownloadError("SAR annotation exceeded its size limit")
                ET.fromstring(archive.read(annotation))
        inspect = raster_check or _raster_read
        raster_details = [inspect(path, files[0]) for files in measurements.values()]
        return {"status": "valid", "verified_at": now(), "sha256": _sha256(path),
                "bytes": path.stat().st_size, "zip_crc": "passed", "manifest": "passed",
                "rasters": raster_details, "note": "Raw GRD readability only; no calibration, geocoding, or flood analysis"}
    except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise DownloadError("SAFE archive could not be read or validated") from exc


def _raster_read(path, member):
    try:
        import rasterio
        from rasterio.windows import Window
        # GDAL supports zipped TIFFs directly; no second full extracted copy.
        # Explicit braces also identify archives named .zip.part during validation.
        vsi_path = f"/vsizip/{{{path.resolve().as_posix()}}}/{member}"
        with rasterio.open(vsi_path) as raster:
            if raster.driver != "GTiff" or raster.count != 1 or raster.width <= 0 or raster.height <= 0:
                raise DownloadError("Invalid GRD measurement raster dimensions/driver")
            width, height = min(16, raster.width), min(16, raster.height)
            positions = [(0, 0), ((raster.width-width)//2, (raster.height-height)//2),
                         (raster.width-width, raster.height-height)]
            for x, y in positions:
                pixels = raster.read(1, window=Window(x, y, width, height))
                if pixels.size == 0:
                    raise DownloadError("Measurement raster read returned no pixels")
            return {"member": member, "driver": raster.driver, "width": raster.width,
                    "height": raster.height, "dtype": raster.dtypes[0], "sample_read": "passed", "windows_read": 3}
    except ImportError as exc:
        raise DownloadError("Install Rasterio with: python -m pip install -e .[download]") from exc
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError("Rasterio could not read the raw measurement TIFF") from exc


def _transfer(product, partial, state_path, tokens, max_bytes, restart=False):
    """Stream bounded bytes and resume only if range identity/offset checks pass."""
    url = f"{DOWNLOAD}({product['Id']})/$value"
    _validate_download_url(url)
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            raise DownloadError("Partial-transfer metadata is corrupt; use --restart-partial explicitly")
    if restart:
        partial.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        state = {}
    if partial.exists() and (state.get("product_uuid") != product["Id"] or state.get("source") != url):
        raise DownloadError("Partial file has no matching transfer identity; use --restart-partial")
    if partial.exists() and state.get("stream_complete") and partial.stat().st_size == state.get("bytes"):
        return state
    for attempt in range(MAX_ATTEMPTS):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > max_bytes:
            raise DownloadError("Existing partial exceeds the configured size limit")
        headers = {"Authorization": "Bearer " + tokens.token(), "Accept-Encoding": "identity"}
        if offset:
            validator = state.get("validator")
            if not validator:
                raise DownloadError("Server supplied no stable range validator; retained .part file cannot safely resume. Use --restart-partial.")
            headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
        request = Request(url, headers=headers)
        try:
            with build_opener(_DownloadRedirects()).open(request, timeout=60) as response:
                status = response.status
                if offset and status != 206:
                    raise DownloadError("Server did not honor resume (or product changed); partial retained. Use --restart-partial to restart explicitly.")
                if not offset and status != 200:
                    raise DownloadError("Unexpected initial download status")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise DownloadError("Encoded transfer cannot be safely range-resumed")
                length = response.headers.get("Content-Length")
                length = int(length) if length is not None else None
                if offset:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset or int(match[2]) != int(match[3]) - 1:
                        raise DownloadError("Invalid Content-Range; refusing to append")
                    total = int(match[3])
                    if length is not None and length != total - offset:
                        raise DownloadError("Range length mismatch")
                    if state.get("total_bytes") not in (None, total):
                        raise DownloadError("Product size changed during resume")
                else:
                    total = length
                if total is not None and (total <= 0 or total > max_bytes):
                    raise DownloadError("Archive exceeds the configured per-product limit (or has invalid length)")
                remaining = (total - offset) if total is not None else (max_bytes - offset)
                if shutil.disk_usage(partial.parent).free < remaining + 256 * 1024 * 1024:
                    raise DownloadError("Insufficient free disk space for bounded raw download")
                etag = response.headers.get("ETag")
                validator = etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified")
                if offset and validator and validator != state.get("validator"):
                    raise DownloadError("Resume validator changed; refusing to append a different archive")
                state = {"product_uuid": product["Id"], "source": url, "total_bytes": total,
                         "validator": validator or state.get("validator"), "stream_complete": False}
                atomic_json(state_path, state)
                written = offset
                last_progress = time.monotonic()
                with partial.open("ab" if offset else "wb") as file:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        if written + len(block) > max_bytes or (total is not None and written + len(block) > total):
                            raise DownloadError("Transfer exceeded its advertised/configured size")
                        file.write(block)
                        written += len(block)
                        if time.monotonic() - last_progress >= 8:
                            print(f"Raw transfer: {written / 1024**2:.0f} MiB received", flush=True)
                            last_progress = time.monotonic()
                if total is not None and written != total:
                    raise URLError("Incomplete transfer")
                state.update({"stream_complete": True, "bytes": written, "completed_at": now()})
                atomic_json(state_path, state)
                return state
        except HTTPError as exc:
            if exc.code == 401 and attempt < MAX_ATTEMPTS - 1:
                tokens.token(refresh=True)
                continue
            if exc.code == 416 and offset and exc.headers.get("Content-Range") == f"bytes */{offset}" and state.get("total_bytes") == offset:
                state.update({"stream_complete": True, "bytes": offset})
                atomic_json(state_path, state)
                return state
            if exc.code in {429, 500, 502, 503, 504} and attempt < MAX_ATTEMPTS - 1:
                _retry_wait(attempt, f"CDSE download returned HTTP {exc.code}", exc.headers)
                continue
            retained = "partial retained" if partial.exists() else "no archive bytes saved"
            raise DownloadError(f"Raw download returned HTTP {exc.code}; {retained}, no success claimed. Rerun the same command later; keep the pinned pair and data cache.") from None
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
            if attempt < MAX_ATTEMPTS - 1:
                _retry_wait(attempt, "CDSE transfer interrupted")
                continue
            raise DownloadError("Transfer interrupted; partial retained. Rerun to resume if the server supports it.") from None
        except ValueError as exc:
            raise DownloadError("Invalid transfer size headers") from exc
    raise DownloadError("Download attempts exhausted")


def download_product(product, raw_root, tokens, *, max_bytes=3 * GIB, restart_partial=False, transfer=None):
    """Verified finals are reused; interruptions are retained as .part files."""
    uuid = str(UUID(product["Id"]))
    name = product["Name"]
    if not re.fullmatch(r"S1[ABCD]_IW_GRDH_1SDV_[A-Za-z0-9_.-]+\.SAFE", name):
        raise DownloadError("Unsafe or unsupported SAFE name")
    folder = Path(raw_root) / uuid
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / (name + ".zip")
    partial = folder / (name + ".zip.part")
    state_path = folder / "transfer.json"
    metadata_path = folder / "metadata.json"
    # Exclusive lock prevents concurrent workers from appending/downloading twice.
    lock = folder / ".download.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise DownloadError("Product is locked by another run. If a previous process crashed, remove its lock only after confirming it stopped.") from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        if final.exists():
            metadata = None
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except ValueError:
                    pass
            verification = (metadata or {}).get("verification", {})
            if verification.get("status") == "valid" and verification.get("bytes") == final.stat().st_size and verification.get("sha256") == _sha256(final):
                return {"product_uuid": uuid, "name": name, "path": str(final), "cache_hit": True, "verification": verification}
            # Existing unmarked finals are validated locally rather than redownloaded.
            verification = validate_safe(final, name)
            atomic_json(metadata_path, {"odata": product, "verification": verification})
            return {"product_uuid": uuid, "name": name, "path": str(final), "cache_hit": True, "verification": verification}
        (transfer or _transfer)(product, partial, state_path, tokens, max_bytes, restart_partial)
        print("Validating full ZIP integrity, SAFE manifest, and VV/VH raster reads...", flush=True)
        verification = validate_safe(partial, name)
        # Mark valid only after every validation succeeds. Rename is atomic.
        partial.replace(final)
        atomic_json(metadata_path, {"odata": product, "verification": verification})
        return {"product_uuid": uuid, "name": name, "path": str(final), "cache_hit": False, "verification": verification}
    finally:
        lock.unlink(missing_ok=True)
