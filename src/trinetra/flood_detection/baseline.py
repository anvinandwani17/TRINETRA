"""Conservative deterministic open-water change baseline; no trained model."""
from dataclasses import dataclass, asdict
import numpy as np
from scipy import ndimage


@dataclass(frozen=True)
class BaselineConfig:
    vv_drop_db: float = 3.0
    vh_drop_db: float = 2.0
    post_vv_max_db: float = -15.0
    post_vh_max_db: float = -22.0
    pre_low_vv_db: float = -17.0
    pre_low_vh_db: float = -24.0
    max_slope_deg: float = 10.0
    min_area_m2: float = 2000.0
    min_filter_neighbors: int = 5

    def validate(self):
        if not all(np.isfinite(x) for x in asdict(self).values()):
            raise ValueError('All baseline parameters must be finite')
        if not 0 < self.vv_drop_db <= 20 or not 0 < self.vh_drop_db <= 20:
            raise ValueError('Backscatter drop thresholds must be positive and <=20 dB')
        if not 0 < self.max_slope_deg <= 30 or not 400 <= self.min_area_m2 <= 1_000_000:
            raise ValueError('Use slope (0,30] degrees and minimum area 400..1,000,000 m2')
        if not isinstance(self.min_filter_neighbors, int) or not 1 <= self.min_filter_neighbors <= 9:
            raise ValueError('Filter support must be an integer from 1 to 9')
        for v in [self.post_vv_max_db,self.post_vh_max_db,self.pre_low_vv_db,self.pre_low_vh_db]:
            if not -50 <= v <= 0:
                raise ValueError('Dark-backscatter thresholds must be between -50 and 0 dB')


REASONS = {1:'input unobservable/outside AOI/invalid radiometry', 2:'missing terrain or slope neighborhood',
           4:'slope exceeds supported terrain; unknown', 8:'insufficient radiometric filter support; unknown',
           16:'already low backscatter before event; no new-water evidence', 32:'insufficient VV drop',
           64:'insufficient VH drop', 128:'post-event backscatter not dark in both polarizations',
           256:'candidate component below minimum mapping area'}


def intensity_mean_db(data, valid, min_neighbors):
    """3x3 intensity mean, excluding all unknown pixels; never fill their centers."""
    kernel = np.ones((3,3),dtype=np.float64)
    count = ndimage.convolve(valid.astype(np.float64), kernel, mode='constant', cval=0)
    output = np.full(data.shape, np.nan,dtype=np.float32)
    supported = valid & (count >= min_neighbors)
    for pol in range(2):
        linear = np.zeros(valid.shape,dtype=np.float64)
        linear[valid] = 10.0 ** (data[pol,valid] / 10.0)
        total = ndimage.convolve(linear,kernel,mode='constant',cval=0)
        output[pol,supported] = 10*np.log10(total[supported]/count[supported])
    return output,supported


def terrain_slope(dem, resolution=20):
    """Horn 3x3 slope in degrees; incomplete DEM neighborhoods remain NaN."""
    dem = np.asarray(dem,dtype=np.float64)
    known = np.isfinite(dem)
    values = np.where(known,dem,0)
    x = np.array([[-1,0,1],[-2,0,2],[-1,0,1]],dtype=float)/(8*resolution)
    y = x.T
    dx = ndimage.correlate(values,x,mode='constant',cval=0)
    dy = ndimage.correlate(values,y,mode='constant',cval=0)
    complete = ndimage.convolve(known.astype(np.uint8),np.ones((3,3),dtype=np.uint8),mode='constant',cval=0)==9
    slope = np.full(dem.shape,np.nan,dtype=np.float32)
    slope[complete] = np.degrees(np.arctan(np.hypot(dx[complete],dy[complete])))
    return slope


def detect_flood(before, after, change, observability, slope, *, config=None, resolution=20):
    config = config or BaselineConfig()
    config.validate()
    if resolution != 20:
        raise ValueError('First baseline operates on the validated 20 m grid')
    before,after,change = [np.asarray(x) for x in [before,after,change]]
    if before.ndim != 3 or before.shape[0] != 2 or before.shape != after.shape or before.shape != change.shape:
        raise ValueError('Expected aligned [VV,VH] before/after/change arrays')
    shape = before.shape[1:]
    if observability.shape != shape or slope.shape != shape or not np.isin(observability,[0,1,255]).all():
        raise ValueError('Observability/slope grid or codes are invalid')
    valid = (observability==1) & np.all(np.isfinite(before)&np.isfinite(after)&np.isfinite(change),axis=0)
    if not np.allclose(change[:,valid],(after-before)[:,valid],atol=1e-5,rtol=1e-6):
        raise ValueError('Input change is not post-minus-pre dB')
    pre,support1 = intensity_mean_db(before,valid,config.min_filter_neighbors)
    post,support2 = intensity_mean_db(after,valid,config.min_filter_neighbors)
    supported = support1 & support2
    terrain_known = np.isfinite(slope) & (slope>=0) & (slope<=90)
    eligible = valid & supported & terrain_known & (slope <= config.max_slope_deg)
    drop = pre-post
    pre_low = (pre[0]<=config.pre_low_vv_db) & (pre[1]<=config.pre_low_vh_db)
    vv = drop[0]>=config.vv_drop_db
    vh = drop[1]>=config.vh_drop_db
    dark = (post[0]<=config.post_vv_max_db) & (post[1]<=config.post_vh_max_db)
    seeds = eligible & ~pre_low & vv & vh & dark
    labels,n = ndimage.label(seeds,structure=ndimage.generate_binary_structure(2,1))
    sizes = np.bincount(labels.ravel())
    retained = sizes >= int(np.ceil(config.min_area_m2/resolution**2))
    retained[0] = False
    candidate = retained[labels]
    # Four-connected components only; never fill holes or bridge unknown pixels.
    cleaned_labels,n = ndimage.label(candidate,structure=ndimage.generate_binary_structure(2,1))
    mask = np.full(shape,255,dtype=np.uint8)
    mask[eligible] = candidate[eligible].astype(np.uint8)
    reasons = np.zeros(shape,dtype=np.uint16)
    reasons[~valid] |= 1
    reasons[~terrain_known] |= 2
    reasons[terrain_known & (slope>config.max_slope_deg)] |= 4
    reasons[valid & ~supported] |= 8
    for flag,failed in [(16,pre_low),(32,~vv),(64,~vh),(128,~dark),(256,seeds&~candidate)]:
        reasons[eligible & failed] |= flag
    # Bounded evidence margins, not a calibrated probability or model accuracy.
    margins = [drop[0]-config.vv_drop_db,drop[1]-config.vh_drop_db,
               config.post_vv_max_db-post[0],config.post_vh_max_db-post[1]]
    strength = np.minimum.reduce([np.clip(.5+m/6,0,1) for m in margins])
    terrain_strength = np.clip(1-slope/(2*config.max_slope_deg),0,1)
    score = np.full(shape,np.nan,dtype=np.float32)
    score[eligible] = (strength*terrain_strength)[eligible]
    score[eligible & (pre_low | (seeds&~candidate))] = 0
    return {'flood_mask':mask,'evidence_score':score,'reasons':reasons,
            'filtered_change_db':post-pre,'filtered_before_db':pre,'filtered_after_db':post,
            'slope_deg':slope.astype(np.float32),'labels':cleaned_labels.astype(np.int32),
            'component_count':int(n),'candidate_pixels':int(candidate.sum()),
            'assessable_pixels':int(eligible.sum()),'unknown_pixels':int((mask==255).sum())}
