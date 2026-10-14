"""Meaningful boundary and provenance checks for the context evaluator."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

script=Path(__file__).resolve().parents[1]/'scripts/evaluate_emsr927_context.py'
if not script.is_file():script=Path(__file__).with_name('evaluate_emsr927_context.py')
spec=importlib.util.spec_from_file_location('context',script)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ContextTests(unittest.TestCase):
    def test_unknown_and_outside_stay_distinct(self):
        a=np.array([[1,0,255,255]],dtype='uint8')
        inside=np.array([[True,True,True,False]])
        c=m.model_codes(a,inside)
        self.assertEqual(c.tolist(),[[1,0,2,255]])
        r=m.accounting(np.ones_like(inside),c,100)
        self.assertEqual([r[k] for k in ['candidate_pixels','non_candidate_pixels','unknown_inside_case_pixels','outside_case_pixels']],[1,1,1,1])
        self.assertEqual(r['reference_area_km2'],.0004)
        self.assertTrue(r['not_water_flood_accuracy'])

    def test_supported_prediction_outside_case_is_rejected(self):
        with self.assertRaises(ValueError):m.model_codes(np.array([[1]],dtype='uint8'),np.array([[False]]))

    def test_pixel_centres_not_all_touched(self):
        grid={'shape':(1,2),'transform':from_origin(0,10,10,10),'crs':'EPSG:32645'}
        # Thin polygon touches the first pixel but not its centre.
        self.assertEqual(int(m.burn(box(0,0,1,10),grid).sum()),0)
        self.assertEqual(int(m.burn(box(0,0,6,10),grid).sum()),1)

    def test_nearest_alignment_preserves_nodata(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'codes.tif'
            a=np.array([[1,255],[0,1]],dtype='uint8')
            with rasterio.open(p,'w',driver='GTiff',height=2,width=2,count=1,dtype='uint8',crs='EPSG:32645',transform=from_origin(0,40,20,20),nodata=255) as ds:ds.write(a,1)
            grid={'shape':(4,4),'transform':from_origin(0,40,10,10),'crs':'EPSG:32645'}
            b=m.warp_codes(p,grid)
            np.testing.assert_array_equal(b,np.repeat(np.repeat(a,2,0),2,1))

    def test_empty_reference_has_no_fabricated_percentage(self):
        r=m.accounting(np.zeros((1,1),dtype=bool),np.array([[1]]),100)
        self.assertEqual(r['reference_pixels'],0)
        self.assertIsNone(r['candidate_share'])

if __name__=='__main__':unittest.main()
