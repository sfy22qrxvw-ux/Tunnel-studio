# Copyright (c) 2026 Damian Gulczynski.
"""Neighbour and lining evidence with wall protection; original coordinates only.

Independent adaptation of Wang, Wei and Wang (2024),
10.12082/dqxxkx.2024.240505. The paper's accuracy has not been reproduced.
Full-density neighbourhoods have spatial halos. A bounded, spatially balanced
sample learns the model; every selected original point receives a review label.
"""
from pathlib import Path
import tempfile,time
import numpy as np
from scipy.optimize import least_squares
import clean_tunnel,tunnel_lining


SAMPLER_VERSION = 2


def voxel_ranks(keys):
    """Assign the calibration hash rank to each integer XYZ key."""
    h = keys.astype(np.uint64)
    rank = h[:, 0]*np.uint64(73856093) ^ h[:, 1]*np.uint64(19349663) ^ h[:, 2]*np.uint64(83492791)
    rank ^= rank >> np.uint64(30)
    rank *= np.uint64(0xbf58476d1ce4e5b9)
    rank ^= rank >> np.uint64(27)
    rank *= np.uint64(0x94d049bb133111eb)
    rank ^= rank >> np.uint64(31)
    return rank


def _lexicographic_minima(keys, points):
    order = np.lexsort((points[:, 2], points[:, 1], points[:, 0], keys[:, 2], keys[:, 1], keys[:, 0]))
    keys = keys[order]
    first = np.r_[True, np.any(keys[1:] != keys[:-1], axis=1)]
    return keys[first], points[order[first]]


def _voxel_minima(keys, points):
    # Pack local 21-bit voxel offsets in XYZ order for one integer sort.
    # Wide extents or overflowing differences use the full coordinate keys.
    low = keys.min(axis=0)
    span = keys.max(axis=0) - low
    if np.any((span < 0) | (span >= 1 << 21)):
        return _lexicographic_minima(keys, points)
    packed = ((keys[:, 0]-low[0]) << 42) | ((keys[:, 1]-low[1]) << 21) | (keys[:, 2]-low[2])
    order = np.argsort(packed, kind='stable')
    packed = packed[order]
    starts = np.flatnonzero(np.r_[True, packed[1:] != packed[:-1]])
    if len(starts) == len(keys):
        return keys[order], points[order]
    # The smallest measured XYZ per voxel is independent of block boundaries.
    x = points[order, 0]
    counts = np.diff(starts, append=len(keys))
    winners = order[x == np.repeat(np.minimum.reduceat(x, starts), counts)]
    if len(winners) == len(starts):
        return keys[winners], points[winners]
    return _lexicographic_minima(keys[winners], points[winners])


def _merge_samples(key_blocks, point_blocks, cap):
    if len(key_blocks) == 1:
        keys, points = key_blocks[0], point_blocks[0]
    else:
        keys, points = _voxel_minima(np.concatenate(key_blocks), np.concatenate(point_blocks))
    if len(keys) <= cap:
        return keys, points, None
    rank = voxel_ranks(keys)
    cutoff = np.partition(rank, cap-1)[cap-1]
    keep = rank < cutoff
    # Preserve the original stable rank tie-break and lexicographic output.
    keep[np.flatnonzero(rank == cutoff)[:cap-int(keep.sum())]] = True
    return keys[keep], points[keep], cutoff


def sample_originals(batches,spacing,cap,progress,cancel):
    """Keep one actual coordinate per voxel, with deterministic bounded hashing."""
    key_blocks, point_blocks = [], []
    buffered = total = 0
    cutoff = None
    for _, points, _ in batches():
        total += len(points)
        tunnel_lining._emit(progress, cancel, f'2/3 Learning lining from measured points · {total:,}')
        points = np.asarray(points, float)
        if not np.isfinite(points).all():
            raise ValueError('Invalid scan coordinates.')
        if not len(points) or not cap:
            continue
        keys = np.floor(points/spacing).astype(np.int64)
        if cutoff is not None:
            # A voxel outside the current best ranks cannot re-enter later.
            use = voxel_ranks(keys) <= cutoff
            keys, points = keys[use], points[use]
        if not len(keys):
            continue
        keys, points = _voxel_minima(keys, points)
        key_blocks.append(keys)
        point_blocks.append(points)
        buffered += len(keys)
        # Deduplicate each block once. Merge only after accumulating another
        # sample's worth of voxels to avoid repeatedly sorting the retained set.
        if buffered >= 2*cap:
            keys, points, next_cutoff = _merge_samples(key_blocks, point_blocks, cap)
            key_blocks, point_blocks = [keys], [points]
            buffered = len(keys)
            if next_cutoff is not None:
                cutoff = next_cutoff
    if not point_blocks:
        return np.empty((0, 3)), total
    _, points, _ = _merge_samples(key_blocks, point_blocks, cap)
    return points, total


def cylinder(sample,model,tolerance):
    """Refine an initial RANSAC cylinder using true measured coordinates."""
    axis=model['axis'];u,v=tunnel_lining.basis(axis);reference=model['reference'];q=sample-reference
    x=np.array([0.,0.,model['radius'],0.,0.]);fit=q
    def residual(x,points):
        n=axis+x[3]*u+x[4]*v;n/=np.linalg.norm(n);d=points-x[0]*u-x[1]*v
        return np.linalg.norm(d-(d@n)[:,None]*n,axis=1)-x[2]
    for _ in range(3):
        use=np.abs(residual(x,q))<max(.04,4*tolerance)
        if use.sum()<100:raise ValueError('Too little support for a circular lining. Select a shorter round branch or choose Follow measured lining.')
        fit=q[use]
        x=least_squares(lambda a:residual(a,fit),x,loss='soft_l1',f_scale=max(.002,min(.02,tolerance)),max_nfev=60,
            bounds=([-1.,-1.,model['radius']*.7,-.2,-.2],[1.,1.,model['radius']*1.3,.2,.2])).x
    n=axis+x[3]*u+x[4]*v;n/=np.linalg.norm(n);ref=reference+x[0]*u+x[1]*v;uu,vv=tunnel_lining.basis(n)
    d=residual(x,q);use=np.abs(d)<max(.02,3*tolerance);cross=np.c_[(sample-ref)@uu,(sample-ref)@vv]
    if use.mean()<.5 or tunnel_lining.coverage(cross[use],[0,0])<.6:
        raise ValueError('A straight circular cylinder does not describe enough of this selection. Choose Follow measured lining or select a shorter round branch.')
    return dict(reference=ref,axis=n,radius=float(x[2]),fit_rms_mm=float(np.sqrt(np.mean(d[use]**2))*1000),support_fraction=float(use.mean()))


def run_batches(batches,work,ids_path,labels_path,settings,progress,cancel,selected_total):
    started=time.monotonic();shape=settings.get('denoise_shape','measured');tolerance=float(settings.get('denoise_distance_mm',20))/1000
    if shape not in ('measured','cylinder') or not .001<=tolerance<=.5:raise ValueError('Check the denoising shape and lining tolerance.')
    protected_wall=0;counts=np.zeros(6,np.int64);sums=np.zeros((6,3));written=0;representatives={k:[] for k in range(6)}
    with tempfile.TemporaryDirectory(prefix='combined_clean_',dir=work) as tmp:
        tmp=Path(tmp);radius_ids=tmp/'radius.ids';radius_labels=tmp/'radius.labels'
        first=clean_tunnel.run_batches(batches(),work,radius_ids,radius_labels,settings,
            lambda m:progress('1/3 Neighbour support · '+m),cancel,selected_total=selected_total)
        ids=np.memmap(radius_ids,'<i8','r');lab=np.memmap(radius_labels,'<i4','r')
        if len(ids)!=selected_total:raise ValueError('The scan changed during cleaning. Run again.')
        # Disk-backed ID lookup keeps sparse selections independent of tile order.
        maximum=max(int(np.max(ids[i:i+200000])) for i in range(0,len(ids),200000))
        lookup_path=tmp/'support.u1'
        with lookup_path.open('wb') as out:out.truncate(maximum+1)
        support=np.memmap(lookup_path,'u1','r+',shape=(maximum+1,))
        try:
            for i in range(0,len(ids),200000):support[ids[i:i+200000]]=lab[i:i+200000]+1
            sample,_=sample_originals(batches,.01,140000,progress,cancel)
            model=tunnel_lining.fit_model(sample,{**settings,'envelope_thickness':max(.01,tolerance)},
                lambda m:progress('2/3 Robust lining fit · '+m),cancel)
            strict=cylinder(sample,model,tolerance) if shape=='cylinder' else None
            with open(ids_path,'wb') as idout,open(labels_path,'wb') as labout:
                for ix,p,_ in batches():
                    tunnel_lining._emit(progress,cancel,f'3/3 Checking original points · {written+len(ix):,}/{selected_total:,}')
                    state=np.asarray(support[ix]);d,known=tunnel_lining.deviations(p,model)
                    if strict:
                        q=np.asarray(p)-strict['reference'];n=strict['axis'];d=np.linalg.norm(q-(q@n)[:,None]*n,axis=1)-strict['radius']
                    labels=np.zeros(len(ix),np.int32);labels[d < -tolerance]=3;labels[d > tolerance]=4
                    # Sparse returns through an occlusion remain genuine lining evidence.
                    # Density may strengthen an off-lining rejection, never override
                    # a supported geometric match or uncertainty in the model.
                    on_wall=known&(np.abs(d)<=tolerance)
                    protected_wall+=int((on_wall&(state!=1)).sum())
                    off_wall=known&~on_wall
                    labels[(state==2)&off_wall]=1;labels[(state==3)&off_wall]=2;labels[~known]=5
                    if np.any(state==0):raise ValueError('The source selection changed during cleaning.')
                    np.asarray(ix,dtype='<i8').tofile(idout);labels.astype('<i4').tofile(labout)
                    for k in range(6):
                        use=labels==k;counts[k]+=int(use.sum());sums[k]+=np.asarray(p)[use].sum(0)
                        if len(representatives[k])<32:representatives[k].extend((written+np.flatnonzero(use)[:32-len(representatives[k])]).tolist())
                    written+=len(ix)
        finally:
            support._mmap.close();ids._mmap.close();lab._mmap.close()
    if written!=selected_total:raise ValueError('The scan changed during cleaning. Run again.')
    names=['Supported lining','Isolated points · review','Uneven neighbour spacing · review',
        'Inside lining / attached objects · review','Outside lining / recesses · review','Uncertain lining area · retained']
    kept=[k for k in (0,5) if counts[k]]
    rows=[dict(id=k,name=names[k],preview_indices=representatives[k],points=int(counts[k]),centre=(sums[k]/counts[k]).tolist(),supervoxels=0,linearity=0.,planar_fraction=0.,overlap=0.,automatic_keep=k in kept) for k in range(6) if counts[k]]
    stats=dict(first['statistics']);stats.update(clusters=len(rows),kept_points=int(counts[0]+counts[5]),noise_candidates=int(counts[1:5].sum()),
        isolated_candidates=int(counts[1]),spacing_candidates=int(counts[2]),surface_departure_candidates=int(counts[3]+counts[4]),
        protected_sparse_wall_points=protected_wall,uncertain_retained_points=int(counts[5]),calibration_points=len(sample),lining_tolerance_mm=tolerance*1000,lining_shape=shape,
        reference_radius_m=strict['radius'] if strict else model['radius'],strategy='full-density neighbour evidence and robust lining fit; geometric wall support protects sparse original points',seconds=round(time.monotonic()-started,3))
    if strict:stats.update(cylinder_fit_rms_mm=strict['fit_rms_mm'],cylinder_support_fraction=strict['support_fraction'])
    return dict(kept=kept,clusters=rows,statistics=stats,sampler_version=SAMPLER_VERSION,warnings=[
        'Red points are candidates, not confirmed noise. Joints, recesses, cables and real defects may be in surface-departure groups. Keep any details you need before applying.',
        'Neighbour support uses every selected retained point; only model fitting uses a bounded sample. Coordinates and intensity are unchanged. Sparse points matching the lining and uncertain lining areas stay green.',
        'Circular mode assumes a short straight round tunnel. Follow measured lining supports gentle variation and floor profiles, but neither mode resolves junctions automatically. The published 4 mm threshold and accuracy do not transfer automatically to this scan.'])
