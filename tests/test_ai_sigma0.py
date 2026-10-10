"""Synthetic unit checks only; these do not validate real SNAP processing."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import rasterio
from affine import Affine
from trinetra.preprocessing.ai_sigma0 import sigma_graph, aligned_sigma, write_raster

class SigmaChecks(unittest.TestCase):
    def test_graph(self):
        root=ET.Element('graph')
        for i,op in enumerate(['Read','Apply-Orbit-File','ThermalNoiseRemoval','Calibration','Multilook','Terrain-Flattening','Terrain-Correction','Write']):
            node=ET.SubElement(root,'node',id=str(i));ET.SubElement(node,'operator').text=op
            ET.SubElement(node,'sources');p=ET.SubElement(node,'parameters')
            if op=='Terrain-Correction':ET.SubElement(p,'demName').text='Copernicus 30m Global DEM'
        changed=ET.fromstring(sigma_graph(ET.tostring(root),'/tmp/sigma.dim'))
        nodes=changed.findall('node');ops=[n.findtext('operator') for n in nodes]
        self.assertNotIn('Multilook',ops);self.assertNotIn('Terrain-Flattening',ops)
        calibration=nodes[ops.index('Calibration')]
        self.assertEqual(calibration.findtext('parameters/outputSigmaBand'),'true')
        self.assertEqual(calibration.findtext('parameters/outputBetaBand'),'false')
        filt=nodes[ops.index('Speckle-Filter')]
        self.assertEqual(filt.findtext('parameters/windowSize'),'7x7')
        tc=nodes[ops.index('Terrain-Correction')]
        self.assertEqual(tc.findtext('parameters/pixelSpacingInMeter'),'10')
        self.assertEqual(tc.findtext('parameters/demName'),'Copernicus 30m Global DEM')
        for previous,current in zip(nodes,nodes[1:]):
            self.assertEqual(current.find('sources/sourceProduct').attrib['refid'],previous.attrib['id'])

    def test_grid_mask_and_geotiff(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);transform=Affine(20,0,312980,0,-20,3098660)
            with rasterio.open(folder/'mask.tif','w',driver='GTiff',count=1,width=2,height=2,dtype='uint8',crs='EPSG:32645',transform=transform,nodata=255) as dst:
                dst.write(np.array([[1,0],[255,1]],dtype='uint8'),1)
            bands={}
            for name in ['Sigma0_VV','Sigma0_VH']:
                bands[name]=folder/(name+'.tif')
                with rasterio.open(bands[name],'w',driver='GTiff',count=1,width=4,height=4,dtype='float32',crs='EPSG:32645',transform=transform*Affine.scale(.5,.5),nodata=0) as dst:
                    values=np.full((4,4),.1,dtype='float32');values[3,3]=0;dst.write(values,1)
            arrays,mask,grid=aligned_sigma(bands,folder/'mask.tif')
            self.assertEqual(arrays.shape,(2,4,4));self.assertEqual(grid['transform'].a,10)
            self.assertTrue(np.all(mask[:2,2:]==0));self.assertTrue(np.all(mask[2:,:2]==255))
            self.assertTrue(np.isnan(arrays[:,3,3]).all())
            usable=(mask==1)&np.all(np.isfinite(arrays)&(arrays>0),axis=0)
            arrays[:,~usable]=np.nan
            write_raster(folder/'output.tif',arrays,grid,['VV_linear','VH_linear'],np.nan)
            with rasterio.open(folder/'output.tif') as src:
                self.assertEqual(src.descriptions,('VV_linear','VH_linear'))
                np.testing.assert_equal(src.read(),arrays)
                self.assertTrue(np.isnan(src.read()[:,mask==0]).all())

if __name__=='__main__':unittest.main()
