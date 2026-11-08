# Copyright (c) 2026 Damian Gulczynski.
"""Immutable point masks, retained properties and individually restorable deletions."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy, hashlib, time, uuid
import numpy as np
import pointio, jobdata


def mask(meta, path=None):
    path = meta.get('mask_file') if path is None else path
    return np.memmap(path, 'u1', 'r', shape=(meta['count'],)) if path else None


def _close_arrays(*values):
    """Release large mappings immediately, including on cancelled edits."""
    for value in values:
        mapped=getattr(value,'_mmap',None)
        if mapped is not None and not mapped.closed:mapped.close()



# Display metadata is deliberately separate from analytical retained properties.
# Missing legacy counts must not make opening the explorer read every XYZ/RGB row.
from collections import OrderedDict, deque
import threading

_DISPLAY_COUNTS = OrderedDict()
_DISPLAY_QUEUE = deque()
_DISPLAY_LOCK = threading.RLock()
_DISPLAY_THREAD = None
_DISPLAY_MAX_RECORDS = 2048
_DISPLAY_MAX_QUEUED = 128
_DISPLAY_MASK_CHUNK = 8 * 1024 * 1024


def _properties_signature(meta):
    count = int(meta['count'])
    if count < 0:
        raise ValueError('Invalid original point count.')
    rows = []
    for name, width in (('file',24), ('attributes_file',16), ('mask_file',1)):
        value = meta.get(name)
        if not value:
            if name == 'file':
                raise ValueError('The point cache is unavailable.')
            continue
        path = Path(value); stat = path.stat()
        if stat.st_size != count * width:
            raise ValueError('A point cache or deletion mask is incomplete.')
        rows.append([name, str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return [count, rows]


def _display_key(signature):
    return (signature[0], tuple(tuple(row) for row in signature[1]))


def cached_properties(meta):
    """Return only an exact cache for the current immutable source/mask.

    Older releases keyed exact metadata by the immutable mask path. Retain that
    contract, then anchor the legacy cache to observed file revisions so later
    in-place replacement cannot leave an exact-looking stale count or bounds.
    """
    cached = meta.get('retained_properties')
    if not isinstance(cached,dict) or cached.get('revision') != (meta.get('mask_file') or ''):
        return None
    try:
        signature = _properties_signature(meta)
        count = cached.get('count')
        if type(count) is not int or not 0 <= count <= int(meta['count']):
            return None
        if count:
            lo=np.asarray(cached.get('minimum'),float);hi=np.asarray(cached.get('maximum'),float)
            if lo.shape!=(3,) or hi.shape!=(3,) or not np.isfinite([lo,hi]).all() or np.any(lo>hi):
                return None
        values=cached.get('attribute_counts')
        if not isinstance(values,list) or len(values)!=4 or any(type(v) is not int or not 0<=v<=count for v in values):
            return None
        previous=cached.get('source_signature')
        if previous is not None and previous!=signature:
            return None
        if previous is None:
            cached['source_signature']=signature
        return cached
    except (OSError,ValueError,TypeError,KeyError):
        return None


def cached_summary(meta):
    """Exact retained count without claiming bounds or channel statistics.

    New edits know how many mask bytes changed. Persist that small fact even
    when legacy projects have no analytical cache, and require the complete
    source/mask revision before reusing it after a reload or an active alias.
    """
    cached=meta.get('retained_summary')
    if not isinstance(cached,dict) or cached.get('revision')!=(meta.get('mask_file') or ''):
        return None
    try:
        count=cached.get('count')
        if type(count) is not int or not 0<=count<=int(meta['count']):return None
        if cached.get('source_signature')!=_properties_signature(meta):return None
        return cached
    except (OSError,ValueError,TypeError,KeyError):
        return None


def _derived_signature(path,count):
    try:
        source=Path(path);stat=source.stat()
        if stat.st_size!=int(count)*32:return None
        return [str(source.resolve()),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]
    except (OSError,ValueError,TypeError):return None


def _derived_count(meta,cached,derived):
    if not isinstance(cached,dict) or not derived or cached.get('derived')!=derived:return None
    value=cached.get('valid_points')
    if type(value) is not int or not 0<=value<=cached['count']:return None
    signature=_derived_signature(derived,meta['count'])
    if signature is None or cached.get('derived_signature')!=signature:return None
    return value


def _summary(meta,count,derived=None,valid_points=None,derived_signature=None):
    out=dict(revision=meta.get('mask_file') or '',source_signature=_properties_signature(meta),count=int(count))
    if derived and type(valid_points) is int and 0<=valid_points<=count:
        signature=_derived_signature(derived,meta['count'])
        if signature is not None and (derived_signature is None or signature==derived_signature):
            out.update(derived=derived,valid_points=valid_points,derived_signature=signature)
    return out


def record_projection(meta,derived,valid_points):
    """Attach a freshly computed, retained-row projection total to its source.

    Permanent analysis producers call this after validating their projection.
    Only a mask count can be needed here; no coordinate/channel statistics are
    calculated. Temporary projections must not attach their short-lived paths.
    """
    source_signature=_properties_signature(meta)
    derived_signature=_derived_signature(derived,meta['count'])
    count=retained_count(meta)
    if type(valid_points) is not int or not 0<=valid_points<=count or derived_signature is None:
        raise ValueError('The retained analysis point count or projection cache is invalid.')
    summary=_summary(meta,count,derived,valid_points,derived_signature)
    if summary['source_signature']!=source_signature or 'derived' not in summary:
        raise ValueError('The scan or projection changed while its analysis count was recorded.')
    props=cached_properties(meta)
    if props is not None:
        if props['count']!=count:meta.pop('retained_properties',None)
        else:
            props.update(derived=derived,valid_points=valid_points,derived_signature=derived_signature)
    meta['retained_summary']=summary
    _record_exact_count(meta,summary)
    return summary


def _source_properties(meta):
    """Use original import statistics only when all exact fields are present."""
    count=int(meta['count']);counts=meta.get('attribute_counts')
    if counts is None and not meta.get('attributes_file'):counts=[0]*4
    if not isinstance(counts,list) or len(counts)!=4 or any(type(v) is not int or not 0<=v<=count for v in counts):return None
    if count:
        lo=np.asarray(meta.get('minimum'),float);hi=np.asarray(meta.get('maximum'),float)
        if lo.shape!=(3,) or hi.shape!=(3,) or not np.isfinite([lo,hi]).all() or np.any(lo>hi):return None
    return dict(count=count,minimum=copy.deepcopy(meta.get('minimum')) if count else None,
                maximum=copy.deepcopy(meta.get('maximum')) if count else None,attribute_counts=list(counts))


def _cheap_properties(meta):
    cached=cached_properties(meta)
    if cached is not None:return copy.deepcopy(cached)
    if meta.get('mask_file'):return None
    out=_source_properties(meta)
    if out is not None:out.update(revision='',source_signature=_properties_signature(meta))
    return out


def _trim_display_counts():
    while len(_DISPLAY_COUNTS)>_DISPLAY_MAX_RECORDS:
        removable=next((key for key,row in _DISPLAY_COUNTS.items() if row['state'] in ('ready','error')),None)
        if removable is None:
            break
        _DISPLAY_COUNTS.pop(removable,None)


def _count_entry(meta,key,enqueue):
    """Called with _DISPLAY_LOCK; at most one active count per file revision."""
    existing=_DISPLAY_COUNTS.get(key)
    if existing is not None:
        _DISPLAY_COUNTS.move_to_end(key)
        return existing
    if enqueue and len(_DISPLAY_QUEUE)>=_DISPLAY_MAX_QUEUED:
        return None
    inputs={k:meta[k] for k in ('file','attributes_file','mask_file','count') if k in meta}
    entry={'state':'queued' if enqueue else 'running','count':None,'error':None,
           'event':threading.Event(),'meta':inputs}
    _DISPLAY_COUNTS[key]=entry
    if enqueue:
        _DISPLAY_QUEUE.append(key)
    _trim_display_counts()
    return entry


def _publish_count(key,entry,count=None,error=None):
    with _DISPLAY_LOCK:
        if _DISPLAY_COUNTS.get(key) is not entry:
            return
        # An exact analytical cache may have completed while this worker read.
        if entry['state']=='ready':
            return
        entry.update(state='error' if error else 'ready',count=count,error=error)
        entry['event'].set()
        _trim_display_counts()


def _read_mask_count(meta,key,entry,cancel=None,background=False):
    """Only deletion bytes are read; no point coordinates or attributes."""
    path=Path(meta['mask_file']);count=int(meta['count']);retained=0
    for start in range(0,count,_DISPLAY_MASK_CHUNK):
        if cancel is not None and cancel():
            raise InterruptedError('The previous viewport request was cancelled.')
        with _DISPLAY_LOCK:
            if _DISPLAY_COUNTS.get(key) is not entry or entry['state']!='running':
                raise InterruptedError('The point count is no longer needed.')
        if _display_key(_properties_signature(meta))!=key:
            raise ValueError('The deletion mask changed while its count was loading.')
        wanted=min(_DISPLAY_MASK_CHUNK,count-start)
        # Close each bounded read promptly so deleting/closing a project does
        # not leave a large Windows mask file locked for the entire count.
        with path.open('rb') as source:
            source.seek(start);flags=np.fromfile(source,'u1',count=wanted)
        if len(flags)!=wanted:
            raise ValueError('The deletion mask is incomplete.')
        retained+=len(flags)-int(np.count_nonzero(flags))
        if background:
            time.sleep(.001)
    if _display_key(_properties_signature(meta))!=key:
        raise ValueError('The deletion mask changed while its count was loading.')
    return retained


def _display_worker():
    global _DISPLAY_THREAD
    while True:
        with _DISPLAY_LOCK:
            if not _DISPLAY_QUEUE:
                _DISPLAY_THREAD=None
                return
            key=_DISPLAY_QUEUE.popleft();entry=_DISPLAY_COUNTS.get(key)
            if entry is None or entry['state']!='queued':
                continue
            entry['state']='running'
        try:
            value=_read_mask_count(entry['meta'],key,entry,background=True)
            _publish_count(key,entry,count=value)
        except Exception as error:
            _publish_count(key,entry,error=str(error)[:512] or 'The retained point count could not be read.')


def _schedule_count(meta,key):
    global _DISPLAY_THREAD
    with _DISPLAY_LOCK:
        entry=_count_entry(meta,key,True)
        if entry is not None and entry['state']=='queued' and _DISPLAY_THREAD is None:
            _DISPLAY_THREAD=threading.Thread(target=_display_worker,name='TunnelStudio-display-count',daemon=True)
            _DISPLAY_THREAD.start()
        return entry


def _record_exact_count(meta,cached):
    if not meta.get('mask_file'):
        return
    key=_display_key(_properties_signature(meta))
    with _DISPLAY_LOCK:
        entry=_DISPLAY_COUNTS.get(key)
        if entry is not None and entry['state']!='ready':
            entry.update(state='ready',count=int(cached['count']),error=None)
            entry['event'].set()


def display_properties(meta,schedule=True):
    """Nonblocking inventory values, with explicit completeness flags.

    Conservative source bounds never enter retained_properties. The count may
    initially be an upper bound, then become exact after a mask-only read.
    Analytical/editing callers continue to use properties().
    """
    cached=cached_properties(meta)
    if cached is not None:
        _record_exact_count(meta,cached)
        return {**cached,'count_exact':True,'bounds_exact':True,'metadata_pending':False,'metadata_error':None}
    out=dict(revision=meta.get('mask_file') or '',count=int(meta['count']),
             minimum=meta.get('minimum'),maximum=meta.get('maximum'),
             attribute_counts=meta.get('attribute_counts',[0]*4),
             count_exact=not bool(meta.get('mask_file')),bounds_exact=not bool(meta.get('mask_file')),
             metadata_pending=False,metadata_error=None)
    if not meta.get('mask_file'):
        return out
    summary=cached_summary(meta)
    if summary is not None:
        _record_exact_count(meta,summary)
        out.update(count=summary['count'],count_exact=True)
        if not out['count']:
            out.update(minimum=None,maximum=None,attribute_counts=[0]*4,bounds_exact=True)
        elif out['count']==int(meta['count']):
            out['bounds_exact']=True
        return out
    try:
        key=_display_key(_properties_signature(meta))
    except (OSError,ValueError,TypeError,KeyError) as error:
        out['metadata_error']=str(error)[:512] or 'The retained point count is unavailable.'
        return out
    if schedule:
        entry=_schedule_count(meta,key)
    else:
        with _DISPLAY_LOCK:
            entry=_DISPLAY_COUNTS.get(key)
    with _DISPLAY_LOCK:
        if entry is not None:
            status=entry['state']
            out['metadata_pending']=status in ('queued','running')
            out['metadata_error']=entry['error']
            if status=='ready':
                out.update(count=int(entry['count']),count_exact=True)
                if not out['count']:
                    out.update(minimum=None,maximum=None,attribute_counts=[0]*4,bounds_exact=True)
        elif schedule:
            out['metadata_pending']=True
            out['metadata_error']='The retained count is deferred until other count requests finish.'
    return out


def retained_count(meta,cancel=None):
    """Exact display count without computing XYZ bounds or attribute statistics."""
    cached=cached_properties(meta)
    if cached is not None:
        _record_exact_count(meta,cached)
        return int(cached['count'])
    summary=cached_summary(meta)
    if summary is not None:
        _record_exact_count(meta,summary)
        return summary['count']
    if not meta.get('mask_file'):
        return int(meta['count'])
    key=_display_key(_properties_signature(meta))
    with _DISPLAY_LOCK:
        entry=_DISPLAY_COUNTS.get(key)
        own=entry is None or entry['state']=='queued'
        if entry is None:
            entry=_count_entry(meta,key,False)
        elif own:
            try:_DISPLAY_QUEUE.remove(key)
            except ValueError:pass
            entry['state']='running'
    if own:
        try:
            value=_read_mask_count(entry['meta'],key,entry,cancel=cancel)
            _publish_count(key,entry,count=value)
        except InterruptedError as error:
            # The caller may abandon a refinement. The inventory still needs
            # the shared count, so queue that revision once in the background.
            with _DISPLAY_LOCK:
                if entry['state']=='ready' and (cancel is None or not cancel()):
                    return int(entry['count'])
                if entry['state']=='running':
                    entry['state']='queued';_DISPLAY_QUEUE.append(key)
            _schedule_count(meta,key)
            raise
        except Exception as error:
            _publish_count(key,entry,error=str(error)[:512] or 'The retained point count could not be read.')
            raise
    else:
        while not entry['event'].wait(.03):
            if cancel is not None and cancel():
                raise InterruptedError('The previous viewport request was cancelled.')
    with _DISPLAY_LOCK:
        if entry['state']!='ready':
            raise ValueError(entry['error'] or 'The retained point count is unavailable.')
        return int(entry['count'])

def properties(meta, progress=lambda message: None):
    key = meta.get('mask_file') or ''
    cached = cached_properties(meta)
    if cached is not None:return cached
    signature=_properties_signature(meta)
    summary=cached_summary(meta)
    out=_source_properties(meta) if not key or (summary is not None and summary['count']==int(meta['count'])) else None
    if summary is not None and summary['count']==0:
        out=dict(count=0,minimum=None,maximum=None,attribute_counts=[0]*4)
    if out is None:
        points, attrs = pointio.arrays(meta); excluded = mask(meta)
        lo = np.full(3, np.inf); hi = -lo; count = 0; counts = np.zeros(4, dtype=np.int64)
        try:
            for start in range(0, meta['count'], pointio.CHUNK):
                stop = min(meta['count'], start+pointio.CHUNK); use = excluded[start:stop] == 0 if excluded is not None else slice(None)
                q = points[start:stop][use]; count += len(q)
                if len(q): lo = np.minimum(lo, q.min(0)); hi = np.maximum(hi, q.max(0))
                if attrs is not None: counts += np.isfinite(attrs[start:stop][use]).sum(0)
                progress(f'Updating remaining points and bounds: {stop:,} / {meta["count"]:,}')
        finally: _close_arrays(excluded,points,attrs)
        out = dict(count=count, minimum=lo.tolist() if count else None, maximum=hi.tolist() if count else None, attribute_counts=counts.tolist())
    if signature!=_properties_signature(meta):raise ValueError('The scan changed while its retained properties were calculated.')
    out.update(revision=key,source_signature=signature)
    if summary is not None and summary['count']==out['count']:
        value=_derived_count(meta,summary,summary.get('derived'))
        if value is not None:
            out.update(derived=summary['derived'],valid_points=value,derived_signature=summary['derived_signature'])
    meta['retained_properties'] = out
    meta['retained_summary']=_summary(meta,out['count'],out.get('derived'),out.get('valid_points'),out.get('derived_signature'))
    _record_exact_count(meta,out)
    return out


def records(item):
    """Older project deletions already contain the two immutable masks we need."""
    if 'deleted_groups' not in item:
        groups = []
        for entry in item.get('edit_history', []):
            if entry.get('count', 0) <= 0 or not entry.get('after_mask'): continue
            ident = hashlib.sha256(str(entry['after_mask']).encode()).hexdigest()[:24]
            groups.append(dict(id=ident, kind='move' if entry.get('separated_item') else 'delete', name=entry.get('reason') or 'Earlier deletion', count=entry['count'], before_mask=entry.get('before_mask'), after_mask=entry['after_mask'], active=True, created=entry.get('time', 0)))
            entry.setdefault('group_id', ident)
        item['deleted_groups'] = groups
    return item['deleted_groups']


def group_ids(meta, group):
    if group.get('remaining') is not None:
        yield from __import__('deleted_points').id_batches(group['remaining'])
        return
    if group.get('archive'):
        a=group['archive'];n=a['input_count']
        from bounded_rows import RowFile
        with RowFile(a['point_ids_file'],'<i8',n) as ids, RowFile(a['labels_file'],'<i4',n) as labels:
            for start in range(0,n,pointio.CHUNK):
                count=min(pointio.CHUNK,n-start)
                use=~np.isin(labels.read(start,count),a['kept'])
                yield ids.read(start,count)[use]
        return
    before = mask(meta, group.get('before_mask') or '')
    after = mask(meta, group['after_mask'])
    try:
        for start in range(0, meta['count'], pointio.CHUNK):
            stop = min(meta['count'], start+pointio.CHUNK)
            use = after[start:stop] != 0
            if before is not None: use &= before[start:stop] == 0
            ids = np.flatnonzero(use).astype('<i8') + start
            if len(ids): yield ids
    finally:
        if before is not None:before._mmap.close()
        if after is not None:after._mmap.close()


def record(item, ident):
    group = next((g for g in records(item) if g['id'] == ident and g['kind'] == 'delete' and g['active']), None)
    if group is None: raise ValueError('This deleted group has already been restored or is no longer available.')
    return group


def update_mask(meta, ids_batches, work, value, progress, created, derived=None, valid_points=None, derived_revision=None):
    """Commit exact changed IDs without an obligatory whole-cloud stats pass.

    Source points and attributes remain immutable. Bounds/channel statistics
    survive only when a small interior edit can update a proven exact cache;
    otherwise properties() computes them when an analytical caller asks.
    """
    from contextlib import ExitStack
    from bounded_rows import RowFile
    original_count=int(meta['count']);signature=_properties_signature(meta)
    old=mask(meta);out=None;dest=None;count=0;prior_count=None;previous=None
    points=None;attrs=None;analysis=None;valid_delta=0;readers=ExitStack()
    derived_signature=_derived_signature(derived,original_count) if derived else None
    derived_known=derived_signature is not None and type(valid_points) is int and 0<=valid_points<=original_count
    if derived_revision is not None and derived_signature!=derived_revision:derived_known=False
    # Optional analytical-cache maintenance must have a fixed work budget,
    # independent of the number of source rows and the size of a deletion.
    property_budget=pointio.CHUNK
    try:
        for ids in ids_batches:
            ids=np.unique(np.asarray(ids,dtype=np.int64))
            if len(ids) and (ids[0]<0 or ids[-1]>=original_count):raise ValueError('The point selection is no longer valid.')
            current=out if out is not None else old
            if current is not None:ids=ids[(current[ids]!=0) != bool(value)]
            elif not value:ids=ids[:0]
            if len(ids):
                if out is None:
                    previous=_cheap_properties(meta)
                    jobdata.room(work,original_count);dest=asset_folder(work,'working')/(uuid.uuid4().hex+'.mask');created.append(dest)
                    excluded=__import__('mask_storage').copy_mask(meta.get('mask_file'),dest,original_count,progress)
                    # Compatibility for older injected copy helpers. Production
                    # counts exclusions in the spans it already has to copy.
                    prior_count=original_count-int(excluded) if excluded is not None else retained_count(meta)
                    if previous is not None and previous['count']!=prior_count:previous=None
                    if derived_known and valid_points>prior_count:derived_known=False
                    out=np.memmap(dest,'u1','r+',shape=(original_count,))
                    _close_arrays(old);old=None
                if previous is not None and count+len(ids)>property_budget:previous=None
                if previous is not None:
                    if points is None:points=readers.enter_context(RowFile(meta['file'],'<f8',original_count,3))
                    q=points.take(ids)
                    lo=np.asarray(previous['minimum'],float) if previous['count'] else np.full(3,np.inf)
                    hi=np.asarray(previous['maximum'],float) if previous['count'] else np.full(3,-np.inf)
                    if value and bool(((q<=lo)|(q>=hi)).any()):
                        previous=None
                    else:
                        if not value:
                            previous['minimum']=np.minimum(lo,q.min(0)).tolist();previous['maximum']=np.maximum(hi,q.max(0)).tolist()
                        counts=np.asarray(previous['attribute_counts'],np.int64)
                        known=counts if value else np.asarray(meta.get('attribute_counts',[-1]*4),np.int64)
                        total=previous['count'] if value else original_count
                        if not meta.get('attributes_file'):delta=np.zeros(4,np.int64)
                        elif np.all((known==0)|(known==total)):delta=(known!=0).astype(np.int64)*len(ids)
                        else:
                            if attrs is None:attrs=readers.enter_context(RowFile(meta['attributes_file'],'<f4',original_count,4))
                            delta=np.isfinite(attrs.take(ids)).sum(0)
                        previous['attribute_counts']=(counts-delta if value else counts+delta).tolist()
                        previous['count']+=-len(ids) if value else len(ids)
                if derived_known:
                    if value and valid_points in (0,prior_count):valid_delta+=len(ids) if valid_points else 0
                    else:
                        if analysis is None:analysis=readers.enter_context(RowFile(derived,'<f8',original_count,4))
                        # Batches supplied by selection/group readers are
                        # bounded; support external larger ID batches too.
                        for begin in range(0,len(ids),pointio.CHUNK):
                            valid_delta+=int(np.isfinite(analysis.take(ids[begin:begin+pointio.CHUNK])[:,0]).sum())
                out[ids]=value;count+=len(ids)
            progress(f'{"Excluding" if value else "Restoring"} points: {count:,}')
        if not count:raise ValueError('No retained points in this selection.' if value else 'No points in this group need restoring.')
        out.flush();_close_arrays(out);out=None
        if signature!=_properties_signature(meta):raise ValueError('The scan changed while its point edit was calculated.')
        retained=prior_count+(-count if value else count)
        future={**meta,'mask_file':str(dest)}
        if derived_known and derived_signature!=_derived_signature(derived,original_count):derived_known=False
        valid_after=valid_points+(-valid_delta if value else valid_delta) if derived_known else None
        summary=_summary(future,retained,derived if derived_known else None,valid_after,derived_signature)
        if retained==0:previous=dict(count=0,minimum=None,maximum=None,attribute_counts=[0]*4)
        elif retained==original_count:previous=_source_properties(meta)
        if previous is not None:
            previous.update(summary)
            # Optional source/derived provenance must never outlive its count.
            for name in ('derived','valid_points','derived_signature'):
                if name not in summary:previous.pop(name,None)
            meta['retained_properties']=previous
        else:meta.pop('retained_properties',None)
        meta['retained_summary']=summary
    finally:
        _close_arrays(out,old);readers.close()
        close=getattr(ids_batches,'close',None)
        if close is not None:close()
    return str(dest),count


def refresh_aliases(s, item):
    meta = item['meta']; props = cached_properties(meta); summary=cached_summary(meta)
    if summary is not None:_record_exact_count(meta,summary)
    s.STATE['export_ready']=None
    if s.STATE.get('active_job_scan') == item['id']:
        s.STATE.update(cloud=copy.deepcopy(meta), edits=copy.deepcopy(item.get('edit_history', [])), redo=copy.deepcopy(item.get('redo_history', [])), joints=None, report_ready=False, clean_ready=False, export_ready=None)
        if s.STATE.get('derived'):
            value=_derived_count(meta,summary,s.STATE['derived'])
            if value is None:value=_derived_count(meta,props,s.STATE['derived'])
            # This is an informational total, not an input to analysis. Keep
            # the valid per-original-row projection and current exact mask;
            # an old project without a proven total must not synchronously
            # reread all analysis rows or discard its displayed analysis.
            s.STATE['valid_points']=value
        else: s.STATE['valid_points'] = retained_count(meta)
        s.SELECTION = None
    if s.STATE.get('surface_source_item') == item['id']:
        s.STATE['surface_source'] = copy.deepcopy(meta)
    if s.STATE.get('active_job_registration') == item['id']:
        registration = s.STATE.get('registration', {})
        if registration.get('cloud', {}).get('file') == meta['file']:
            registration.update(cloud=copy.deepcopy(meta), candidates=[], solution=None, transformed=None, revision=uuid.uuid4().hex)
            item['registration_state'] = copy.deepcopy(registration)


def apply(s, item, action, selection=None, group_id=None, reason='Selected points', selected_file=None, progress=None, commit=True, rollback_state=True, remainder=None):
    """One state commit per edit, including both halves of a separation."""
    progress=progress or s.progress
    if selection is not None and action in ('delete_selection','separate_selection'):
        import preview_index
        if not preview_index.selection_possible(item['meta'],jobdata.selection_spec(selection)):
            raise ValueError('No retained points in this selection.')
    # A multi-scan transaction owns one outer snapshot. Avoid copying the entire
    # project once per scan while preserving the ordinary single-edit boundary.
    previous = copy.deepcopy(s.STATE) if rollback_state else None; created = []; newmeta = None
    old_selection=getattr(s,'SELECTION',None)
    try:
        meta = item['meta']; groups = records(item)
        derived=s.STATE.get('derived') if s.STATE.get('active_job_scan')==item['id'] else None
        derived_revision=_derived_signature(derived,meta['count']) if derived else None
        # Older projection producers counted finite rows before applying an
        # existing deletion mask. That scalar is trustworthy for an unmasked
        # cloud only; a masked cloud needs revision-validated retained evidence.
        valid_points=s.STATE.get('valid_points') if derived and not meta.get('mask_file') else None
        active=s.STATE.get('cloud')
        if derived and isinstance(active,dict) and any(active.get(k)!=meta.get(k) for k in ('file','count','mask_file')):valid_points=None
        if derived:
            saved_summary=cached_summary(meta)
            saved=_derived_count(meta,saved_summary,derived)
            if saved is None and (saved_summary is None or not saved_summary.get('derived')):
                saved=_derived_count(meta,cached_properties(meta),derived)
            if saved is not None:valid_points=saved
            elif meta.get('retained_summary') is not None and saved_summary is None:valid_points=None
            elif isinstance(saved_summary,dict) and saved_summary.get('derived')==derived:
                # A changed/replaced analysis file invalidates an old scalar
                # total even if the active alias still holds that old number.
                valid_points=None
            if derived_revision is None:valid_points=None
        if action in ('undo_edit', 'redo_edit'):
            undo = action == 'undo_edit'; src = item.setdefault('edit_history' if undo else 'redo_history', [])
            dst = item.setdefault('redo_history' if undo else 'edit_history', [])
            if not src: raise ValueError('No point edit to '+('undo.' if undo else 'redo.'))
            entry = src[-1]; child = entry.get('separated_item')
            if child:
                existing = next((x for x in s.STATE['job_items'] if x['id'] == child['id']), None)
                if undo:
                    if existing and (any(existing['meta'].get(k)!=child['meta'].get(k) for k in ('file','count','attributes_file')) or retained_count(existing['meta'])!=child['meta']['count']):
                        raise ValueError('The separated scan has been edited. Undo its edits first before undoing this separation.')
                    if existing:entry['separated_item']=copy.deepcopy(existing)
                    s.STATE['job_items'] = [x for x in s.STATE['job_items'] if x['id'] != child['id']]
                    for key in ('active_job_scan','active_job_registration','surface_source_item'):
                        if s.STATE.get(key)==child['id']:s.STATE[key]=None
                elif existing: raise ValueError('The separated scan is already present.')
                else: s.STATE['job_items'].append(copy.deepcopy(child))
            meta['mask_file'] = entry.get('before_mask' if undo else 'after_mask')
            for suffix,key in (('properties','retained_properties'),('summary','retained_summary')):
                cached=entry.get(('before_' if undo else 'after_')+suffix)
                if cached:meta[key]=copy.deepcopy(cached)
                else:meta.pop(key,None)
            saved_groups = entry.get('before_groups' if undo else 'after_groups')
            if saved_groups is not None: item['deleted_groups'] = copy.deepcopy(saved_groups)
            else:
                for group in groups:
                    if group['id'] == entry.get('group_id'): group['active'] = not undo
            src.pop(); dst.append(entry)
        else:
            before_groups = copy.deepcopy(groups); before = meta.get('mask_file');before_properties=_cheap_properties(meta)
            if action == 'restore_deleted':
                group = record(item, group_id)
                batches=(__import__('deleted_points').id_batches({'ids_file':selected_file['file'],'count':selected_file['count']})
                         if selected_file else group_ids(meta,group))
                after, count = update_mask(meta,batches,s.WORK,0,progress,created,derived,valid_points,derived_revision)
                if remainder is None:group['active']=False
                else:
                    group.update(active=bool(remainder['count']),count=remainder['count'])
                    if remainder['count']:group['remaining']=copy.deepcopy(remainder)
                    else:group.pop('remaining',None)
                reason = ('Restore selected: ' if remainder is not None else 'Restore: ')+group['name']; signed_count = -count
            else:
                if selected_file:
                    def selected_batches():
                        points=None;ids=None
                        try:
                            ids=np.memmap(selected_file['file'],'<i8','r',shape=(selected_file['count'],))
                            if item.get('limit_box') and item.get('limit_hide_outside',True):
                                points=np.memmap(meta['file'],'<f8','r',shape=(meta['count'],3))
                            for i in range(0,len(ids),pointio.CHUNK):
                                selected=ids[i:i+pointio.CHUNK]
                                if points is not None:selected=selected[jobdata.selection_mask(__import__('registration_pose').points(meta,points[selected]),{'mode':'box',**item['limit_box']})]
                                yield selected
                        finally:_close_arrays(points,ids)
                    batches=selected_batches()
                else: batches = jobdata.selection_ids(meta, selection, progress, visibility=item if action=='delete_selection' else None)
                if action == 'separate_selection':
                    if selected_file:
                        newmeta=jobdata.extract_ids(meta,__import__('deleted_points').id_batches({'ids_file':selected_file['file'],'count':selected_file['count']}),selected_file['count'],s.WORK,item['name']+' · separated',progress)
                    else:newmeta=jobdata.extract_selection(meta,selection,s.WORK,item['name']+' · separated',progress)
                    created.extend(Path(newmeta[k]) for k in ('file','attributes_file','preview_file') if newmeta.get(k))
                after, count = update_mask(meta, batches, s.WORK, 1, progress, created,derived,valid_points,derived_revision); signed_count = count
                group = dict(id=uuid.uuid4().hex, kind='move' if newmeta else 'delete', name=reason, count=count, before_mask=before, after_mask=after, active=True, created=time.time())
                groups.append(group)
            before_summary=_summary({**meta,'mask_file':before},meta['retained_summary']['count']+signed_count,derived,valid_points,derived_revision)
            if before_properties is not None and before_properties['count']!=before_summary['count']:
                # Copying the mask provides authoritative count evidence. A
                # conflicting legacy cache must not be re-signed with that new
                # count while retaining its unproven old bounds/channels.
                before_properties=None
            if before_properties is not None:
                before_properties.update(before_summary)
                for name in ('derived','valid_points','derived_signature'):
                    if name not in before_summary:before_properties.pop(name,None)
            entry = dict(time=time.time(), before_mask=before, after_mask=after, count=signed_count, reason=reason, group_id=group['id'], before_properties=before_properties,after_properties=copy.deepcopy(meta.get('retained_properties')),
                         before_summary=before_summary,after_summary=copy.deepcopy(meta['retained_summary']),before_groups=before_groups, after_groups=copy.deepcopy(groups))
            if newmeta:
                child = dict(id=uuid.uuid4().hex, kind='scan', name=newmeta['name'], meta=newmeta, visible=True, source_bytes=0, position_status=item.get('position_status','')+' · separated selection', hidden_boxes=[], isolate_box=None, edit_history=[], redo_history=[], deleted_groups=[])
                entry['separated_item'] = copy.deepcopy(child); s.STATE['job_items'].append(child)
            meta['mask_file'] = after; item.setdefault('edit_history', []).append(entry); item['redo_history'] = []
        s.STATE.setdefault('audit',[]).append(dict(action=action,time=time.time(),count=entry.get('count',0),reason=entry.get('reason',''),source_item_id=item['id']))
        refresh_aliases(s, item); progress('Saving reversible point edit')
        if commit:s.save()
        return dict(count=entry.get('count',0),created_paths=[str(path) for path in created])
    except BaseException:
        if previous is not None:s.STATE.clear(); s.STATE.update(previous);s.SELECTION=old_selection
        for path in created: path.unlink(missing_ok=True)
        raise


def delete_many(s, identifiers, selection, reason='Selected points', progress=None):
    """One exact, reversible point deletion across visible scans; one save.

    Each mask remains immutable. A later scan failure or cancelled save restores
    the complete pre-edit metadata and removes only new masks from this batch.
    The normal request history wrapper records one workspace Undo checkpoint.
    """
    progress=progress or s.progress
    if not isinstance(identifiers,list) or not identifiers or any(not isinstance(i,str) for i in identifiers) or len(set(identifiers))!=len(identifiers):
        raise ValueError('Select available scans for the point deletion.')
    available={x['id']:x for x in s.STATE.get('job_items',[])}
    selected=[available.get(ident) for ident in identifiers]
    if any(x is None or x.get('kind')!='scan' or not x.get('visible',True) or x.get('locked') for x in selected):
        raise ValueError('A selected scan is unavailable, hidden or locked. Refresh the selection.')
    import registered_cloud
    for item in selected:registered_cloud.require_current(item)
    spec=jobdata.selection_spec(selection)
    previous=copy.deepcopy(s.STATE);old_selection=getattr(s,'SELECTION',None)
    created=[];reports=[]
    try:
        for number,item in enumerate(selected,1):
            local=copy.deepcopy(spec)
            if not local.get('clip_box') and item.get('limit_box') and item.get('limit_hide_outside',True):
                local['clip_box']=copy.deepcopy(item['limit_box'])
            progress(f'Deleting selected points · scan {number:,}/{len(selected):,} · {item.get("name","Scan")}')
            try:
                result=apply(s,item,'delete_selection',local,reason=reason,progress=progress,commit=False,rollback_state=False)
            except ValueError as error:
                if str(error) not in ('No retained points in this selection.','The selected area contains no retained full-density points.'):raise
                reports.append(dict(id=item['id'],count=0));continue
            created.extend(Path(path) for path in result['created_paths'])
            reports.append(dict(id=item['id'],count=int(result['count'])))
        count=sum(row['count'] for row in reports)
        if count:
            progress('Saving reversible point edit · all selected scans')
            s.save()
        else:
            # An empty selection must not create a metadata-only Undo step.
            s.STATE.clear();s.STATE.update(previous);s.SELECTION=old_selection
        return dict(count=count,scan_count=len(selected),changed_scans=sum(row['count']>0 for row in reports),items=reports,undo_available=bool(count))
    except BaseException:
        s.STATE.clear();s.STATE.update(previous);s.SELECTION=old_selection
        for path in created:path.unlink(missing_ok=True)
        raise


def deleted_preview(item, ident, budget=60000):
    from bounded_rows import RowFile
    group=record(item,ident);meta=group.get('archive',{}).get('source_meta',item['meta'])
    step=max(1,int(np.ceil(group['count']/budget)));arrays=[];offset=0
    iterator=group_ids(meta,group)
    try:
        with RowFile(meta['file'],'<f8',meta['count'],3) as points:
            for ids in iterator:
                take=ids[(-offset)%step::step];offset+=len(ids)
                if len(take):arrays.append(points.take(take))
    finally:
        close=getattr(iterator,'close',None)
        if close:close()
    q=np.concatenate(arrays) if arrays else np.empty((0,3))
    q=__import__('registration_pose').points(meta,q)
    origin=(q.min(0)+q.max(0))/2 if len(q) else np.zeros(3)
    return dict(origin=origin.tolist(),points=np.column_stack((q-origin,np.tile([1.,.4,.2],(len(q),1)))).tolist(),count=group['count'],name=group['name'])


def deleted_preview_many(items, selections=None, budget=180000):
    choices=[]
    wanted=None if selections is None else {tuple(pair) for pair in selections}
    for item in items:
        if item.get('kind')!='scan':continue
        for group in records(item):
            if group['active'] and group['kind']=='delete' and (wanted is None or (item['id'],group['id']) in wanted):
                choices.append((item,group))
    if not choices:return dict(origin=[0.,0.,0.],points=[],count=0,name='No groups selected')
    parts=[];bounds=[];count=0;per=max(1,budget//len(choices))
    for item,group in choices:
        preview=deleted_preview(item,group['id'],per);part=np.asarray(preview['points'],float).reshape(-1,6)
        if len(part):part[:,:3]+=preview['origin'];parts.append(part)
        count+=preview['count']
        source=group.get('archive',{}).get('source_meta',item['meta'])
        lo,hi=__import__('registration_pose').world_bounds(source)
        bounds.append(dict(minimum=lo.tolist(),maximum=hi.tolist()))
    values=np.vstack(parts) if parts else np.empty((0,6));origin=(values[:,:3].min(0)+values[:,:3].max(0))/2 if len(values) else np.zeros(3)
    values[:,:3]-=origin
    return dict(origin=origin.tolist(),points=values.tolist(),count=count,name=f'{len(choices)} selected groups',bounds=bounds)


def archive_cleaning(item,result):
    provenance=item.get('cleaning_provenance') or {}
    if provenance.get('revision')==result.get('revision') and provenance.get('restoration_archive_removed'):return
    count=result['statistics']['input_points']-__import__('surface_api').kept_count(result)
    if not count:return
    groups=records(item)
    if any(g.get('archive',{}).get('result_revision')==result['revision'] for g in groups):return
    groups.append(dict(id=uuid.uuid4().hex,kind='delete',name='Cleaning · removed points',count=count,
        active=True,created=time.time(),archive=dict(source_meta=copy.deepcopy(result['source_meta']),
        point_ids_file=result['point_ids_file'],labels_file=result['labels_file'],
        input_count=result['statistics']['input_points'],kept=list(result['kept']),result_revision=result['revision'])))


def restore_archive(s,item,group,progress):
    # Cleaned copies have new point identities. Restore archived exclusions as
    # their own scan, preserving coordinates and all available attributes.
    import job_api
    previous=copy.deepcopy(s.STATE);meta=None
    try:
        source=group['archive']['source_meta']
        meta=jobdata.extract_ids(source,group_ids(source,group),group['count'],s.WORK,'Restored · '+item['name'],progress)
        group['active']=False
        job_api.append(s,'scan',meta,bundle_id=item.get('bundle_id'))
    except Exception:
        s.STATE.clear();s.STATE.update(previous)
        if meta:jobdata.remove_assets(meta)
        raise
