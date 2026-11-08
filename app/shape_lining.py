# Copyright (c) 2026 Damian Gulczynski.
"""Follow nearby measured lining for compact shape selection around a tunnel.

A bounded calibration sample supplies periodic station/arc neighbours. Robust
quadratic patches exclude a small central disk and reject departures before
predicting a smooth wall-offset grid. Original observations are classified by
bilinear interpolation, with explicit support checks and no coordinate edits.
All patch batches, grid storage and neighbour queries have bounded working sets.
This is a measured background estimate, not design geometry or survey control.
"""
import math
import numpy as np
from scipy.spatial import cKDTree
import compute_runtime
import tunnel_lining

MAX_GRID_CELLS=350000
PATCH_BATCH=384
NEIGHBOURS=256


def coordinates(points,model):
    q=np.asarray(points,float)-model['reference'];station=q@model['axis'];nodes=model['nodes']
    x=q@model['u']-np.interp(station,nodes[:,0],nodes[:,1])
    y=q@model['v']-np.interp(station,nodes[:,0],nodes[:,2])
    angle=np.mod(np.arctan2(y,x),2*np.pi)
    offset=np.hypot(x,y)-np.interp(station,nodes[:,0],nodes[:,3])
    return station,angle,offset


def _patches(xy,z,queries,tree,period,limit):
    distance,indices=tree.query(queries,k=min(NEIGHBOURS,len(xy)*3),workers=compute_runtime.workers())
    if indices.ndim==1:indices=indices[:,None];distance=distance[:,None]
    owner=indices%len(xy);copy=indices//len(xy)-1
    delta=xy[owner]-queries[:,None,:];delta[:,:,1]+=copy*period
    scale=np.maximum(.20,distance[:,-1]);local=delta/scale[:,None,None];x,y=local[:,:,0],local[:,:,1]
    design=np.stack([np.ones(x.shape),x,y,x*x,x*y,y*y],axis=-1);values=z[owner]
    # A hole should not define its own background. Dense calibration points
    # within 90 mm of a query do not participate in that query's wall fit.
    base=(distance>=.09)&(distance<=min(1.5,period*.22))
    weights=base.astype(float);safe=np.zeros(len(queries),bool);coef=np.zeros((len(queries),6))
    for _ in range(5):
        normal=np.einsum('nki,nk,nkj->nij',design,weights,design,optimize=True)
        rhs=np.einsum('nki,nk,nk->ni',design,weights,values,optimize=True)
        eigen=np.linalg.eigvalsh(normal);safe=(eigen[:,0]>np.maximum(eigen[:,-1],1)*1e-7)&(np.count_nonzero(weights,axis=1)>=24)
        normal[~safe]=np.eye(6);rhs[~safe]=0
        coef=np.linalg.solve(normal+np.eye(6)*1e-10,rhs[...,None])[...,0]
        error=values-np.einsum('nki,ni->nk',design,coef)
        masked=np.where(weights>0,error,np.nan);masked[~safe]=0;med=np.nanmedian(masked,axis=1)
        masked=np.where(weights>0,abs(error-med[:,None]),np.nan);masked[~safe]=0;mad=1.4826*np.nanmedian(masked,axis=1)
        cut=np.maximum(.003,4*mad);ratio=abs(error-med[:,None])/cut[:,None]
        weights=base*np.where(ratio<1,(1-np.minimum(ratio,1)**2)**2,0)
        weights[~safe]=0
    residual=values-np.einsum('nki,ni->nk',design,coef)
    rms=np.sqrt(np.sum(weights*residual**2,axis=1)/np.maximum(weights.sum(1),1e-12))
    safe&=(np.count_nonzero(weights,axis=1)>=24)&np.isfinite(rms)&(rms<=limit)&np.isfinite(coef[:,0])
    return coef[:,0],safe,rms


def build(sample,model,min_offset_mm=5.,progress=lambda _:None,cancel=lambda:False):
    sample=np.asarray(sample,float)
    if sample.ndim!=2 or sample.shape[1]!=3 or len(sample)<100 or not np.isfinite(sample).all():raise ValueError('Local lining needs at least 100 finite measured calibration points.')
    if not np.isfinite(min_offset_mm) or min_offset_mm<=0:raise ValueError('Enter a positive lining-offset threshold.')
    station,angle,offset=coordinates(sample,model);period=2*np.pi*float(model['radius'])
    if not math.isfinite(period) or period<=0:raise ValueError('The local lining reference needs a positive tunnel radius.')
    # Reject remote services before the robust neighbourhood fit. This gate is
    # deliberately wider than compact bolt depths and ordinary lining changes.
    angular=np.minimum(model['nangle']-1,(angle*model['nangle']/(2*np.pi)).astype(int))
    keep=abs(offset-np.asarray(model['profile'])[angular])<=max(.18,4*model.get('thickness',.04))
    if keep.sum()<100:raise ValueError('Too little nearby measured lining supports local shape matching.')
    xy=np.c_[station[keep],angle[keep]*model['radius']];z=offset[keep]
    periodic=np.vstack([xy-[0,period],xy,xy+[0,period]]);tree=cKDTree(periodic)
    columns=max(48,min(720,int(math.ceil(period/.12))));first=float(station.min());last=float(station.max())
    rows=max(2,min(MAX_GRID_CELLS//columns,int(math.ceil((last-first)/.30))+1));pitch=max((last-first)/(rows-1),.01)
    target=np.zeros((rows,columns));supported=np.zeros((rows,columns),bool);rms=np.zeros((rows,columns))
    limit=max(.00025,min_offset_mm/1000*.65);total=rows*columns
    for start in range(0,total,PATCH_BATCH):
        tunnel_lining._emit(progress,cancel,f'Following nearby measured lining · {start:,}/{total:,} reference patches')
        ids=np.arange(start,min(total,start+PATCH_BATCH));queries=np.c_[first+(ids//columns)*pitch,(ids%columns)*period/columns]
        value,valid,error=_patches(xy,z,queries,tree,period,limit)
        target.flat[ids]=value;supported.flat[ids]=valid;rms.flat[ids]=error
    tunnel_lining._emit(progress,cancel,f'Local lining ready · {supported.sum():,}/{total:,} supported reference patches')
    result=dict(model);result['shape_reference']=dict(target=target,supported=supported,first=first,pitch=pitch,period=period,
        rows=rows,columns=columns,valid_patches=int(supported.sum()),patches=total,fit_limit_mm=limit*1000,
        median_wall_rms_mm=float(np.median(rms[supported]))*1000 if supported.any() else None)
    return result


def deviations(points,model,with_angle=False):
    reference=model['shape_reference'];station,angle,offset=coordinates(points,model)
    row=(station-reference['first'])/reference['pitch'];col=angle*reference['columns']/(2*np.pi)
    r=np.clip(np.floor(row).astype(np.int64),0,reference['rows']-2);c=np.floor(col).astype(np.int64)%reference['columns'];next_c=(c+1)%reference['columns']
    x=np.clip(row-r,0,1);y=col-np.floor(col);target=reference['target'];valid=reference['supported']
    value=(1-x)*((1-y)*target[r,c]+y*target[r,next_c])+x*((1-y)*target[r+1,c]+y*target[r+1,next_c])
    supported=(row>=0)&(row<=reference['rows']-1)&valid[r,c]&valid[r,next_c]&valid[r+1,c]&valid[r+1,next_c]
    return (offset-value,supported,angle) if with_angle else (offset-value,supported)
