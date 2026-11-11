# Copyright (c) 2026 Damian Gulczynski.
"""Reviewable, non-destructive surface extraction workflow for the local server."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from dataclasses import asdict
import copy, csv, html, io, json, shutil, threading, time, uuid, tempfile, hashlib
import numpy as np
import editing, pointio, jobdata, registration_pose
from surface_extraction import Settings, extract

CANCEL = threading.Event()
LABEL = {'separate':'Current scene scan', 'survey':'Active survey',
         'registered':'Positioned scan', 'registration':'Registration source'}


def source(s, key):
    if key == 'survey': return s.STATE.get('cloud')
    if key == 'registration': return s.STATE.get('registration', {}).get('cloud')
    if key == 'registered': return s.STATE.get('registration', {}).get('transformed')
    if key == 'separate': return s.STATE.get('surface_source')
    raise ValueError('Choose a surface extraction source.')


def same_geometry(a, b):
    return bool(a and b and all(a.get(k) == b.get(k) for k in ('file','count','mask_file'))
                and registration_pose.revision(a)==registration_pose.revision(b))

def source_item(s,key):
    ident=s.STATE.get('surface_source_item') if key=='separate' else s.STATE.get('active_job_scan') if key=='survey' else None
    return next((x for x in s.STATE.get('job_items',[]) if x['id']==ident and x['kind']=='scan'),None)


def result_source_changed(s, r):
    saved = r['source_meta']
    item_id = r.get('source_item_id')
    if item_id:
        item = next((x for x in s.STATE.get('job_items', [])
                     if x['id'] == item_id and x['kind'] == 'scan'), None)
        if item:
            return not same_geometry(item['meta'], saved)
    current = source(s, r['source_kind'])
    # Older results and standalone sources have no persistent item ID.
    # Switching to another scan does not invalidate a saved result.
    return bool(current and current.get('file') == saved.get('file')
                and not same_geometry(current, saved))


def patch_fingerprint(item):
    meta=item['meta'];files=[]
    for key in ('file','mask_file'):
        if meta.get(key):
            p=Path(meta[key]);st=p.stat();files.append([str(p),st.st_size,st.st_mtime_ns])
    return hashlib.sha256(json.dumps([files,meta['count'],registration_pose.revision(meta),item.get('limit_box'),item.get('limit_hide_outside',True)],sort_keys=True).encode()).hexdigest()


def patch_source(s,r):
    if r.get('settings',{}).get('method') not in ('irregular','lining_features'):return None
    item=next((x for x in s.STATE.get('job_items',[]) if x['id']==r.get('source_item_id') and x['kind']=='scan'),None)
    if item and same_geometry(item['meta'],r['source_meta']):
        try:
            if patch_fingerprint(item)==r.get('source_fingerprint'):return item
        except OSError:pass
    return None


def load(s, kind, path):
    if kind != 'surface': return False
    path=Path(path)
    # E57 uses the same isolated worker as the Job Workspace so a slow vendor
    # reader cannot blank the desktop WebView.  All E57 setups are read into the
    # one surface source in their file coordinates/poses.
    if path.suffix.lower() in ('.e57','.hspca'):
        parts=jobdata.scan_parts(path,s.progress);expected=sum(n for _,n in parts if n)
        meta=jobdata.import_scan(path,s.WORK,index=None,expected=expected or None,progress=s.progress)
    else:
        meta=pointio.import_cloud(path, asset_folder(s.WORK,'imported') / (uuid.uuid4().hex + '.xyz64'), s.progress)
    s.STATE['surface_source'] = meta
    s.STATE['surface_source_item'] = None
    s.save()
    return True


def result(s):
    r = s.STATE.get('surface_result')
    if not r: raise ValueError('Run Extract tunnel surface first.')
    return r


def result_arrays(r):
    n = r['statistics']['input_points']
    return (np.memmap(r['point_ids_file'], '<i8', 'r', shape=(n,)),
            np.memmap(r['labels_file'], '<i4', 'r', shape=(n,)))


def kept_count(r):
    kept = set(r['kept'])
    return sum(c['points'] for c in r['clusters'] if c['id'] in kept) + (r['statistics']['noise_points'] if -1 in kept else 0)


def metrics(r):
    """Ground truth is used only to evaluate our synthetic demonstration, never to classify."""
    meta = r['source_meta']
    if not meta.get('synthetic_surface_demo') or not meta.get('truth_file'): return None
    ids, labels = result_arrays(r)
    truth = np.memmap(meta['truth_file'], 'u1', 'r', shape=(meta['count'],))[ids].astype(bool)
    predicted = np.isin(labels, r['kept'])
    tp = int((predicted & truth).sum()); fp = int((predicted & ~truth).sum()); fn = int((~predicted & truth).sum())
    return {'scope':'Synthetic selected area only; not a real-scan accuracy estimate', 'tp':tp, 'fp':fp, 'fn':fn,
            'iou':tp/(tp+fp+fn) if tp+fp+fn else None,
            'precision':tp/(tp+fp) if tp+fp else None, 'recall':tp/(tp+fn) if tp+fn else None}


def public_result(s, r):
    if not r: return None
    out = {k:copy.deepcopy(r[k]) for k in ('id','revision','created_at','source_kind','selection','settings','statistics','warnings','clusters','kept','review_log','method','doi')}
    for k in ('features','lining_frame','research_dois','rings','classification','detection','algorithm_notes','processing_area'):
        if k in r:out[k]=copy.deepcopy(r[k])
    out['source_name'] = r['source_meta']['name']
    out['source_count'] = r['source_meta']['count']
    out['source_item_id'] = r.get('source_item_id')
    out['reviewed_kept_points'] = kept_count(r)
    out['source_changed'] = result_source_changed(s, r)
    out['can_apply'] = r['source_kind'] == 'survey' and same_geometry(source(s, 'survey'), r['source_meta'])
    out['can_delete_patches'] = patch_source(s,r) is not None
    out['evaluation'] = metrics(r)
    return out


def status(s):
    # Internal workflow aliases can refer to the same underlying scan.  Expose
    # each actual geometry only once so a user who imported one scan sees one scan.
    sources=[]; seen=[]
    for key,label in LABEL.items():
        m=source(s,key)
        if not m: continue
        duplicate=next((row for row,meta in seen if same_geometry(meta,m)),None)
        if duplicate is not None: continue
        lo,hi=registration_pose.world_bounds(m)
        row={'key':key,'label':label,'name':m['name'],'count':m['count'],
             'minimum':lo.tolist(),'maximum':hi.tolist(),
             'synthetic':bool(m.get('synthetic_surface_demo'))}
        item=source_item(s,key)
        row['has_limit_box']=bool(item and item.get('limit_box'))
        sources.append(row); seen.append((row,m))
    return {'sources':sources,'defaults':asdict(Settings()),'result':public_result(s,s.STATE.get('surface_result')),'job_selection':copy.deepcopy(s.STATE.get('surface_job_selection'))}


def _selection_spec(s,meta,key,selection):
    if not isinstance(selection,dict):raise ValueError('Choose the processing area.')
    mode=selection.get('mode','full');spec={'mode':mode};lo=hi=start=end=None
    if mode in ('box','oriented_box','screen_polygon','screen_brush'):
        spec=jobdata.selection_spec(selection)
    elif mode=='chainage':
        if key!='survey' or not s.STATE.get('derived'):raise ValueError('Chainage range requires the active survey and its design alignment.')
        start=float(selection['start']);end=float(selection['end'])
        if not np.isfinite([start,end]).all() or start>=end:raise ValueError('Enter an increasing, finite chainage range.')
        spec.update(start=start,end=end)
    elif mode!='full':raise ValueError('Choose full scan, current 3D selection, coordinate box or chainage range.')
    if mode in ('full','chainage') and selection.get('clip_box'):
        clip=selection['clip_box'];spec['clip_box']=jobdata.selection_spec({'mode':'box',**clip,'invert':False})
    return spec,lo,hi,start,end


SMRG_MAX_SELECTED_POINTS=3_000_000

def _smrg_guard(count,partial=False):
    if count>SMRG_MAX_SELECTED_POINTS:
        raise ValueError(f'SMRG surface-patch grouping is limited to {SMRG_MAX_SELECTED_POINTS:,} selected original points. This area contains {"at least " if partial else ""}{count:,}. Draw a smaller local selection or use primary GuSAC tunnel cleaning for the whole scan. SMRG groups geometry and does not reliably label pipes versus lining; no processing result or scan was changed.')


def _area_count(s,meta,key,selection,cancelled=lambda:False,max_selected=None):
    """Count retained selections with fixed-size owned reads, never a full map.

    A whole-file mapping can retain previously touched pages in process RSS even
    when arithmetic is chunked. Source, mask and derived reads here are bounded
    to 100k records; the operating system's file cache is not process mappings.
    """
    from contextlib import ExitStack
    import surface_streaming
    spec,lo,hi,start,end=_selection_spec(s,meta,key,selection)
    total=int(meta['count']);read_points=max(1,min(int(pointio.CHUNK),100000))
    if surface_streaming.index_box(spec):
        selected = 0
        for ids, _, _ in surface_streaming.batches(s,meta,key,spec,read_points,cancelled):
            selected += len(ids)
            if max_selected is not None and selected > max_selected:
                _smrg_guard(selected,partial=True)
        return selected,spec
    selected=0;seen=0
    with ExitStack() as files:
        source_file=files.enter_context(open(meta['file'],'rb'))
        mask_file=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        derived_file=files.enter_context(open(s.STATE['derived'],'rb')) if spec['mode']=='chainage' else None
        for i in range(0,total,read_points):
            if cancelled():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
            count=min(read_points,total-i)
            xyz=np.fromfile(source_file,dtype='<f8',count=count*3)
            if len(xyz)!=count*3:raise ValueError('The source point cache is incomplete or changed during selection.')
            q=registration_pose.points(meta,xyz.reshape(count,3));use=np.isfinite(q).all(1)
            if mask_file is not None:
                mask=np.fromfile(mask_file,dtype='u1',count=count)
                if len(mask)!=count:raise ValueError('The source exclusion mask is incomplete or changed during selection.')
                use&=mask==0
            if spec['mode'] in ('box','oriented_box','screen_polygon','screen_brush'):use&=jobdata.selection_mask(q,spec)
            elif spec['mode']=='chainage':
                info=np.fromfile(derived_file,dtype='<f8',count=count*4)
                if len(info)!=count*4:raise ValueError('The survey analysis cache is incomplete or changed during selection.')
                ch=info.reshape(count,4)[:,0];use&=np.isfinite(ch)&(ch>=start)&(ch<=end)
            if spec['mode'] in ('full','chainage') and spec.get('clip_box'):use&=jobdata.selection_mask(q,spec['clip_box'])
            selected+=int(use.sum());seen+=count
            if max_selected is not None and selected>max_selected:_smrg_guard(selected,partial=True)
            s.progress(f'Checking source file for {"whole scan" if spec["mode"]=="full" else "selected area only"}: {seen:,} / {total:,} read · {selected:,} points inside area')
    return selected,spec


def _area_batches(s,meta,key,selection,block_points,cancelled=lambda:False):
    """Yield all original selected records with bounded owned coordinate reads.

    Source windows contain <=100k records and output buffers <=500k. This is a
    processing-batch bound, not a cloud-size or selection-size cutoff. Buffers
    already yielded to a caller are never overwritten by subsequent reads.
    """
    from contextlib import ExitStack
    import surface_streaming
    spec,lo,hi,start,end=_selection_spec(s,meta,key,selection)
    block_points=int(block_points)
    if block_points<1:raise ValueError('Selection batch size must be positive.')
    block_points=min(block_points,500000)
    if surface_streaming.index_box(spec):
        yield from surface_streaming.batches(s,meta,key,spec,block_points,cancelled)
        return
    total=int(meta['count']);read_points=max(1,min(int(pointio.CHUNK),100000))
    selected=0;seen=0;filled=0
    ids_buffer=np.empty(block_points,dtype='<i8');points_buffer=np.empty((block_points,3),dtype='<f8')
    with ExitStack() as files:
        source_file=files.enter_context(open(meta['file'],'rb'))
        mask_file=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        derived_file=files.enter_context(open(s.STATE['derived'],'rb')) if spec['mode']=='chainage' else None
        for i in range(0,total,read_points):
            if cancelled():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
            count=min(read_points,total-i)
            xyz=np.fromfile(source_file,dtype='<f8',count=count*3)
            if len(xyz)!=count*3:raise ValueError('The source point cache is incomplete or changed during selection.')
            q=registration_pose.points(meta,xyz.reshape(count,3));use=np.isfinite(q).all(1)
            if mask_file is not None:
                mask=np.fromfile(mask_file,dtype='u1',count=count)
                if len(mask)!=count:raise ValueError('The source exclusion mask is incomplete or changed during selection.')
                use&=mask==0
            if spec['mode'] in ('box','oriented_box','screen_polygon','screen_brush'):use&=jobdata.selection_mask(q,spec)
            elif spec['mode']=='chainage':
                info=np.fromfile(derived_file,dtype='<f8',count=count*4)
                if len(info)!=count*4:raise ValueError('The survey analysis cache is incomplete or changed during selection.')
                ch=info.reshape(count,4)[:,0];use&=np.isfinite(ch)&(ch>=start)&(ch<=end)
            if spec['mode'] in ('full','chainage') and spec.get('clip_box'):use&=jobdata.selection_mask(q,spec['clip_box'])
            positions=np.flatnonzero(use);selected+=len(positions);seen+=count
            s.progress(f'Reading source to locate {"whole scan" if spec["mode"]=="full" else "selected area only"}: {seen:,} / {total:,} read · {selected:,} points inside area')
            offset=0
            while offset<len(positions):
                if cancelled():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
                take=min(block_points-filled,len(positions)-offset)
                local=positions[offset:offset+take]
                ids_buffer[filled:filled+take]=local+i
                points_buffer[filled:filled+take]=q[local]
                filled+=take;offset+=take
                if filled==block_points:
                    yield ids_buffer,points_buffer,spec
                    # Ownership passes to the caller. Allocate a fresh batch;
                    # a caller retaining a preceding batch sees immutable data.
                    ids_buffer=np.empty(block_points,dtype='<i8');points_buffer=np.empty((block_points,3),dtype='<f8')
                    filled=0
        if filled:
            if cancelled():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
            yield ids_buffer[:filled].copy(),points_buffer[:filled].copy(),spec


def select_area(s,meta,key,selection,cap=None,cancelled=lambda:False):
    """Compatibility helper. Small callers can still request one in-memory area."""
    blocks=list(_area_batches(s,meta,key,selection,max(1000,int(cap or max(meta['count'],1000))),cancelled))
    if not blocks:raise ValueError('The selected area contains no retained points.')
    ids=np.concatenate([b[0] for b in blocks]);pts=np.concatenate([b[1] for b in blocks]);spec=blocks[0][2]
    # Preserve the legacy helper's explicit cap contract for callers/tests that
    # request it. The production Extract Surface workflow no longer uses this
    # helper for large selections; run() streams every selected point through
    # automatic internal blocks instead.
    if cap is not None and len(ids)>int(cap):
        raise ValueError(f'Area contains {len(ids):,} retained points, above the requested {int(cap):,} point compatibility cap.')
    if len(ids)<50:raise ValueError('The area contains fewer than 50 retained points. Enlarge it or check coordinates and units.')
    return ids,pts,spec


def _merge_block_result(r,offset,block_index):
    labels=r.pop('labels').astype('<i4',copy=False)
    # Tunnel-envelope labels have stable semantics across blocks: 0 = lining, 1 = other.
    # Keep those IDs global so the user reviews one lining result rather than dozens
    # of artificial per-block clusters. SMRG clusters remain independent by design.
    if r.get('method_code')=='tunnel_envelope':
        clusters=[]
        for c in r['clusters']:
            row=copy.deepcopy(c);row['block']=block_index;clusters.append(row)
        return labels,clusters,[int(k) for k in r['kept']],max(offset,2)
    positive=labels>=0
    if positive.any():labels[positive]+=offset
    clusters=[]
    for c in r['clusters']:
        row=copy.deepcopy(c);row['id']=int(row['id'])+offset;row['block']=block_index;clusters.append(row)
    kept=[int(k)+offset for k in r['kept'] if k>=0]
    if -1 in r['kept']:kept.append(-1)
    next_offset=offset+(max((c['id'] for c in r['clusters']),default=-1)+1)
    return labels,clusters,kept,next_offset


def run(s, body):
    body=copy.deepcopy(body)
    key=body.get('source','survey');item=source_item(s,key)
    area=body.get('selection') or {'mode':'full'}
    mode=area.get('mode','full')
    body['processing_area']=None
    limit=item.get('limit_box') if item else None
    if mode=='limit_box' and not limit:
        raise ValueError('The saved limit box changed. Set it again or choose another processing area.')
    if limit and not (mode=='full' and body.get('ignore_limit_box') is True):
        if mode in ('full','limit_box'):
            area={'mode':'box',**limit}
            body['processing_area']='limit_box'
        else:area['clip_box']=limit
    body['selection']=area
    # Small selections are collected once; later passes never rescan the source.
    mode=(body.get('selection') or {'mode':'full'}).get('mode','full')
    if mode=='full':return _run(s,body)
    key=body.get('source','survey');meta=source(s,key)
    if not meta:raise ValueError('Import or choose a source scan first.')
    free=shutil.disk_usage(s.WORK).free
    cache_bytes=max(0,min(2*1024**3,free//5,free-jobdata.RESERVE))
    with tempfile.TemporaryDirectory(prefix='tunnel_area_',dir=s.WORK) as folder:
        ids_path=Path(folder)/'selected.ids';points_path=Path(folder)/'selected.xyz64';count=0;spec=None;cached=None
        area_batches=__import__('surface_streaming').batches if (body.get('settings') or {}).get('method')=='lining_features' else _area_batches
        with ids_path.open('wb') as idout,points_path.open('wb') as pointout:
            for ids,points,area in area_batches(s,meta,key,body['selection'],150000,CANCEL.is_set):
                if (count+len(ids))*32>cache_bytes:break
                jobdata.room(s.WORK,len(ids)*32);ids.astype('<i8').tofile(idout);points.astype('<f8').tofile(pointout);count+=len(ids);spec=area
                if (body.get('settings') or {}).get('method')=='smrg':_smrg_guard(count,partial=True)
            else:
                if count<50:raise ValueError('The selected area contains fewer than 50 retained points.')
                cached=(ids_path,points_path,count,spec)
        if cached:return _run(s,body,cached)
    return _run(s,body)


def _cached_area_batches(cached, block, cancelled):
    """Read owned, bounded arrays; no temporary file stays open across a yield.

    Analysis can retain its current batch in an exception traceback. Returning
    memmap views here used to keep selected.xyz64/selected.ids locked during
    TemporaryDirectory cleanup on Windows, hiding the original analysis error.
    """
    ids_path, points_path, total, spec = cached
    for start in range(0, total, block):
        if cancelled():
            raise ValueError('Surface extraction cancelled.')
        count = min(block, total - start)
        with Path(ids_path).open('rb') as id_file, Path(points_path).open('rb') as point_file:
            id_file.seek(start * 8)
            point_file.seek(start * 24)
            ids = np.fromfile(id_file, dtype='<i8', count=count)
            points = np.fromfile(point_file, dtype='<f8', count=count * 3)
        if len(ids) != count or len(points) != count * 3:
            raise ValueError('The temporary selection cache is incomplete. Select the area and run the operation again.')
        yield ids, points.reshape(count, 3), spec


def _run(s, body, cached=None):
    key=body.get('source','survey');meta=source(s,key)
    if not meta:raise ValueError('Import or choose a source scan first.')
    meta=copy.deepcopy(meta);settings=asdict(Settings.read(body.get('settings')));block=max(50000,min(int(settings.get('max_points',300000)),500000))
    source_item_id=s.STATE.get('surface_source_item') if key=='separate' else s.STATE.get('active_job_scan') if key=='survey' else None
    selection=body.get('selection',{'mode':'full'})
    area_batches=__import__('surface_streaming').batches if settings['method']=='lining_features' else _area_batches
    if cached:selected_total,count_spec=cached[2:]
    elif settings['method']=='smrg':
        count_spec=_selection_spec(s,meta,key,selection)[0]
        if count_spec['mode']=='full' and not count_spec.get('clip_box'):
            # The first whole-cloud guard uses point metadata or the exclusion
            # mask only; a 550M-point request need not read 13GB of XYZ first.
            selected_total=int(meta['count'])
            if meta.get('mask_file'):
                selected_total=0
                with open(meta['mask_file'],'rb') as excluded:
                    for start in range(0,int(meta['count']),1_000_000):
                        s.progress('Checking SMRG retained-point workload')
                        n=min(1_000_000,int(meta['count'])-start);part=np.fromfile(excluded,'u1',count=n)
                        if len(part)!=n:raise ValueError('The source exclusion mask is incomplete.')
                        selected_total+=int(np.count_nonzero(part==0));_smrg_guard(selected_total,partial=True)
            _smrg_guard(selected_total)
        else:selected_total,count_spec=_area_count(s,meta,key,selection,CANCEL.is_set,max_selected=SMRG_MAX_SELECTED_POINTS)
    elif settings['method']=='lining_features':
        count_spec=_selection_spec(s,meta,key,selection)[0]
        selected_total=sum(len(ids) for ids,_,_ in area_batches(s,meta,key,selection,block,CANCEL.is_set))
    else:selected_total,count_spec=_area_count(s,meta,key,selection,CANCEL.is_set)
    def batches():
        if cached:
            yield from _cached_area_batches(cached,block,CANCEL.is_set)
        else:yield from area_batches(s,meta,key,selection,block,CANCEL.is_set)
    if settings['method']=='smrg':_smrg_guard(selected_total)
    if selected_total<50:raise ValueError('The selected area contains fewer than 50 retained points. Enlarge the area or check coordinates and units.')
    expected_blocks=max(1,int(np.ceil(selected_total/block)));s.progress(f'{"Whole retained scan" if count_spec["mode"]=="full" else "Selected area only"} · cleaning {selected_total:,} points from {meta["count"]:,} source points · {expected_blocks} batches')
    ident=uuid.uuid4().hex;idpath=asset_folder(s.WORK,'analysis')/(ident+'.surface_ids');labelpath=asset_folder(s.WORK,'analysis')/(ident+'.surface_labels')
    if settings['method'] in ('gusac','tmatch','noise_clean','tunnel_envelope','ray_box','irregular','combined_denoise','reflection','lining_features'):
        import clean_tunnel
        try:
            progress=lambda message:s.progress(f'{"Whole retained scan" if count_spec["mode"]=="full" else "Selected area only"} · {selected_total:,} points · {message}')
            source_item=next((x for x in s.STATE.get('job_items',[]) if x['id']==source_item_id),None)
            fingerprint=patch_fingerprint(source_item) if source_item and settings['method'] in ('irregular','lining_features') else None
            if settings['method'] in ('gusac','tmatch'):
                engine=__import__(settings['method'])
                r=engine.run_batches(batches,s.WORK,idpath,labelpath,settings,progress,CANCEL.is_set,selected_total)
                method=('GuSAC-based regional lining cleaning' if settings['method']=='gusac' else 'TMatch-based tunnel lining clustering')+'; Wang et al. (2025), documented interpretation'
            elif settings['method']=='lining_features':
                import lining_features
                intensity=(lambda ids:jobdata._display_rows(meta['attributes_file'],meta['count'],ids,4,'<f4')[:,0]) if meta.get('attributes_file') else None
                r=lining_features.run_batches(batches,idpath,labelpath,settings,progress,CANCEL.is_set,selected_total,intensity_for_ids=intensity)
                method='Lining features: supported unwrapping, compact pockets / fittings, intensity valleys, robust seams and bounded segments'
            elif settings['method']=='reflection':
                import reflection_cleaning
                _,attributes=pointio.arrays(meta)
                intensity=(lambda ids:np.asarray(attributes[ids,0])) if attributes is not None else None
                r=reflection_cleaning.run_batches(batches,idpath,labelpath,settings,progress,CANCEL.is_set,selected_total,
                    origin=reflection_cleaning.scanner_origin(source_item),intensity_for_ids=intensity)
                method='Reflection candidates: lining, mirrored geometry, scanner visibility and intensity evidence'
            elif settings['method']=='combined_denoise':
                import combined_denoising
                r=combined_denoising.run_batches(batches,s.WORK,idpath,labelpath,settings,progress,CANCEL.is_set,selected_total)
                method='Combined denoising: radius support and robust tunnel lining'
            elif settings['method']=='irregular':
                import irregular_surfaces
                r=irregular_surfaces.run_batches(batches,idpath,labelpath,settings,progress,CANCEL.is_set)
                method='Irregular surfaces: supported lining deviations and connected rough patches'
            elif settings['method']=='ray_box':
                import ray_box_extraction
                r=ray_box_extraction.run_batches(batches,idpath,labelpath,settings,progress,CANCEL.is_set)
                method='Ray/box lining extraction: outward radial modes with neighbouring-box support'
            elif settings['method']=='tunnel_envelope':
                import tunnel_lining
                r=tunnel_lining.run_batches(batches,idpath,labelpath,settings,progress,CANCEL.is_set)
                method='Clean Tunnel: one supported lining model with full-density classification'
            else:
                r=clean_tunnel.run_batches(batches(),s.WORK,idpath,labelpath,settings,progress,CANCEL.is_set,selected_total=selected_total)
                method='Remove stray points: radius support and locally adaptive neighbour-distance filtering'
            r.update(settings=settings,method=method,doi='10.3390/app15062926' if settings['method'] in ('gusac','tmatch') else '10.1016/j.measurement.2026.123609' if settings['method']=='ray_box' else '',
                id=ident,revision=uuid.uuid4().hex,created_at=time.time(),source_kind=key,source_meta=meta,
                source_item_id=source_item_id,source_fingerprint=fingerprint,processing_area=body.get('processing_area'),
                point_ids_file=str(idpath),labels_file=str(labelpath),selection=count_spec,review_log=[],outputs={})
            commit_result(s,r)
        except Exception:
            idpath.unlink(missing_ok=True);labelpath.unlink(missing_ok=True);raise
        return
    total=unique=duplicates=supervoxels=expected=passes=initial_edges=removed_edges=retained_edges=noise=0;seconds=0.;spacing_sum=0.;clusters=[];kept=set();offset=0;blocks=0;spec=count_spec;warnings=[]
    try:
        with idpath.open('wb') as idout,labelpath.open('wb') as lout:
            for ids,points,area_spec in batches():
                spec=area_spec
                if len(ids)<50:
                    # Preserve every point in the selected area. Tiny trailing blocks are
                    # reviewable as density noise rather than silently discarded.
                    ids.astype('<i8').tofile(idout);np.full(len(ids),-1,dtype='<i4').tofile(lout);total+=len(ids);noise+=len(ids);blocks+=1
                    warnings.append(f'Processing block {blocks} contained fewer than 50 points and is shown as density noise.')
                    continue
                blocks+=1;s.progress(f'Processing tunnel surface — batch {blocks}/{expected_blocks} · {len(ids):,} points')
                local=extract(points,settings,lambda msg:s.progress(f'Processing batch {blocks}/{expected_blocks} · {msg}'),CANCEL.is_set)
                labels,rows,auto,new_offset=_merge_block_result(local,offset,blocks);offset=new_offset
                ids.astype('<i8').tofile(idout);labels.tofile(lout);clusters.extend(rows);kept.update(auto)
                st=local['statistics'];total+=st['input_points'];unique+=st['unique_points'];duplicates+=st['duplicate_locations'];supervoxels+=st['supervoxels'];expected+=st['expected_supervoxels'];passes+=st['merge_passes'];initial_edges+=st['initial_edges'];removed_edges+=st['removed_edges'];retained_edges+=st['retained_edges'];noise+=st['noise_points'];seconds+=st['seconds'];spacing_sum+=st['spacing_m']*st['input_points'];warnings.extend(local.get('warnings',[]))
        if total<50:raise ValueError('The selected area contains fewer than 50 retained points. Enlarge the area or check coordinates and units.')
        if settings.get('method')=='tunnel_envelope':
            # Aggregate stable semantic clusters across processing blocks.
            combined={}
            for c in clusters:
                k=int(c['id']);a=combined.setdefault(k,{'id':k,'points':0,'supervoxels':0,'centre_sum':np.zeros(3),'linearity_sum':0.0,'planar_sum':0.0,'overlap':c.get('overlap',0.0),'automatic_keep':bool(c.get('automatic_keep'))})
                n=int(c.get('points',0));a['points']+=n;a['supervoxels']+=int(c.get('supervoxels',0));a['centre_sum']+=np.asarray(c.get('centre',[0,0,0]),float)*n;a['linearity_sum']+=float(c.get('linearity',0))*n;a['planar_sum']+=float(c.get('planar_fraction',0))*n
            clusters=[]
            for k,a in combined.items():
                n=max(1,a['points']);clusters.append({'id':k,'points':a['points'],'supervoxels':a['supervoxels'],'centre':(a['centre_sum']/n).tolist(),'linearity':a['linearity_sum']/n,'planar_fraction':a['planar_sum']/n,'overlap':a['overlap'],'automatic_keep':a['automatic_keep']})
            kept={0}
        clusters.sort(key=lambda x:x['points'],reverse=True)
        kept_points=sum(c['points'] for c in clusters if c['id'] in kept)+(noise if -1 in kept else 0)
        if blocks>1:
            if settings.get('method')=='tunnel_envelope':warnings.insert(0,f'All {total:,} selected points were processed in {blocks} memory-safe batches and merged into one tunnel-envelope review result.')
            else:warnings.insert(0,f'All {total:,} selected points were processed automatically in {blocks} bounded blocks. SMRG cluster IDs are independent across block boundaries; inspect seams before applying.')
        # De-duplicate repeated generic warnings while preserving order.
        warnings=list(dict.fromkeys(warnings))
        r={'kept':sorted(kept),'clusters':clusters,'settings':settings,'warnings':warnings,
           'statistics':{'input_points':total,'unique_points':unique,'duplicate_locations':duplicates,'supervoxels':supervoxels,'expected_supervoxels':expected,'merge_passes':passes,'initial_edges':initial_edges,'removed_edges':removed_edges,'retained_edges':retained_edges,'clusters':len(clusters),'kept_points':kept_points,'noise_points':noise,'spacing_m':spacing_sum/max(total,1),'seconds':round(seconds,3),'processing_blocks':blocks},
           'method':('Tunnel Studio tunnel-envelope extraction; block-processed and merged for tunnel lining review.' if settings.get('method')=='tunnel_envelope' else 'SMRG research implementation, Song et al. 2026; independent interpretation, automatically block-processed for large selections.'),'doi':('' if settings.get('method')=='tunnel_envelope' else '10.1038/s41598-026-73755-6')}
        r.update(id=ident,revision=uuid.uuid4().hex,created_at=time.time(),source_kind=key,source_meta=meta,point_ids_file=str(idpath),labels_file=str(labelpath),selection=spec or {'mode':'full'},review_log=[],outputs={})
        if CANCEL.is_set():raise ValueError('Surface extraction cancelled. The previous result is unchanged.')
        r.update(source_item_id=source_item_id,processing_area=body.get('processing_area'))
        commit_result(s,r)
    except Exception:
        idpath.unlink(missing_ok=True);labelpath.unlink(missing_ok=True);raise

def commit_result(s,r):
    if CANCEL.is_set():raise ValueError('Clean Tunnel cancelled. Previous result is unchanged.')
    r['automatic']={'labels_file':r['labels_file'],'clusters':copy.deepcopy(r['clusters']),'kept':list(r['kept'])}
    old=s.STATE.get('surface_result');old_export=s.STATE.get('export_ready');s.STATE.update(surface_result=r,export_ready=None)
    try:s.save()
    except Exception:s.STATE.update(surface_result=old,export_ready=old_export);raise

def review_selection(s,body):
    r=result(s)
    if body.get('revision')!=r['revision']:raise ValueError('The result changed. Refresh it before reviewing points.')
    keep=body.get('keep')
    if type(keep) is not bool:raise ValueError('Choose whether to keep or remove the selected points.')
    spec=jobdata.selection_spec(body.get('selection'))
    ids,labels=result_arrays(r)
    newpath=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.surface_labels')
    new_id=max((c['id'] for c in r['clusters']),default=-1)+1
    counts={};sums={};changed=0
    try:
        with newpath.open('wb') as out:
            for start in range(0,len(ids),pointio.CHUNK):
                s.progress('Updating full-density cleaning review')
                q=jobdata._display_rows(r['source_meta']['file'],r['source_meta']['count'],ids[start:start+pointio.CHUNK])
                q=registration_pose.points(r['source_meta'],q)
                lab=np.array(labels[start:start+len(q)],copy=True)
                chosen=jobdata.selection_mask(q,spec);changed+=int(chosen.sum());lab[chosen]=new_id
                lab.astype('<i4').tofile(out)
                for k in np.unique(lab):
                    use=lab==k;key=int(k);counts[key]=counts.get(key,0)+int(use.sum());sums[key]=sums.get(key,np.zeros(3))+q[use].sum(0)
        if not changed:raise ValueError('No processed points are inside this 3D selection.')
        next_r=copy.deepcopy(r);by_id={c['id']:c for c in next_r['clusters']}
        by_id[new_id]=dict(id=new_id,name='Manually kept' if keep else 'Manually removed',supervoxels=0,
                          linearity=0.,planar_fraction=0.,overlap=0.,automatic_keep=False)
        next_r['clusters']=[dict(by_id[k],points=n,centre=(sums[k]/n).tolist()) for k,n in counts.items() if k>=0]
        next_r['statistics']['noise_points']=counts.get(-1,0)
        next_r['statistics']['clusters']=len(next_r['clusters'])
        kept=set(next_r['kept']) & set(counts);kept.add(new_id) if keep else kept.discard(new_id)
        next_r.update(kept=sorted(kept),labels_file=str(newpath),revision=uuid.uuid4().hex,outputs={})
        next_r['review_log'].append(dict(time=time.time(),points=changed,keep=keep,selection=spec))
        old_export=s.STATE.get('export_ready');s.STATE['export_ready']=None
        s.STATE['surface_result']=next_r
        try:s.save()
        except Exception:s.STATE.update(surface_result=r,export_ready=old_export);raise
    except Exception:newpath.unlink(missing_ok=True);raise
    finally:
        ids._mmap.close();labels._mmap.close()

def reset_review(s,body):
    r=result(s)
    if body.get('revision')!=r['revision']:raise ValueError('The result changed. Refresh and try again.')
    if not r.get('automatic'):raise ValueError('Run cleaning again to enable full review reset.')
    auto=r['automatic'];n=copy.deepcopy(r);n.update(labels_file=auto['labels_file'],clusters=copy.deepcopy(auto['clusters']),
        kept=list(auto['kept']),revision=uuid.uuid4().hex,outputs={})
    n['statistics']['noise_points']=n['statistics']['input_points']-sum(c['points'] for c in n['clusters'])
    n['statistics']['clusters']=len(n['clusters']);n['review_log'].append(dict(time=time.time(),action='restore_automatic'))
    old_export=s.STATE.get('export_ready');s.STATE.update(surface_result=n,export_ready=None)
    try:s.save()
    except Exception:s.STATE.update(surface_result=r,export_ready=old_export);raise

def add_clean_scan(s,body):
    r=result(s)
    if body.get('revision')!=r['revision']:raise ValueError('The result changed. Review the current result.')
    if body.get('reviewed') is not True:raise ValueError('Inspect the cleaning preview before creating the cleaned scan.')
    import job_api
    meta=copy.deepcopy(filtered_cloud(s));meta['name']='Tunnel · '+r['source_meta']['name']
    jobdata.preview_cache(meta,asset_folder(s.WORK,'derived'))
    entry={'id':uuid.uuid4().hex,'kind':'scan','name':meta['name'],'meta':meta,'visible':True,'source_bytes':0,
           'position_status':'Tunnel / kept points · registered position' if not registration_pose.is_identity(meta) else 'Tunnel / kept points · original coordinates','hidden_boxes':[],
           'isolate_box':None,'edit_history':[],'redo_history':[],
           'cleaning_provenance':{'result_id':r['id'],'revision':r['revision'],'method':r['method'],
                'source_item_id':r.get('source_item_id'),'source_name':r['source_meta']['name'],
                'settings':copy.deepcopy(r['settings']),'area':copy.deepcopy(r['selection'])}}
    __import__('scan_edits').archive_cleaning(entry,r)
    entries=job_api.items(s)
    original=next((x for x in entries if x['id']==r.get('source_item_id') and x['kind']=='scan'),None)
    previous_visible=original.get('visible') if original else None
    had_visible=original is not None and 'visible' in original
    entries.append(entry)
    if original is not None and body.get('hide_source') is True:original['visible']=False
    try:s.save()
    except Exception:
        entries.remove(entry)
        if original is not None:
            if had_visible:original['visible']=previous_visible
            else:original.pop('visible',None)
        raise

def _recess_positions(labels, rows, budget=160000):
    """Sample each requested group across its full original-ID range, in bounded reads."""
    if not rows: return np.empty(0,dtype=np.int64)
    if len(rows)>budget: raise ValueError('Select fewer recess groups for a detailed point preview.')
    targets={};seen={};parts=[];remaining=budget
    for index,row in enumerate(sorted(rows,key=lambda c:c['points'])):
        count=int(row['points']);quota=min(count,remaining//(len(rows)-index));remaining-=quota
        ident=row['id'];targets[ident]=np.linspace(0,count-1,quota,dtype=np.int64);seen[ident]=0
    wanted=np.asarray(list(targets),dtype=np.int32)
    for start in range(0,len(labels),pointio.CHUNK):
        block=np.asarray(labels[start:start+pointio.CHUNK]);positions=np.flatnonzero(np.isin(block,wanted))
        if not len(positions):continue
        values=block[positions];order=np.argsort(values,kind='stable');positions=positions[order];values=values[order]
        groups,offsets,counts=np.unique(values,return_index=True,return_counts=True)
        for ident,offset,count in zip(groups,offsets,counts):
            before=seen[ident];after=before+count;target=targets[ident]
            lo,hi=np.searchsorted(target,[before,after]);chosen=target[lo:hi]-before
            if len(chosen):parts.append(start+positions[offset+chosen])
            seen[ident]=after
    if any(seen[row['id']]!=row['points'] for row in rows):
        raise ValueError('The detected point groups changed. Run the analysis again before selecting them.')
    return np.sort(np.concatenate(parts)) if parts else np.empty(0,dtype=np.int64)


def preview(s, requested=None):
    r = result(s); ids, labels = result_arrays(r)
    # Stable evenly spaced display sample only. Computation, review and export use all selected points.
    step = max(1,int(np.ceil(len(ids)/80000))); positions=np.arange(0,len(ids),step,dtype=np.int64)
    candidates=[c for c in r['clusters'] if c.get('preview_indices')]
    if candidates:
        quota=max(1,min(32,20000//len(candidates)))
        extra=[i for c in candidates for i in c.get('preview_indices',[])[:quota] if 0<=i<len(ids)]
        positions=np.unique(np.r_[positions,np.asarray(extra,np.int64)])
    try:
        detail=None
        if requested is not None:
            allowed={c['id']:c for c in r['clusters'] if c.get('feature_kind') in ('bolt_hole','bolt_fitting','lining_departure') and c['points']>0}
            if not isinstance(requested,list) or not requested or any(type(k) is not int or k not in allowed for k in requested):
                raise ValueError('Select detected recess point groups or raised fitting groups for detailed review.')
            if patch_source(s,r) is None:raise ValueError('The source scan changed. Run bolt-feature analysis again before selecting its points.')
            wanted=set(requested);rows=[allowed[k] for k in sorted(wanted)];key=(r['id'],r['revision'])
            cache=getattr(s,'_surface_recess_detail',None)
            if cache and cache['key']==key and cache['complete'] and wanted.issubset(cache['groups']):
                detailed=cache['positions'][np.isin(cache['labels'],list(wanted))]
            else:
                detailed=_recess_positions(labels,rows)
                s._surface_recess_detail=dict(key=key,groups=wanted,positions=detailed,labels=np.asarray(labels[detailed]),
                    complete=len(detailed)==sum(c['points'] for c in rows))
            positions=np.unique(np.r_[positions,detailed])
            shown=int(np.isin(labels[positions],list(wanted)).sum());total=sum(c['points'] for c in rows)
            detail=dict(clusters=sorted(wanted),selected_points=total,displayed_points=shown,complete=shown==total)
        sample_ids=np.asarray(ids[positions]);q=jobdata._display_rows(r['source_meta']['file'],r['source_meta']['count'],sample_ids)
        q=registration_pose.points(r['source_meta'],q)
        origin = (q.min(0)+q.max(0))/2
        if requested is not None and (result(s) is not r or patch_source(s,r) is None):
            raise ValueError('The source or analysis changed while loading points. Select the current result again.')
        return {'origin':origin.tolist(),'points':np.column_stack((q-origin,labels[positions],sample_ids)).tolist(),
                'sample_count':len(q),'source_count':len(ids),'stride':step,'result_id':r['id'],'revision':r['revision'],'detail':detail}
    finally:
        ids._mmap.close();labels._mmap.close()



def source_preview(s,key,budget=120000):
    meta=source(s,key)
    if not meta:raise ValueError('Choose a surface extraction source first.')
    budget=max(10000,min(200000,int(budget)));ids=jobdata._display_sample_ids(meta['count'],budget)
    q=registration_pose.points(meta,jobdata._display_rows(meta['file'],meta['count'],ids))
    use=np.isfinite(q).all(1)
    if meta.get('mask_file'):
        use&=jobdata._display_rows(meta['mask_file'],meta['count'],ids,1,'u1').ravel()==0
    ids=ids[use];q=q[use]
    origin=(q.min(0)+q.max(0))/2 if len(q) else np.zeros(3)
    lo,hi=registration_pose.world_bounds(meta)
    return {'origin':origin.tolist(),'points':np.column_stack((q-origin,np.full(len(q),-2),ids)).tolist(),'sample_count':len(q),'source_count':meta['count'],'minimum':lo.tolist(),'maximum':hi.tolist()}

def review(s, body):
    r = result(s)
    if body.get('revision') != r['revision']: raise ValueError('The extraction result changed. Refresh and review it again.')
    valid = {c['id'] for c in r['clusters']} | {-1}
    kept = body.get('kept')
    if not isinstance(kept,list) or any(type(k) is not int or k not in valid for k in kept): raise ValueError('Invalid cluster selection.')
    kept = sorted(set(kept)); previous = set(r['kept'])
    if previous == set(kept): return
    next_r = copy.deepcopy(r)
    next_r['review_log'].append({'time':time.time(),'added':sorted(set(kept)-previous),'excluded':sorted(previous-set(kept))})
    next_r.update(kept=kept,revision=uuid.uuid4().hex,outputs={})
    old_export=s.STATE.get('export_ready');s.STATE.update(surface_result=next_r,export_ready=None)
    try:s.save()
    except Exception:s.STATE.update(surface_result=r,export_ready=old_export);raise


def delete_patches(s,body):
    """Delete explicit candidate clusters as one reversible source edit."""
    r=result(s)
    if body.get('revision')!=r['revision']:raise ValueError('The detection result changed. Select the patches again.')
    item=patch_source(s,r)
    if item is None:raise ValueError('The source scan or limit box changed. Run detection again before deleting these points.')
    requested=body.get('clusters');allowed={c['id'] for c in r['clusters'] if c.get('irregular_candidate') or c.get('feature_kind') in ('bolt_hole','bolt_fitting','lining_departure')}
    if not isinstance(requested,list) or not requested or any(type(k) is not int or k not in allowed for k in requested):raise ValueError('Select detected bolts or irregular candidate patches. Other lining points are retained.')
    selected=set(requested);ids,labels=result_arrays(r);ident=uuid.uuid4().hex
    reject=asset_folder(s.WORK,'working')/(ident+'.patch_ids');newids=asset_folder(s.WORK,'analysis')/(ident+'.surface_ids');newlabels=asset_folder(s.WORK,'analysis')/(ident+'.surface_labels')
    previous=copy.deepcopy(s.STATE);old_selection=getattr(s,'SELECTION',None);mask_path=None;count=0;edit_paths=[]
    try:
        representatives={}
        with reject.open('wb') as deleted,newids.open('wb') as keptids,newlabels.open('wb') as keptlabels:
            for start in range(0,len(ids),pointio.CHUNK):
                use=np.isin(labels[start:start+pointio.CHUNK],list(selected));part=ids[start:start+len(use)];lab=labels[start:start+len(use)]
                remaining_lab=lab[~use];offset=start-count
                for k in np.unique(remaining_lab):
                    if int(k) not in representatives:
                        positions=np.flatnonzero(remaining_lab==k)
                        representatives[int(k)]=(offset+positions[np.linspace(0,len(positions)-1,min(32,len(positions)),dtype=int)]).tolist()
                part[use].astype('<i8').tofile(deleted);part[~use].astype('<i8').tofile(keptids);lab[~use].astype('<i4').tofile(keptlabels);count+=int(use.sum())
        if not count:raise ValueError('Those patches contain no remaining points.')
        import scan_edits
        edit=scan_edits.apply(s,item,'separate_selection' if body.get('action')=='separate_patches' else 'delete_selection',selected_file=dict(file=str(reject),count=count),reason=('Lining departures' if r['settings'].get('lining_task')=='departures' else 'Bolt removal' if r['settings']['method']=='lining_features' else 'Irregular surfaces')+' · '+str(len(selected))+' reviewed groups',commit=False)
        edit_paths=edit['created_paths']
        mask_path=item['meta'].get('mask_file')
        next_r=copy.deepcopy(r);next_r['clusters']=[c for c in next_r['clusters'] if c['id'] not in selected]
        for c in next_r['clusters']:
            if c.get('preview_indices'):c['preview_indices']=representatives.get(c['id'],[])
        remaining=r['statistics']['input_points']-count
        if remaining:
            next_r.update(source_meta=copy.deepcopy(item['meta']),source_fingerprint=patch_fingerprint(item),point_ids_file=str(newids),labels_file=str(newlabels),kept=[k for k in r['kept'] if k not in selected],revision=uuid.uuid4().hex,outputs={})
            stats=next_r['statistics'];stats.setdefault('original_processed_points',stats['input_points']);stats.update(input_points=remaining,clusters=len(next_r['clusters']),kept_points=kept_count(next_r),noise_candidates=sum(c['points'] for c in next_r['clusters'] if c.get('irregular_candidate')),irregular_patches=sum(c.get('irregular_candidate',False) for c in next_r['clusters']))
            if next_r['settings']['method']=='lining_features':
                next_r['features']=[f for f in next_r.get('features',[]) if f['id'] not in selected]
                stats['bolt_holes']=sum(c.get('feature_kind')=='bolt_hole' for c in next_r['clusters'])
                stats['bolt_fittings']=sum(c.get('feature_kind')=='bolt_fitting' for c in next_r['clusters'])
                if next_r['settings'].get('lining_task')=='departures':
                    stats.update(raised_points=sum(c['points'] for c in next_r['clusters'] if c['id']==1),recessed_points=sum(c['points'] for c in next_r['clusters'] if c['id']==2))
                    next_r['detection']['summary']=f"Remaining departures: {stats['raised_points']:,} raised and {stats['recessed_points']:,} recessed points outside the selected tolerance."
            next_r['automatic']=dict(labels_file=str(newlabels),clusters=copy.deepcopy(next_r['clusters']),kept=[c['id'] for c in next_r['clusters'] if c['automatic_keep']])
            next_r['review_log'].append(dict(time=time.time(),action='delete_patches',clusters=sorted(selected),points=count))
            s.STATE['surface_result']=next_r
        else:s.STATE['surface_result']=None
        s.SELECTION=None;s.progress(f'Saving {count:,} reviewed point changes. Undo reverses this operation.');s.save()
    except Exception:
        s.STATE.clear();s.STATE.update(previous);s.SELECTION=old_selection
        for p in (newids,newlabels):p.unlink(missing_ok=True)
        for path in edit_paths:Path(path).unlink(missing_ok=True)
        if mask_path:Path(mask_path).unlink(missing_ok=True)
        raise
    finally:reject.unlink(missing_ok=True)


def filtered_cloud(s, included=True):
    r = result(s); key = 'surface' if included else 'other'; cached = r.get('outputs',{}).get(key)
    if result_source_changed(s, r):
        raise ValueError('The source scan changed after cleaning. Run cleaning again before creating or exporting a cleaned scan.')
    output_name=('Tunnel · ' if included else 'Noise_removed · ')+r['source_meta']['name']
    if cached and Path(cached['file']).is_file():
        return {**cached,'name':output_name}
    count=kept_count(r) if included else r['statistics']['input_points']-kept_count(r)
    total=int(r['statistics']['input_points']);block=max(1,min(int(pointio.CHUNK),100000))
    def batches():
        # Result files can themselves exceed RAM. Read only owned ID/label
        # blocks; the coordinate gatherer separately bounds its source windows.
        with open(r['point_ids_file'],'rb') as idfile,open(r['labels_file'],'rb') as labfile:
            for start in range(0,total,block):
                size=min(block,total-start)
                ids=np.fromfile(idfile,dtype='<i8',count=size)
                labels=np.fromfile(labfile,dtype='<i4',count=size)
                if len(ids)!=size or len(labels)!=size:
                    raise ValueError('The cleaning result cache is incomplete. Run the analysis again.')
                use=np.isin(labels,r['kept'])
                if not included:use=~use
                yield ids[use]
    stream=batches()
    try:meta=jobdata.extract_ids(r['source_meta'],stream,count,s.WORK,output_name,s.progress)
    finally:stream.close()

    r.setdefault('outputs',{})[key] = meta
    return meta


def apply(s, body):
    r = result(s)
    if body.get('revision') != r['revision'] or body.get('reviewed') is not True: raise ValueError('Review the current result before applying it.')
    if r['source_kind'] != 'survey' or not same_geometry(source(s,'survey'),r['source_meta']):
        raise ValueError('The active survey differs from this result. Run extraction on the current survey before applying; the saved result can still be exported.')
    rejected_count=r['statistics']['input_points']-kept_count(r)
    if not rejected_count: raise ValueError('All processed points are kept; there is nothing to exclude.')
    if not kept_count(r): raise ValueError('Keep at least one surface cluster before applying.')
    path = asset_folder(s.WORK,'working')/(uuid.uuid4().hex+'.selection')
    try:
        total=int(r['statistics']['input_points']);block=max(1,min(int(pointio.CHUNK),100000));written=0
        with path.open('wb') as out,open(r['point_ids_file'],'rb') as idfile,open(r['labels_file'],'rb') as labfile:
            for start in range(0,total,block):
                size=min(block,total-start)
                ids=np.fromfile(idfile,dtype='<i8',count=size)
                labels=np.fromfile(labfile,dtype='<i4',count=size)
                if len(ids)!=size or len(labels)!=size:
                    raise ValueError('The cleaning result cache is incomplete. Run the analysis again.')
                if (ids<0).any() or (ids>=r['source_meta']['count']).any():
                    raise ValueError('The cleaning result contains invalid source point IDs.')
                use=~np.isin(labels,r['kept']);ids[use].tofile(out);written+=int(use.sum())
                s.progress(f'Preparing reviewed exclusions · {start+size:,}/{total:,}')
        if written!=rejected_count:raise ValueError('The cleaning result counts changed. Review or run the analysis again.')
        selection = {'file':str(path),'count':rejected_count,'spec':{'surface_result':r['id'],'revision':r['revision'],'area':r['selection']}}
        active=next((x for x in s.STATE.get('job_items',[]) if x['id']==s.STATE.get('active_job_scan')),None)
        if active:
            import scan_edits,job_api
            job_api.sync_active(s)
            scan_edits.apply(s,active,'delete_selection',selected_file=selection,reason='Reviewed cleaning: excluded points')
            return
        entry = editing.apply_edit(s.STATE['cloud'],selection,s.WORK,s.STATE.setdefault('edits',[]),'Reviewed '+r['method']+': exclude other points inside processed area')
        s.STATE.setdefault('audit',[]).append({'action':'surface_extract','time':entry['time'],'count':entry['count'],'result_id':r['id']})
        s.STATE.update(redo=[],joints=None,report_ready=False,clean_ready=False,export_ready=None)
        s.SELECTION = None; s.save()
    finally: path.unlink(missing_ok=True)


def report(s, fmt):
    saved = result(s)
    r = public_result(s,saved); kept = set(r['kept'])
    if fmt == 'json':
        # Detailed per-call measurements belong in the export, off the review response.
        if 'regional_plane_workload' in saved:
            r['statistics']['regional_plane_workload'] = copy.deepcopy(saved['regional_plane_workload'])
        return r,'application/json','surface_extraction.json'
    if fmt == 'csv' and r['settings'].get('method') in ('gusac','tmatch'):
        out=io.StringIO();w=csv.writer(out)
        w.writerow(['cluster_id','name','cleaning_class','ring_id','points','kept','automatic_keep','centre_e_m','centre_n_m','centre_h_m'])
        for c in r['clusters']:
            w.writerow([c['id'],c.get('name',''),c.get('cleaning_class',''),c.get('ring_id',0),c['points'],c['id'] in kept,c['automatic_keep'],*c['centre']])
        return out.getvalue(),'text/csv','tunnel_classification.csv'
    if fmt == 'csv' and r['settings'].get('method')=='lining_features':
        out=io.StringIO();w=csv.writer(out);w.writerow(['feature_id','type','name','status','original_points','station_local_m','coverage_fraction','fit_rms_m','vertex','E_m','N_m','H_m','metadata_json'])
        for f in r.get('features',[]):
            vertices=f.get('points') or [f.get('centre') or f.get('point_centroid') or [None,None,None]]
            for i,p in enumerate(vertices):w.writerow([f['id'],f['kind'],f['name'],f['status'],f.get('original_points',0),f.get('station_m',''),f.get('coverage_fraction',''),f.get('fit_rms_m',''),i,*p,json.dumps({k:v for k,v in f.items() if k not in ('points','centre','point_centroid')}) if i==0 else ''])
        return out.getvalue(),'text/csv','lining_features.csv'
    if fmt == 'csv':
        out = io.StringIO(); w = csv.writer(out); w.writerow(['cluster_id','points','kept','automatic_keep','supervoxels','centre_e_m','centre_n_m','centre_h_m','boundary_overlap_fraction'])
        for c in r['clusters']: w.writerow([c['id'],c['points'],c['id'] in kept,c['automatic_keep'],c['supervoxels'],*c['centre'],c['overlap']])
        w.writerow([-1,r['statistics']['noise_points'],-1 in kept,False,0,'','','',''])
        return out.getvalue(),'text/csv','surface_clusters.csv'
    if fmt != 'html': raise ValueError('Choose HTML, JSON or CSV report.')
    esc = html.escape
    method=r['settings'].get('method','smrg')
    paper_method=method in ('gusac','tmatch')
    if paper_method:
        cluster_headers='<th>Cluster</th><th>Name</th><th>Cleaning class</th><th>Ring</th><th>Points</th><th>Current decision</th>'
        rows=''.join(f'<tr><td>{c["id"]}</td><td>{esc(c.get("name",""))}</td><td>{c.get("cleaning_class","")}</td><td>{c.get("ring_id",0) or "—"}</td><td>{c["points"]:,}</td><td>{"Keep" if c["id"] in kept else "Exclude"}</td></tr>' for c in r['clusters'])
        classification_details='<h2>Classification and ring evidence</h2><pre>'+esc(json.dumps({'classification':r.get('classification',{}),'rings':r.get('rings',[])},indent=2))+'</pre>' if method=='tmatch' else ''
        method_notes='<h2>Implementation notes</h2><ul>'+''.join('<li>'+esc(note)+'</li>' for note in r.get('algorithm_notes',[]))+'</ul>' if r.get('algorithm_notes') else ''
    else:
        cluster_headers='<th>Cluster</th><th>Points</th><th>Current decision</th><th>Boundary overlap</th>'
        rows = ''.join(f'<tr><td>{c["id"]}</td><td>{c["points"]:,}</td><td>{"Keep" if c["id"] in kept else "Exclude"}</td><td>{c["overlap"]:.3f}</td></tr>' for c in r['clusters'])
        rows+=f'<tr><td>Density noise (-1)</td><td>{r["statistics"]["noise_points"]:,}</td><td>{"Keep" if -1 in kept else "Exclude"}</td><td>—</td></tr>'
        classification_details='';method_notes=''
    evaluation = f'<h2>Synthetic evaluation</h2><pre>{esc(json.dumps(r["evaluation"],indent=2))}</pre>' if r['evaluation'] else '<p>No labelled ground truth was supplied. Accuracy, precision and recall have not been measured for this scan.</p>'
    attribution={
        'ray_box':'Independent adaptation inspired by Xu et al. (2026), Tunnel lining point cloud extraction algorithm based on ray projection and box-neighborhood acceleration, DOI 10.1016/j.measurement.2026.123609. The full paper and author code were not available during implementation, so this is not a reproduction. It uses an independently fitted tunnel frame, outward radial modes and neighbouring station/angle boxes; unsupported boxes remain retained. No published accuracy or speed is claimed.',
        'gusac':'GuSAC-based regional RANSAC lining cleaning, based on Wang et al. (2025), Automated Tunnel Point Cloud Segmentation and Extraction Method, Applied Sciences 15, 2926, DOI 10.3390/app15062926 (CC BY 4.0). The inconsistent printed normal equations are corrected; sparse and ambiguous lining is retained. Implementation decisions and limits are recorded in GUSAC_TMATCH_METHOD_0_15_0.txt. The authors’ reported accuracy has not been reproduced.',
        'tmatch':'TMatch-based lining instance classification after GuSAC, based on Wang et al. (2025), Automated Tunnel Point Cloud Segmentation and Extraction Method, Applied Sciences 15, 2926, DOI 10.3390/app15062926 (CC BY 4.0). Uses original-point radius connectivity and physical longitudinal GMM evidence. Cluster colours are geometric instances, not service-type recognition. The author-specific axis and reassignment steps are not reproduced exactly. See GUSAC_TMATCH_METHOD_0_15_0.txt and the implementation notes.',
        'reflection':'Independent conservative candidate detector using supported lining departure, approximate mirrored geometry, scanner visibility when the acquisition pose is known, and optional intensity evidence. It requires multiple cues and retains weak evidence. Research background: Zhao, Yang and Schwertfeger, Mapping with Reflection (SSRR 2020), https://arxiv.org/abs/1909.12483. This is not a reproduction of their plane/dual-return classifier; no published accuracy is claimed.',
        'combined_denoise':'Independent adaptation of radius filtering plus RANSAC cylinder fitting in Wang, Wei and Wang (2024), DOI 10.12082/dqxxkx.2024.240505. Full-density spatial support and a bounded actual-point calibration sample; optional measured lining profile. Published errors have not been reproduced. Review red candidates; uncertain areas remain retained.',
        'irregular':'Independent candidate detector: broad longitudinal median of a supported lining reference, radial departures, original-point local roughness and connected patches on the reference surface. This is not a design comparison, defect diagnosis, or reproduction of a published accuracy result.',
        'noise_clean':'Independent radius-support and local neighbour-distance filter. Radius filtering follows the PCL principle; the local median/MAD threshold and spatial halo implementation are Tunnel Studio additions. See CLEAN_TUNNEL_METHOD_0_12_0.txt.',
        'tunnel_envelope':'Independent tunnel-envelope extraction using local cylindrical sampling. See the recorded settings. This is not an implementation of Leica proprietary code.',
        'smrg':'Independent interpretation of Song, Jin, Ji and Chen (2026), Supervoxel-based multi-level region growing method for surface point cloud extraction in intersecting tunnels, Scientific Reports, DOI 10.1038/s41598-026-73755-6. The authors’ reported results have not been reproduced. The paper is CC BY 4.0; see CLEAN_TUNNEL_METHOD_0_12_0.txt for implementation limits.'}.get(method,r['method'])
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Tunnel Studio — surface extraction report</title>
    <style>body{{font:15px/1.5 system-ui;max-width:1000px;margin:35px auto;padding:0 25px;color:#183041}}h1{{color:#125f63}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;border-bottom:1px solid #ccc;padding:6px}}pre{{white-space:pre-wrap;background:#f1f5f6;padding:15px;font-size:12px}}@media print{{button{{display:none}}}}</style>
    <button onclick="window.print()">Print / save PDF</button><h1>Tunnel surface extraction</h1><p>Tunnel Studio · {esc(r['method'])} · Review required</p>
    <p><b>Source:</b> {esc(r['source_name'])}<br><b>Created:</b> {time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(r['created_at']))}<br><b>Result:</b> {r['id']}</p>
    <p>{r['statistics']['input_points']:,} processed points; <b>{r['reviewed_kept_points']:,} currently kept</b>; {r['statistics']['noise_points']:,} density-noise points ({'kept' if -1 in kept else 'excluded'}).</p>
    <p>Only the selected area is represented. Original coordinates and available intensity/RGB are preserved. Applying excludes other points within this area; points outside it remain unchanged. The display is sampled; processing and exports use all selected points.</p>
    <h2>Area and settings</h2><pre>{esc(json.dumps({'area':r['selection'],'settings':r['settings']},indent=2))}</pre>
    <h2>Processing statistics</h2><pre>{esc(json.dumps(r['statistics'],indent=2))}</pre>{evaluation}
    <h2>Review notes</h2><ul>{''.join('<li>'+esc(w)+'</li>' for w in r['warnings'])}</ul>
    <p>Automatic labels are a starting suggestion. Boundary overlap, where used, is a geometric merging test and not a confidence score. Saved meshes and previous exported reports do not automatically regenerate after editing.</p>
    <h2>Cluster decisions</h2><table><thead><tr>{cluster_headers}</tr></thead><tbody>{rows}</tbody></table>{classification_details}{method_notes}
    <h2>Detected lining features</h2><pre>{esc(json.dumps(r.get('features',[]),indent=2))}</pre>
    <h2>Manual review history</h2><pre>{esc(json.dumps(r['review_log'],indent=2))}</pre>
    <h2>Method and attribution</h2><p>{esc(attribution)}</p>
    <p>© 2026 Damian Gulczynski. Third-party notices apply to dependencies and the referenced paper.</p></html>'''
    return content,'text/html; charset=utf-8','surface_extraction_report.html'


def get(s,h,path,args):
    if path == '/api/surface': h.send(status(s)); return True
    if path == '/api/surface-preview':
        if s.JOB['busy']: raise ValueError('Wait for the current operation to finish.')
        h.send(preview(s)); return True
    if path == '/api/surface-source-preview':
        if s.JOB['busy']: raise ValueError('Wait for the current operation to finish.')
        h.send(source_preview(s,args.get('source',['separate'])[0],args.get('budget',[120000])[0])); return True
    if path == '/api/surface-report':
        if s.JOB['busy']: raise ValueError('Wait for the current operation to finish.')
        content,ctype,name = report(s,args.get('format',['html'])[0]); h.send(content,ctype,filename=name); return True
    return False


def post(s,h,path,body):
    if path == '/api/surface-preview':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        r=result(s)
        if body.get('result_id')!=r['id'] or body.get('revision')!=r['revision']:
            raise ValueError('The analysis result changed. Select its current point groups again.')
        h.send(preview(s,body.get('clusters')));return True
    if path != '/api/surface-action': return False
    action = body.get('action')
    if action == 'cancel':
        if s.JOB.get('kind') == 'surface' and s.JOB['busy']: CANCEL.set(); s.progress('Cancelling surface extraction at the next processing checkpoint…')
        h.send({'cancel_requested':True}); return True
    if s.JOB['busy']: raise ValueError('Wait for the current operation to finish.')
    if action == 'run':
        CANCEL.clear(); s.start_job(lambda:run(s,body),kind='surface',native_progress=True)
    elif action == 'demo':
        def demo():
            from surface_demo import generate
            s.progress('Creating a labelled synthetic tunnel intersection, pipe and equipment box')
            folder = s.WORK/('surface_demo_'+uuid.uuid4().hex)
            path,truth,_,_ = generate(folder,spacing=.09)
            for product in (path,truth):__import__('storage_cleanup').record_product(s.WORK,product,'demo',s.STATE)
            meta = pointio.import_cloud(path,asset_folder(s.WORK,'imported')/(uuid.uuid4().hex+'.xyz64'),s.progress)
            meta.update(truth_file=str(truth),synthetic_surface_demo=True)
            s.STATE['surface_source'] = meta; s.STATE['surface_source_item']=None; s.save()
        s.start_job(demo)
    elif action == 'review': review(s,body)
    elif action == 'review_selection': s.start_job(lambda:review_selection(s,body),kind='surface')
    elif action in ('delete_patches','separate_patches'): s.start_job(lambda:delete_patches(s,body),kind='surface')
    elif action == 'reset_review': reset_review(s,body)
    elif action == 'add_clean_scan': s.start_job(lambda:add_clean_scan(s,body),kind='surface')
    elif action == 'apply': s.start_job(lambda:apply(s,body))
    else: raise ValueError('Unknown surface extraction command.')
    h.send({'started':s.JOB['busy'],'saved':True}); return True
