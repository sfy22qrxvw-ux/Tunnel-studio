# Copyright (c) 2026 Damian Gulczynski.
"""Memory-bounded engineering analysis helpers for Tunnel Studio."""
from pathlib import Path
import math
import numpy as np
from scipy.spatial import cKDTree


def sample_points(meta,max_points=200000):
    import jobdata,registration_pose
    n=int(meta.get('count',0))
    if n<1:raise ValueError('The selected scan has no points.')
    max_points=max(1000,min(1_000_000,int(max_points)))
    mask=Path(meta['mask_file']) if meta.get('mask_file') else None
    if mask:
        if not mask.is_file() or mask.stat().st_size!=n:raise ValueError('The scan deletion mask is missing or incomplete.')
        retained=0
        with mask.open('rb') as f:
            for start in range(0,n,1_000_000):retained+=int(np.count_nonzero(np.fromfile(f,'u1',count=min(1_000_000,n-start))==0))
        stride=max(1,math.ceil(retained/max_points));offset=0;parts=[]
        with mask.open('rb') as f:
            for start in range(0,n,1_000_000):
                local=np.flatnonzero(np.fromfile(f,'u1',count=min(1_000_000,n-start))==0)
                chosen=local[(-offset)%stride::stride]+start;offset+=len(local)
                if len(chosen):parts.append(chosen)
        ids=np.concatenate(parts) if parts else np.empty(0,dtype=np.int64)
    else:ids=np.arange(0,n,max(1,math.ceil(n/max_points)),dtype=np.int64)
    pts=registration_pose.points(meta,jobdata._display_rows(meta['file'],n,ids))
    return pts[np.isfinite(pts).all(axis=1)]


def cloud_compare(source,reference,max_points=200000,cutoff=0,progress=lambda s:None):
    a=sample_points(source,max_points);b=sample_points(reference,max_points)
    if len(a)<3 or len(b)<3:raise ValueError('Both scans need at least 3 valid points for comparison.')
    progress(f'Cloud comparison · source {len(a):,} pts · reference {len(b):,} pts')
    tree=cKDTree(b);dist,_=tree.query(a,k=1,workers=-1)
    dist=np.asarray(dist,float);cutoff=float(cutoff or 0)
    if cutoff>0:dist=dist[dist<=cutoff]
    if not len(dist):raise ValueError('No comparison distances remain inside the chosen cutoff.')
    return {
        'mode':'Sampled Cloud vs Cloud · nearest point',
        'source_sample':int(len(a)),'reference_sample':int(len(b)),'used':int(len(dist)),
        'min_mm':float(np.min(dist)*1000),'max_mm':float(np.max(dist)*1000),
        'mean_mm':float(np.mean(dist)*1000),'median_mm':float(np.median(dist)*1000),
        'rms_mm':float(np.sqrt(np.mean(dist**2))*1000),
        'p95_mm':float(np.percentile(dist,95)*1000),'p99_mm':float(np.percentile(dist,99)*1000),
        'cutoff_mm':cutoff*1000 if cutoff>0 else None,
        'note':'Sampled screening only; use Compare / Inspect for every retained point in a full scan or whole bundle. Nearest-point cloud comparison is unsigned. Use a surface/design normal-based workflow for signed deviations.'
    }


def geometry_qa(meta,max_points=30000,k=12,progress=lambda s:None):
    pts=sample_points(meta,max_points);k=max(4,min(32,int(k)))
    if len(pts)<=k:raise ValueError('Not enough valid points for geometry QA.')
    progress(f'Geometry QA · {len(pts):,} sampled points · {k} neighbours')
    tree=cKDTree(pts);dist,ids=tree.query(pts,k=k+1,workers=-1);knn=dist[:,1:]
    density_radius=np.maximum(knn[:,-1],1e-12);surface_density=k/(math.pi*density_radius**2)
    # Robust roughness on a bounded subset; local PCA distance to best-fit plane.
    step=max(1,len(pts)//12000);indices=np.arange(0,len(pts),step);rough=[]
    for c,i in enumerate(indices):
        q=pts[ids[i,1:]];centre=q.mean(0);cov=(q-centre).T@(q-centre)/max(1,len(q)-1);vals,vecs=np.linalg.eigh(cov);normal=vecs[:,np.argmin(vals)];rough.append(abs(float((pts[i]-centre)@normal)))
        if c and c%2000==0:progress(f'Geometry QA roughness · {c:,} / {len(indices):,}')
    rough=np.asarray(rough,float)
    return {
        'mode':'Geometry QA', 'sample':int(len(pts)), 'neighbours':k,
        'nn_median_mm':float(np.median(knn[:,0])*1000),'nn_p95_mm':float(np.percentile(knn[:,0],95)*1000),
        'surface_density_median':float(np.median(surface_density)),
        'roughness_median_mm':float(np.median(rough)*1000),'roughness_p95_mm':float(np.percentile(rough,95)*1000),
        'note':'Roughness is local point-to-best-fit-plane distance on a representative sample. Density is neighbourhood-based and varies with scan incidence/range.'
    }
