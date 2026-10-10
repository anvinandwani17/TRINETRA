"""Offline transport fixtures only; no real scenes, credentials, or network."""

from io import BytesIO
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import zipfile

from trinetra.downloads import sentinel1 as d

NAME = "S1A_IW_GRDH_1SDV_TEST_ONLY.SAFE"
PRODUCT = {"Id": "00000000-0000-4000-8000-000000000001", "Name": NAME, "Online": True}


class Response(BytesIO):
    def __init__(self, data=b"", status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data)), "ETag": '"fixture"'}


def fixture(path, *, wrong_root=False, real_tiff=False):
    """Tiny disposable ZIP fixture; never written to data/raw or shown as a scene."""
    root = "wrong.SAFE" if wrong_root else NAME
    members = ["measurement/test-vv-test.tiff", "measurement/test-vh-test.tiff", "annotation/test.xml"]
    manifest = '<root>' + ''.join(f'<fileLocation href="./{m}"/>' for m in members) + '</root>'
    data = b"not-a-real-tiff"
    if real_tiff:
        import numpy as np
        from rasterio.io import MemoryFile
        from rasterio.transform import from_origin
        with MemoryFile() as mem:
            with mem.open(driver="GTiff", width=2, height=2, count=1, dtype="uint16", transform=from_origin(0, 2, 1, 1)) as raster:
                raster.write(np.ones((2, 2), dtype="uint16"), 1)
            data = mem.read()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(root + "/manifest.safe", manifest)
        for member in members:
            archive.writestr(root + "/" + member, data if member.endswith("tiff") else "<annotation/>")


class DownloadTests(unittest.TestCase):
    def test_502_retries_then_downloads_without_duplicate_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            error = HTTPError(d.DOWNLOAD, 502, "private-response", {}, None)
            opener = Mock(open=Mock(side_effect=[error, error, Response(b"abcdef")]))
            with patch.object(d, "build_opener", return_value=opener), patch.object(d.time, "sleep") as sleep:
                result = d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertTrue(result["stream_complete"])
            self.assertEqual(partial.read_bytes(), b"abcdef")
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 20])

    def test_502_exhaustion_preserves_partial_and_never_claims_success(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abc")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "validator": '"fixture"'})
            opener = Mock(open=Mock(side_effect=HTTPError(d.DOWNLOAD, 502, "private-response", {}, None)))
            with patch.object(d, "build_opener", return_value=opener), patch.object(d.time, "sleep"), self.assertRaisesRegex(d.DownloadError, "HTTP 502; partial retained"):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertEqual(opener.open.call_count, d.MAX_ATTEMPTS)
            self.assertEqual(partial.read_bytes(), b"abc")
            self.assertFalse(json.loads(state.read_text()).get("stream_complete", False))

    def test_retry_after_numeric_date_and_invalid_are_bounded(self):
        for value, expected in [("45", 45), ("9999", 60), ("invalid", 10), ("Wed, 01 Jan 2099 00:00:00 GMT", 60)]:
            with self.subTest(value=value), patch.object(d.time, "sleep") as sleep:
                d._retry_wait(0, "HTTP 503", {"Retry-After": value})
                sleep.assert_called_once_with(expected)

    def test_archive_and_manifest_validated_with_mocked_raster_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.zip.part"
            fixture(path)
            inspect = Mock(return_value={"sample_read": "passed"})
            result = d.validate_safe(path, NAME, inspect)
            self.assertEqual(result["zip_crc"], "passed")
            self.assertEqual(inspect.call_count, 2)
            self.assertEqual(result["sha256"], d._sha256(path))

    def test_wrong_product_root_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.zip"
            fixture(path, wrong_root=True)
            with self.assertRaises(d.DownloadError):
                d.validate_safe(path, NAME, Mock())

    def test_html_error_not_accepted_as_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.zip"
            path.write_bytes(b"<html>403 forbidden</html>")
            with self.assertRaises(d.DownloadError):
                d.validate_safe(path, NAME, Mock())

    def test_missing_manifest_reference_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(NAME + "/manifest.safe", '<root><fileLocation href="./missing.tiff"/></root>')
            with self.assertRaises(d.DownloadError):
                d.validate_safe(path, NAME, Mock())

    def test_zip_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(NAME + "/../bad", "test")
            with self.assertRaises(d.DownloadError):
                d.validate_safe(path, NAME, Mock())

    def test_initial_stream_persists_no_token(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            opener = Mock()
            opener.open.return_value = Response(b"abcdef")
            with patch.object(d, "build_opener", return_value=opener):
                result = d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="private-fixture-token")), 100)
            self.assertEqual(partial.read_bytes(), b"abcdef")
            self.assertTrue(result["stream_complete"])
            self.assertNotIn("private-fixture-token", state.read_text())

    def test_matching_range_resumes_without_duplicate_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abc")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "validator": '"fixture"', "total_bytes": 6})
            opener = Mock()
            opener.open.return_value = Response(b"def", 206, {"Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": '"fixture"'})
            with patch.object(d, "build_opener", return_value=opener):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertEqual(partial.read_bytes(), b"abcdef")
            self.assertEqual(opener.open.call_args.args[0].get_header("Range"), "bytes=3-")

    def test_ignored_range_does_not_corrupt_or_silently_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abc")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "validator": '"fixture"'})
            opener = Mock(open=Mock(return_value=Response(b"abcdef")))
            with patch.object(d, "build_opener", return_value=opener), self.assertRaises(d.DownloadError):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertEqual(partial.read_bytes(), b"abc")

    def test_wrong_range_offset_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abc")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "validator": '"fixture"'})
            opener = Mock(open=Mock(return_value=Response(b"def", 206, {"Content-Range": "bytes 2-5/6"})))
            with patch.object(d, "build_opener", return_value=opener), self.assertRaises(d.DownloadError):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertEqual(partial.read_bytes(), b"abc")

    def test_size_limit_enforced_before_streaming(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            opener = Mock(open=Mock(return_value=Response(b"abcdef")))
            with patch.object(d, "build_opener", return_value=opener), self.assertRaises(d.DownloadError):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 3)
            self.assertFalse(partial.exists())

    def test_unknown_length_stream_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            opener = Mock(open=Mock(return_value=Response(b"abcdef", headers={"ETag": '"fixture"'})))
            with patch.object(d, "build_opener", return_value=opener), self.assertRaises(d.DownloadError):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 3)

    def test_complete_partial_reused_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abcdef")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "stream_complete": True, "bytes": 6})
            with patch.object(d, "build_opener") as opener:
                d._transfer(PRODUCT, partial, state, Mock(), 100)
                opener.assert_not_called()

    def test_verified_final_never_redownloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / PRODUCT["Id"]
            root.mkdir()
            final = root / (NAME + ".zip")
            fixture(final)
            verification = d.validate_safe(final, NAME, Mock(return_value={"sample_read": "passed"}))
            d.atomic_json(root / "metadata.json", {"odata": PRODUCT, "verification": verification})
            with patch.object(d, "_transfer") as transfer:
                result = d.download_product(PRODUCT, folder, Mock())
                transfer.assert_not_called()
            self.assertTrue(result["cache_hit"])

    def test_final_without_marker_locally_validated_not_downloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / PRODUCT["Id"]
            root.mkdir()
            final = root / (NAME + ".zip")
            fixture(final)
            with patch.object(d, "_transfer") as transfer, patch.object(d, "validate_safe", return_value={"status": "valid"}):
                result = d.download_product(PRODUCT, folder, Mock())
                transfer.assert_not_called()
            self.assertTrue(result["cache_hit"])

    def test_concurrent_product_download_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / PRODUCT["Id"]
            root.mkdir()
            (root / ".download.lock").write_text("test")
            with self.assertRaises(d.DownloadError):
                d.download_product(PRODUCT, folder, Mock())

    def test_foreign_auth_redirect_rejected(self):
        with self.assertRaises(d.DownloadError):
            d._validate_download_url("https://example.com/scene.zip")

    def test_changed_range_validator_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.part", Path(folder) / "transfer.json"
            partial.write_bytes(b"abc")
            d.atomic_json(state, {"product_uuid": PRODUCT["Id"], "source": f"{d.DOWNLOAD}({PRODUCT['Id']})/$value", "validator": '"old"', "total_bytes": 6})
            opener = Mock(open=Mock(return_value=Response(b"def", 206, {"Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": '"new"'})))
            with patch.object(d, "build_opener", return_value=opener), self.assertRaises(d.DownloadError):
                d._transfer(PRODUCT, partial, state, Mock(token=Mock(return_value="fixture")), 100)
            self.assertEqual(partial.read_bytes(), b"abc")

    def test_failed_validation_never_promoted_to_final(self):
        with tempfile.TemporaryDirectory() as folder:
            def invalid_transfer(product, partial, state, tokens, max_bytes, restart):
                partial.write_bytes(b"<html>not a SAFE archive</html>")
            with patch.object(d, "_transfer", side_effect=invalid_transfer), self.assertRaises(d.DownloadError):
                d.download_product(PRODUCT, folder, Mock())
            root = Path(folder) / PRODUCT["Id"]
            self.assertFalse((root / (NAME + ".zip")).exists())
            self.assertFalse((root / "metadata.json").exists())
            self.assertTrue((root / (NAME + ".zip.part")).exists())
            self.assertFalse((root / ".download.lock").exists())

    def test_odata_lookup_resolves_only_exact_selected_name(self):
        payload = json.dumps({"value": [PRODUCT]}).encode()
        opener = Mock(open=Mock(return_value=Response(payload)))
        with patch.object(d, "build_opener", return_value=opener):
            result = d.resolve_product(NAME.removesuffix(".SAFE"))
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
        self.assertIn("Name eq '" + NAME + "'", query["$filter"][0])
        self.assertEqual(result["Id"], PRODUCT["Id"])

    def test_auth_error_never_includes_password_or_response_body(self):
        opener = Mock(open=Mock(side_effect=HTTPError(d.IDENTITY, 401, "secret-response-body", {}, None)))
        with patch.object(d, "build_opener", return_value=opener):
            with self.assertRaises(d.DownloadError) as raised:
                d.TokenProvider(lambda: ("fixture-user", "fixture-secret-password")).token()
        self.assertNotIn("fixture-secret-password", str(raised.exception))
        self.assertNotIn("secret-response-body", str(raised.exception))

    @unittest.skipUnless(importlib.util.find_spec("rasterio"), "Real raster reads require installed Rasterio")
    def test_real_raster_read_from_zip_part(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.zip.part"
            fixture(path, real_tiff=True)
            result = d.validate_safe(path, NAME)
            self.assertEqual(len(result["rasters"]), 2)
            self.assertEqual(result["rasters"][0]["sample_read"], "passed")


if __name__ == "__main__":
    unittest.main()
