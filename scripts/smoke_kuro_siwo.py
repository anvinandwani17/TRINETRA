"""Load exactly one real Kuro Siwo batch; never initialize/train a model."""
from pathlib import Path
import argparse
import importlib.util
import json
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='data/kuro_siwo')
    parser.add_argument('--batch-size',type=int,default=2)
    parser.add_argument('--engine',choices=['auto','numpy','torch'],default='auto')
    args=parser.parse_args()
    from trinetra.datasets.kuro_siwo import KuroSiwoDataset,load_batch,torch_loader,CHANNELS
    from trinetra.core.files import atomic_json
    try:
        dataset=KuroSiwoDataset(args.root,'train')
        if not 1<=args.batch_size<=min(8,len(dataset)):raise ValueError('Use an available batch size from 1 to 8')
        engine=args.engine
        if engine=='auto':engine='torch' if importlib.util.find_spec('torch') else 'numpy'
        if engine=='torch':
            batch=next(iter(torch_loader(dataset,args.batch_size)))
            arrays={k:batch[k].numpy() for k in ['sar_linear','dem_m','labels','flood_target','valid_mask']}
            events=batch['event_id']
        else:
            batch=load_batch(dataset,args.batch_size);arrays=batch;events=batch['event_ids']
        valid=arrays['valid_mask']
        if not valid.any() or not np.isfinite(arrays['sar_linear']).all() or not np.isfinite(arrays['dem_m']).all():
            raise ValueError('Batch contains unusable/nonfinite inputs')
        if not np.all(arrays['labels'][~valid]==255) or not np.all(arrays['flood_target'][~valid]==255):
            raise ValueError('Invalid pixels became supervised labels')
        if arrays['sar_linear'].shape!=(args.batch_size,4,224,224):raise ValueError('Wrong batch/channel shape')
        report={'status':'passed','data':'real official Kuro Siwo GRD samples','engine':engine,'split':'train',
                'event_ids':list(events),'channels':CHANNELS,'dem':'meters; separate channel',
                'shapes':{k:list(arrays[k].shape) for k in ['sar_linear','dem_m','labels','flood_target','valid_mask']},
                'dtypes':{k:str(arrays[k].dtype) for k in ['sar_linear','dem_m','labels','flood_target','valid_mask']},
                'label_counts':{str(int(v)):int((arrays['labels']==v).sum()) for v in np.unique(arrays['labels'])},
                'valid_pixels':int(valid.sum()),'invalid_pixels':int((~valid).sum()),
                'training_started':False}
        atomic_json(Path(args.root)/'reports/smoke_batch.json',report)
        print(json.dumps(report,indent=2));return 0
    except (OSError,ValueError,KeyError,ImportError) as exc:
        print('Kuro Siwo batch test failed:',exc);return 1


if __name__=='__main__':raise SystemExit(main())
