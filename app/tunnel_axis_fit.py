# Copyright (c) 2026 Damian Gulczynski.
"""Robust lining sections and an iteratively reoriented, regularised centreline.

This estimates a circular lining, not a design alignment. Unsupported sections
are interpolated and explicitly labelled; they never become measured evidence.
"""
import numpy as np
from scipy.linalg import solve_banded
from scipy.optimize import least_squares
from tunnel_lining import circle_seed


def frame(axis):
    axis=np.asarray(axis,float);axis/=np.linalg.norm(axis)
    ref=np.eye(3)[np.argmin(abs(axis))];u=np.cross(ref,axis);u/=np.linalg.norm(u)
    return axis,u,np.cross(axis,u)


def lining(q,rng,radius_hint=None):
    if len(q)<60:return None
    # A dense wall must not overwhelm the sparsely observed opposite wall.
    _,unique=np.unique(np.floor(q/.035).astype(np.int64),axis=0,return_index=True)
    q=q[unique]
    if len(q)<40:return None
    try:seed,_=circle_seed(q,.12,rng)
    except ValueError:return None
    centre,radius,_,_=seed
    if radius_hint and not .72*radius_hint<radius<1.28*radius_hint:return None
    shell=q[abs(np.linalg.norm(q-centre,axis=1)-radius)<max(.10,.045*radius)]
    if len(shell)<35:return None
    angle=np.mod(np.arctan2(*(shell-centre).T[::-1]),2*np.pi)
    bins=np.minimum(71,(angle*72/(2*np.pi)).astype(int))
    balanced=np.array([np.median(shell[bins==b],axis=0) for b in np.unique(bins)])
    if len(balanced)<24:return None
    def residual(p):return np.linalg.norm(balanced-p[:2],axis=1)-p[2]
    fit=least_squares(residual,[*centre,radius],loss='soft_l1',f_scale=.025,max_nfev=60)
    centre=fit.x[:2];radius=float(fit.x[2]);errors=residual(fit.x)
    good=abs(errors)<max(.08,.04*radius)
    if good.sum()<24 or not .15<radius<50:return None
    angles=np.mod(np.arctan2(*(balanced[good]-centre).T[::-1]),2*np.pi)
    occupied=len(np.unique((angles*72/(2*np.pi)).astype(int)))*5.
    if occupied<150:return None
    residual_m=float(np.sqrt(np.mean(errors[good]**2)))
    # Partial arcs can inform the curve, but are not called well-supported.
    return dict(centre=centre,radius=radius,residual_m=residual_m,
                coverage_deg=occupied,sample_points=len(q),supported=occupied>=210,
                weight=(occupied/360)**2/(1+(residual_m/.025)**2))


def smooth(stations,values,weights,amount):
    """The same robust second-difference fit in O(nodes) matrix storage."""
    n=len(stations);good=np.flatnonzero(weights>0)
    if len(good)<2:return values.copy(),np.zeros(n)
    filled=np.column_stack([np.interp(stations,stations[good],values[good,j]) for j in range(values.shape[1])])
    if not amount:return filled,np.linalg.norm(filled-values,axis=1)
    ds=float(np.median(np.diff(stations)));strength=((2+amount*.5)/max(ds,.1))**4
    # Each row of the old D matrix is [1, -2, 1]. D.T @ D is
    # pentadiagonal, including its different first/last two coefficients.
    # Store scipy's five diagonals directly; never form an n-by-n identity,
    # penalty or weighted system. This also covers the n=2 zero-penalty case.
    penalty=np.zeros((5,n))
    if n>=3:
        penalty[2,:-2]+=strength;penalty[2,1:-1]+=4*strength;penalty[2,2:]+=strength
        penalty[1,1:-1]-=2*strength;penalty[1,2:]-=2*strength
        penalty[3,:-2]-=2*strength;penalty[3,1:-1]-=2*strength
        penalty[0,2:]=strength;penalty[4,:-2]=strength
    offset=filled[good[0]].copy();local=filled-offset;w=weights.copy();result=local.copy()
    for _ in range(4):
        system=penalty.copy();system[2]+=w+1e-9
        result=solve_banded((2,2),system,w[:,None]*local,overwrite_ab=True,overwrite_b=True,check_finite=False)
        error=np.linalg.norm(result-local,axis=1)
        scale=max(.02,1.4826*float(np.median(error[good])))
        w=weights*np.minimum(1,2*scale/np.maximum(error,1e-12))
    return result+offset,np.linalg.norm(result-local,axis=1)


def fit(points,origin,axis,interval,smoothing,progress):
    normal,u,v=frame(axis);local=points-origin;t=local@normal
    low,high=np.quantile(t,[.01,.99])
    if high-low<1e-5:raise ValueError('The selected points have no usable length in the guessed direction. Select a larger area.')
    stations=np.arange(low,high+interval*.01,interval)
    if len(stations)<3:stations=np.linspace(low,high,3)
    # All stations retain the requested interval. A stable one-dimensional
    # index avoids repeatedly checking the whole fitting sample at every node.
    order=np.argsort(t,kind='stable');ordered_t=t[order]
    centres=np.array([origin+s*normal for s in stations]);tangents=np.tile(normal,(len(stations),1))
    radius_hint=None;quality=[];rng=np.random.default_rng(134);reasons=[]
    for iteration in range(3):
        measured=[];quality=[];radii=[];weights=[]
        for i,(station,centre,tangent) in enumerate(zip(stations,centres,tangents)):
            tangent,right,up=frame(tangent)
            # Restrict along the initial branch first, then cut perpendicular to
            # the current local tangent. This avoids mixing neighbouring bends.
            reach=max(interval*2.5,(radius_hint or 5)*1.5)
            first=np.searchsorted(ordered_t,np.nextafter(station-reach,-np.inf),side='left')
            last=np.searchsorted(ordered_t,np.nextafter(station+reach,np.inf),side='right')
            near=np.sort(order[first:last])
            # Keep the original strict predicate and original row order, so
            # section/RANSAC inputs retain their established numerical behavior.
            near=near[abs(t[near]-station)<reach]
            delta=points[near]-centre
            delta=delta[abs(delta@tangent)<=max(.18,interval*.38)]
            q=np.c_[delta@right,delta@up]
            info=lining(q,rng,radius_hint)
            if info:
                xy=info.pop('centre');c=centre+xy[0]*right+xy[1]*up
                # Keep stations comparable between iterations and avoid drift.
                c+=normal*(station-(c-origin)@normal)
                measured.append(c);radii.append(info.pop('radius'));weights.append(info.pop('weight'));quality.append(info)
            else:
                if iteration==0 and len(q):
                    middle=(np.quantile(q,.05,axis=0)+np.quantile(q,.95,axis=0))/2
                    c=centre+middle[0]*right+middle[1]*up
                else:c=centre
                measured.append(c);radii.append(radius_hint or (max(.15,float(np.median(np.linalg.norm(q,axis=1)))) if len(q) else 1))
                weights.append(0.);quality.append(dict(residual_m=None,coverage_deg=0,sample_points=len(q),supported=False))
            progress(f'Following tunnel lining · pass {iteration+1}/3 · section {i+1}/{len(stations)}')
        measured=np.asarray(measured);weights=np.asarray(weights);radii=np.asarray(radii);good=weights>0
        if good.sum()>=3:
            radius_hint=float(np.median(radii[good]))
            # Section radius outliers usually indicate a junction or an object.
            good&=abs(radii-radius_hint)<=max(.15,.12*radius_hint);weights[~good]=0
        if good.sum()<3:
            centres=measured;reasons.append('Too few sections support the circular lining; this is an unverified editable guess.');break
        centres,shifts=smooth(stations,measured,weights,smoothing)
        radius_curve,_=smooth(stations,radii[:,None],weights,max(1,smoothing));radii=radius_curve[:,0]
        for j,info in enumerate(quality):
            info['supported']=bool(info['supported'] and weights[j]>0)
            info['interpolated']=bool(weights[j]==0);info['curve_offset_m']=float(shifts[j])
        tangents=np.gradient(centres,axis=0);tangents/=np.maximum(1e-12,np.linalg.norm(tangents,axis=1))[:,None]
    if any(not q['supported'] for q in quality):reasons.append('Some sections have incomplete lining or were bridged by the smooth curve; review these sections.')
    if any(q.get('curve_offset_m',0)>.15 for q in quality):reasons.append('Some fitted section centres disagree with the smooth line by over 0.15 m; check for branches or missing walls.')
    rows=np.c_[stations,centres,np.maximum(.15,radii)]
    return rows,quality,reasons
