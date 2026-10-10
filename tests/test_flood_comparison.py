import importlib.util,json,tempfile,unittest
from pathlib import Path
import numpy as np
import rasterio
from affine import Affine
spec=importlib.util.spec_from_file_location('comparison_script',Path(__file__).resolve().parents[1]/'scripts/compare_flood_outputs.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Checks(unittest.TestCase):
    def test_unknown_excluded(self):
        labels,stats=m.comparison(np.array([[1,1,0,0,1]],dtype='uint8'),np.array([[1,0,1,0,255]],dtype='uint8'))
        np.testing.assert_equal(labels,[[1,2,3,0,255]])
        self.assertEqual(stats['jointly_assessable_pixels'],4)
        self.assertAlmostEqual(stats['candidate_agreement_iou'],1/3)
    def test_geospatial_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);transform=Affine(20,0,312980,0,-20,3098660)
            arrays=[np.array([[1,1,0,0],[1,1,0,0],[1,1,1,1],[1,1,1,1]],dtype='uint8'),np.array([[1,0],[255,1]],dtype='uint8')]
            reports=[]
            for i,a in enumerate(arrays):
                path=base/(str(i)+'.tif')
                with rasterio.open(path,'w',driver='GTiff',width=a.shape[1],height=a.shape[0],count=1,dtype='uint8',nodata=255,crs='EPSG:32645',transform=transform*Affine.scale(.5,.5) if i==0 else transform) as dst:dst.write(a,1)
                key='mask' if i==0 else 'flood_mask'
                report={'case':'trishuli','status':'complete_experimental' if i==0 else 'complete','outputs':{key:str(path)},'output_sha256':{key:m.sha(path)}}
                manifest=base/(str(i)+'.json');manifest.write_text(json.dumps(report));reports.append(manifest)
            m.main(*reports,base/'output')
            result=json.loads((base/'output/comparison_report.json').read_text())
            self.assertEqual(result['statistics']['excluded_pixels'],4)
            self.assertEqual(result['statistics']['both_candidate'],8)
            with rasterio.open(base/'output/agreement.tif') as src:self.assertTrue(np.all(src.read(1)[2:,:2]==255))
            self.assertTrue((base/'output/comparison.png').is_file())
if __name__=='__main__':unittest.main()
