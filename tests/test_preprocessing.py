"""Synthetic, tiny numerical/transport fixtures only; not Trishuli imagery."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import numpy as np

from trinetra.preprocessing.quality import pair_quality, db_change, dilate
from trinetra.preprocessing.snap import build_graph, single_band_export_graph, border_noise_policy, check_calibration_log, snap_processing_metadata
from trinetra.preprocessing.io import bands_from_dim, physical_mask_band
from trinetra.preprocessing.metadata import restore_ipf_identifier, assert_ipf_identifier, preparation_graph, prepare_source
from trinetra.preprocessing.pipeline import checked_pair
from trinetra.downloads.sentinel1 import DownloadError, _sha256

GEO = all(importlib.util.find_spec(m) for m in ['rasterio','pyproj','shapely'])


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.pre = np.ones((2,5,5),dtype=np.float32)*.1
        self.post = self.pre*.5
        self.mask = np.zeros((5,5),dtype=np.uint8)
        self.valid = np.ones((5,5),dtype=bool)

    def quality(self,**kwargs):
        values = dict(pre=self.pre,post=self.post,pre_mask=self.mask,post_mask=self.mask,
                      geometry_valid=self.valid,inside=self.valid,radius=0)
        values.update(kwargs)
        return pair_quality(**values)

    def test_known_radiometric_change_and_band_order(self):
        observed,reasons,usable = self.quality()
        before,after,change = db_change(self.pre,self.post,usable)
        self.assertTrue(np.all(observed == 1)); self.assertTrue(np.all(reasons == 0))
        np.testing.assert_allclose(before,-10,atol=1e-5)
        np.testing.assert_allclose(change,10*np.log10(.5),atol=1e-5)
        self.post[1] = self.pre[1]*2
        _,_,change = db_change(self.pre,self.post,usable)
        self.assertGreater(change[1,0,0],0); self.assertLess(change[0,0,0],0)

    def test_pre_and_post_shadow_and_layover_are_always_unknown(self):
        for code in [1,2,3]:
            for role in ['pre_mask','post_mask']:
                flags = self.mask.copy(); flags[2,2] = code
                observed,reasons,usable = self.quality(**{role:flags})
                self.assertEqual(observed[2,2],0); self.assertFalse(usable[2,2])
                before,after,change = db_change(self.pre,self.post,usable)
                self.assertTrue(np.all(np.isnan(change[:,2,2])))
                self.assertTrue(np.all(np.isnan(before[:,2,2])))

    def test_either_pol_zero_negative_nan_missing_is_unknown(self):
        for value in [0,-1,np.nan,np.inf]:
            for role in ['pre','post']:
                data = self.pre.copy(); data[1,1,1] = value
                observed,reasons,usable = self.quality(**{role:data})
                self.assertEqual(observed[1,1],0)
                self.assertNotEqual(reasons[1,1],0)
                self.assertFalse(usable[1,1])

    def test_unknown_mask_codes_and_dem_holes_are_not_dry(self):
        for value in [255,4,13,np.nan]:
            flags = self.mask.astype(np.float32); flags[0,0] = value
            observed,reasons,_ = self.quality(pre_mask=flags)
            self.assertEqual(observed[0,0],0); self.assertTrue(reasons[0,0] & 64)
        validity = self.valid.copy(); validity[1,1] = False
        observed,reasons,_ = self.quality(geometry_valid=validity)
        self.assertEqual(observed[1,1],0); self.assertTrue(reasons[1,1] & 64)

    def test_quality_buffer_and_outside_aoi_do_not_become_dry(self):
        flags = self.mask.copy(); flags[2,2] = 2
        inside = self.valid.copy(); inside[0,0] = False
        observed,reasons,usable = self.quality(pre_mask=flags,inside=inside,radius=1)
        self.assertEqual(observed[0,0],255)
        self.assertTrue(reasons[0,0] & 128)
        self.assertEqual(np.count_nonzero(observed == 0),9)
        self.assertTrue(reasons[1,1] & 256)
        self.assertFalse(usable[1,1])

    def test_dilation_does_not_wrap_edges(self):
        flags = self.mask.astype(bool); flags[0,0] = True
        expanded = dilate(flags)
        self.assertEqual(expanded.sum(),4)
        self.assertFalse(expanded[-1,-1])

    def test_shape_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.quality(post=self.post[:,:,:-1])

    def test_log_floor_is_never_used(self):
        pre = self.pre.copy(); pre[0,0,0] = 0
        with self.assertRaises(ValueError):
            db_change(pre,self.post,self.valid)


class GraphAndIdentityTests(unittest.TestCase):
    def test_physical_mask_names_allow_only_explicit_snap_variants(self):
        for name in ['layover_shadow_mask', 'layover_shadow_mask_VV', 'layover_shadow_mask_VH']:
            with self.subTest(name=name):
                self.assertEqual(physical_mask_band({name:Path('mask.img'), 'elevation':Path('dem.img')}), name)
        for bands in [{}, {'elevation':Path('dem.img')}, {'dark_water_mask':Path('fake.img')},
                      {'layover_shadow_mask_HH':Path('unsupported.img')},
                      {'layover_shadow_mask_VV':Path('vv.img'), 'layover_shadow_mask_VH':Path('vh.img')}]:
            with self.subTest(bands=bands), self.assertRaisesRegex(DownloadError,'Missing or ambiguous'):
                physical_mask_band(bands)

    def test_cog_identifier_restored_from_raw_ipf_without_changing_luts_or_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); raw = root/'raw.zip'; dim = root/'source.dim'
            with zipfile.ZipFile(raw, 'w') as archive:
                archive.writestr('fixture.SAFE/manifest.safe', '<root><software name="Sentinel-1 IPF" version="004.03"/></root>')
            original = ('<root><MDElem name="Abstracted_Metadata"><MDATTR name="Processing_system_identifier">'
                        'CloudFerro Sentinel-1 COGifier 001.00</MDATTR><MDATTR name="subset_offset_x">250</MDATTR>'
                        '</MDElem><MDElem name="Original_Product_Metadata"><MDATTR name="betaNought">'
                        '2.5 3.5</MDATTR></MDElem></root>')
            dim.write_text(original)
            raw_hash = _sha256(raw)
            report = restore_ipf_identifier(dim, raw)
            self.assertEqual(report['ipf_version'], '004.03')
            self.assertEqual(_sha256(raw), raw_hash)
            assert_ipf_identifier(dim, '004.03')
            changed = ET.parse(dim)
            self.assertEqual(changed.find('.//MDATTR[@name="subset_offset_x"]').text, '250')
            self.assertEqual(changed.find('.//MDATTR[@name="betaNought"]').text, '2.5 3.5')
            self.assertEqual(dim.with_suffix('.reader_original.xml').read_text(), original)
            with self.assertRaisesRegex(DownloadError, 'retain'):
                assert_ipf_identifier(dim, '004.04')

    def test_unknown_reader_identifier_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); raw = root/'raw.zip'; dim = root/'source.dim'
            with zipfile.ZipFile(raw, 'w') as archive:
                archive.writestr('fixture.SAFE/manifest.safe', '<root><software name="Sentinel-1 IPF" version="004.03"/></root>')
            dim.write_text('<root><MDElem name="Abstracted_Metadata"><MDATTR name="Processing_system_identifier">'
                           'Unknown Processor 1.0</MDATTR></MDElem></root>')
            before = dim.read_bytes()
            with self.assertRaisesRegex(DownloadError, 'Unexpected'):
                restore_ipf_identifier(dim, raw)
            self.assertEqual(dim.read_bytes(), before)

    def test_preparation_runs_no_version_sensitive_operator_and_no_second_subset(self):
        graph = ET.fromstring(preparation_graph('raw.zip','source.dim','POLYGON ((85 28,85.1 28,85.1 28.1,85 28))'))
        self.assertEqual([n.findtext('operator') for n in graph.findall('node')], ['Read','Subset','Write'])
        self.assertEqual(graph.find('.//copyMetadata').text, 'true')
        corrected = ET.fromstring(build_graph('source.dim','out.dim','unused','EPSG:32645',
                                             prepared_source=True,border_noise_required=False))
        operators = [n.findtext('operator') for n in corrected.findall('node')]
        self.assertNotIn('Subset', operators)
        self.assertLess(operators.index('ThermalNoiseRemoval'), operators.index('Calibration'))

    def test_prepared_cache_reused_but_modified_data_requires_reprocessing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); raw = root/'raw.zip'; dim = root/'source.dim'
            with zipfile.ZipFile(raw, 'w') as archive:
                archive.writestr('fixture.SAFE/manifest.safe', '<root><software name="Sentinel-1 IPF" version="004.03"/></root>')
            def fake_gpt(*args, **kwargs):
                for pol in ['VV','VH']:
                    (root/f'Amplitude_{pol}.hdr').write_text('ENVI\n')
                    (root/f'Amplitude_{pol}.img').write_bytes(b'tiny synthetic unit fixture')
                dim.write_text('<root><MDElem name="Abstracted_Metadata"><MDATTR name="Processing_system_identifier">'
                    'CloudFerro Sentinel-1 COGifier 001.00</MDATTR></MDElem>'
                    '<DATA_FILE_PATH href="Amplitude_VV.hdr"/><DATA_FILE_PATH href="Amplitude_VH.hdr"/></root>')
                from subprocess import CompletedProcess
                return CompletedProcess([], 0)
            with patch('trinetra.preprocessing.metadata.subprocess.run', side_effect=fake_gpt) as run:
                for _ in range(2):
                    prepare_source('fixture-gpt',raw,dim,'fixture-region',_sha256(raw),cache_mb=256,threads=1)
                self.assertEqual(run.call_count, 1)
                (root/'Amplitude_VV.img').write_bytes(b'changed fixture')
                prepare_source('fixture-gpt',raw,dim,'fixture-region',_sha256(raw),cache_mb=256,threads=1)
                self.assertEqual(run.call_count, 2)

    def test_ipf_threshold_reads_original_software_not_cog_conversion(self):
        for version,required in [('002.82',True),('002.90',False),('003.71',False),('004.00',False)]:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'fixture.zip'
                with zipfile.ZipFile(path,'w') as archive:
                    archive.writestr('TEST_COG.SAFE/manifest.safe',f'<root><software name="Sentinel-1 IPF" version="{version}"/><software name="GDAL COG conversion" version="3.9.0"/></root>')
                policy=border_noise_policy(path)
                self.assertEqual(policy['ipf_version'],version)
                self.assertEqual(policy['remove_grd_border_noise'],required)

    def test_unknown_or_conflicting_ipf_never_silently_skips_border_processing(self):
        for xml in ['<root/>','<root><software name="Sentinel-1 IPF" version="unknown"/></root>',
                    '<root><software name="Sentinel-1 IPF" version="002.82"/><software name="Sentinel-1 IPF" version="004.00"/></root>']:
            with self.subTest(xml=xml),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'fixture.zip'
                with zipfile.ZipFile(path,'w') as archive:
                    archive.writestr('TEST.SAFE/manifest.safe',xml)
                with self.assertRaises(DownloadError):
                    border_noise_policy(path)

    def test_modern_ipf_graph_omits_only_legacy_border_operator(self):
        for mask in [False,True]:
            root=ET.fromstring(build_graph('raw.zip','out.dim','POLYGON ((85 28,85.1 28,85.1 28.1,85 28))','EPSG:32645',
                                         border_noise_required=False,mask=mask))
            nodes={n.attrib['id']:n for n in root.findall('node')}
            self.assertNotIn('border',nodes)
            self.assertEqual(nodes['noise'].find('sources/sourceProduct').attrib['refid'],'orbit')
            self.assertEqual(nodes['noise'].findtext('parameters/removeThermalNoise'),'true')
            self.assertEqual(nodes['calibrate'].findtext('parameters/outputBetaBand'),'true')
            self.assertIn('geocode',nodes)
            self.assertIn('simulation' if mask else 'flatten',nodes)

    def test_single_band_export_uses_snap_geocoding_not_assigned_crs(self):
        root = ET.fromstring(single_band_export_graph('terrain.dim','band.tif','Gamma0_VV'))
        self.assertEqual(root.findtext("node[@id='select']/parameters/sourceBands"),'Gamma0_VV')
        self.assertEqual(root.findtext("node[@id='write']/parameters/formatName"),'GeoTIFF')
        self.assertIsNone(root.find('.//mapProjection'))
    def test_radiometry_graph_uses_beta0_flattening_20m_and_no_double_normalization(self):
        root = ET.fromstring(build_graph('raw.zip','out.dim','POLYGON ((85 28,85.1 28,85.1 28.1,85 28))','EPSG:32645'))
        nodes = {n.attrib['id']:n for n in root.findall('node')}
        self.assertEqual(nodes['calibrate'].findtext('parameters/outputBetaBand'),'true')
        self.assertEqual(nodes['flatten'].findtext('operator'),'Terrain-Flattening')
        self.assertEqual(nodes['geocode'].findtext('parameters/sourceBands'),'Gamma0_VV,Gamma0_VH')
        self.assertEqual(nodes['geocode'].findtext('parameters/pixelSpacingInMeter'),'20.0')
        self.assertEqual(nodes['geocode'].findtext('parameters/applyRadiometricNormalization'),'false')
        self.assertEqual(nodes['orbit'].findtext('parameters/continueOnFail'),'false')
        self.assertEqual(nodes['noise'].findtext('parameters/removeThermalNoise'),'true')

    def test_mask_comes_from_sar_simulation_and_uses_nearest_neighbour(self):
        root = ET.fromstring(build_graph('raw.zip','mask.dim','POLYGON ((85 28,85.1 28,85.1 28.1,85 28))','EPSG:32645',mask=True))
        nodes = {n.attrib['id']:n for n in root.findall('node')}
        self.assertEqual(nodes['simulation'].findtext('parameters/saveLayoverShadowMask'),'true')
        self.assertEqual(nodes['geocode'].findtext('parameters/imgResamplingMethod'),'NEAREST_NEIGHBOUR')
        self.assertEqual(nodes['geocode'].findtext('parameters/sourceBands'),'layover_shadow_mask')
        self.assertNotIn('flatten',nodes)

    def test_external_ellipsoidal_dem_never_applies_egm_twice(self):
        root = ET.fromstring(build_graph('raw.zip','out.dim','POLYGON ((85 28,85.1 28,85.1 28.1,85 28))','EPSG:32645',dem='dem.tif'))
        self.assertEqual(root.find("node[@id='flatten']/parameters/externalDEMApplyEGM").text,'false')

    def test_unfinished_download_is_rejected(self):
        with self.assertRaisesRegex(DownloadError,'Finish and verify'):
            checked_pair({'case':'trishuli','flood_date':'2026-08-26','status':'failed'})

    def test_raw_cache_identity_and_hash_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = {'case':'trishuli','flood_date':'2026-08-26','status':'complete','pair':{},'products':{},'resolved_metadata':{}}
            for role in ['pre','post']:
                path = Path(folder)/(role+'.zip'); path.write_bytes((role+'-test-fixture-not-a-scene').encode())
                name = 'S1A_'+role+'.SAFE'
                manifest['pair'][role+'_id'] = name
                manifest['products'][role] = {'name':name,'product_uuid':role,'path':str(path),
                    'verification':{'status':'valid','bytes':path.stat().st_size,'sha256':_sha256(path)}}
                manifest['resolved_metadata'][role] = {'Name':name,'Id':role}
            self.assertEqual(len(checked_pair(manifest)),2)
            Path(manifest['products']['pre']['path']).write_bytes(b'altered')
            with self.assertRaisesRegex(DownloadError,'changed'):
                checked_pair(manifest)

    def test_dimap_paths_cannot_escape_output_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); outside = root/'outside.img'; outside.write_bytes(b'fixture')
            sub = root/'sub'; sub.mkdir(); path=sub/'test.dim'
            path.write_text('<root><DATA_FILE_PATH href="../outside.img"/></root>')
            with self.assertRaisesRegex(DownloadError,'escapes'):
                bands_from_dim(path)

    def test_dimap_header_resolves_data_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = root/'radiometry.data'; data.mkdir()
            (data/'Gamma0_VV.hdr').write_text('ENVI\n')
            (data/'Gamma0_VV.img').write_bytes(b'unit fixture, not satellite data')
            dim = root/'radiometry.dim'
            dim.write_text('<root><DATA_FILE_PATH href="radiometry.data/Gamma0_VV.hdr"/>'
                           '<DATA_FILE_PATH href="radiometry.data/Gamma0_VV.img"/></root>')
            bands = bands_from_dim(dim)
            self.assertEqual(set(bands), {'Gamma0_VV'})
            # Windows may expand a temporary folder's 8.3 alias (ARUNKU~1)
            # during resolve(). Verify file identity instead of path spelling.
            self.assertTrue(bands['Gamma0_VV'].samefile(data/'Gamma0_VV.img'))

    def test_dimap_header_without_data_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'Gamma0_VV.hdr').write_text('ENVI\n')
            dim = root/'radiometry.dim'
            dim.write_text('<root><DATA_FILE_PATH href="Gamma0_VV.hdr"/></root>')
            with self.assertRaisesRegex(DownloadError, 'companion'):
                bands_from_dim(dim)

    def test_calibration_warning_blocks_certification_and_records_identifier(self):
        with tempfile.TemporaryDirectory() as folder:
            dim = Path(folder)/'radiometry.dim'
            dim.write_text('<root><MDATTR name="Processing_system_identifier">Converter 1.0</MDATTR></root>')
            dim.with_suffix('.log').write_text('WARNING: The calibration LUT for this product could be incorrect and therefore the calibration result may not be reliable.')
            with self.assertRaisesRegex(DownloadError, 'calibration needs investigation'):
                check_calibration_log(dim)
            record = json.loads(dim.with_suffix('.calibration_diagnostic.json').read_text())
            self.assertEqual(record['status'], 'calibration_unverified')
            self.assertEqual(record['snap_processing_identifiers'][0]['value'], 'Converter 1.0')
            dim.with_suffix('.log').write_text('Executing processing graph... done.')
            check_calibration_log(dim)



@unittest.skipUnless(GEO,'Real GeoTIFF/grid checks require Rasterio, Shapely, and pyproj')
class GeoTests(unittest.TestCase):
    def test_trishuli_20m_grid_is_utm45_and_snapped(self):
        from trinetra.preprocessing.io import common_grid
        geometry = json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'trishuli_satellite.geojson').read_text(encoding='utf-8-sig'))['geometry']
        grid,inside,context = common_grid(geometry)
        self.assertEqual(grid['crs'],'EPSG:32645')
        self.assertEqual(grid['transform'].a,20)
        self.assertEqual(grid['transform'].e,-20)
        self.assertEqual(grid['transform'].c % 20,0)
        self.assertEqual(grid['transform'].f % 20,0)
        self.assertGreater(inside.sum(),0)

    def test_geotiff_unknown_pixels_and_grid_roundtrip(self):
        import rasterio
        from rasterio.transform import from_origin
        from trinetra.preprocessing.io import write_tif
        grid = {'crs':'EPSG:32645','transform':from_origin(300000,3100000,20,20),'width':3,'height':3}
        data=np.ones((2,3,3),dtype=np.float32); data[:,1,1]=np.nan
        with tempfile.TemporaryDirectory() as folder:
            path=write_tif(Path(folder)/'fixture.tif',data,grid,descriptions=['VV_dB','VH_dB'])
            with rasterio.open(path) as src:
                self.assertEqual(src.res,(20,20)); self.assertEqual(src.descriptions,('VV_dB','VH_dB'))
                self.assertTrue(np.isnan(src.read()[:,1,1]).all())
                self.assertTrue(src.read(masked=True).mask[:,1,1].all())

    def test_mask_zero_is_usable_code_not_nodata_and_missing_mask_fails(self):
        import rasterio
        from rasterio.transform import from_origin
        from trinetra.preprocessing.io import aligned_scene
        grid={'crs':'EPSG:32645','transform':from_origin(300000,3100000,20,20),'width':3,'height':3}
        with tempfile.TemporaryDirectory() as folder:
            bands={}
            for name,value in [('Gamma0_VV',.1),('Gamma0_VH',.05),('elevation',1000),('localIncidenceAngle',35),('layover_shadow_mask',0)]:
                path=Path(folder)/(name+'.tif')
                with rasterio.open(path,'w',driver='GTiff',**grid,count=1,dtype='float32',nodata=0) as dst:
                    dst.write(np.full((1,3,3),value,dtype=np.float32))
                bands[name]=path
            data,flags,valid=aligned_scene(bands,bands,grid)
            self.assertTrue(np.isfinite(data).all()); self.assertTrue((flags==0).all()); self.assertTrue(valid.all())
            with self.assertRaisesRegex(DownloadError,'Missing or ambiguous physical'):
                aligned_scene(bands,{},grid)


if __name__ == '__main__':
    unittest.main()
