"""Masked BCE plus soft Dice; hard metrics accumulated without ignored pixels."""
import torch
from torch.nn import functional as F


def bce_dice_loss(logits, target, valid):
    logits = logits[:, 0] if logits.ndim == 4 else logits
    valid = valid.bool() & (target != 255)
    if logits.shape != target.shape or target.shape != valid.shape:
        raise ValueError('Logit, label and mask shapes differ')
    if not valid.any(): raise ValueError('Batch has no observable supervised pixels')
    if not torch.isin(target[valid], torch.tensor([0, 1], device=target.device)).all():
        raise ValueError('Binary flood target must be 0 or 1')
    y = torch.where(valid, target, 0).float()
    bce = F.binary_cross_entropy_with_logits(logits, y, reduction='none')[valid].mean()
    p = torch.sigmoid(logits)
    weights = valid.float()
    intersection = (p*y*weights).sum(dim=(-2, -1))
    denominator = ((p+y)*weights).sum(dim=(-2, -1))
    active = valid.sum(dim=(-2, -1)) > 0
    dice_loss = (1-(2*intersection+1)/(denominator+1))[active].mean()
    return bce+dice_loss


class FloodMetrics:
    def __init__(self): self.counts = [0, 0, 0, 0]; self.events = {}

    def update(self, logits, target, valid, event_ids):
        pred = torch.sigmoid(logits.detach()[:, 0]) >= 0.5
        for p, y, v, event in zip(pred, target, valid, event_ids):
            v = v.bool() & (y != 255); t = y == 1
            counts = [int((p&t&v).sum()), int((p&~t&v).sum()),
                      int((~p&t&v).sum()), int((~p&~t&v).sum())]
            self.counts = [a+b for a, b in zip(self.counts, counts)]
            self.events[str(event)] = [a+b for a, b in zip(self.events.get(str(event), [0]*4), counts)]

    @staticmethod
    def scores(counts):
        tp, fp, fn, tn = counts
        divide = lambda a, b: a/b if b else 0.0
        return {'iou': divide(tp, tp+fp+fn), 'dice_f1': divide(2*tp, 2*tp+fp+fn),
                'precision': divide(tp, tp+fp), 'recall': divide(tp, tp+fn),
                'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn}

    def compute(self):
        result = self.scores(self.counts)
        per_event = {e: self.scores(c) for e, c in self.events.items()}
        result['per_event'] = per_event
        result['macro_event'] = {k: sum(m[k] for m in per_event.values())/len(per_event)
                                 if per_event else 0.0 for k in ['iou', 'dice_f1', 'precision', 'recall']}
        result['threshold'] = 0.5
        result['empty_positive_denominator_policy'] = 'zero; inspect TP/FP/FN counts'
        return result
