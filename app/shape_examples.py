# Copyright (c) 2026 Damian Gulczynski.
"""Capture a compact measured object with separately fitted wall context.

Raw object observations are preserved in float64, with independent neighbouring
wall observations and a local geometric descriptor. A robust quadratic patch
measures offsets in a local surface frame. The front depth layer of a screen
selection is isolated so an opposite tunnel wall does not teach a second object.
Only a cropped example is held in RAM; whole-cloud matching remains streamed.
This is labelled example geometry, not a survey measurement or a neural model.
"""
from pathlib import Path
from contextlib import ExitStack
import hashlib
import json
import math
import numpy as np
from scipy.ndimage import binary_closing, distance_transform_edt, label
from bounded_rows import RowFile
import jobdata
import preview_index
import registration_pose

MAX_CAPTURE = 2_000_000
CONTEXT_SAMPLE = 50000


def region(item, selection, progress, cap=MAX_CAPTURE):
    """Exact original retained observations from conservative source blocks."""
    m=item['meta'];points=[];identities=[];count=0
    spec=jobdata.selection_spec(selection)
    with ExitStack() as stack:
        xyz=stack.enter_context(RowFile(m['file'],'<f8',m['count'],3))
        mask=stack.enter_context(RowFile(m['mask_file'],'u1',m['count'])) if m.get('mask_file') else None
        boxes=preview_index.world_boxes(m,preview_index.bounds(m,progress))
        for start,stop in preview_index.selection_blocks(m,spec,progress,boxes=boxes):
            for begin in range(start,stop,100000):
                n=min(100000,stop-begin);p=registration_pose.points(m,xyz.read(begin,n))
                keep=np.isfinite(p).all(1)&jobdata.selection_mask(p,spec)
                if mask is not None:keep &= mask.read(begin,n)==0
                if keep.any():
                    points.append(p[keep]);identities.append(np.flatnonzero(keep).astype('<i8')+begin);count+=int(keep.sum())
                progress(f'Reading teaching example · {count:,} cropped original points')
                if count>cap:raise ValueError('The teaching crop contains too many points to describe one compact object. Draw one pocket more tightly; whole-cloud searches have no such input-point cap.')
    if not count:raise ValueError('The drawn example contains no retained measured points.')
    return np.vstack(points),np.concatenate(identities)


def context(item, low, high, progress):
    """Bounded actual wall sample; no source points are moved or invented."""
    m=item['meta'];pad=.15
    spec={'mode':'box','minimum':(low-pad).tolist(),'maximum':(high+pad).tolist()}
    sample=np.empty((0,3));seen=0
    with ExitStack() as stack:
        xyz=stack.enter_context(RowFile(m['file'],'<f8',m['count'],3))
        mask=stack.enter_context(RowFile(m['mask_file'],'u1',m['count'])) if m.get('mask_file') else None
        boxes=preview_index.world_boxes(m,preview_index.bounds(m,progress))
        for start,stop in preview_index.selection_blocks(m,spec,progress,boxes=boxes):
            for begin in range(start,stop,100000):
                n=min(100000,stop-begin);p=registration_pose.points(m,xyz.read(begin,n))
                keep=np.isfinite(p).all(1)&jobdata.selection_mask(p,spec)
                keep &= ~((p>=low)&(p<=high)).all(1)
                if mask is not None:keep &= mask.read(begin,n)==0
                q=p[keep];seen+=len(q)
                if len(q):
                    q=np.vstack([sample,q]);_,ix=np.unique(np.floor(q/.003).astype(np.int64),axis=0,return_index=True)
                    q=q[np.sort(ix)];sample=q[np.linspace(0,len(q)-1,min(len(q),CONTEXT_SAMPLE)).astype(int)]
                progress(f'Reading separate wall context · {seen:,} nearby observations checked')
    return sample


def local_offsets(points,wall,view_direction=None):
    if len(wall)<60:raise ValueError('Not enough neighbouring lining outside the example. Include a clear measured wall around the pocket.')
    origin=np.median(wall,axis=0);delta=wall-origin
    _,axes=np.linalg.eigh(delta.T@delta/len(delta));normal=axes[:,0]
    if view_direction is not None:
        view=np.asarray(view_direction,float)
        if view.shape!=(3,) or not np.isfinite(view).all() or np.linalg.norm(view)<1e-9:raise ValueError('Invalid example viewing direction.')
        if normal@view>0:normal=-normal
    else:
        # For explicit 3D boxes the side can be inferred from the example's
        # departure relative to surrounding wall, then retained in metadata.
        if normal[np.argmax(abs(normal))]<0:normal=-normal
    u=axes[:,2];v=np.cross(normal,u);basis=np.column_stack([u,v,normal])
    w=delta@basis;q=(points-origin)@basis;scale=max(float(np.max(np.linalg.norm(w[:,:2],axis=1))),.01)
    def design(xy):
        x,y=(xy/scale).T;return np.c_[np.ones(len(x)),x,y,x*x,x*y,y*y]
    X=design(w[:,:2]);use=np.ones(len(w),bool);old=None;converged=False
    for _ in range(30):
        coef,_,rank,_=np.linalg.lstsq(X[use],w[use,2],rcond=None)
        if rank<6 or np.linalg.cond(X[use])>10000:raise ValueError('Wall context does not span a reliable local surface. Choose a clearer example.')
        error=w[:,2]-X@coef;med=np.median(error[use]);mad=1.4826*np.median(abs(error[use]-med))
        next_use=abs(error-med)<=max(.002,3.5*mad)
        if next_use.sum()<max(60,.4*len(w)):raise ValueError('The surrounding wall does not provide a consistent local reference.')
        if old is not None and np.max(abs(coef-old))<1e-6 and np.mean(use!=next_use)<.002:converged=True;break
        old=coef.copy();use=next_use
    residual=q[:,2]-design(q[:,:2])@coef
    # Signed radial-style convention: positive is away from the camera/interior.
    d=-residual
    rms=float(np.sqrt(np.mean(error[use]**2)))*1000
    return q[:,:2],d,dict(origin=origin.tolist(),basis=basis.tolist(),coefficients=coef.tolist(),scale=scale,
                           wall_rms_mm=rms,wall_points=int(use.sum()),converged=converged)


def template(xy, departures, cfg):
    cell=cfg['grid_mm']/1000
    keys=np.floor((xy-xy.min(0))/cell).astype(int)
    shape=keys.max(0)+1
    if max(shape)>512 or np.prod(shape)>262144:raise ValueError('The example is too large for this grid. Draw one compact object or use a coarser shape grid.')
    mask=np.zeros(shape,bool);depth=np.zeros(shape,float);counts=np.zeros(shape,np.int64)
    np.add.at(depth,tuple(keys.T),abs(departures));np.add.at(counts,tuple(keys.T),1)
    mask=counts>0;depth=np.divide(depth,counts,out=depth,where=counts>0)
    joined=binary_closing(np.pad(mask,2),structure=np.ones((3,3)))[2:-2,2:-2]|mask
    groups,n=label(joined,structure=np.ones((3,3)));sizes=np.bincount(groups.ravel());sizes[0]=0
    best=int(np.argmax(sizes));chosen=groups==best
    if chosen.sum()<8:raise ValueError('The crop contains too little connected shape detail. Draw a clearer pocket or use a finer grid.')
    original=chosen[tuple(keys.T)];rr,cc=np.where(chosen);box=np.s_[rr.min():rr.max()+1,cc.min():cc.max()+1]
    chosen=chosen[box];depth=depth[box];observed=(counts[box]>0)&chosen
    nearest=distance_transform_edt(~observed,return_distances=False,return_indices=True)
    depth=np.where(chosen,depth[tuple(nearest)],0)
    return dict(mask=chosen,depth=depth,long=False,grid_mm=cfg['grid_mm'],example_sign=int(np.sign(np.median(departures))),
                components=n,example_points=int(original.sum()),footprint_cells=int(chosen.sum()),size_m=(np.asarray(chosen.shape)*cell).tolist()),original


def write(folder,points,wall,t,metadata):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    p=np.asarray(points,'<f8');w=np.asarray(wall,'<f8')
    p.tofile(folder/'object.xyz64');w.tofile(folder/'context.xyz64')
    np.savez_compressed(folder/'descriptor.npz',mask=t['mask'],depth=t['depth'],grid_mm=t['grid_mm'])
    data={**metadata,'points':len(p),'context_points':len(w),'grid_mm':t['grid_mm'],
          'side':t.get('side','recess' if t.get('example_sign',1)>0 else 'raised'),'size_m':t['size_m'],'footprint_cells':int(t['mask'].sum()),
          'digest':hashlib.sha256(p.tobytes()).hexdigest()}
    (folder/'example.json').write_text(json.dumps(data,allow_nan=False,separators=(',',':')),encoding='utf8')
    return data


def capture(item,selection,folder,cfg,progress):
    p,ids=region(item,selection,progress);spec=jobdata.selection_spec(selection)
    view=spec.get('forward')
    if view is not None and len(p)>1:
        depths=p@np.asarray(view,float);ordered=np.sort(depths)
        gaps=np.flatnonzero(np.diff(ordered)>.30)
        if len(gaps):
            # Camera forward increases into the scene. Retain the first
            # visible depth layer, not the wall behind the selected object.
            threshold=ordered[gaps[0]]+.001;keep=depths<=threshold;p=p[keep];ids=ids[keep]
    return capture_points(item,p,folder,cfg,progress,view=view)


def capture_points(item,p,folder,cfg,progress,view=None,provenance=None):
    """Use existing measured object points and an independently selected wall."""
    p=np.asarray(p,float)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<8 or len(p)>MAX_CAPTURE or not np.isfinite(p).all():raise ValueError('Teach one compact measured object with at least eight finite observations.')
    low,high=p.min(0),p.max(0)
    if np.linalg.norm(high-low)>3:raise ValueError('Draw one compact object, less than three metres across, as a teaching example.')
    wall=context(item,low,high,progress)
    xy,d,fit=local_offsets(p,wall,view)
    if fit['wall_rms_mm']>cfg['min_offset_mm']:raise ValueError('Surrounding wall roughness exceeds the minimum object offset. Choose clearer measured lining or increase the minimum distance before teaching.')
    if cfg['side']!='either':
        wanted=1 if cfg['side']=='recess' else -1
        departures=d[abs(d)>.003]
        if len(departures) and np.median(departures)*wanted<0:d=-d;fit['side_inferred']=True
    use=(abs(d)>cfg['min_offset_mm']/1000)&(abs(d)<=cfg['max_offset_mm']/1000)
    if cfg['side']=='recess':use &= d>0
    elif cfg['side']=='raised':use &= d<0
    if use.sum()<8:raise ValueError('No measured object points lie outside the wall band in this crop. Check the selected side and depth.')
    t,connected=template(xy[use],d[use],cfg);chosen=p[use][connected]
    if cfg['side']=='either':t['side']='either'
    warnings=[] if fit['converged'] else ['The local wall fit did not fully stabilize. Review this example and its separate wall context.']
    metadata=dict(reference=fit,warnings=warnings,source_name=item['name'],source_item_id=item['id'],kind='manual',method='Local measured object with separate quadratic wall context')
    metadata.update(provenance or {})
    return write(folder,chosen,wall,t,metadata)
