"""Conservative pair observability. Unknown is never a dry/flood prediction."""
import numpy as np

PRE_MISSING, POST_MISSING = 1, 2
PRE_LAYOVER, POST_LAYOVER = 4, 8
PRE_SHADOW, POST_SHADOW = 16, 32
GEOMETRY_UNKNOWN, OUTSIDE_AOI, QUALITY_BUFFER = 64, 128, 256
REASONS = {1: 'pre missing/nonpositive radiometry', 2: 'post missing/nonpositive radiometry',
           4: 'pre layover', 8: 'post layover', 16: 'pre radar shadow', 32: 'post radar shadow',
           64: 'unknown terrain geometry/DEM/incidence', 128: 'outside AOI',
           256: 'one-pixel buffer around terrain risk/unknown geometry'}


def dilate(mask, radius=1):
    if radius < 0 or radius > 5:
        raise ValueError('Quality buffer must be between zero and five pixels')
    out = mask.copy()
    padded = np.pad(mask, radius, constant_values=False)
    for y in range(2 * radius + 1):
        for x in range(2 * radius + 1):
            out |= padded[y:y+mask.shape[0], x:x+mask.shape[1]]
    return out


def pair_quality(pre, post, pre_mask, post_mask, geometry_valid, inside, radius=1):
    """Both polarizations and dates must be observable; mask 0=unknown, 1=usable."""
    if pre.shape != post.shape or pre.shape[0] != 2:
        raise ValueError('Expected matching [VV,VH] grids')
    if any(a.shape != pre.shape[1:] for a in [pre_mask, post_mask, geometry_valid, inside]):
        raise ValueError('Quality inputs must share the radiometry grid')
    reasons = np.zeros(inside.shape, dtype=np.uint16)
    reasons[~np.all(np.isfinite(pre) & (pre > 0), axis=0)] |= PRE_MISSING
    reasons[~np.all(np.isfinite(post) & (post > 0), axis=0)] |= POST_MISSING
    for mask, lay, shadow in [(pre_mask, PRE_LAYOVER, PRE_SHADOW), (post_mask, POST_LAYOVER, POST_SHADOW)]:
        known = np.isin(mask, [0, 1, 2, 3])
        reasons[~known] |= GEOMETRY_UNKNOWN
        bits = np.where(known, mask, 0).astype(np.uint8)
        reasons[(bits & 1) != 0] |= lay
        reasons[(bits & 2) != 0] |= shadow
    reasons[~geometry_valid] |= GEOMETRY_UNKNOWN
    risk = (reasons & (PRE_LAYOVER | POST_LAYOVER | PRE_SHADOW | POST_SHADOW | GEOMETRY_UNKNOWN)) != 0
    reasons[dilate(risk, radius) & ~risk] |= QUALITY_BUFFER
    reasons[~inside] |= OUTSIDE_AOI
    usable = (reasons == 0) & inside
    observable = np.where(inside, usable.astype(np.uint8), 255).astype(np.uint8)
    return observable, reasons, usable


def db_change(pre, post, usable):
    """Gamma0 dB and post-minus-pre dB; zero/invalid values never get a floor."""
    if np.any(~np.isfinite(pre[:,usable])) or np.any(~np.isfinite(post[:,usable])) or np.any(pre[:,usable] <= 0) or np.any(post[:,usable] <= 0):
        raise ValueError('Cannot calculate dB for nonpositive/invalid observable pixels')
    before = np.full(pre.shape, np.nan, dtype=np.float32)
    after = np.full(post.shape, np.nan, dtype=np.float32)
    for pol in range(2):
        before[pol, usable] = 10 * np.log10(pre[pol, usable])
        after[pol, usable] = 10 * np.log10(post[pol, usable])
    return before, after, after - before
