# Copyright (c) 2026 Damian Gulczynski.
"""Bounded original-point reads in the registered frame for lining analysis."""
from contextlib import ExitStack
import numpy as np
import pointio,jobdata,preview_index,registration_pose

def batches(s,meta,key,selection,block,cancelled=lambda:False):
    from surface_api import _selection_spec
    spec,_,_,start,end=_selection_spec(s,meta,key,selection)
    total=meta['count'];pending_ids=[];pending_points=[];pending_count=selected=0
    area=spec.get('clip_box') or (spec if spec['mode'] in ('box','oriented_box') else None)

    def checkpoint(message=None):
        if cancelled():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
        if message:s.progress(message)

    if area and total:
        ranges=list(preview_index.indexed_selection_blocks(meta,area,checkpoint))
        read_total=sum(stop-i for i,stop in ranges)
    else:
        ranges=((i,min(total,i+pointio.CHUNK)) for i in range(0,total,pointio.CHUNK))
        read_total=total
    read=0
    with ExitStack() as stack:
        points=stack.enter_context(open(meta['file'],'rb'))
        mask=stack.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        derived=stack.enter_context(open(s.STATE['derived'],'rb')) if spec['mode']=='chainage' else None
        for i,stop in ranges:
            checkpoint()
            if area:points.seek(i*24)
            n=stop-i;q=np.fromfile(points,'<f8',count=n*3)
            if len(q)!=n*3:raise ValueError('Incomplete point cache.')
            q=registration_pose.points(meta,q.reshape(n,3));use=np.isfinite(q).all(1)
            if mask is not None:
                if area:mask.seek(i)
                excluded=np.fromfile(mask,'u1',count=n)
                if len(excluded)!=n:raise ValueError('Incomplete point mask.')
                use&=excluded==0
            if spec['mode'] in ('box','oriented_box','screen_polygon','screen_brush'):use&=jobdata.selection_mask(q,spec)
            elif derived is not None:
                if area:derived.seek(i*32)
                d=np.fromfile(derived,'<f8',count=n*4)
                if len(d)!=n*4:raise ValueError('Incomplete analysis cache.')
                ch=d.reshape(n,4)[:,0];use&=np.isfinite(ch)&(ch>=start)&(ch<=end)
            if spec['mode'] in ('full','chainage') and spec.get('clip_box'):use&=jobdata.selection_mask(q,spec['clip_box'])
            ids=np.flatnonzero(use).astype('<i8')+i
            if len(ids):pending_ids.append(ids);pending_points.append(q[use]);pending_count+=len(ids);selected+=len(ids)
            read+=n
            s.progress(f'Reading original coordinates · {read:,}/{read_total:,} source rows read · {selected:,} points inside area')
            while pending_count>=block:
                all_ids=np.concatenate(pending_ids);all_points=np.concatenate(pending_points)
                pending_count-=block
                pending_ids=[all_ids[block:].copy()] if pending_count else []
                pending_points=[all_points[block:].copy()] if pending_count else []
                yield all_ids[:block],all_points[:block],spec
        if pending_count:yield np.concatenate(pending_ids),np.concatenate(pending_points),spec
