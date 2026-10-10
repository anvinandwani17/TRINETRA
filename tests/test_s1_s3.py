"""Offline S3 transport fixtures. No real satellite pixels or live credentials."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from trinetra.downloads.s3 import S3Transfer, _location, ENDPOINT, _error_code
from trinetra.downloads import sentinel1 as d

NAME = "S1A_IW_GRDH_1SDV_TEST_ONLY.SAFE"
PREFIX = "Sentinel-1/SAR/IW_GRDH_1S-COG/2026/08/16/" + NAME + "/"
PRODUCT = {"Id": "00000000-0000-4000-8000-000000000001", "Name": NAME,
           "S3Path": "/eodata/" + PREFIX, "Online": True}


def object_record(relative="manifest.safe", data=b"fixture"):
    return {"Key": PREFIX + relative, "Size": len(data), "ETag": '"fixture-etag"'}


def response(data, offset=0, total=None):
    result = {"Body": BytesIO(data), "ETag": '"fixture-etag"', "ContentLength": len(data),
              "ResponseMetadata": {"HTTPStatusCode": 206 if offset else 200}}
    if offset:
        result["ContentRange"] = f"bytes {offset}-{total-1}/{total}"
    return result


def client_fixture(data=b"fixture"):
    client = Mock()
    client.list_objects_v2.return_value = {"Contents": [object_record(data=data)], "IsTruncated": False}
    client.get_object.return_value = response(data)
    return client


class S3Tests(unittest.TestCase):
    def test_streaming_error_with_none_response_resumes_retained_bytes(self):
        class StreamingError(Exception):
            response = None
        class Interrupted(BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise StreamingError('fixture stream interruption')
                return super().read(3)
        client = client_fixture(b'abcdef')
        first = response(b'abcdef'); first['Body'] = Interrupted(b'abc')
        client.get_object.side_effect = [first, response(b'def', offset=3, total=6)]
        t = S3Transfer(client)
        obj = t._objects('eodata', PREFIX, 100)[0][0]
        with tempfile.TemporaryDirectory() as folder, patch('trinetra.downloads.s3._retry_wait') as retry:
            path, _ = t._object('eodata', obj, Path(folder))
            self.assertEqual(path.read_bytes(), b'abcdef')
            retry.assert_called_once()
        self.assertEqual(client.get_object.call_args.kwargs['Range'], 'bytes=3-')
        self.assertEqual(client.get_object.call_args.kwargs['IfMatch'], obj['etag'])

    def test_streaming_error_exhaustion_preserves_partial_without_attribute_crash(self):
        class StreamingError(Exception):
            response = None
        class Interrupted(BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise StreamingError('fixture stream interruption')
                return super().read(3)
        client = client_fixture(b'abcdef')
        first = response(b'abcdef'); first['Body'] = Interrupted(b'abc')
        client.get_object.side_effect = [first] + [StreamingError('fixture')] * 4
        t = S3Transfer(client)
        obj = t._objects('eodata', PREFIX, 100)[0][0]
        with tempfile.TemporaryDirectory() as folder, patch('trinetra.downloads.s3._retry_wait'):
            with self.assertRaisesRegex(d.DownloadError, 'after five attempts'):
                t._object('eodata', obj, Path(folder))
            self.assertEqual(next(Path(folder).glob('*.part')).read_bytes(), b'abc')
            self.assertFalse(list(Path(folder).glob('*.bin')))
        self.assertEqual(client.get_object.call_count, 5)

    def test_non_client_error_metadata_is_safe_for_transfer_and_listing(self):
        for value in [None, 'not-a-dict', {'Error':None}, {'Error':{'Code':[]}}]:
            exc = RuntimeError('private-fixture-message')
            exc.response = value
            self.assertIsNone(_error_code(exc))
        exc = RuntimeError('private-fixture-message'); exc.response = None
        client = Mock(list_objects_v2=Mock(side_effect=exc))
        with self.assertRaisesRegex(d.DownloadError,'S3 object listing failed') as raised:
            S3Transfer(client)._objects('eodata',PREFIX,100)
        self.assertNotIn('private-fixture-message',str(raised.exception))
        exc.response = {'Error':{'Code':'AccessDenied'}}
        self.assertEqual(_error_code(exc),'AccessDenied')

    def test_short_response_resumes_after_eof(self):
        client = client_fixture(b"abcdef")
        first = response(b"abc")
        first["ContentLength"] = 6
        client.get_object.side_effect = [first, response(b"def", offset=3, total=6)]
        t = S3Transfer(client)
        obj = t._objects("eodata", PREFIX, 100)[0][0]
        with tempfile.TemporaryDirectory() as folder, patch("trinetra.downloads.s3._retry_wait"):
            path, _ = t._object("eodata", obj, Path(folder))
            self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertEqual(client.get_object.call_args.kwargs["Range"], "bytes=3-")

    def test_adapter_runs_safe_validation_and_then_uses_final_cache(self):
        from test_s1_downloads import fixture
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/"fixture.zip"
            fixture(source)
            with zipfile.ZipFile(source) as archive:
                payloads = {PREFIX + name[len(NAME)+1:]: archive.read(name) for name in archive.namelist()}
            client = Mock()
            client.list_objects_v2.return_value = {"Contents": [
                {"Key": key, "Size": len(data), "ETag": '"fixture-etag"'} for key, data in payloads.items()
            ], "IsTruncated": False}
            client.get_object.side_effect = lambda **kw: response(payloads[kw["Key"]])
            t = S3Transfer(client)
            with patch.object(d, "_raster_read", return_value={"sample_read": "mocked; fixture only"}):
                result = d.download_product(PRODUCT, folder, None, max_bytes=100000, transfer=t)
            self.assertEqual(result["verification"]["zip_crc"], "passed")
            self.assertEqual(result["verification"]["manifest"], "passed")
            self.assertEqual(len(result["verification"]["rasters"]), 2)
            requests = client.get_object.call_count
            again = d.download_product(PRODUCT, folder, None, max_bytes=100000, transfer=t)
            self.assertTrue(again["cache_hit"])
            self.assertEqual(client.get_object.call_count, requests)

    def test_invalid_s3_safe_never_promoted_to_verified_final(self):
        client = client_fixture(b"not-xml")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(d.DownloadError):
                d.download_product(PRODUCT, folder, None, max_bytes=100000, transfer=S3Transfer(client))
            self.assertFalse((Path(folder)/PRODUCT["Id"]/(NAME+".zip")).exists())

    def test_exact_prefix_and_bucket_never_global(self):
        bucket, prefix = _location(PRODUCT)
        client = client_fixture()
        S3Transfer(client)._objects(bucket, prefix, 100)
        self.assertEqual(client.list_objects_v2.call_args.kwargs,
                         {"Bucket": "eodata", "Prefix": PREFIX, "MaxKeys": 100})
        for path in ["/eodata/", "/other/" + PREFIX, "/eodata/Sentinel-1/SAR/other.SAFE", "/eodata/Sentinel-1/SAR/../" + NAME]:
            with self.subTest(path=path), self.assertRaises(d.DownloadError):
                _location({**PRODUCT, "S3Path": path})

    def test_size_rejected_before_any_object_download(self):
        client = client_fixture()
        with self.assertRaises(d.DownloadError):
            S3Transfer(client)._objects("eodata", PREFIX, 3)
        client.get_object.assert_not_called()

    def test_traversal_and_other_products_rejected(self):
        for key in [PREFIX + "../bad", "another/product/manifest.safe", PREFIX + "unsafe\\name"]:
            client = client_fixture()
            client.list_objects_v2.return_value["Contents"][0]["Key"] = key
            with self.subTest(key=key), self.assertRaises(d.DownloadError):
                S3Transfer(client)._objects("eodata", PREFIX, 100)

    def test_metadata_pagination_stays_in_selected_prefix(self):
        client = Mock()
        client.list_objects_v2.side_effect = [
            {"Contents": [object_record("one")], "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": [object_record("two")], "IsTruncated": False}]
        objects, total = S3Transfer(client)._objects("eodata", PREFIX, 100)
        self.assertEqual(len(objects), 2)
        self.assertEqual(total, 14)
        self.assertEqual(client.list_objects_v2.call_args.kwargs["Prefix"], PREFIX)
        self.assertEqual(client.list_objects_v2.call_args.kwargs["ContinuationToken"], "next")

    def test_object_cache_prevents_duplicate_get(self):
        client = client_fixture()
        t = S3Transfer(client)
        obj = t._objects("eodata", PREFIX, 100)[0][0]
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            path, hit = t._object("eodata", obj, stage)
            self.assertFalse(hit)
            cached, hit = t._object("eodata", obj, stage)
            self.assertTrue(hit)
            self.assertEqual(path.read_bytes(), b"fixture")
            self.assertEqual(path, cached)
            self.assertEqual(client.get_object.call_count, 1)
            self.assertEqual(client.get_object.call_args.kwargs["IfMatch"], '"fixture-etag"')

    def test_interrupted_object_resumes_exact_range(self):
        class Interrupted(BytesIO):
            def read(self, size=-1):
                if self.tell() >= 3:
                    raise RuntimeError("fixture connection interrupted")
                return super().read(3)
        client = client_fixture(b"abcdef")
        first = response(b"abc")
        first["ContentLength"] = 6
        first["Body"] = Interrupted(b"abc")
        client.get_object.side_effect = [first, response(b"def", offset=3, total=6)]
        t = S3Transfer(client)
        obj = t._objects("eodata", PREFIX, 100)[0][0]
        with tempfile.TemporaryDirectory() as folder, patch("trinetra.downloads.s3._retry_wait"):
            path, _ = t._object("eodata", obj, Path(folder))
            self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertEqual(client.get_object.call_args.kwargs["Range"], "bytes=3-")
        self.assertEqual(client.get_object.call_args.kwargs["IfMatch"], obj["etag"])

    def test_ignored_resume_range_preserves_partial(self):
        client = client_fixture(b"abcdef")
        t = S3Transfer(client)
        obj = t._objects("eodata", PREFIX, 100)[0][0]
        class Interrupted(BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise RuntimeError("fixture interruption")
                return super().read(3)
        first = response(b"abcdef")
        first["Body"] = Interrupted(b"abc")
        client.get_object.side_effect = [first, response(b"abcdef")]
        with tempfile.TemporaryDirectory() as folder, patch("trinetra.downloads.s3._retry_wait"):
            with self.assertRaisesRegex(d.DownloadError, "range was ignored"):
                t._object("eodata", obj, Path(folder))
            self.assertEqual(next(Path(folder).glob("*.part")).read_bytes(), b"abc")
            self.assertFalse(list(Path(folder).glob("*.bin")))

    def test_changed_etag_or_corrupt_cache_never_silently_replaced(self):
        for changed in (True, False):
            client = client_fixture()
            t = S3Transfer(client)
            obj = t._objects("eodata", PREFIX, 100)[0][0]
            with tempfile.TemporaryDirectory() as folder:
                path, _ = t._object("eodata", obj, Path(folder))
                if changed:
                    obj = {**obj, "etag": '"new"'}
                else:
                    path.write_bytes(b"corrupt")
                with self.assertRaises(d.DownloadError):
                    t._object("eodata", obj, Path(folder))
                self.assertEqual(client.get_object.call_count, 1)

    def test_packaging_preserves_bytes_and_complete_partial_needs_no_get(self):
        client = client_fixture()
        t = S3Transfer(client)
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder) / "scene.zip.part", Path(folder) / "transfer.json"
            result = t(PRODUCT, partial, state, None, 100000)
            self.assertEqual(result["transport"], "s3")
            with zipfile.ZipFile(partial) as archive:
                self.assertEqual(archive.read(NAME + "/manifest.safe"), b"fixture")
            count = client.get_object.call_count
            t(PRODUCT, partial, state, None, 100000)
            self.assertEqual(client.get_object.call_count, count)
            self.assertNotIn("access_key", state.read_text())

    def test_product_change_before_packaging_is_rejected(self):
        client = client_fixture()
        client.list_objects_v2.side_effect = [
            {"Contents": [object_record()], "IsTruncated": False},
            {"Contents": [object_record("different")], "IsTruncated": False}]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(d.DownloadError, "list changed"):
                S3Transfer(client)(PRODUCT, Path(folder)/"scene.zip.part", Path(folder)/"transfer.json", None, 100000)
            self.assertFalse((Path(folder)/"scene.zip.part").exists())

    def test_foreign_http_partial_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            partial, state = Path(folder)/"scene.zip.part", Path(folder)/"transfer.json"
            partial.write_bytes(b"retained-http-bytes")
            d.atomic_json(state, {"source": "https://download.dataspace.copernicus.eu"})
            client = client_fixture()
            with self.assertRaises(d.DownloadError):
                S3Transfer(client)(PRODUCT, partial, state, None, 100000)
            self.assertEqual(partial.read_bytes(), b"retained-http-bytes")
            client.get_object.assert_not_called()

    def test_verified_final_reused_without_s3_login_or_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/PRODUCT["Id"]
            root.mkdir()
            final = root/(NAME + ".zip")
            final.write_bytes(b"fixture-verified-only-for-cache-test")
            d.atomic_json(root/"metadata.json", {"verification": {"status": "valid", "bytes": final.stat().st_size, "sha256": d._sha256(final)}})
            client = Mock()
            result = d.download_product(PRODUCT, folder, None, transfer=S3Transfer(client))
            self.assertTrue(result["cache_hit"])
            client.list_objects_v2.assert_not_called()
            client.get_object.assert_not_called()

    def test_cleanup_only_owned_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/PRODUCT["Id"]
            stage = root/"s3_objects"
            stage.mkdir(parents=True)
            (stage/"fixture.bin").write_bytes(b"fixture")
            final = root/(NAME + ".zip")
            final.write_bytes(b"fixture")
            S3Transfer.cleanup(PRODUCT, folder)
            self.assertFalse(stage.exists())
            self.assertTrue(final.exists())


if __name__ == "__main__":
    unittest.main()
