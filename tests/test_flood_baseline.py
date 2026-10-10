"""Tiny synthetic unit fixtures only. These tests do not represent real Trishuli results."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from trinetra.flood_detection.baseline import BaselineConfig, detect_flood, terrain_slope, intensity_mean_db


class RuleTests(unittest.TestCase):
    def test_band_order_accepts_preprocessing_db_labels_but_never_guesses(self):
        from trinetra.flood_detection.pipeline import validate_band_order
        for labels in [('VV','VH'),('VV_dB','VH_dB'),(' vv_db ','vh_DB')]:
            validate_band_order(2,labels,{'band_order':'VV,VH'})
        for count,labels,tags in [(2,('VH_dB','VV_dB'),{}),(2,(None,None),{'band_order':'VV,VH'}),
                                 (1,('VV_dB',),{}),(2,('VV_dB','HH_dB'),{}),
                                 (2,('VV_dB','VH_dB'),{'band_order':'VH,VV'})]:
            with self.subTest(labels=labels,tags=tags),self.assertRaises(RuntimeError):
                validate_band_order(count,labels,tags)

    def inputs(self,size=15):
        before = np.stack([np.full((size,size),-10.),np.full((size,size),-17.)]).astype(np.float32)
        after = before-10
        obs = np.ones((size,size),dtype=np.uint8)
        slope = np.zeros((size,size),dtype=np.float32)
        return before,after,obs,slope

    def run_case(self,before,after,obs,slope,config=None):
        return detect_flood(before,after,after-before,obs,slope,config=config)

    def test_dark_dual_pol_drop_retained_and_score_bounded(self):
        result = self.run_case(*self.inputs())
        self.assertEqual(result['flood_mask'][7,7],1)
        self.assertEqual(result['component_count'],1)
        self.assertTrue(np.all(result['reasons'][result['flood_mask']==1]==0))
        score = result['evidence_score']
        self.assertTrue(np.all((score[np.isfinite(score)]>=0)&(score[np.isfinite(score)]<=1)))

    def test_no_change_returns_valid_empty_mask(self):
        before,after,obs,slope = self.inputs()
        result = self.run_case(before,before.copy(),obs,slope)
        self.assertEqual(result['candidate_pixels'],0)
        self.assertEqual(result['flood_mask'][7,7],0)

    def test_change_without_dark_post_or_without_both_pol_drops_is_rejected(self):
        for kind in ['bright_post','vh_unchanged','vv_unchanged']:
            before,after,obs,slope = self.inputs()
            if kind=='bright_post': before[:]=np.array([-3,-10])[:,None,None]; after[:]=before-4
            elif kind=='vh_unchanged': after[1]=before[1]
            else: after[0]=before[0]
            self.assertEqual(self.run_case(before,after,obs,slope)['candidate_pixels'],0)

    def test_preexisting_low_backscatter_is_not_called_new_flood(self):
        before,after,obs,slope = self.inputs()
        before[:]=np.array([-20,-27])[:,None,None]; after[:]=before-10
        result = self.run_case(before,after,obs,slope)
        self.assertEqual(result['candidate_pixels'],0)
        self.assertTrue(result['reasons'][7,7]&16)

    def test_unknown_and_shadow_holes_never_become_dry_or_flood(self):
        before,after,obs,slope = self.inputs()
        obs[5:9,5:9]=0; obs[0,:]=255
        result = self.run_case(before,after,obs,slope)
        self.assertTrue(np.all(result['flood_mask'][obs!=1]==255))
        self.assertTrue(np.isnan(result['evidence_score'][obs!=1]).all())
        self.assertEqual(result['flood_mask'][6,6],255)

    def test_steep_missing_dem_and_invalid_radiometry_are_unknown(self):
        for kind in ['steep','missing_dem','missing_vh']:
            before,after,obs,slope = self.inputs()
            if kind=='steep': slope[7,7]=25
            elif kind=='missing_dem': slope[7,7]=np.nan
            else: after[1,7,7]=np.nan
            result = self.run_case(before,after,obs,slope)
            self.assertEqual(result['flood_mask'][7,7],255)
            self.assertTrue(np.isnan(result['evidence_score'][7,7]))

    def test_small_component_removed_and_not_bridged_through_unknown(self):
        before,after,obs,slope = self.inputs()
        obs[:]=0; obs[7,7]=1; obs[8,8]=1
        config = replace(BaselineConfig(),min_filter_neighbors=1,min_area_m2=800)
        result = self.run_case(before,after,obs,slope,config)
        self.assertEqual(result['candidate_pixels'],0) # diagonal pixels are separate
        self.assertEqual(result['flood_mask'][7,7],0)
        self.assertTrue(result['reasons'][7,7]&256)
        config = replace(config,min_area_m2=400)
        result = self.run_case(before,after,obs,slope,config)
        self.assertEqual(result['component_count'],2)

    def test_filter_averages_linear_power_and_preserves_unknown_centers(self):
        data = np.full((2,3,3),-20.)
        data[:,1,1]=-10
        valid = np.ones((3,3),dtype=bool)
        result,support = intensity_mean_db(data,valid,1)
        self.assertAlmostEqual(float(result[0,1,1]),10*np.log10((.1+8*.01)/9),places=5)
        valid[1,1]=False
        result,support = intensity_mean_db(data,valid,1)
        self.assertFalse(support[1,1]); self.assertTrue(np.isnan(result[:,1,1]).all())

    def test_horn_slope_of_known_plane_and_nodata_neighborhood(self):
        dem = np.tile(np.arange(7)*2.,(7,1)) # rise 2 m for each 20 m eastward
        slope = terrain_slope(dem)
        self.assertAlmostEqual(float(slope[3,3]),np.degrees(np.arctan(.1)),places=5)
        self.assertTrue(np.isnan(slope[0]).all())
        dem[3,3]=np.nan
        self.assertTrue(np.isnan(terrain_slope(dem)[2:5,2:5]).all())

    def test_invalid_configuration_change_and_observation_codes_rejected(self):
        for config in [replace(BaselineConfig(),max_slope_deg=np.nan),replace(BaselineConfig(),min_area_m2=-1),
                       replace(BaselineConfig(),min_filter_neighbors=2.5)]:
            with self.assertRaises(ValueError): self.run_case(*self.inputs(),config)
        before,after,obs,slope = self.inputs()
        with self.assertRaisesRegex(ValueError,'post-minus-pre'):
            detect_flood(before,after,np.zeros_like(before),obs,slope)
        obs[7,7]=2
        with self.assertRaises(ValueError): self.run_case(before,after,obs,slope)


@unittest.skipUnless(importlib.util.find_spec('rasterio'),'Real raster/vector I/O tests require Rasterio')
class GeoTests(unittest.TestCase):
    def fixture(self,folder,changed=True):
        import rasterio
        from rasterio.transform import from_origin
        from trinetra.downloads.sentinel1 import atomic_json, _sha256
        root = Path(folder)/'synthetic_preprocessed'; root.mkdir()
        grid = {'crs':'EPSG:32645','transform':from_origin(316000,3092000,20,20),'width':16,'height':16}
        before = np.stack([np.full((16,16),-10.),np.full((16,16),-17.)]).astype(np.float32)
        after = before-10 if changed else before.copy()
        obs = np.ones((16,16),dtype=np.uint8); obs[5:8,5:8]=0
        before[:,obs==0]=np.nan; after[:,obs==0]=np.nan
        arrays = {'before_gamma0_db':before,'after_gamma0_db':after,'change_db':after-before,
                  'observability':obs,'quality_reasons':np.where(obs==0,16,0).astype(np.uint16),
                  'pre_layover_shadow':np.where(obs==0,2,0).astype(np.uint8),
                  'post_layover_shadow':np.zeros((16,16),dtype=np.uint8)}
        report = {'case':'trishuli','status':'complete','outputs':{},'output_sha256':{}}
        for key,array in arrays.items():
            values = array if array.ndim==3 else array[None]
            path = root/(key+'.tif')
            with rasterio.open(path,'w',driver='GTiff',**grid,count=values.shape[0],dtype=values.dtype) as dst:
                dst.write(values)
                if values.shape[0]==2:
                    # Match actual TRINETRA preprocessing, not simplified labels.
                    dst.descriptions=('VV_dB','VH_dB')
                    dst.update_tags(band_order='VV,VH')
            report['outputs'][key]=str(path); report['output_sha256'][key]=_sha256(path)
        for role in ['pre','post']:
            path = root/'intermediate'/role; path.mkdir(parents=True)
            dem = path/'elevation.tif'
            with rasterio.open(dem,'w',driver='GTiff',**grid,count=1,dtype='float32') as dst:
                dst.write(np.full((1,16,16),1000,dtype=np.float32))
            atomic_json(path/'radiometry.verified.json',{'bands':{'elevation':str(dem)},'files':{str(dem):_sha256(dem)}})
        manifest = root/'processing_manifest.json'; atomic_json(manifest,report)
        return manifest

    def test_end_to_end_raster_vector_previews_and_unknown_holes(self):
        from trinetra.flood_detection.pipeline import run_baseline
        from trinetra.flood_detection.verify import verify_baseline
        for changed in [True,False]:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as folder:
                report = run_baseline(self.fixture(folder,changed),Path(folder)/'synthetic_baseline')
                self.assertEqual(report['status'],'complete')
                self.assertEqual(len(report['previews']),3)
                summary = verify_baseline(Path(folder)/'synthetic_baseline/baseline_manifest.json')
                self.assertGreater(summary['unknown_pixels'],0)
                if changed: self.assertGreater(summary['candidate_pixels'],0)
                else: self.assertEqual(summary['candidate_pixels'],0)
                path = Path(report['outputs']['flood_mask']); path.write_bytes(b'corrupted fixture')
                with self.assertRaisesRegex(ValueError,'checksum'): verify_baseline(Path(folder)/'synthetic_baseline/baseline_manifest.json')

    def test_changed_terrain_cache_rejected(self):
        from trinetra.flood_detection.pipeline import load_inputs
        with tempfile.TemporaryDirectory() as folder:
            manifest = self.fixture(folder)
            (manifest.parent/'intermediate/pre/elevation.tif').write_bytes(b'corrupt fixture')
            with self.assertRaisesRegex(RuntimeError,'terrain cache'): load_inputs(manifest)


if __name__=='__main__': unittest.main()
