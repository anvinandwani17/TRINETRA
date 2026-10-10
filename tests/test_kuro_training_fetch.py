"""Bounded HTTP fetching/cache and tar header indexing behavior."""
from pathlib import Path
import sys
import io
import json
import tempfile
import tarfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.datasets.kuro_training_fetch import RangeBudget,candidates,sample_extent,INFO_OFFSET


class RangeTests(unittest.TestCase):
    def test_range_requires_exact_response_and_reuses_cache(self):
        class Response(io.BytesIO):
            status=206
            headers={'Content-Range':'bytes 0-3/100'}
        with tempfile.TemporaryDirectory() as root:
            budget=RangeBudget(root, 4)
            with patch('trinetra.datasets.kuro_training_fetch.urlopen', return_value=Response(b'abcd')) as get:
                self.assertEqual(budget.get('https://example.org',0,4),b'abcd')
                self.assertEqual(budget.get('https://example.org',0,4),b'abcd')
                self.assertEqual(get.call_count,1)
            with self.assertRaises(ValueError):budget.get('https://example.org',4,1)
        with tempfile.TemporaryDirectory() as root:
            response=Response(b'abcd');response.status=200
            with patch('trinetra.datasets.kuro_training_fetch.urlopen', return_value=response):
                with self.assertRaises(ValueError):RangeBudget(root,4).get('https://example.org',0,4)

    def test_header_checksum_and_variable_metadata_extent(self):
        m=tarfile.TarInfo('000000.sec1_vv.npy');m.size=200832
        raw=m.tobuf(format=tarfile.USTAR_FORMAT)
        self.assertEqual(list(candidates(raw,403456)),[('000000',0)])
        changed=bytearray(raw);changed[0]^=1
        self.assertEqual(list(candidates(bytes(changed),403456)),[])
        info={'actid':470,'grid_id':'fixture'}; payload=json.dumps(info).encode()
        m=tarfile.TarInfo('000000.info.json');m.size=len(payload)
        parsed,size=sample_extent('000000',0,m.tobuf()+payload)
        self.assertEqual(parsed,info);self.assertEqual(size,9*201728+1024)


if __name__ == '__main__':unittest.main()
