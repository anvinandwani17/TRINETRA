"""One resumable baseline satellite chain; reuse the tested step-1..4 runners."""
import importlib.util
import json
from pathlib import Path
import time

from trinetra.core.cases import make_case, case_paths
from trinetra.downloads.case_runner import plan_download, download_planned, read_json
from trinetra.downloads.sentinel1 import DownloadError, TokenProvider, atomic_json, now
from trinetra.preprocessing.pipeline import preprocess_case
from trinetra.flood_detection.pipeline import run_baseline, load_inputs

ROOT = Path(__file__).resolve().parents[3]


def _discover(case, root):
    # Reuse the already-tested discovery function without changing its CLI/tests.
    spec = importlib.util.spec_from_file_location('trinetra_case_discovery', ROOT / 'scripts/discover_case.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.discover(case, root)


def credentials():
    from trinetra.core.config import Settings
    settings = Settings(_env_file=ROOT / '.env')
    return settings.cdse_username.get_secret_value(), settings.cdse_password.get_secret_value()


def ensure_baseline(aoi, flood_date, data_root, *, allow_downloads=False,
                    transport='s3', max_product_gib=3.0, progress=None):
    """Produce/verify satellite baseline outputs; API then adds OSM/access/report."""
    if transport not in {'s3','odata'}:
        raise ValueError('Choose the official s3 or odata transport')
    case = make_case(aoi, flood_date)
    paths = case_paths(case, data_root)
    paths['run'].mkdir(parents=True, exist_ok=True)
    record_path = paths['run'] / 'pipeline_manifest.json'
    record = {'schema':1, 'case':case.case_id, 'flood_date':flood_date,
              'request_sha256':case.fingerprint, 'aoi':case.aoi,
              'status':'running', 'started_at':now(), 'stages':[],
              'scope':'baseline satellite stages; API adds historical OSM/access/report'}
    lock = paths['run'] / '.pipeline.lock'
    try:
        handle = lock.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise DownloadError('This case pipeline is already locked; confirm old processes stopped before removing a crash lock') from exc
    def stage(name, message, action):
        item = {'stage':name,'status':'running','started_at':now()}
        record['stages'].append(item)
        atomic_json(record_path, record)
        if progress:
            progress(name, message, {})
        started = time.perf_counter()
        try:
            result = action()
        except Exception:
            item.update(status='failed', elapsed_seconds=time.perf_counter()-started, ended_at=now())
            atomic_json(record_path, record)
            raise
        item.update(status='complete', elapsed_seconds=time.perf_counter()-started, ended_at=now())
        atomic_json(record_path, record)
        return result
    try:
        with handle:
            import os
            handle.write(str(os.getpid()))
        pair_result = stage('DISCOVERING', 'Finding or verifying the pinned same-orbit VV/VH pair...', lambda: _discover(case, data_root))
        plan, paths = stage('PLANNING', 'Resolving only the selected products and checking size limits...',
                            lambda: plan_download(case, data_root, max_product_gib=max_product_gib))
        if progress:
            progress('PLANNING', 'Selected pair and estimated product sizes recorded.',
                     {'source_pair':pair_result['pair'], 'estimated_bytes':plan['catalogue_estimated_total_bytes']})
        tokens, transfer = None, None
        if not allow_downloads:
            def transfer(*args, **kwargs):
                raise DownloadError('A required scene is not cached. Enable the two-scene download option to process this case; no scene bytes were requested')
        elif transport == 's3':
            from dotenv import load_dotenv
            load_dotenv(ROOT / '.env', override=False)
            from trinetra.downloads.s3 import S3Transfer
            transfer = S3Transfer()
        else:
            tokens = TokenProvider(credentials)
        stage('DOWNLOADING', 'Checking cached raw archives or transferring the two selected products...',
              lambda: download_planned(plan, paths, tokens=tokens, transfer=transfer,
                                       transport=transport))
        processing_path = paths['processed'] / 'processing_manifest.json'
        def physical():
            process_lock = paths['processed'] / '.preprocess.lock'
            if process_lock.exists():
                raise DownloadError('A separate preprocessing run already holds this case lock')
            if processing_path.is_file() and read_json(processing_path).get('status') == 'complete':
                # Checks all processed hashes/grids/unknown semantics and terrain cache.
                load_inputs(processing_path)
                report = read_json(processing_path)
                downloads = read_json(paths['run']/'download_manifest.json')
                if (report.get('source_pair') != plan['pair']
                        or report.get('raw_sha256') != {role:downloads['products'][role]['verification']['sha256'] for role in ['pre','post']}):
                    raise DownloadError('Processed inputs differ from the currently verified raw pair')
                return read_json(processing_path)
            from trinetra.preprocessing.snap import gpt_path
            gpt = gpt_path()
            paths['processed'].mkdir(parents=True, exist_ok=True)
            try:
                process_handle = process_lock.open('x', encoding='utf-8')
            except FileExistsError as exc:
                raise DownloadError('Another preprocessing run started for this case') from exc
            try:
                with process_handle:
                    process_handle.write(str(os.getpid()))
                # Immutable input snapshot keeps preprocessing provenance readable
                # even when later cache checks update download_manifest.json.
                from trinetra.downloads.sentinel1 import _sha256
                source = paths['run']/'download_manifest.json'
                sha = _sha256(source)
                snapshot = paths['run']/('download-input-' + sha + '.json')
                if snapshot.exists():
                    if _sha256(snapshot) != sha:
                        raise DownloadError('Immutable raw-manifest snapshot was modified')
                else:
                    with snapshot.open('xb') as stream:
                        stream.write(source.read_bytes())
                return preprocess_case(snapshot, output=paths['processed'], gpt=gpt)
            finally:
                process_lock.unlink(missing_ok=True)
        stage('PREPROCESSING', 'Running or verifying physical SNAP VV/VH preprocessing on the common grid...', physical)
        baseline_path = paths['processed'] / 'baseline/baseline_manifest.json'
        def classify():
            if baseline_path.is_file() and read_json(baseline_path).get('status') == 'complete':
                from trinetra.flood_detection.verify import verify_baseline
                verify_baseline(baseline_path)
                return read_json(baseline_path)
            return run_baseline(processing_path, paths['processed']/'baseline')
        result = stage('DETECTING', 'Running or verifying the frozen deterministic baseline...', classify)
        record.update(status='satellite_ready', completed_at=now(), baseline_manifest=str(baseline_path))
        atomic_json(record_path, record)
        return {'case':case.case_id,'baseline':result,'trace':record}
    except Exception as exc:
        record.update(status='failed', error=str(exc), failed_at=now())
        atomic_json(record_path, record)
        raise
    finally:
        lock.unlink(missing_ok=True)
