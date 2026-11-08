# Copyright (c) 2026 Damian Gulczynski.
"""Local section frames. Exact polyline tangents, or an estimated smooth curve.

No design geometry is inferred by this module. The fitted path is only an
orientation aid for a selected tunnel branch; measured points are not moved.
"""
import numpy as np


def model(rows, smooth=False, label='Tunnel direction'):
    a = np.asarray(rows, dtype=float)
    if a.ndim != 2 or a.shape[1] < 4 or len(a) < 2 or not np.isfinite(a[:, :4]).all():
        raise ValueError('The section path needs at least two finite nodes.')
    s, p = a[:, 0], a[:, 1:4]
    if np.any(np.diff(s) <= 0) or np.any(np.linalg.norm(np.diff(p, axis=0), axis=1) < 1e-8):
        raise ValueError('The section path has repeated nodes or non-increasing positions.')
    out = dict(stations=s.tolist(), points=p.tolist(), label=label, smooth=bool(smooth))
    if smooth:
        out['slopes'] = np.gradient(p-p[0], s-s[0], axis=0, edge_order=2 if len(p)>2 else 1).tolist()
    return out


def at(path, station):
    s = np.asarray(path['stations']); p = np.asarray(path['points'])
    if not np.isfinite(station) or station < s[0]-1e-8 or station > s[-1]+1e-8:
        raise ValueError('Choose a section position within the displayed tunnel path.')
    station = float(np.clip(station, s[0], s[-1]))
    i = int(np.clip(np.searchsorted(s, station, side='right')-1, 0, len(s)-2))
    h = s[i+1]-s[i]; t = (station-s[i])/h; delta = p[i+1]-p[i]
    if path.get('smooth'):
        d = np.asarray(path['slopes']); a, b = h*d[i], h*d[i+1]
        centre = p[i] + (-2*t**3+3*t*t)*delta + (t**3-2*t*t+t)*a + (t**3-t*t)*b
        tangent = ((-6*t*t+6*t)*delta + (3*t*t-4*t+1)*a + (3*t*t-2*t)*b)/h
    else:
        centre = p[i] + t*delta; tangent = delta/h
    norm = np.linalg.norm(tangent)
    if norm < 1e-8 or not np.isfinite(norm):raise ValueError('The tunnel path has no direction at this position.')
    return centre, tangent/norm


def nearest(path, point):
    """Find a starting station on the displayed path; refined for smooth curves."""
    from scipy.optimize import minimize_scalar
    p=np.asarray(path['points']);s=np.asarray(path['stations']);q=np.asarray(point,float)
    d=np.diff(p,axis=0);t=np.clip(np.einsum('ij,ij->i',q-p[:-1],d)/np.sum(d*d,axis=1),0,1)
    ds=np.linalg.norm(p[:-1]+t[:,None]*d-q,axis=1);i=int(np.argmin(ds))
    station=float(s[i]+t[i]*(s[i+1]-s[i]))
    if path.get('smooth'):
        fit=minimize_scalar(lambda x:float(np.sum((at(path,x)[0]-q)**2)),bounds=(s[i],s[i+1]),method='bounded')
        station=min([station,float(fit.x),float(s[i]),float(s[i+1])],key=lambda x:np.linalg.norm(at(path,x)[0]-q))
    return station


def smooth_centres(rows, quality):
    """Local quadratic fits retain end curvature instead of flattening it.

    Fits use relative coordinates and robust residual weights. This is an
    orientation aid, not a replacement for a reviewed survey alignment.
    """
    out=rows.copy();s=rows[:,0];window=min(7,len(rows))
    support=np.array([0. if q.get('interpolated') else max(.05,(q.get('coverage_deg',0)/360)**2) for q in quality])
    for i,x in enumerate(s):
        lo=max(0,min(i-window//2,len(s)-window));ids=np.arange(lo,lo+window)
        if np.count_nonzero(support[ids])<3:continue
        d=(s[ids]-x)/max(1e-8,np.ptp(s[ids]));a=np.c_[np.ones(window),d,d*d]
        local=rows[ids,1:4]-rows[i,1:4];weights=support[ids].copy()
        for _ in range(3):
            w=np.sqrt(weights);coef=np.linalg.lstsq(a*w[:,None],local*w[:,None],rcond=None)[0]
            error=np.linalg.norm(a@coef-local,axis=1);scale=max(.01,1.4826*float(np.median(error)))
            weights=support[ids]*np.minimum(1,2*scale/np.maximum(error,1e-12))
        out[i,1:4]+=coef[0]
    out[:,0]=np.r_[0,np.cumsum(np.linalg.norm(np.diff(out[:,1:4],axis=0),axis=1))]
    return out


def estimate(points, progress=lambda _:None):
    """Bounded robust circular-lining fit, with explicit unsupported fallback."""
    import scan_geometry
    p=np.asarray(points,float);p=p[np.isfinite(p).all(1)]
    if len(p)<50:raise ValueError('Too few retained overview points to estimate a tunnel direction. Select a longer branch or use a custom normal.')
    # Balance spatial density before PCA and circle fitting; keep memory bounded.
    span=float(np.linalg.norm(np.ptp(p,axis=0)));cell=max(.025,span/900)
    _,ids=np.unique(np.floor((p-p.min(0))/cell).astype(np.int64),axis=0,return_index=True)
    p=p[ids];p=p[np.linspace(0,len(p)-1,min(18000,len(p)),dtype=int)]
    if len(p)<50:raise ValueError('Too few distinct points support a local tunnel path. Enlarge the selected branch.')
    interval=max(.4,min(50.,span/35))
    result=scan_geometry.estimate({'name':'Section direction'},None,interval,0,prepared=(p,len(p)),progress=progress)
    rows=smooth_centres(np.asarray(result['rows']),result['quality']);path=model(rows,True,'Estimated local tunnel direction')
    zero=nearest(path,np.median(p,axis=0));rows[:,0]-=zero;path=model(rows,True,path['label'])
    path['warning']='Estimated from retained points. Check the guide line; select one branch at junctions.'
    if sum(bool(q.get('supported')) for q in result['quality'])<3:
        raise ValueError('Not enough lining supports a local direction. Select a longer single branch, use an alignment, or set a custom normal.')
    path['supported_nodes']=[bool(q.get('supported')) for q in result['quality']]
    path['warning'] += ' Some path sections have weak support.' if not all(path['supported_nodes']) else ''
    return path
