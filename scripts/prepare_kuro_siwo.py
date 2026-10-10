"""Verify prepared Kuro Siwo manifests; optional bounded official sample fetch."""
from pathlib import Path
import argparse
import json
import sys
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='data/kuro_siwo')
    parser.add_argument('--download-smoke',action='store_true',help='Fetch only a bounded prefix if two real samples are not already present')
    args=parser.parse_args()
    from trinetra.datasets.kuro_siwo import verify_splits,KuroSiwoDataset,ingest_prefix,REPO
    try:
        root=Path(args.root); report=verify_splits(root)
        if args.download_smoke:
            try:
                dataset=KuroSiwoDataset(root)
                cached=len(dataset)>=2
                if cached:
                    for i in range(2): dataset[i]
            except (OSError,ValueError): cached=False
            if not cached:
                snapshot=json.loads((root/'metadata/source_snapshot.json').read_text())
                rev=snapshot['hf_revision']; shard='train_GRD/shard-00000.tar'
                cap=8*1024**2
                url=f'https://huggingface.co/datasets/{REPO}/resolve/{rev}/{shard}'
                cache=root/'cache'; cache.mkdir(parents=True,exist_ok=True)
                path=cache/'smoke_prefix.tar.part'
                with urlopen(Request(url,headers={'Range':f'bytes=0-{cap-1}'}),timeout=60) as response:
                    if response.status==206 and not response.headers.get('Content-Range','').startswith('bytes 0-'):
                        raise ValueError('Unexpected download range')
                    total=0
                    with path.open('wb') as stream:
                        while total<cap:
                            chunk=response.read(min(256*1024,cap-total))
                            if not chunk:break
                            stream.write(chunk); total+=len(chunk)
                ingest_prefix(root,path,{'repo':REPO,'revision':rev,'shard':shard,'received_prefix_bytes':total},limit=2)
                path.unlink(missing_ok=True)
        print(json.dumps({'full_index':report['counts'],'event_overlap':False,
                          'note':'Counts describe metadata-indexed samples, not all downloaded imagery.'},indent=2))
        return 0
    except (OSError,ValueError,KeyError) as exc:
        print('Kuro Siwo setup stopped:',exc);return 1


if __name__=='__main__':raise SystemExit(main())
