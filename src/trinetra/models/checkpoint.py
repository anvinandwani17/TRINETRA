"""Inspect a trusted trained checkpoint without loading operational satellite data."""
import hashlib
import json
from pathlib import Path
import re
import torch
from trinetra.models.siamese_unet import SiameseUNet

EXPECTED_TRANSFORM = {
    'channel_order': ['pre_VV', 'pre_VH', 'post_VV', 'post_VH'],
    'source': 'Kuro Siwo GRD linear backscatter', 'db_clip': [-35.0, 5.0],
    'scale': '(clip(db,-35,5)+15)/20', 'invalid_input': 0,
    'statistics_fitted': False, 'dem_input': False,
}


def inspect_checkpoint(path, run_config=None):
    path = Path(path)
    saved = torch.load(path, map_location='cpu', weights_only=True)
    if saved.get('schema_version') != 1:
        raise ValueError('Unsupported checkpoint schema')
    if saved['config']['mode'] not in ['fit', 'pilot']:
        raise ValueError('Use a trained fit/pilot checkpoint, not the sanity checkpoint')
    if saved['transform'] != EXPECTED_TRANSFORM or saved['threshold'] != 0.5:
        raise ValueError('Checkpoint transform or fixed threshold differs from this model contract')
    data = saved['data']
    train, val = data['train_events'], data['validation_events']
    if not train or not val or set(train) & set(val):
        raise ValueError('Missing training/validation provenance or overlapping events')
    for event in train+val:
        compact = re.sub('[^a-z0-9]', '', str(event).lower())
        if 'trishul' in compact or 'emsr927' in compact or compact == '927':
            raise ValueError('Forbidden case in checkpoint training/validation provenance')
    if data.get('test_used') is not False:
        raise ValueError('Checkpoint must document that test data was not used')
    if set(data.get('excluded', [])) != {'Trishuli', 'EMSR927'}:
        raise ValueError('Checkpoint does not record both required holdout exclusions')
    if run_config:
        external = json.loads(Path(run_config).read_text(encoding='utf-8'))
        for key in ['config', 'data', 'transform']:
            if external[key] != saved[key]:
                raise ValueError('run_config.json differs from checkpoint '+key)
        if external['threshold'] != saved['threshold']:
            raise ValueError('External threshold differs from checkpoint')
    model = SiameseUNet(saved['config']['base_channels'])
    model.load_state_dict(saved['model_state'], strict=True); model.eval()
    torch.set_num_threads(2)
    # A zero-tensor architecture check, not satellite imagery or a flood result.
    with torch.no_grad(): logits = model(torch.zeros(1, 4, 224, 224))
    if logits.shape != (1, 1, 224, 224) or not torch.isfinite(logits).all():
        raise ValueError('Checkpoint forward-shape/finite-output check failed')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return {
        'status': 'passed', 'checkpoint': str(path.resolve()), 'sha256': digest.hexdigest(),
        'mode': saved['config']['mode'], 'checkpoint_epoch': saved['epoch']+1,
        'parameters': sum(p.numel() for p in model.parameters()),
        'train_events': len(train), 'validation_events': len(val),
        'train_samples': data.get('train_samples'), 'validation_samples_at_training': data.get('validation_samples'),
        'transform': saved['transform'], 'threshold': saved['threshold'],
        'output_shape_check': list(logits.shape),
        'provenance_checked': 'checkpoint declarations and optional matching run_config; original samples not audited here',
        'trishuli_domain_compatibility_verified': False,
        'limitations': ['Kuro Siwo training grid is 10 m; Trishuli grid is 20 m',
                        'Kuro Siwo exact backscatter coefficient/processing equivalence to Gamma0 RTC is not established',
                        'Trishuli rasters are already dB; do not apply log10 a second time'],
        'training_performed': False, 'satellite_inference_performed': False,
        'operational_fallback': 'existing deterministic detector',
    }
