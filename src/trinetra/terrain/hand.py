"""Bounded priority-flood conditioned D8 HAND; metric, finite-positive DEM domain."""
import heapq
from collections import deque
import numpy as np
from scipy import ndimage

OFFSETS = [(-1,0),(-1,1),(0,1),(1,1),(1,0),(1,-1),(0,-1),(-1,-1)]


def drainage_hand(dem, resolution, drainage_area_km2=1.0):
    dem = np.asarray(dem, dtype=np.float64)
    if dem.ndim != 2 or dem.size > 2_000_000 or not 0 < resolution <= 100:
        raise ValueError('Expected bounded metric DEM (at most 2 million cells)')
    if not np.isfinite(drainage_area_km2) or drainage_area_km2 <= 0:
        raise ValueError('Drainage area must be positive')
    valid = np.isfinite(dem) & (dem > 0)
    if not valid.any(): raise ValueError('No supported elevation cells')
    h,w=dem.shape; n=dem.size
    boundary=valid & ~ndimage.binary_erosion(valid,structure=np.ones((3,3)),border_value=0)
    filled=dem.ravel().copy();seen=~valid.ravel();parent=np.full(n,-1,dtype=np.int32)
    order=[];heap=[];fifo=deque()
    for i in np.flatnonzero(boundary):
        seen[i]=True;heapq.heappush(heap,(filled[i],int(i)))
    while heap or fifo:
        if fifo: level,i=fifo.popleft()
        else: level,i=heapq.heappop(heap)
        order.append(i);r,c=divmod(i,w)
        for dr,dc in OFFSETS:
            rr,cc=r+dr,c+dc
            if not (0<=rr<h and 0<=cc<w):continue
            j=rr*w+cc
            if seen[j]:continue
            seen[j]=True;parent[j]=i
            if filled[j]<=level:
                filled[j]=level;fifo.append((level,j))
            else:heapq.heappush(heap,(filled[j],j))
    order=np.array(order,dtype=np.int32)
    rank=np.full(n,-1,dtype=np.int32);rank[order]=np.arange(len(order))
    flow=parent.copy();best=np.zeros(dem.shape,dtype=np.float64)
    level=filled.reshape(dem.shape)
    # Strict downhill D8; flats follow priority-flood parents, which have earlier ranks.
    for dr,dc in OFFSETS:
        rs=slice(max(0,-dr),min(h,h-dr));cs=slice(max(0,-dc),min(w,w-dc))
        rt=slice(max(0,dr),min(h,h+dr));ct=slice(max(0,dc),min(w,w+dc))
        drop=(level[rs,cs]-level[rt,ct])/(resolution*np.hypot(dr,dc))
        eligible=valid[rs,cs]&valid[rt,ct]&(drop>best[rs,cs])
        rows,cols=np.indices(drop.shape)
        target=(rows+max(0,dr))*w+cols+max(0,dc)
        current=(rows+max(0,-dr))*w+cols+max(0,-dc)
        eligible &= rank[target] < rank[current]
        view=flow.reshape(dem.shape)[rs,cs];view[eligible]=target[eligible]
        best[rs,cs][eligible]=drop[eligible]
    # Every edge strictly decreases flood rank, so no flow cycles are possible.
    indices=np.flatnonzero(flow>=0)
    if np.any(rank[flow[indices]]>=rank[indices]):raise ValueError('Flow topology is cyclic')
    accumulation=np.zeros(n,dtype=np.float64);accumulation[valid.ravel()]=resolution**2
    for i in order[::-1]:
        j=flow[i]
        if j>=0:accumulation[j]+=accumulation[i]
    drain=(accumulation>=drainage_area_km2*1e6)&valid.ravel()&~boundary.ravel()
    anchor=np.full(n,-1,dtype=np.int32)
    for i in order:
        if drain[i]:anchor[i]=i
        elif flow[i]>=0:anchor[i]=anchor[flow[i]]
    hand=np.full(n,np.nan,dtype=np.float32)
    routed=(anchor>=0)&valid.ravel()
    height=dem.ravel()[routed]-dem.ravel()[anchor[routed]]
    positions=np.flatnonzero(routed)
    # Strongly negative original-elevation HAND inside a filled depression is unsupported.
    nonnegative=height>=-0.01
    hand[positions[nonnegative]]=np.maximum(height[nonnegative],0)
    return hand.reshape(dem.shape),accumulation.reshape(dem.shape),{
        'valid_cells':int(valid.sum()),'boundary_outlet_cells':int(boundary.sum()),
        'drainage_cells':int(drain.sum()),'hand_known_cells':int(np.isfinite(hand).sum()),
        'depression_raised_cells':int(np.sum(valid.ravel()&(filled>dem.ravel()+.01))),
        'max_depression_raise_m':float(np.nanmax(np.where(valid.ravel(),filled-dem.ravel(),np.nan))),
        'negative_hand_unknown_cells':int((~nonnegative).sum()),
        'drainage_area_km2':drainage_area_km2,'routing_resolution_m':resolution}
