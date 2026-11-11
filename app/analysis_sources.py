"""Read-only whole-project/bundle inputs for full-density analysis.

An analysis collection streams the retained rows of each current setup in its
registered frame. Scoped reads skip blocks outside the processing area. Global
indices remain reversible through source_ranges.
"""
from pathlib import Path
from contextlib import ExitStack
import copy
import numpy as np
import registration_pose
import jobdata

ALL_ID = 'analysis:all'
BUNDLE_PREFIX = 'analysis:bundle:'
BLOCK = 100_000


def parts(meta):
    rows = meta.get('cloud_parts')
    if rows is None:
        return [dict(id=meta.get('source_id', ''), name=meta.get('name', 'Scan'), meta=meta)]
    if not isinstance(rows, list) or not rows:
        raise ValueError('This analysis scope has no source scans.')
    if any('cloud_parts' in row.get('meta', {}) for row in rows):
        raise ValueError('Nested analysis scopes are not supported.')
    return rows


def source_ranges(meta):
    offset = 0
    result = []
    for row in parts(meta):
        n = int(row['meta']['count'])
        result.append(dict(id=row.get('id', ''), name=row.get('name', 'Scan'),
                           start=offset, count=n))
        offset += n
    return result


def require_current(item):
    import registered_cloud
    registered_cloud.require_current(item)
    return item


def _members(s, ids=None):
    import registered_cloud
    items = s.STATE.get('job_items', [])
    originals = [x for x in items if x.get('kind') == 'scan'
                 and not x.get('registered_merge') and not x.get('merge_archived')
                 and (ids is None or x['id'] in ids)]
    chosen = list(originals)
    available = {x['id'] for x in originals}
    current_sources={x['id']:x for x in originals}
    # A current edited merge is an explicit working result. Use it in place of
    # its original setups; never quietly discard its own deletion mask.
    for merged in reversed(items):
        if not merged.get('registered_merge') or merged.get('merge_archived') or merged.get('merge_stale'):
            continue
        members = set(merged.get('merge_source_ids', []))
        if members and members <= available and registered_cloud.matches_sources(merged,current_sources):
            require_current(merged)
            chosen = [x for x in chosen if x['id'] not in members]
            chosen.append(merged)
            available -= members
    return sorted(chosen, key=lambda x: x['id'])


def collection(ident, name, members):
    if not members:
        raise ValueError('This analysis scope no longer contains any scans.')
    rows = [dict(id=x['id'], name=x['name'], meta=copy.deepcopy(x['meta'])) for x in members]
    bounds=[registration_pose.world_bounds(x['meta']) for x in rows]
    meta = dict(name=name, count=sum(int(x['meta']['count']) for x in rows), cloud_parts=rows,
                minimum=np.min([v[0] for v in bounds],axis=0).tolist(),
                maximum=np.max([v[1] for v in bounds],axis=0).tolist())
    return dict(id=ident, name=name, kind='scan', meta=meta, analysis_collection=True)


def collections(s):
    import workspace_tools
    groups = workspace_tools.bundles(s)
    result = []
    all_members = _members(s)
    if len(all_members) > 1:
        result.append(collection(ALL_ID, 'Whole project · all source scans', all_members))
    for group in groups:
        members = _members(s, set(group['scan_ids']))
        if members:
            result.append(collection(BUNDLE_PREFIX + group['id'],
                                     group['name'] + ' · whole registered bundle', members))
    return result


def resolve(s, ident):
    if ident == ALL_ID or (isinstance(ident, str) and ident.startswith(BUNDLE_PREFIX)):
        result = next((x for x in collections(s) if x['id'] == ident), None)
        if result is None:
            raise ValueError('The analysis scope changed. Choose the whole tunnel or bundle again.')
        return result
    item = next((x for x in s.STATE.get('job_items', []) if x.get('id') == ident), None)
    if item is None:
        raise ValueError('Choose an existing scan or registered bundle.')
    require_current(item)
    return dict(id=item['id'], name=item['name'], kind=item['kind'], meta=item['meta'])


def overlap(a, b):
    aa = {str(Path(row['meta']['file']).resolve()) for row in parts(a)}
    return any(str(Path(row['meta']['file']).resolve()) in aa for row in parts(b))


def scan_ids(source):
    return [row.get('id') or source['id'] for row in parts(source['meta'])]

def saved_boxes(s,source):
    scans={x['id']:x.get('limit_box') for x in s.STATE.get('job_items',[]) if x.get('kind')=='scan'}
    return [scans.get(ident) for ident in scan_ids(source)]

def processing_scope(s,source,mode='auto',selection=None):
    """Resolve a world-space area against the actual scans in this source."""
    if mode not in ('auto','selection','limit','whole'):
        raise ValueError('Choose highlighted points, saved limit boxes or the whole source.')
    if source['kind']!='scan' or mode=='whole':return None
    boxes=saved_boxes(s,source)
    limits=[jobdata.selection_spec({'mode':'box',**box,'invert':False}) if box else None for box in boxes]
    if selection is not None and mode in ('auto','selection'):
        return dict(mode='selection',selection=jobdata.selection_spec(selection),limits=limits)
    if mode=='selection':raise ValueError('Highlight points in the main 3D view first.')
    if any(limits):return dict(mode='limit',selection=None,limits=limits)
    if mode=='limit':raise ValueError('The saved limit box changed. Set the box again or choose another processing area.')
    return None

def scope_label(scope):
    if scope:return 'Highlighted points' if scope['mode']=='selection' else 'Saved limit box'
    return 'Whole retained source'

def check_scope(s,source,scope):
    if scope and processing_scope(s,source,scope['mode'],scope['selection'])!=scope:
        raise ValueError('The saved processing area changed.')

def blocks(meta, counters=None, progress=lambda message: None, cancel=lambda: False, block=BLOCK, *, scope=None):
    """Yield global original indices and retained finite world XYZ in owned blocks."""
    block = max(1, min(BLOCK, int(block)))
    offset = 0
    total = int(meta['count'])
    for index,row in enumerate(parts(meta)):
        m = row['meta']; n = int(m['count'])
        if scope:
            limit=scope['limits'][index]
            spec=scope['selection'] or limit
            if spec is not None:
                visible=dict(limit_box=limit) if scope['selection'] and limit else None
                label=scope_label(scope);included=0
                def report(message):
                    if cancel():raise jobdata.Cancelled('Analysis cancelled. The previous result is unchanged.')
                    progress(f'{label} · {message}')
                for ids,q in jobdata.selection_rows(m,spec,report,visibility=visible):
                    ids=ids.astype(np.uint64)+offset
                    for start in range(0,len(ids),block):
                        stop=min(start+block,len(ids));p=q[start:stop];batch=ids[start:stop]
                        if counters is not None:counters['retained_points']+=len(batch)
                        included+=len(batch)
                        progress(f'{label} · {row["name"]} · {included:,} retained points in area')
                        yield batch,p
            offset+=n
            continue
        source = Path(m['file']); mask_path = Path(m['mask_file']) if m.get('mask_file') else None
        if n < 0 or not source.is_file() or source.stat().st_size != n * 24:
            raise ValueError('An analysis source coordinate cache is missing or incomplete.')
        if mask_path and (not mask_path.is_file() or mask_path.stat().st_size != n):
            raise ValueError('An analysis source deletion mask is missing or incomplete.')
        R, t = registration_pose.get(m)
        identity = registration_pose.is_identity(m)
        with ExitStack() as files:
            stream = files.enter_context(source.open('rb'))
            excluded = files.enter_context(mask_path.open('rb')) if mask_path else None
            for start in range(0, n, block):
                if cancel(): raise jobdata.Cancelled('Analysis cancelled. The previous result is unchanged.')
                count = min(block, n - start)
                q = np.fromfile(stream, '<f8', count=count * 3)
                if q.size != count * 3: raise ValueError('An analysis source changed during processing.')
                q = q.reshape(-1, 3)
                flags = np.fromfile(excluded, 'u1', count=count) if excluded else None
                if flags is not None and len(flags) != count:
                    raise ValueError('An analysis deletion mask changed during processing.')
                keep = flags == 0 if flags is not None else np.ones(count, dtype=bool)
                finite = np.isfinite(q).all(axis=1)
                if counters is not None:
                    counters['retained_points'] += int(keep.sum())
                    counters['deleted_points'] += int((~keep).sum())
                    counters['invalid_points'] += int((keep & ~finite).sum())
                use = keep & finite
                ids = np.arange(offset + start, offset + start + count, dtype=np.uint64)[use]
                q = q[use]
                if not identity: q = q @ R.T + t
                if not np.isfinite(q).all(): raise ValueError('Registration produced non-finite analysis coordinates.')
                progress(f'Analysing {row.get("name", "scan")} · {offset + start + count:,} / {total:,} original points')
                if len(ids): yield ids, q
        offset += n
    if offset != total:
        raise ValueError('The analysis scope point count changed. Run the operation again.')
