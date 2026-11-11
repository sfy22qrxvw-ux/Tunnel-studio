# Copyright (c) 2026 Damian Gulczynski.
"""Review whole-cloud shape matches and edit exact original point identities.

The drawn area is the teaching example, not a search boundary. Bounded owned
reads, disk-backed evidence and immutable ID/selection files keep the workload
independent of source point count. Publication replaces a completed preview
only after revision and cancellation checks. Reviewed removal/separation uses
the existing atomic scan-edit and workspace Undo path. Geometric matches are
not a trained semantic classifier, survey control or reconstructed lining.
"""
from bisect import bisect_left
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
import copy
import errno
import hashlib
import json
import math
import shutil
import sqlite3
import tempfile
import uuid

import numpy as np
from bounded_rows import RowFile
from project_data_layout import folder as asset_folder
import feature_selection
import jobdata
import registration_pose
import scan_edits
import shape_matching

PAGE_SIZE=40
CIRCLE_SCOPE_CACHE_BYTES=64*1024**2
CIRCLE_INPUT=np.dtype([('id','<i8'),('xyz','<f8',(3,))])


def review_view(row,region,sort):
    """Share one ordering between the review list and pocket navigation."""
    table='matches m'
    params=()
    points='m.points'
    if region is not None:
        coverage=row.get('coverage',[])
        if type(region) is not int or not 0<=region<len(coverage) or 'candidate_count' not in coverage[region]:
            raise ValueError('Choose a coverage region from this shape search.')
        table='match_regions r JOIN matches m ON m.id=r.match_id WHERE r.region=?'
        params=(region,)
        points='r.points'
    ordering={'id':'m.id ASC','best':'m.score DESC, m.id ASC','worst':'m.score ASC, m.id ASC',
              'most':points+' DESC, m.id ASC','fewest':points+' ASC, m.id ASC',
              'station':'m.station ASC, m.id ASC'}
    if not isinstance(sort,str) or sort not in ordering:
        raise ValueError('Choose a candidate sort order.')
    return table,params,points,ordering[sort]


def review_view_args(args):
    try:
        region=int(args['region'][0]) if 'region' in args else None
    except (TypeError,ValueError):
        raise ValueError('Choose a coverage region from this shape search.') from None
    return region,args.get('sort',['id'])[0]


def source(s, ident):
    item=next((x for x in s.STATE.get('job_items',[]) if x.get('id')==ident and x.get('kind')=='scan'),None)
    if item is None:raise ValueError('Choose an existing measured scan.')
    __import__('registered_cloud').require_current(item)
    if item.get('merge_archived'):raise ValueError('Choose the current registered cloud rather than an archived merge.')
    # Explicit source choice permits a hidden scan. Visibility does not remove
    # its measured data or change the whole-retained-cloud search scope.
    return item


def search_limit(item, cfg):
    scope=cfg.get('search_scope')
    box=item.get('limit_box') if item.get('limit_hide_outside',True) else None
    # Other consumers without a shape scope keep their existing view-filter policy.
    if box and (scope=='auto' or scope is None and cfg['respect_view']):
        return jobdata.selection_spec({'mode':'box',**box})
    return None


def batches(item, cfg, progress, cancel=lambda:False):
    """Stream exact retained rows from the chosen area in original ID order."""
    meta=item['meta']; n=int(meta['count'])
    limit=search_limit(item,cfg)
    isolate=item.get('isolate_box') if cfg['respect_view'] else None
    isolate=jobdata.selection_spec(isolate) if isolate is not None else None
    area=limit or isolate
    if area and n:
        import preview_index
        def indexing(message):
            shape_matching.emit(progress,cancel,message.replace('Preparing spatial index','Preparing spatial index for the restricted search'))
        ranges=list(preview_index.indexed_selection_blocks(meta,area,indexing))
        read_total=sum(end-start for start,end in ranges)
    else:
        ranges=((start,min(n,start+shape_matching.BLOCK)) for start in range(0,n,shape_matching.BLOCK))
        read_total=n
    label='active limit box' if limit else 'current view filters' if cfg['respect_view'] else 'whole retained cloud'
    read=checked=0
    with ExitStack() as stack:
        xyz=stack.enter_context(RowFile(meta['file'],'<f8',n,3))
        mask=stack.enter_context(RowFile(meta['mask_file'],'u1',n)) if meta.get('mask_file') else None
        for start,end in ranges:
            shape_matching.emit(progress,cancel,f'Reading {label} · {read:,}/{read_total:,} source rows read · {checked:,} in search area')
            count=end-start; p=registration_pose.points(meta,xyz.read(start,count))
            keep=np.isfinite(p).all(1)
            if mask is not None:keep&=mask.read(start,count)==0
            if limit:keep&=jobdata._selection_mask_validated(p,limit)
            if isolate:keep&=jobdata._selection_mask_validated(p,isolate)
            if cfg['respect_view']:
                for box in item.get('hidden_boxes',[]):keep&=~jobdata.selection_mask(p,box)
            ids=np.flatnonzero(keep).astype('<i8')+start; checked+=len(ids);read+=count
            if len(ids):yield ids,p[keep],{'mode':'full'}


@contextmanager
def circle_source(item,cfg,selection,limit,folder,progress,cancel):
    """Read the scoped source once, with bounded RAM and a temporary spill file."""
    visibility=dict(item) if cfg['respect_view'] else {}
    visibility.update(limit_box=limit if selection else None,limit_hide_outside=True)
    area=selection or limit or visibility.get('isolate_box')
    if area is None:
        yield lambda:batches(item,cfg,progress,cancel)
        return
    cached=[]
    cache_bytes=0
    complete=False
    spool=None
    lengths=[]
    pass_number=0
    scope='selected points' if selection else 'saved limit box' if limit else 'filtered view'

    def report(message):
        shape_matching.emit(progress,cancel,f'{pass_number}/3 Local circles · {scope} · {message}')

    with ExitStack() as files:
        def write(ids,points):
            rows=np.empty(len(ids),CIRCLE_INPUT)
            rows['id'],rows['xyz']=ids,points
            rows.tofile(spool)
            lengths.append(len(ids))

        def remember(part):
            nonlocal cached,cache_bytes,spool
            ids,points,_=part
            size=len(ids)*CIRCLE_INPUT.itemsize
            if spool is None and cache_bytes+size<=CIRCLE_SCOPE_CACHE_BYTES:
                cached.append(part);cache_bytes+=size
                return
            needed=size+(cache_bytes if spool is None else 0)
            try:
                jobdata.room(folder,needed)
                if spool is None:
                    spool=files.enter_context(tempfile.TemporaryFile(dir=folder))
                    for old_ids,old_points,_ in cached:write(old_ids,old_points)
                    cached.clear()
                write(ids,points)
            except (ValueError,OSError) as error:
                if isinstance(error,OSError) and error.errno!=errno.ENOSPC:raise
                if spool is not None:spool.close()
                cached=None;spool=None;lengths.clear()
                report('Temporary space limited · subsequent stages will read the indexed area')

        def read():
            nonlocal complete,pass_number
            pass_number+=1
            if complete:
                if spool is None:
                    yield from cached
                else:
                    spool.seek(0)
                    for count in lengths:
                        rows=np.fromfile(spool,CIRCLE_INPUT,count=count)
                        if len(rows)!=count:raise ValueError('The temporary local-circle input cache is incomplete. Preview the area again.')
                        yield rows['id'],rows['xyz'],{'mode':'full'}
                return
            for ids,points in jobdata.selection_rows(item['meta'],area,report,visibility=visibility):
                part=ids,points,{'mode':'full'}
                if cached is not None:remember(part)
                yield part
            complete=cached is not None
        yield read


def discard(row):
    if row and row.get('folder'):shutil.rmtree(row['folder'],ignore_errors=True)


def current(s, body=None):
    row=getattr(s,'SHAPE_SELECTION',None)
    if not row:raise ValueError('Draw an example and preview similar shapes first.')
    if body is not None and (body.get('preview_id')!=row['id'] or body.get('revision')!=row['revision']):
        raise ValueError('The shape review changed. Refresh it before applying an action.')
    if feature_selection.fingerprint(source(s,row['source_id']))!=row['source_fingerprint']:
        raise ValueError('The source changed. Preview similar shapes again before editing points.')
    return row


def review_scope(item,cfg):
    """Only suppress repeat footprints from the same source and lining search."""
    limit=search_limit(item,cfg)
    source_signature=(feature_selection.fingerprint(item) if cfg['respect_view'] or limit
                      else __import__('scan_export').signature(item))
    settings=[cfg[k] for k in ('grid_mm','min_offset_mm','max_offset_mm','lining_reference','respect_view')]
    if limit and not cfg['respect_view']:settings.append('active limit box')
    if cfg.get('search_scope')=='whole' and cfg['respect_view'] and item.get('limit_box') and item.get('limit_hide_outside',True):
        settings.append('ignore active limit box')
    return hashlib.sha256(json.dumps([item['id'],source_signature,settings]).encode()).hexdigest()


def preview(s, body):
    item=source(s,body.get('source_id'));mode=body.get('mode','shape')
    if mode not in ('shape','circle'):raise ValueError('Choose example matching or local-circle cleaning.')
    selection=None
    if mode=='circle':
        import section_circle
        cfg=section_circle.settings(body.get('settings'));example=None
        scope=body.get('scope','auto')
        if scope not in ('auto','selection','limit','view','whole'):raise ValueError('Choose a local-circle processing area.')
        if scope=='whole':cfg['respect_view']=False
        selection=jobdata.selection_spec(body['selection']) if scope in ('auto','selection') and body.get('selection') is not None else None
        limit=item.get('limit_box') if scope!='whole' else None
        if limit:limit=jobdata.selection_spec({'mode':'box',**limit,'invert':False})
        if scope=='limit' and not limit:raise ValueError('The saved limit box changed. Set the box and preview again.')
        if scope=='selection' and selection is None:raise ValueError('Select points and reopen Local circles.')
        if scope in ('selection','limit','view'):cfg['respect_view']=True
        if scope=='view' and not (limit or item.get('isolate_box') or item.get('hidden_boxes')):
            raise ValueError('The view restrictions changed. Choose the processing area again.')
    else:
        cfg=shape_matching.settings(body.get('settings'));bank=None
        if body.get('class_id'):
            import shape_library
            bank=shape_library.bank(s,[body['class_id']]);example=None
        else:example=jobdata.selection_spec(body.get('example') or {})
        if not bank and example.get('mode') not in ('box','oriented_box','screen_polygon','screen_brush'):
            raise ValueError('Draw an area around one object or a short section as the example.')
    scope=None
    reviewed=set()
    if mode=='shape':
        scope=review_scope(item,cfg)
        reviewed={signature for event in s.STATE.get('shape_library_events',[])
                  if (record:=event.get('review')) and record['scope']==scope
                  for signature in record['signatures']}
    before=feature_selection.fingerprint(item); folder=asset_folder(s.WORK,'analysis')/('shape-'+uuid.uuid4().hex)
    folder.mkdir(); cancelled=lambda:__import__('job_api').CANCEL.is_set()
    try:
        if mode=='circle':
            axis=s.STATE.get('axis');rows=np.array(axis.a,copy=True) if axis is not None else None
            with circle_source(item,cfg,selection,limit,folder,s.progress,cancelled) as read:
                result=section_circle.run(read,folder,cfg,s.progress,cancelled,axis_rows=rows)
            if 'statistics' in result:result['statistics']['source_points']=int(item['meta']['count'])
        else:result=shape_matching.run(lambda:batches(item,cfg,s.progress,cancelled),example,folder,cfg,s.progress,cancelled,
                                      bank=bank,reviewed=reviewed)
        if feature_selection.fingerprint(source(s,item['id']))!=before:
            raise ValueError('The source changed during shape matching. The previous preview is unchanged.')
        area='whole'
        if selection is not None:area='selection'
        elif (limit if mode=='circle' else search_limit(item,cfg)):area='limit_box'
        elif cfg['respect_view']:area='view'
        shape_matching.emit(s.progress,cancelled,'Local-circle candidates ready for review' if mode=='circle' else 'Shape candidates ready for review')
        row={**result,'id':uuid.uuid4().hex,'revision':uuid.uuid4().hex,'source_id':item['id'],'source_name':item['name'],
             'source_fingerprint':before,'settings':cfg,'example':example,'folder':str(folder),'mode':mode,
             'class_id':body.get('class_id'),'review_scope':scope,'search_area':area}
        old=getattr(s,'SHAPE_SELECTION',None);s.SHAPE_SELECTION=row;discard(old)
        return row
    except BaseException:
        shutil.rmtree(folder,ignore_errors=True);raise


def public(s, page=0, region=None, sort='id', *, include_points=True):
    row=getattr(s,'SHAPE_SELECTION',None)
    if not row:return None
    try:stale=feature_selection.fingerprint(source(s,row['source_id']))!=row['source_fingerprint']
    except (OSError,ValueError):stale=True
    db=sqlite3.connect(row['review_file'])
    try:
        table,params,points,ordering=review_view(row,region,sort)
        total=db.execute('SELECT count(*) FROM matches').fetchone()[0]
        visible=db.execute('SELECT count(*) FROM '+table,params).fetchone()[0] if region is not None else total
        page=max(0,min(int(page),max(0,math.ceil(visible/PAGE_SIZE)-1)))
        query=f'''SELECT m.id,m.station,m.angle,m.score,m.side,m.points,m.selected_points,{points}
                  FROM {table} ORDER BY {ordering} LIMIT ? OFFSET ?'''
        groups=[dict(id=k,name=(('Outward' if side>0 else 'Inward')+f' departures · near {t:.2f} m') if row.get('mode')=='circle' else f'Shape match {k}',station_m=t,angle_deg=math.degrees(a),similarity_percent=None if row.get('mode')=='circle' else score*100,
                     side='recess' if side>0 else 'raised',points=n,region_points=regional,
                     selected_points=selected,selected=selected>0,partial=0<selected<n)
                for k,t,a,score,side,n,selected,regional in db.execute(query,(*params,PAGE_SIZE,page*PAGE_SIZE))]
    finally:db.close()
    out={k:copy.deepcopy(row[k]) for k in ('id','revision','source_id','source_name','mode','settings','statistics','warnings','method','research_dois')}
    out.update(groups=groups,group_count=visible,total_group_count=total,page=page,region=region,sort=sort,stale=stale,last_edit=copy.deepcopy(row.get('last_edit')))
    out.update(class_id=row.get('class_id'),library_names=copy.deepcopy(row.get('library_names',{})))
    out['search_area']=row.get('search_area','view' if row['settings'].get('respect_view') else 'whole')
    out.update(coverage=copy.deepcopy(row.get('coverage',[])),lining_reference=copy.deepcopy(row.get('lining_reference')),
               reviewed_skipped=row.get('reviewed_skipped',0))
    for group in out['groups']:
        if row.get('class_id'):group['name']=row.get('library_names',{}).get(row['class_id'],'Saved shape')+' · match '+str(group['id'])
    if row.get('circle_sections') is not None:out['circle_sections']=copy.deepcopy(row['circle_sections'])
    if not include_points:return out
    with np.load(row['preview_file'],allow_pickle=False) as cached:
        p=cached['points']; context=cached['context']; labels=cached['labels']; indices=cached['indices']
        with RowFile(row['selected_file'],'u1',row['count']) as flags:selected=flags.take(indices).astype(bool)
        all_points=np.vstack([context,p]);origin=np.median(context,axis=0) if len(context) else np.zeros(3)
        colour=np.tile([.42,.53,.62],(len(all_points),1));colour[len(context):]=[.1,.8,.85]
        if row.get('mode')=='circle' and 'context_status' in cached:
            colour[:len(context)]=[.25,.78,.58]
            colour[np.flatnonzero(cached['context_status']==3)]=[1.,.79,.20]
        colour[len(context)+np.flatnonzero(selected)]=[1.,.55,.12]
        out['preview']=dict(origin=origin.tolist(),points=np.c_[all_points-origin,colour].tolist(),source_id=row['source_id'],
                            count=row['statistics']['input_points'],preview_count=len(all_points),sampled=True,selected_count=row['statistics']['selected_points'])
        if row.get('mode')!='circle':
            out['preview']['match_ids']=labels.tolist()
            if 'regions' in cached:
                out['preview']['match_regions']=cached['regions'].tolist()
        if row.get('mode')=='circle' and 'context_station' in cached:
            out['preview']['stations']=np.r_[cached['context_station'],cached['point_station']].tolist()
    return out


def choose(s, body):
    row=current(s,body);action=body['action']; mode=body.get('operation')
    ids=body.get('group_ids',[]);selected=body.get('selected')
    if action=='select':
        if mode not in ('all','none','toggle','invert','side'):raise ValueError('Choose Select all, Clear selection or an existing group.')
        if mode=='side' and (row.get('mode')!='circle' or body.get('side') not in ('inward','outward')):raise ValueError('Choose inward or outward local-circle departures.')
        if mode in ('toggle','invert'):
            if not isinstance(ids,list) or not ids or any(type(i) is not int for i in ids):
                raise ValueError('Choose existing integer shape-match groups.')
            if mode=='toggle' and type(selected) is not bool:
                raise ValueError('Choose a selection state.')
            if mode=='invert' and (len(ids)!=1 or row.get('mode')=='circle'):
                raise ValueError('Choose one 3D shape match.')
    elif action=='refine':
        if mode not in ('keep','exclude'):raise ValueError('Choose Keep within area or Exclude area.')
        spec=jobdata.selection_spec(body.get('selection') or {})
    else:raise ValueError('Unknown shape-review selection action.')
    dest=Path(row['folder'])/(uuid.uuid4().hex+'.selected');counts={};selected_count=0
    db=sqlite3.connect(row['review_file'])
    try:
        if action=='select' and mode in ('toggle','invert'):
            ids=list(dict.fromkeys(ids));found={}
            for start in range(0,len(ids),500):
                batch=ids[start:start+500]
                found.update(db.execute('SELECT id,selected_points FROM matches WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
            if len(found)!=len(ids):raise ValueError('A shape match no longer exists.')
            if mode=='invert':selected=not found[ids[0]]
        item=source(s,row['source_id']);meta=item['meta'];n=row['count']
        with ExitStack() as stack, dest.open('wb') as out:
            flags=stack.enter_context(RowFile(row['selected_file'],'u1',n));labs=stack.enter_context(RowFile(row['labels_file'],'<i4',n))
            if action=='refine':
                original=stack.enter_context(RowFile(row['point_ids_file'],'<i8',n));xyz=stack.enter_context(RowFile(meta['file'],'<f8',meta['count'],3))
            for start in range(0,n,shape_matching.BLOCK):
                size=min(shape_matching.BLOCK,n-start);chosen=flags.read(start,size).astype(bool);labels=labs.read(start,size)
                if action=='refine':
                    points=registration_pose.points(meta,xyz.take(original.read(start,size)));inside=jobdata.selection_mask(points,spec)
                    chosen&=inside if mode=='keep' else ~inside
                elif mode in ('all','none'):chosen[:]=mode=='all'
                elif mode=='side':chosen=(labels%2)==(1 if body['side']=='inward' else 0)
                else:chosen[np.isin(labels,ids)]=selected
                chosen.astype('u1').tofile(out);selected_count+=int(chosen.sum())
                for k,c in zip(*np.unique(labels[chosen],return_counts=True)):counts[int(k)]=counts.get(int(k),0)+int(c)
                s.progress(f'Updating original-point shape selection · {start+size:,}/{n:,} candidate IDs')
        current(s,body);s.progress('Saving reviewed shape selection')
        db.execute('UPDATE matches SET selected_points=0')
        db.executemany('UPDATE matches SET selected_points=? WHERE id=?',((c,k) for k,c in counts.items()));db.commit()
        old=row['selected_file'];row['selected_file']=str(dest);row['statistics']['selected_points']=selected_count;row['revision']=uuid.uuid4().hex
        try:Path(old).unlink(missing_ok=True)
        except OSError:pass  # An obsolete locked cache must not undo publication.
        return row
    except BaseException:
        db.rollback();dest.unlink(missing_ok=True);raise
    finally:db.close()


def edit(s, body):
    row=current(s,body)
    if body.get('confirmed') is not True:raise ValueError('Review shape candidates before removing or separating points.')
    total=row['statistics']['selected_points']
    if not total:raise ValueError('Select shape matches before applying this action.')
    selected=Path(row['folder'])/(uuid.uuid4().hex+'.edit_ids')
    try:
        written=0;n=row['count']
        with RowFile(row['point_ids_file'],'<i8',n) as ids,RowFile(row['selected_file'],'u1',n) as flags,selected.open('wb') as out:
            for start in range(0,n,shape_matching.BLOCK):
                size=min(shape_matching.BLOCK,n-start);use=flags.read(start,size)!=0
                ids.read(start,size)[use].tofile(out);written+=int(use.sum())
                s.progress(f'Preparing reviewed shape edit · {start+size:,}/{n:,} candidate identities')
        if written!=total:raise ValueError('The reviewed shape selection changed before the edit.')
        current(s,body);item=source(s,row['source_id'])
        operation='separate_selection' if body['action']=='separate' else 'delete_selection'
        reason=(f"Local circles · {row['settings']['section_m']:g} m windows · inward {row['settings']['inward_mm']:g} mm / outward {row['settings']['outward_mm']:g} mm") if row.get('mode')=='circle' else 'Reviewed example-shape matches'
        taught=None
        if body.get('teach'):
            import shape_library,shape_library_api
            with shape_library_api.selected_examples(s,row) as (folders,review):
                taught=shape_library.transaction(s,folders,body.get('class_id'),'positive',
                     review=review,
                    mutate=lambda:scan_edits.apply(s,item,operation,selected_file={'file':str(selected),'count':total},reason=reason,commit=False))
        else:scan_edits.apply(s,item,operation,selected_file={'file':str(selected),'count':total},reason=reason)
        row['last_edit']={'count':total,'action':body['action'],'taught':taught['added'] if taught else 0};return row['last_edit']
    finally:selected.unlink(missing_ok=True)


def get(s,h,path,args):
    if path!='/api/shape-selection':return False
    if 'detail_view' in args:
        if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
        import detail_preview, display_buffers
        view=json.loads(args['detail_view'][0])
        review={key:args.get(key,[None])[0] for key in ('preview_id','revision')}
        region,_=review_view_args(args)
        try:
            preview=view_detail(s,review,view,region,lambda:detail_preview.request_cancelled(h))
            h.send(display_buffers.encode(preview),'application/octet-stream')
        except InterruptedError:
            h.close_connection=True
        return True
    try:page=int(args.get('page',[0])[0])
    except (TypeError,ValueError):raise ValueError('Enter a valid shape-review page.')
    region,sort=review_view_args(args)
    h.send({'defaults':shape_matching.DEFAULTS,'circle_defaults':__import__('section_circle').DEFAULTS,'axis_available':s.STATE.get('axis') is not None,
            'result':public(s,page,region,sort,include_points=args.get('points',['1'])[0]!='0')});return True


def view_detail(s, review, view, region=None, cancel=None):
    """Use indexed viewport detail and colour sampled matches by original ID."""
    import detail_preview
    row=current(s,review)
    if row.get('mode')!='shape':raise ValueError('Open Find similar shapes to load this detail.')
    origin=detail_preview.validate_view(view)[0]
    item=source(s,row['source_id'])
    meta=item['meta']
    low,high=registration_pose.world_bounds(meta)
    offsets=[]; group_ids=[]
    groups=None
    if region is not None:
        review_view(row,region,'id')
        with closing(sqlite3.connect(row['review_file'])) as db:
            groups=np.fromiter((x[0] for x in db.execute('SELECT match_id FROM match_regions WHERE region=?',(region,))),dtype='<i4')

    def colour_matches(ids, packed):
        # Both streams are in original source order. Seek to the next relevant
        # block so a small viewport does not read every point in the review.
        n=row['count']; start=cursor=0
        with (RowFile(row['point_ids_file'],'<i8',n) as original,
              RowFile(row['labels_file'],'<i4',n) as labels,
              RowFile(row['selected_file'],'u1',n) as flags):
            while cursor<len(ids) and start<n:
                detail_preview._check_cancel(cancel)
                start=bisect_left(original,ids[cursor],start)
                if start==n:break
                block=original.read(start,min(original.block,n-start))
                end=np.searchsorted(ids,block[-1],side='right')
                idx=np.searchsorted(block,ids[cursor:end])
                hits=np.flatnonzero(block[idx]==ids[cursor:end])
                rows=start+idx[hits]
                hits+=cursor
                ids_here=labels.take(rows)
                if groups is not None:
                    keep=np.isin(ids_here,groups)
                    rows,hits,ids_here=rows[keep],hits[keep],ids_here[keep]
                packed[hits,3:]=np.where(flags.take(rows)[:,None]!=0,[1.,.55,.12],[.1,.8,.85])
                offsets.extend(hits.tolist());group_ids.extend(ids_here.tolist())
                cursor=end;start+=len(block)

    # Keep per-viewport GPU data and binary match metadata bounded. Use
    # measured intensity for the nearby wall and the normal review colours
    # for candidates. The full-scene overview stays resident in the browser.
    packed=detail_preview.sample({**item,'display_mode':'global'},50_000,origin,[.42,.53,.62],
                                 'intensity-gray',(float(low[2]),float(high[2])),view,
                                 cancel=cancel,decorate=colour_matches)
    current(s,review)
    return dict(packed=packed,origin=origin.tolist(),review_id=row['id'],revision=row['revision'],
                match_offsets=offsets,match_ids=group_ids,sampled=True)


def post(s,h,path,body):
    if path!='/api/shape-selection':return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    action=body.get('action')
    if action=='clear':discard(getattr(s,'SHAPE_SELECTION',None));s.SHAPE_SELECTION=None;h.send({'cleared':True});return True
    if action not in ('preview','select','refine','delete','separate','inspect'):raise ValueError('Unknown shape-selection action.')
    request=copy.deepcopy(body)
    if action!='preview':current(s,request)
    fn=preview if action=='preview' else choose if action in ('select','refine') else __import__('shape_inspection').prepare if action=='inspect' else edit
    circle=request.get('mode')=='circle' or (getattr(s,'SHAPE_SELECTION',None) or {}).get('mode')=='circle' and action!='preview'
    s.start_job(lambda:fn(s,request),'pocket inspection' if action=='inspect' else 'local-circle cleaning' if circle else 'shape matching',
                native_progress=not (action=='select' and request.get('operation')=='invert'))
    h.send({'started':True});return True
