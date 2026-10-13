"""Build experimental Sigma0/model outputs for a verified general baseline case."""
from pathlib import Path
import os
import json
import time
from trinetra.core.cases import make_case, case_paths
from trinetra.core.files import atomic_json, _sha256


ROOT = Path(__file__).resolve().parents[3]


def ensure_ai(aoi, flood_date, data_root, progress=None, checkpoint=None, run_config=None):
    case = make_case(aoi, flood_date)
    paths = case_paths(case, data_root)
    parent = paths['processed']/'processing_manifest.json'
    if not parent.is_file(): raise ValueError('Complete physical preprocessing before general AI')
    checkpoint = Path(checkpoint or os.getenv('TRINETRA_AI_CHECKPOINT', str(ROOT/'artifacts/models/siamese_v1/pilot/best.pt')))
    run_config = Path(run_config or os.getenv('TRINETRA_AI_RUN_CONFIG', str(ROOT/'artifacts/models/siamese_v1/pilot/run_config.json')))
    if not checkpoint.is_file() or not run_config.is_file():
        raise ValueError('Set TRINETRA_AI_CHECKPOINT and TRINETRA_AI_RUN_CONFIG to the existing trusted trained model files')
    # Inspect before any expensive SNAP/model execution, without changing weights.
    from trinetra.models.checkpoint import inspect_checkpoint
    check = inspect_checkpoint(checkpoint, run_config)
    lock = paths['run']/'.ai_pipeline.lock'
    paths['run'].mkdir(parents=True, exist_ok=True)
    with lock.open('x') as handle: handle.write(str(os.getpid()))
    trace = {'case': case.case_id, 'method': 'experimental_ai', 'status': 'running',
             'checkpoint_sha256': check['sha256'], 'stages': [], 'training_performed': False}
    trace_path = paths['run']/'ai_pipeline_manifest.json'
    def stage(name, message, action):
        if progress: progress(name, message, {})
        start = time.perf_counter(); item = {'stage': name, 'status': 'running'}
        trace['stages'].append(item); atomic_json(trace_path, trace)
        try: result = action()
        except Exception:
            item.update(status='failed', elapsed_seconds=time.perf_counter()-start); raise
        item.update(status='complete', elapsed_seconds=time.perf_counter()-start)
        atomic_json(trace_path, trace)
        return result
    try:
        from trinetra.preprocessing.ai_sigma0 import preprocess_ai_sigma0
        from trinetra.inference.trishuli import run_case
        sigma = stage('AI_PREPROCESSING', 'Preparing or verifying separate 10 m linear Sigma0 inputs...',
                      lambda: preprocess_ai_sigma0(parent))
        result = stage('INFERENCE', 'Running or verifying frozen experimental AI on CPU...',
                       lambda: run_case(paths['processed']/'ai_sigma0_10m/ai_processing_manifest.json',
                                        checkpoint, run_config, paths['processed']/'ai_flood_experimental'))
        trace.update(status='satellite_ready', inference_manifest_sha256=_sha256(paths['processed']/'ai_flood_experimental/inference_manifest.json'))
        atomic_json(trace_path, trace)
        return {'trace': trace, 'inference': result}
    except Exception as exc:
        trace.update(status='failed', error=str(exc)); atomic_json(trace_path, trace); raise
    finally: lock.unlink(missing_ok=True)
