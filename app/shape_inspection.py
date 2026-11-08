# Copyright (c) 2026 Damian Gulczynski.
"""Inspect a shape match using paged original observations and nearby lining.

Exact 64-bit review identities retrieve every matched observation in bounded
owned reads. A disposable disk cache holds float64 world coordinates, measured
intensity and review flags; 100k-point binary display pages use a local origin.
Nearby lining is a separately bounded context sample. View actions never alter
selection, training labels, source masks, poses or project state. Publication is
atomic with respect to cancellation, changed sources and replaced reviews.
This is inspection of geometric candidates, not proof of an object's identity.
"""
from contextlib import ExitStack
from pathlib import Path
import math
import shutil
import sqlite3
import uuid
import numpy as np
from bounded_rows import RowFile
import display_buffers
import shape_examples
import shape_matching
import shape_selection

BLOCK=100000
MAX_CACHED=4
CACHE_BYTES=512*1024*1024


def _group(row,ident,region=None,sort='id'):
    if type(ident) is not int or ident<1:raise ValueError('Choose a matching pocket to inspect.')
    db=sqlite3.connect(row['review_file'])
    try:
        table,params,_,ordering=shape_selection.review_view(row,region,sort)
        query=f'''WITH ordered AS (
                    SELECT m.id,m.points,m.selected_points,m.score,m.side,
                           lag(m.id) OVER scope AS previous,lead(m.id) OVER scope AS following,
                           row_number() OVER scope - 1 AS rank
                    FROM {table} WINDOW scope AS (ORDER BY {ordering})
                  ) SELECT * FROM ordered WHERE id=?'''
        item=db.execute(query,(*params,ident)).fetchone()
        if item is None or item[1]<1:raise ValueError('This shape match is unavailable. Refresh the review.')
        return dict(group_id=item[0],count=item[1],selected_count=item[2],similarity_percent=item[3]*100,
                    side='recess' if item[4]>0 else 'raised',previous=item[5],next=item[6],page=item[7]//shape_selection.PAGE_SIZE)
    finally:db.close()


def _discard(entry):
    shutil.rmtree(entry['folder'],ignore_errors=True)


def prepare(s,body):
    row=shape_selection.current(s,body)
    if row.get('mode')!='shape':raise ValueError('Inspect a matching shape from Find similar shapes.')
    if row['statistics'].get('continuing_service'):raise ValueError('Inspect individual compact pockets or fittings. Continuing service groups may span the tunnel; use the main view to review them.')
    group=_group(row,body.get('group_id'),body.get('region'),body.get('sort','id'))
    cache=row.get('inspections',[])
    for entry in cache:
        if entry['group_id']==group['group_id'] and entry['revision']==row['revision']:
            _validate(entry);row['inspections']=[entry]+[e for e in cache if e is not entry];row['inspection_id']=entry['id'];return _public(entry,group)
    folder=Path(row['folder'])/('inspect-'+uuid.uuid4().hex);folder.mkdir()
    keep=False
    try:
        item=shape_selection.source(s,row['source_id']);meta=item['meta'];n=row['count']
        point_path=folder/'object.xyz64';id_path=folder/'object.ids';flags_path=folder/'object.selection';intensity_path=folder/'object.intensity32'
        written=0;low=np.full(3,np.inf);high=-low;values=[];stride=max(1,math.ceil(group['count']/20000))
        with ExitStack() as stack,point_path.open('wb') as out,id_path.open('wb') as io,flags_path.open('wb') as fo,intensity_path.open('wb') as co:
            ids=stack.enter_context(RowFile(row['point_ids_file'],'<i8',n))
            labels=stack.enter_context(RowFile(row['labels_file'],'<i4',n))
            flags=stack.enter_context(RowFile(row['selected_file'],'u1',n))
            xyz=stack.enter_context(RowFile(meta['file'],'<f8',meta['count'],3))
            attrs=stack.enter_context(RowFile(meta['attributes_file'],'<f4',meta['count'],4)) if meta.get('attributes_file') else None
            for start in range(0,n,BLOCK):
                size=min(BLOCK,n-start);which=labels.read(start,size)==group['group_id']
                if which.any():
                    selected_ids=ids.read(start,size)[which];q=shape_selection.registration_pose.points(meta,xyz.take(selected_ids))
                    if not np.isfinite(q).all():raise ValueError('The inspected object contains invalid measured coordinates.')
                    intensity=attrs.take(selected_ids)[:,0] if attrs is not None else np.full(len(q),np.nan,'<f4')
                    use=(np.arange(len(q))+written)%stride==0
                    if use.any():values.extend(intensity[use].tolist())
                    q.astype('<f8',copy=False).tofile(out);selected_ids.astype('<i8',copy=False).tofile(io)
                    flags.read(start,size)[which].tofile(fo);intensity.astype('<f4',copy=False).tofile(co)
                    low=np.minimum(low,q.min(0));high=np.maximum(high,q.max(0));written+=len(q)
                s.progress(f'Loading original pocket points · {written:,}/{group["count"]:,} · {start+size:,}/{n:,} review IDs checked')
        if written!=group['count']:raise ValueError('The shape match changed while its detail was loading. Refresh the review.')
        context=shape_examples.context(item,low,high,s.progress)
        context_path=folder/'context.xyz64';context.astype('<f8',copy=False).tofile(context_path)
        normal=None
        if len(context)>=6:
            delta=context-np.mean(context,axis=0);eigen,basis=np.linalg.eigh(delta.T@delta/len(delta))
            if eigen[1]>1e-10 and eigen[0]<.35*eigen[1]:normal=basis[:,0].tolist()
        finite=np.asarray(values,float);finite=finite[np.isfinite(finite)]
        intensity_range=np.percentile(finite,[2,98]).tolist() if len(finite) else [0.,1.]
        origin=low/2+high/2
        entry=dict(id=uuid.uuid4().hex,group_id=group['group_id'],revision=row['revision'],review_id=row['id'],source_id=row['source_id'],
                   folder=str(folder),file=str(point_path),point_ids_file=str(id_path),mask_file=str(flags_path),attributes_file=str(intensity_path),count=written,
                   context=dict(file=str(context_path),count=len(context)),origin=origin.tolist(),minimum=low.tolist(),maximum=high.tolist(),normal=normal,
                   intensity_range=intensity_range,has_intensity=bool(len(finite)),bytes=written*37+len(context)*24)
        shape_selection.current(s,body);s.progress('Pocket detail ready · original geometry preserved')
        row['inspections']=[entry]+[e for e in cache if e['revision']==row['revision']]
        for old in cache:
            if old['revision']!=row['revision']:_discard(old)
        while len(row['inspections'])>1 and (len(row['inspections'])>MAX_CACHED or sum(e['bytes'] for e in row['inspections'])>CACHE_BYTES):
            _discard(row['inspections'].pop())
        row['inspection_id']=entry['id'];keep=True
        return _public(entry,group)
    finally:
        if not keep:shutil.rmtree(folder,ignore_errors=True)


def _validate(entry):
    for key,dtype,width in [('file','<f8',3),('point_ids_file','<i8',1),('mask_file','u1',1),('attributes_file','<f4',1)]:RowFile(entry[key],dtype,entry['count'],width)
    RowFile(entry['context']['file'],'<f8',entry['context']['count'],3)


def _public(entry,group):
    return {**group,**{k:entry[k] for k in ('id','review_id','revision','source_id','origin','minimum','maximum','normal','has_intensity')},
            'context_count':entry['context']['count'],'context_sampled':True,'page_points':BLOCK,'original_points':entry['count'],
            'method':'Original measured match observations; paged full-density inspection',
            'note':'Inspection does not change removal selection or teach the library. Nearby lining is a display sample.'}


def current(s,args):
    row=shape_selection.current(s)
    ident=args.get('inspection',[row.get('inspection_id')])[0]
    entry=next((e for e in row.get('inspections',[]) if e['id']==ident),None)
    if entry is None or entry['review_id']!=row['id'] or entry['revision']!=row['revision']:raise ValueError('This pocket view is out of date. Inspect it again from the current review.')
    return row,entry


def get(s,h,path,args):
    if path!='/api/shape-inspection':return False
    if s.JOB.get('busy'):raise ValueError('Wait for pocket preparation or the current operation to finish.')
    row,entry=current(s,args)
    if args.get('part'):
        part=args['part'][0]
        if part not in ('object','context'):raise ValueError('Choose measured pocket points or nearby context.')
        try:start=int(args.get('start',[0])[0]);size=int(args.get('size',[BLOCK])[0])
        except (TypeError,ValueError):raise ValueError('Enter a valid pocket display page.')
        count=entry['count'] if part=='object' else entry['context']['count']
        if start<0 or start>count or not 1<=size<=BLOCK:raise ValueError('The pocket display page is outside its supported range.')
        size=min(size,count-start);file=entry['file'] if part=='object' else entry['context']['file']
        with RowFile(file,'<f8',count,3) as rows:q=rows.read(start,size)
        colours=np.tile([.42,.53,.60],(len(q),1)).astype('<f4')
        if part=='object':
            with RowFile(entry['attributes_file'],'<f4',count) as values:raw=values.read(start,size)
            valid=np.isfinite(raw);lo,hi=entry['intensity_range'];shade=np.full(len(q),.75)
            if hi>lo:shade[valid]=.08+.92*np.clip((raw[valid]-lo)/(hi-lo),0,1)
            colours[:]=shade[:,None]
            if args.get('highlight',['1'])[0]!='0':
                with RowFile(entry['mask_file'],'u1',count) as flags:selected=flags.read(start,size)!=0
                colours[:]=np.where(selected[:,None],[1.,.55,.12],[.1,.8,.85])*(.35+.65*shade[:,None])
        preview=dict(packed=np.c_[q-entry['origin'],colours].astype('<f4'),origin=entry['origin'],source_id=entry['source_id'],sample_count=len(q),
                     inspection_id=entry['id'],group_id=entry['group_id'],start=start,part=part,total_points=count,complete=start+size==count,sampled=False)
        # Re-check the active revision immediately before publishing any page.
        current(s,args);h.send(display_buffers.encode(preview),'application/octet-stream');return True
    region,sort=shape_selection.review_view_args(args)
    h.send(_public(entry,_group(row,entry['group_id'],region,sort)));return True
