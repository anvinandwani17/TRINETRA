"""Package existing case/model files or fetch a trusted hash-checked asset bundle.

No discovery, inference, raw-scene download or training is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import urllib.parse
import urllib.request
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value):
    p = PurePosixPath(value)
    if (not value or p.is_absolute() or '..' in p.parts or '\\' in value
            or ':' in value or p.parts[0] not in {'data', 'artifacts'}):
        raise ValueError('Unsafe asset path: ' + value)
    return p


def package(case_dir, model_dir, output):
    case_dir, model_dir, output = map(lambda p: Path(p).resolve(), (case_dir, model_dir, output))
    required = ['processing_manifest.json', 'observability.tif',
                'baseline/baseline_manifest.json', 'baseline/flood_mask.tif',
                'baseline/evidence_score.tif', 'baseline/flood_candidates.geojson',
                'ai_flood_experimental/inference_manifest.json',
                'ai_flood_experimental/flood_candidates.tif',
                'ai_flood_experimental/flood_score.tif',
                'ai_flood_experimental/flood_candidates.geojson',
                'comparison/agreement.tif', 'comparison/comparison_report.json']
    sources = []
    for name in required:
        p = case_dir / name
        if not p.is_file(): raise ValueError('Required release file is missing: ' + str(p))
    for name in ['best.pt', 'run_config.json', 'history.json']:
        if not (model_dir / name).is_file(): raise ValueError('Required model file is missing: ' + name)
    names = set(required)
    for pattern in ['previews/*.png', 'baseline/previews/*.png', 'ai_flood_experimental/*.png', 'comparison/*.png']:
        names.update(p.relative_to(case_dir).as_posix() for p in case_dir.glob(pattern) if p.is_file())
    for name in sorted(names): sources.append((case_dir / name, 'data/processed/trishuli/' + name))
    for name in ['best.pt', 'run_config.json', 'history.json']:
        sources.append((model_dir / name, 'artifacts/models/siamese_v1/pilot/' + name))
    if output.exists(): raise ValueError('Output already exists; choose a new release directory')
    entries = [{'path': target, 'size_bytes': source.stat().st_size, 'sha256': sha256(source)} for source, target in sources]
    output.mkdir(parents=True)
    archive = output / 'TRINETRA_case_assets.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for source, target in sources: z.write(source, target)
    manifest = {'schema_version': 1, 'case': 'trishuli',
                'bundle_filename': archive.name, 'bundle_sha256': sha256(archive),
                'bundle_size_bytes': archive.stat().st_size, 'files': entries,
                'scope': 'Saved outputs/model only; raw scenes and fresh SNAP/AI preprocessing inputs are not included'}
    (output / 'assets_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Created:', archive)
    print('Created:', output / 'assets_manifest.json')
    print('Bundle SHA256:', manifest['bundle_sha256'])
    print('Publish these files only after checking source redistribution terms; no publication was performed.')


def fetch(manifest_path, bundle_url, repo):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    if manifest.get('schema_version') != 1: raise ValueError('Unsupported asset manifest')
    parsed = urllib.parse.urlsplit(bundle_url)
    if parsed.scheme != 'https': raise ValueError('Use an HTTPS release URL')
    size = manifest['bundle_size_bytes']
    if not isinstance(size, int) or not 0 < size <= 8 * 1024**3: raise ValueError('Invalid bundle size (maximum 8 GiB)')
    repo = Path(repo).resolve()
    if not (repo / 'pyproject.toml').is_file(): raise ValueError('Choose the existing repository root')
    entries = manifest['files']; seen = set()
    for item in entries:
        rel = safe_relative(item['path'])
        if item['path'] in seen: raise ValueError('Duplicate asset path')
        seen.add(item['path'])
        if not isinstance(item['size_bytes'], int) or item['size_bytes'] < 0: raise ValueError('Invalid asset size')
        target = repo.joinpath(*rel.parts)
        if not target.resolve().is_relative_to(repo): raise ValueError('Asset escapes repository')
        if target.exists() and (not target.is_file() or sha256(target) != item['sha256']):
            raise ValueError('Existing asset differs; no files replaced: ' + item['path'])
    with tempfile.TemporaryDirectory(prefix='trinetra-assets-') as temp:
        temp = Path(temp); archive = temp / 'bundle.zip'
        with urllib.request.urlopen(bundle_url, timeout=120) as response, archive.open('wb') as stream:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > size: raise ValueError('Bundle exceeds manifest size')
                stream.write(block)
        if archive.stat().st_size != size or sha256(archive) != manifest['bundle_sha256']:
            raise ValueError('Bundle size/hash mismatch; nothing installed')
        staged = []
        with zipfile.ZipFile(archive) as z:
            if len(z.namelist()) != len(seen) or set(z.namelist()) != seen: raise ValueError('Archive file list differs')
            for index, item in enumerate(entries):
                info = z.getinfo(item['path'])
                if info.file_size != item['size_bytes']: raise ValueError('Asset size differs')
                path = temp / str(index)
                with z.open(info) as source, path.open('wb') as destination:
                    digest = hashlib.sha256(); total = 0
                    while block := source.read(1024 * 1024):
                        total += len(block)
                        if total > item['size_bytes']: raise ValueError('Expanded asset size exceeded')
                        digest.update(block); destination.write(block)
                if total != item['size_bytes'] or digest.hexdigest() != item['sha256']:
                    raise ValueError('Asset hash differs; nothing installed')
                staged.append((path, repo.joinpath(*safe_relative(item['path']).parts), item['sha256']))
        # Everything has been validated before any repository write.
        for source, target, expected in staged:
            if target.exists():
                if sha256(target) != expected: raise ValueError('Asset changed during fetch: ' + str(target))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as dest, source.open('rb') as src:
                while block := src.read(1024 * 1024): dest.write(block)
            print('Installed:', target.relative_to(repo))
    print('Assets verified. No existing different files were overwritten.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    build = sub.add_parser('package')
    build.add_argument('--case-dir', default='data/processed/trishuli')
    build.add_argument('--model-dir', default='artifacts/models/siamese_v1/pilot')
    build.add_argument('--output', required=True)
    get = sub.add_parser('fetch')
    get.add_argument('--manifest', required=True)
    get.add_argument('--bundle-url', required=True)
    get.add_argument('--repo', default='.')
    args = p.parse_args()
    try:
        if args.action == 'package': package(args.case_dir, args.model_dir, args.output)
        else: fetch(args.manifest, args.bundle_url, args.repo)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        p.exit(1, 'Asset operation stopped: ' + str(exc) + '\n')


if __name__ == '__main__': main()
