"""Small shared-encoder Siamese U-Net; four ordered SAR channels only."""
import torch
from torch import nn
from torch.nn import functional as F


class ConvBlock(nn.Sequential):
    def __init__(self, inputs, outputs):
        super().__init__(
            nn.Conv2d(inputs, outputs, 3, padding=1, bias=False),
            nn.GroupNorm(4, outputs), nn.ReLU(inplace=True),
            nn.Conv2d(outputs, outputs, 3, padding=1, bias=False),
            nn.GroupNorm(4, outputs), nn.ReLU(inplace=True))


class SiameseUNet(nn.Module):
    """Input Bx4xHxW: pre-VV, pre-VH, post-VV, post-VH. Output logits Bx1xHxW."""
    def __init__(self, base_channels=16):
        super().__init__()
        if base_channels < 4 or base_channels % 4:
            raise ValueError('base_channels must be a positive multiple of four')
        self.base_channels = base_channels
        widths = [base_channels * 2**i for i in range(4)]
        self.encoder = nn.ModuleList([ConvBlock(2 if i == 0 else widths[i-1], w)
                                      for i, w in enumerate(widths)])
        self.fusion = nn.ModuleList([nn.Conv2d(3*w, w, 1) for w in widths])
        self.decoder = nn.ModuleList([ConvBlock(widths[i+1]+widths[i], widths[i])
                                      for i in [2, 1, 0]])
        self.head = nn.Conv2d(widths[0], 1, 1)

    def encode(self, x):
        features = []
        for i, block in enumerate(self.encoder):
            if i: x = F.max_pool2d(x, 2)
            x = block(x); features.append(x)
        return features

    def forward(self, x):
        if x.ndim != 4 or x.shape[1] != 4 or min(x.shape[-2:]) < 8:
            raise ValueError('Expected Bx4xHxW with height/width at least eight')
        before, after = self.encode(x[:, :2]), self.encode(x[:, 2:])
        fused = [layer(torch.cat([a, b, (b-a).abs()], dim=1))
                 for layer, a, b in zip(self.fusion, before, after)]
        out = fused[-1]
        for block, skip in zip(self.decoder, reversed(fused[:-1])):
            out = F.interpolate(out, size=skip.shape[-2:], mode='bilinear', align_corners=False)
            out = block(torch.cat([out, skip], dim=1))
        return self.head(out)
