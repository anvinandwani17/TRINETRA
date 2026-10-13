"""Tiny real-data sanity run and event-disjoint GPU training with checkpoints."""
from pathlib import Path
import json
import os
import random
import time
import numpy as np
import torch
from torch.utils.data import DataLoader
from trinetra.models.siamese_unet import SiameseUNet
from trinetra.training.objective import bce_dice_loss, FloodMetrics
from trinetra.training.data import TrainingDataset, sar_transform, TRANSFORM
from trinetra.core.files import atomic_json


def seed_all(seed):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False


def checkpoint(path, model, optimizer, epoch, config, data, extra):
    value = {'schema_version': 1, 'model_state': model.state_dict(),
             'optimizer_state': optimizer.state_dict(), 'epoch': epoch,
             'config': config, 'data': data, 'extra': extra, 'transform': TRANSFORM,
             'threshold': 0.5, 'torch_rng_state': torch.get_rng_state(),
             'cuda_rng_state': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp'); torch.save(value, temp); os.replace(temp, path)


def tensors(batch, device):
    target = batch['flood_target'].to(device)
    valid = batch['valid_mask'].to(device)
    x = sar_transform(batch['sar_linear'].to(device), valid)
    return x, target, valid


def evaluate(model, loader, device):
    model.eval(); metrics = FloodMetrics(); total = 0; batches = 0
    with torch.no_grad():
        for batch in loader:
            x, target, valid = tensors(batch, device)
            if not valid.any(): continue
            logits = model(x); loss = bce_dice_loss(logits, target, valid)
            if not torch.isfinite(loss): raise ValueError('Nonfinite evaluation loss')
            total += float(loss); batches += 1
            metrics.update(logits, target, valid, batch['event_id'])
    if not batches: raise ValueError('No valid evaluation batches')
    return {'loss': total/batches, **metrics.compute()}


def sanity(root, out, steps=40, seed=17, base_channels=8):
    if not 2 <= steps <= 200: raise ValueError('Sanity steps must be between 2 and 200')
    seed_all(seed); torch.set_num_threads(min(2, os.cpu_count() or 1))
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    dataset = TrainingDataset(root, 'train')
    if len(dataset) < 2: raise ValueError('Sanity run requires two real train samples')
    loader = DataLoader(torch.utils.data.Subset(dataset, [0, 1]), batch_size=2, num_workers=0)
    batch = next(iter(loader)); device = torch.device('cpu')
    x, target, valid = tensors(batch, device)
    model = SiameseUNet(base_channels); optimizer = torch.optim.AdamW(model.parameters(), lr=0.003)
    initial_weights = next(model.parameters()).detach().clone()
    model.eval()
    with torch.no_grad(): initial_loss = float(bce_dice_loss(model(x), target, valid))
    losses = []; started = time.monotonic()
    for step in range(steps):
        model.train(); optimizer.zero_grad(set_to_none=True)
        logits = model(x); loss = bce_dice_loss(logits, target, valid)
        if not torch.isfinite(loss): raise ValueError('Nonfinite loss')
        loss.backward()
        if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
            raise ValueError('Nonfinite gradients')
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
        optimizer.step(); losses.append(float(loss.detach()))
        if step % 10 == 0: print(f'Sanity step {step+1}/{steps}: loss={losses[-1]:.4f}', flush=True)
    model.eval()
    with torch.no_grad(): logits = model(x); final_loss = float(bce_dice_loss(logits, target, valid))
    if final_loss >= initial_loss: raise ValueError('Sanity loss did not decrease')
    if torch.equal(initial_weights, next(model.parameters()).detach()): raise ValueError('Weights did not update')
    metrics = FloodMetrics(); metrics.update(logits, target, valid, batch['event_id'])
    config = {'base_channels': base_channels, 'seed': seed, 'mode': 'sanity', 'steps': steps}
    data = {'local_manifest_sha256': dataset.fingerprint, 'train_events': dataset.event_ids,
            'excluded': ['Trishuli', 'EMSR927'], 'split_sha256': dataset.report['split_sha256']}
    path = out/'sanity_last.pt'
    checkpoint(path, model, optimizer, steps, config, data, {'initial_loss': initial_loss, 'final_loss': final_loss})
    loaded = torch.load(path, map_location='cpu', weights_only=True)
    restored = SiameseUNet(base_channels); restored.load_state_dict(loaded['model_state']); restored.eval()
    with torch.no_grad(): equal = torch.equal(logits, restored(x))
    if not equal: raise ValueError('Checkpoint prediction round trip differs')
    report = {'status': 'passed', 'mode': 'sanity', 'data': 'two real Kuro Siwo training patches',
              'device': 'cpu', 'steps': steps, 'parameters': sum(p.numel() for p in model.parameters()),
              'input_shape': list(x.shape), 'output_shape': list(logits.shape),
              'initial_loss': initial_loss, 'final_loss': final_loss, 'loss_history': losses,
              'training_patch_metrics_only': metrics.compute(), 'checkpoint_roundtrip_identical': equal,
              'generalization_evaluated': False, 'seconds': time.monotonic()-started,
              'checkpoint': path.name, 'data_provenance': data}
    atomic_json(out/'sanity_report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'loss_history'}, indent=2))
    return report


def fit(root, out, sanity_report, epochs=20, batch_size=8, base_channels=16, seed=17, resume=None):
    gate = json.loads(Path(sanity_report).read_text())
    if gate.get('status') != 'passed' or not gate.get('checkpoint_roundtrip_identical'):
        raise ValueError('A passed real-data sanity run is required before proper training')
    if not torch.cuda.is_available(): raise ValueError('Proper training requires the requested Colab/Kaggle GPU')
    if epochs < 1 or batch_size < 1: raise ValueError('Epochs and batch size must be positive')
    seed_all(seed); device = torch.device('cuda'); out = Path(out)
    train, val = TrainingDataset(root, 'train'), TrainingDataset(root, 'validation')
    for split, dataset in [('train', train), ('validation', val)]:
        expected = {e for e, s in dataset.report['assignments'].items() if s == split}
        if set(dataset.event_ids) != expected:
            raise ValueError(f'Proper training requires coverage of all official {split} events; fetch the bounded subset first')
    if set(train.event_ids) & set(val.event_ids): raise ValueError('Training and validation events overlap')
    data = {'local_manifest_sha256': train.fingerprint, 'split_sha256': train.report['split_sha256'],
            'train_events': train.event_ids, 'validation_events': val.event_ids,
            'train_samples': len(train), 'validation_samples': len(val),
            'excluded': ['Trishuli', 'EMSR927'], 'test_used': False}
    config = {'base_channels': base_channels, 'batch_size': batch_size, 'seed': seed,
              'learning_rate': 0.001, 'mode': 'fit', 'loss': 'masked BCE + soft Dice',
              'checkpoint_selection': 'validation macro-event IoU', 'patience': 5}
    model = SiameseUNet(base_channels).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    start = 0; best = -1.; stale = 0; history = []
    if resume:
        saved = torch.load(resume, map_location=device, weights_only=True)
        if saved['config'] != config or saved['data'] != data or saved['transform'] != TRANSFORM:
            raise ValueError('Resume configuration/dataset/transform mismatch')
        model.load_state_dict(saved['model_state']); optimizer.load_state_dict(saved['optimizer_state'])
        start = saved['epoch']+1; best = saved['extra']['best_iou']; stale = saved['extra']['stale']
        history = saved['extra']['history']; torch.set_rng_state(saved['torch_rng_state'].cpu())
        if saved['cuda_rng_state']: torch.cuda.set_rng_state_all([s.cpu() for s in saved['cuda_rng_state']])
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out/'run_config.json', {'config': config, 'data': data, 'transform': TRANSFORM,
                                      'gpu': torch.cuda.get_device_name(0), 'threshold': 0.5})
    val_loader = DataLoader(val, batch_size=batch_size, shuffle=False, num_workers=0)
    for epoch in range(start, epochs):
        # Epoch-derived ordering makes resume independent of notebook process state.
        generator = torch.Generator().manual_seed(seed+epoch)
        loader = DataLoader(train, batch_size=batch_size, shuffle=True, generator=generator, num_workers=0)
        model.train(); meters = FloodMetrics(); total = 0.; batches = 0
        for batch in loader:
            x, target, valid = tensors(batch, device)
            if not valid.any(): continue
            optimizer.zero_grad(set_to_none=True); logits = model(x)
            loss = bce_dice_loss(logits, target, valid)
            if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
            loss.backward()
            if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                raise ValueError('Nonfinite training gradients')
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1); optimizer.step()
            total += float(loss.detach()); batches += 1
            meters.update(logits, target, valid, batch['event_id'])
        if not batches: raise ValueError('No observable training pixels')
        scores = evaluate(model, val_loader, device)
        current = scores['macro_event']['iou']; improved = current > best
        if improved: best = current; stale = 0
        else: stale += 1
        row = {'epoch': epoch+1, 'train': {'loss': total/batches, **meters.compute()}, 'validation': scores}
        history.append(row)
        extra = {'best_iou': best, 'stale': stale, 'history': history}
        checkpoint(out/'last.pt', model, optimizer, epoch, config, data, extra)
        if improved: checkpoint(out/'best.pt', model, optimizer, epoch, config, data, extra)
        atomic_json(out/'history.json', history)
        print(json.dumps({'epoch': epoch+1, 'train_loss': total/batches,
                          'validation': {k: scores[k] for k in ['loss', 'iou', 'dice_f1', 'precision', 'recall']},
                          'macro_event_iou': current}), flush=True)
        if stale >= 5: break
    return history


def evaluate_checkpoint(root, checkpoint_path, split, output):
    if split not in ['validation', 'test']: raise ValueError('Evaluate only validation or held-out test')
    saved = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    from trinetra.models.checkpoint import inspect_checkpoint
    check = inspect_checkpoint(checkpoint_path)
    if saved['config']['mode'] not in {'fit', 'pilot'} or saved['transform'] != TRANSFORM or saved['threshold'] != 0.5:
        raise ValueError('Evaluate a trained fit/pilot checkpoint with the fixed input transform/threshold')
    torch.set_num_threads(min(2, os.cpu_count() or 1))
    dataset = TrainingDataset(root, split)
    from trinetra.training.evaluation_contract import evaluation_coverage
    coverage = evaluation_coverage(saved['data'], dataset.report, dataset.event_ids, len(dataset), split)
    model = SiameseUNet(saved['config']['base_channels']); model.load_state_dict(saved['model_state'])
    loader = DataLoader(dataset, batch_size=saved['config']['batch_size'], shuffle=False, num_workers=0)
    scores = evaluate(model, loader, torch.device('cpu'))
    result = {'split': split, 'samples': len(dataset), 'events': dataset.event_ids,
              'checkpoint': str(checkpoint_path), 'threshold': 0.5, 'threshold_tuned': False,
              'excluded': ['Trishuli', 'EMSR927'], 'metrics': scores,
              'checkpoint_sha256': check['sha256'], 'checkpoint_mode': saved['config']['mode'],
              'evaluated_local_manifest_sha256': dataset.fingerprint,
              'split_sha256': dataset.report['split_sha256'],
              **coverage,
              'training_performed': False, 'probability_calibrated': False,
              'limitations': ['Metrics cover only the locally cached samples listed here',
                             'Validation events selected the checkpoint; validation is not an independent test',
                             'Kuro Siwo metrics do not validate Trishuli or Himalayan domain compatibility',
                             'Pilot training coverage remains partial; evaluation does not retrain the model']}
    atomic_json(Path(output), result); return result
