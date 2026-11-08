# Copyright (c) 2026 Damian Gulczynski.
"""Non-destructive full-density selection, exclusion masks and edit history."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from datetime import datetime, timezone
import os, uuid
import numpy as np
import registration_pose
from core import CHUNK


def mask_for(meta, mode='r'):
    path=meta.get('mask_file')
    return np.memmap(path,dtype='u1',mode=mode,shape=(meta['count'],)) if path else None


def select(meta, derived, spec, destination):
    origin=np.asarray(spec['origin'],float); target=np.asarray(spec['target'],float)
    right=np.asarray(spec['right'],float); up=np.asarray(spec['up'],float); forward=np.asarray(spec['forward'],float)
    rect=np.asarray(spec['rect'],float); scale=float(spec['scale']); aspect=float(spec['aspect'])
    start=float(spec['start']); end=float(spec['end']); band=float(spec['band'])
    if any(a.shape!=(3,) or not np.isfinite(a).all() for a in [origin,target,right,up,forward]): raise ValueError('Invalid camera.')
    if rect.shape!=(4,) or not np.isfinite(rect).all() or not all(np.isfinite([scale,aspect,start,end,band])) or scale<=0 or aspect<=0 or band<=0 or end<start: raise ValueError('Invalid selection settings.')
    if rect[2]<=rect[0] or rect[3]<=rect[1]: raise ValueError('Drag a rectangle to select points.')
    p=np.memmap(meta['file'],dtype='<f8',mode='r',shape=(meta['count'],3))
    d=np.memmap(derived,dtype='<f8',mode='r',shape=(meta['count'],4)); mask=mask_for(meta)
    total=0
    with open(destination,'wb') as out:
        for i in range(0,len(p),CHUNK):
            world=registration_pose.points(meta,np.asarray(p[i:i+CHUNK]));v=world-origin-target; info=d[i:i+CHUNK]
            x=v@right/scale; y=v@up*aspect/scale; z=-(v@forward)/(scale*12)
            use=(x>=rect[0])&(x<=rect[2])&(y>=rect[1])&(y<=rect[3])&(abs(z)<=1)&np.isfinite(info[:,0])&(info[:,0]>=start)&(info[:,0]<=end)&(abs(info[:,3])<=band)
            if mask is not None: use&=mask[i:i+len(use)]==0
            def box_mask(box):
                delta=world-np.asarray(box['origin'])-np.asarray(box['target'])
                bx=delta@np.asarray(box['right'])/box['scale']; by=delta@np.asarray(box['up'])*box['aspect']/box['scale']; bz=-(delta@np.asarray(box['forward']))/(box['scale']*12)
                r=box['rect']
                return (bx>=r[0])&(bx<=r[2])&(by>=r[1])&(by<=r[3])&(abs(bz)<=1)&(info[:,0]>=box['start'])&(info[:,0]<=box['end'])&(abs(info[:,3])<=box['band'])
            if len(spec.get('hidden',[]))>20: raise ValueError('Too many hidden regions.')
            for hidden in spec.get('hidden',[]): use &= ~box_mask(hidden)
            if spec.get('isolated'): use &= box_mask(spec['isolated'])
            ids=np.flatnonzero(use).astype('<i8')+i; ids.tofile(out); total+=len(ids)
    return {'file':str(destination),'count':total,'spec':spec}


def apply_edit(meta, selection, work, history, reason, progress=lambda message:None):
    if not selection or selection['count']==0: raise ValueError('Select points first.')
    if not str(reason).strip(): raise ValueError('Enter a reason for removing these points.')
    count=int(selection['count']);n=int(meta['count']);path=asset_folder(work,'working')/(uuid.uuid4().hex+'.mask');new=None;changed=0
    source=Path(selection['file'])
    if source.stat().st_size!=count*8:raise ValueError('The point selection is incomplete. Select the area again.')
    try:
        # Keep the original mask immutable for Undo and shared project snapshots.
        # A first edit creates a sparse zero mask instead of writing N zeroes.
        __import__('mask_storage').copy_mask(meta.get('mask_file'),path,n,progress)
        new=np.memmap(path,dtype='u1',mode='r+',shape=(n,))
        with source.open('rb') as ids_file:
            for start in range(0,count,100000):
                size=min(100000,count-start);ids=np.fromfile(ids_file,'<i8',count=size)
                if len(ids)!=size or (ids<0).any() or (ids>=n).any():raise ValueError('The point selection is no longer valid.')
                ids=np.unique(ids);ids=ids[new[ids]==0]
                new[ids]=1;changed+=len(ids)
                progress(f'Excluding selected points: {start+size:,}/{count:,}')
        if not changed:raise ValueError('No retained points in this selection.')
        new.flush();new._mmap.close();new=None
    except BaseException:
        if new is not None:new._mmap.close()
        path.unlink(missing_ok=True);raise
    entry={'time':datetime.now(timezone.utc).isoformat(),'reason':str(reason).strip()[:500],'count':changed,'before_mask':meta.get('mask_file'),'after_mask':str(path),'spec':selection['spec']}
    meta['mask_file']=str(path); history.append(entry)
    return entry


def export_clean(meta, destination):
    p=np.memmap(meta['file'],dtype='<f8',mode='r',shape=(meta['count'],3)); mask=mask_for(meta)
    with open(destination,'w',encoding='utf8') as f:
        f.write('e,n,h\n')
        for i in range(0,len(p),CHUNK):
            pts=registration_pose.points(meta,np.asarray(p[i:i+CHUNK]))
            if mask is not None: pts=pts[mask[i:i+len(pts)]==0]
            np.savetxt(f,pts,delimiter=',',fmt='%.12f')
    return str(destination)
