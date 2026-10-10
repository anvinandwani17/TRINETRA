"""Focused model, masked supervision, metrics and holdout provenance tests."""
from pathlib import Path
import sys
import unittest
import torch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from trinetra.models.siamese_unet import SiameseUNet
from trinetra.training.objective import bce_dice_loss, FloodMetrics
from trinetra.training.data import sar_transform, guard_record


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): torch.set_num_threads(2)

    def test_four_channels_shared_encoder_and_odd_grid(self):
        model = SiameseUNet(8)
        self.assertLess(sum(p.numel() for p in model.parameters()), 200_000)
        x = torch.randn(2, 4, 31, 35, requires_grad=True)
        logits = model(x)
        self.assertEqual(tuple(logits.shape), (2, 1, 31, 35))
        logits.mean().backward()
        self.assertTrue(torch.isfinite(x.grad).all())
        self.assertGreater(float(x.grad[:, :2].abs().sum()), 0)
        self.assertGreater(float(x.grad[:, 2:].abs().sum()), 0)

    def test_masked_loss_has_zero_invalid_gradient(self):
        logits = torch.zeros(1, 1, 2, 2, requires_grad=True)
        y = torch.tensor([[[1, 0], [255, 255]]])
        valid = y != 255
        a = bce_dice_loss(logits, y, valid)
        changed = logits.detach().clone(); changed[0, 0, 1] = 100
        b = bce_dice_loss(changed, y, valid)
        self.assertAlmostEqual(float(a.detach()), float(b), places=6)
        a.backward()
        self.assertTrue(torch.equal(logits.grad[0, 0, 1], torch.zeros(2)))
        with self.assertRaises(ValueError): bce_dice_loss(logits, y, torch.zeros_like(valid))

    def test_confusion_counts_and_event_macro(self):
        m = FloodMetrics()
        logits = torch.tensor([[[[10., 10., -10., 10.]]]])
        y = torch.tensor([[[1, 0, 1, 255]]]); v = y != 255
        m.update(logits, y, v, ['470'])
        r = m.compute()
        self.assertEqual([r[k] for k in ['tp', 'fp', 'fn', 'tn']], [1, 1, 1, 0])
        self.assertAlmostEqual(r['iou'], 1/3)
        self.assertEqual(r['dice_f1'], .5)
        self.assertEqual(r['precision'], .5); self.assertEqual(r['recall'], .5)
        self.assertEqual(r['macro_event']['iou'], r['iou'])

    def test_fixed_transform_and_forbidden_cases(self):
        x = torch.ones(1, 4, 2, 2)
        v = torch.tensor([[[True, False], [True, True]]])
        normalized = sar_transform(x, v)
        self.assertEqual(float(normalized[0, 0, 0, 0]), .75)
        self.assertEqual(float(normalized[0, 0, 0, 1]), 0)
        row = {'event_id': '470', 'split': 'train', 'source': {'repo': 'orion-ai-lab/Kuro-Siwo-Webdataset'}}
        info = {'flood_date': '2020-10-05', 'sources': {'MS1': {'source_date': '2020-10-14'}}}
        guard_record(row, info, {'470': 'train'})
        for case in ['Trishuli', 'EMSR927', 'EMSR-927']:
            with self.assertRaises(ValueError): guard_record(row, {**info, 'case': case}, {'470': 'train'})
        with self.assertRaises(ValueError): guard_record(row, {**info, 'flood_date': '2026-08-26'}, {'470': 'train'})


if __name__ == '__main__': unittest.main()
