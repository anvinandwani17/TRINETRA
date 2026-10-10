"""Independent checks of real Trishuli outputs; no image generation or processing."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Verify real preprocessing outputs and unknown-pixel policy')
    parser.add_argument('--manifest',type=Path,default=Path('data/processed/trishuli/processing_manifest.json'))
    args=parser.parse_args()
    import numpy as np
    import rasterio
    try:
        report=json.loads(args.manifest.read_text())
        if report.get('status') != 'complete':
            raise ValueError('Processing manifest is not complete')
        products={}
        reference=None
        for name,path in report['outputs'].items():
            path=Path(path)
            digest=hashlib.sha256()
            with path.open('rb') as file:
                for block in iter(lambda:file.read(4*1024*1024),b''):
                    digest.update(block)
            if digest.hexdigest() != report['output_sha256'][name]:
                raise ValueError('Output checksum mismatch: '+name)
            with rasterio.open(path) as src:
                actual=(src.crs,src.transform,src.width,src.height)
                if reference is None:
                    reference=actual
                if actual != reference or src.res != (20,20):
                    raise ValueError('Output grid mismatch: '+name)
                products[name]=src.read()
        observed=products['observability'][0]
        reasons=products['quality_reasons'][0]
        if not np.isin(observed,[0,1,255]).all():
            raise ValueError('Invalid observability codes')
        usable=observed==1
        if not usable.any() or not np.all(reasons[usable]==0):
            raise ValueError('No valid observable pixels or contradictory quality flags')
        for name in ['before_gamma0_db','after_gamma0_db','change_db']:
            data=products[name]
            if data.shape[0] != 2 or not np.isnan(data[:,~usable]).all() or not np.isfinite(data[:,usable]).all():
                raise ValueError('Radiometry/change unknown policy failed: '+name)
        expected=products['after_gamma0_db']-products['before_gamma0_db']
        np.testing.assert_allclose(products['change_db'],expected,rtol=1e-6,atol=1e-5,equal_nan=True)
        for name in ['pre_layover_shadow','post_layover_shadow']:
            mask=products[name][0]
            if not np.isin(mask,[0,1,2,3,255]).all() or not np.all(mask[usable]==0):
                raise ValueError('Shadow/layover became observable: '+name)
        for path in report['previews'].values():
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
        print('PASS: shared 20 m grid, hashes, VV/VH change, unknown policy, terrain masks and four readable PNGs')
        print('Observable pixels:',int(usable.sum()))
        for name,path in report['previews'].items():
            print(name+': '+path)
        return 0
    except (OSError,ValueError,KeyError,AssertionError) as exc:
        print('Verification failed:',exc)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
