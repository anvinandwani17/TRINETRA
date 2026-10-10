"""Event leakage, label safety and bounded metadata tests; no training."""
import io
import pickle
from pathlib import Path
import tempfile
import json
import numpy as np
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trinetra.datasets.kuro_siwo import split_assignment,PrimitiveMetadata,KuroSiwoDataset,FIELDS
from trinetra.downloads.sentinel1 import _sha256


class KuroSiwoTests(unittest.TestCase):
    def test_loader_uses_closest_pre_date_and_never_supervises_invalid_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'samples/fixture';path.mkdir(parents=True)
            arrays={field:np.ones((1,224,224),dtype=np.float32) for field in FIELDS[:-1]}
            arrays['sec1_vv.npy'][:]=2; arrays['sec2_vv.npy'][:]=3
            arrays['mask.npy'][0,0,0]=1 # permanent water is not newly flooded
            arrays['mask.npy'][0,0,1]=2
            arrays['mask.npy'][0,0,2]=3; arrays['valid_mask.npy'][0,0,2]=0
            arrays['dem.npy'][0,1,1]=np.nan
            for field,array in arrays.items():np.save(path/field,array)
            info={'actid':470,'grid_id':'fixture','flood_date':'2020-05-20',
                  'sources':{'SL1':{'source_date':'2020-05-01'},'SL2':{'source_date':'2020-05-10'},
                             'MS1':{'source_date':'2020-05-22'}}}
            (path/'info.json').write_text(json.dumps(info))
            row={'path':'samples/fixture','event_id':'470','grid_id':'fixture',
                 'sha256':{field:_sha256(path/field) for field in FIELDS}}
            dataset=KuroSiwoDataset.__new__(KuroSiwoDataset);dataset.root=root;dataset.rows=[row]
            sample=dataset[0]
            self.assertEqual(sample['pre_date'],'2020-05-10')
            self.assertEqual(float(sample['sar_linear'][0,2,2]),3)
            self.assertEqual(sample['flood_target'][0,0],0)
            self.assertEqual(sample['flood_target'][0,1],1)
            self.assertEqual(sample['flood_target'][0,2],255)
            self.assertEqual(sample['labels'][1,1],255)
            self.assertTrue((sample['sar_linear'][:,0,2]==0).all())
            (path/'info.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'checksum'):dataset[0]

    def test_whole_event_partition_and_empty_entry(self):
        config={'train_acts':[1,2],'val_acts':[3,99],'test_acts':[4]}
        assignment,empty=split_assignment(config,['1','2','3','4'])
        self.assertEqual(assignment,{'1':'train','2':'train','3':'validation','4':'test'})
        self.assertEqual(empty,['99'])

    def test_event_overlap_or_missing_assignment_stops(self):
        with self.assertRaisesRegex(ValueError,'leakage'):
            split_assignment({'train_acts':[1],'val_acts':[2],'test_acts':[1]},['1','2'])
        with self.assertRaisesRegex(ValueError,'Unassigned'):
            split_assignment({'train_acts':[1],'val_acts':[2],'test_acts':[3]},['1','2','3','4'])

    def test_metadata_rejects_globals_but_accepts_literal_containers(self):
        data={'event':1,'samples':['a','b'],'valid':True}
        self.assertEqual(PrimitiveMetadata(io.BytesIO(pickle.dumps(data))).load(),data)
        with self.assertRaisesRegex(ValueError,'Non-primitive'):
            PrimitiveMetadata(io.BytesIO(b'cos\nsystem\n.')).load()


if __name__=='__main__':unittest.main()
