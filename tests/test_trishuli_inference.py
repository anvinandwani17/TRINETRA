"""Synthetic fixtures only; no trained predictions or real case evaluation."""
import json,tempfile,unittest
from pathlib import Path
import numpy as np
import torch
import rasterio
from affine import Affine
from trinetra.inference.trishuli import normalize_linear,tiled_scores,run_case
from trinetra.models.siamese_unet import SiameseUNet
from trinetra.models.checkpoint import EXPECTED_TRANSFORM
from trinetra.preprocessing.ai_sigma0 import write_raster
from trinetra.core.files import _sha256

class Checks(unittest.TestCase):
    def test_normalization_and_unknown(self):
        pre=np.full((2,9,13),.01,dtype='float32');post=pre.copy();obs=np.ones((9,13),dtype='uint8')
        obs[0,0]=0;obs[0,1]=255;pre[0,1,1]=0
        x,valid=normalize_linear(pre,post,obs)
        self.assertAlmostEqual(float(x[0,2,2]),-.25,places=6)
        self.assertFalse(valid[0,0]);self.assertFalse(valid[0,1]);self.assertFalse(valid[1,1])
        self.assertTrue(np.all(x[:,~valid]==0))
        class Constant(torch.nn.Module):
            def forward(self,x):return torch.zeros((x.shape[0],1,*x.shape[-2:]))
        scores=tiled_scores(Constant(),x,valid,tile=8,stride=4)
        self.assertTrue(np.all(scores[valid]==.5));self.assertTrue(np.isnan(scores[~valid]).all())

    def test_fixture_roundtrip_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);grid={'crs':rasterio.crs.CRS.from_epsg(32645),'transform':Affine(10,0,312980,0,-10,3098660),'height':8,'width':8}
            obs=np.ones((8,8),dtype='uint8');obs[0,0]=0;obs[0,1]=255
            arrays=np.full((2,8,8),.01,dtype='float32');arrays[:,obs!=1]=np.nan
            outputs={}
            for key in ['before_sigma0_linear','after_sigma0_linear']:
                outputs[key]=write_raster(base/(key+'.tif'),arrays,grid,['VV_linear','VH_linear'],np.nan)
            outputs['observability']=write_raster(base/'obs.tif',obs,grid,['pair_observability'],255)
            parent={'case':'trishuli','status':'complete_experimental','radiometry':'linear Sigma0; fixture only','resolution_m':10,'outputs':outputs,'output_sha256':{k:_sha256(p) for k,p in outputs.items()},'source_pair':{'fixture_only':True},'limitations':['Synthetic unit fixture; not a real case result']}
            manifest=base/'manifest.json';manifest.write_text(json.dumps(parent))
            model=SiameseUNet(4)
            config={'mode':'pilot','base_channels':4}
            data={'train_events':['fixture_train'],'validation_events':['fixture_validation'],'test_used':False,'excluded':['Trishuli','EMSR927']}
            checkpoint={'schema_version':1,'model_state':model.state_dict(),'config':config,'transform':EXPECTED_TRANSFORM,'threshold':.5,'data':data,'epoch':0}
            torch.save(checkpoint,base/'fixture.pt')
            external={k:checkpoint[k] for k in ['config','data','transform','threshold']};(base/'config.json').write_text(json.dumps(external))
            report=run_case(manifest,base/'fixture.pt',base/'config.json',base/'inference')
            self.assertEqual(report['status'],'complete_experimental');self.assertFalse(report['threshold_tuned'])
            with rasterio.open(report['outputs']['score']) as src:
                self.assertTrue(np.isnan(src.read(1)[0,:2]).all());self.assertEqual(src.transform,grid['transform'])
            with rasterio.open(report['outputs']['mask']) as src:self.assertTrue(np.all(src.read(1)[0,:2]==255))
            first=_sha256(report['outputs']['score'])
            again=run_case(manifest,base/'fixture.pt',base/'config.json',base/'inference')
            self.assertEqual(first,again['output_sha256']['score'])
            self.assertTrue(all(_sha256(p)==parent['output_sha256'][k] for k,p in outputs.items()))
            with rasterio.open(outputs['before_sigma0_linear'],'r+') as src:
                changed=src.read();changed[:,2,2]=.02;src.write(changed)
            with self.assertRaisesRegex(ValueError,'checksum changed'):
                run_case(manifest,base/'fixture.pt',base/'config.json',base/'inference')

if __name__=='__main__':unittest.main()
