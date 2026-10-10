"""Fixture tests for execution failure, output validation and cache reuse."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
import rasterio
from affine import Affine
from trinetra.preprocessing.ai_snap_runner import run_graph

class RunnerChecks(unittest.TestCase):
    def test_cache_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);target=base/'sigma.dim';band=base/'Sigma0_VV.tif'
            with rasterio.open(band,'w',driver='GTiff',count=1,width=2,height=2,dtype='float32',crs='EPSG:32645',transform=Affine(10,0,312980,0,-10,3098660)) as dst:
                dst.write(np.full((2,2),.1,dtype='float32'),1)
            target.write_text('<root><DATA_FILE_PATH href="Sigma0_VV.tif"/></root>')
            with patch('trinetra.preprocessing.ai_snap_runner.subprocess.run',return_value=SimpleNamespace(returncode=0)) as invoke:
                self.assertTrue(run_graph('gpt','<graph/>',target,'signature')['Sigma0_VV'].samefile(band))
                self.assertEqual(invoke.call_count,1)
                run_graph('gpt','<graph/>',target,'signature')
                self.assertEqual(invoke.call_count,1)
                target.write_text(target.read_text()+'\n')
                run_graph('gpt','<graph/>',target,'signature')
                self.assertEqual(invoke.call_count,2)
    def test_failure_not_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'sigma.dim'
            with patch('trinetra.preprocessing.ai_snap_runner.subprocess.run',return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(RuntimeError,'SNAP failed'):
                    run_graph('gpt','<graph/>',target,'signature')
            self.assertFalse(target.with_suffix('.verified.json').exists())

if __name__=='__main__':unittest.main()
