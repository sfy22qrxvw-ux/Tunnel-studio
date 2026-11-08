"""Stream retained registered setups into an independent, editable cloud."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import uuid
import numpy as np
import jobdata, registration_pose


def source_state(item):
    """Structured provenance survives the path remapping of portable projects."""
    m=item['meta'];R,t=registration_pose.get(m)
    return dict(file=m['file'],mask_file=m.get('mask_file'),count=int(m['count']),
                rotation=R.tolist(),translation=t.tolist())


def _source_changes(merged,sources):
    """Separate changed point masks from a changed registration/coordinate frame.

    A materialised cloud owns its XYZ/attributes. An original setup's deletion
    mask or removal does not change those bytes, but the saved result must no
    longer stand in for that setup in an automatic current-source calculation.
    """
    ids=set(merged.get('merge_source_ids',[]));available=ids<=sources.keys()
    saved=merged.get('merge_source_revisions') or {}
    if not isinstance(saved,dict):saved={}
    geometry_changed=False;point_edits=[]
    geometry_keys=('file','count','rotation','translation')
    for ident in ids & sources.keys():
        previous=saved.get(ident);current=source_state(sources[ident])
        if previous==current:continue
        if isinstance(previous,dict) and all(key in previous for key in geometry_keys):
            if any(previous[key]!=current[key] for key in geometry_keys):geometry_changed=True
            elif previous.get('mask_file')!=current.get('mask_file'):point_edits.append(ident)
            elif available:geometry_changed=True
        elif saved and available:
            # Unknown old provenance cannot establish that only a mask changed.
            geometry_changed=True
    removed=sorted(ids-sources.keys())
    changes=dict(point_edits=sorted(point_edits),removed=removed) if point_edits or removed else None
    return geometry_changed,changes


def matches_sources(merged,sources):
    """Read-only eligibility for automatic whole-project/bundle source choice."""
    geometry_changed,changes=_source_changes(merged,sources)
    return not geometry_changed and not changes


def require_current(item):
    if item.get('merge_stale'):
        raise ValueError('This saved merged cloud predates the current registration. '
                         'Choose the registered setups or build a new merged cloud.')
    return item


def _clear_aliases(state,ids):
    if state.get('active_job_scan') in ids:
        state.update(active_job_scan=None,cloud=None,derived=None,valid_points=0,
                     joints=None,report_ready=False,clean_ready=False,edits=[],redo=[])
    if state.get('surface_source_item') in ids:
        state.update(surface_source_item=None,surface_source=None,surface_result=None)
    state['export_ready']=None


def reconcile_state(state,changed_ids=()):
    """Retire superseded physical copies without deleting coordinates or edits.

    This also repairs saved 0.17.x projects where Apply had made the original
    setups visible alongside their old merged cloud. Sources become the current
    representation; the retained old result is explicitly an archived cache.
    """
    items=state.get('job_items',[])
    sources={x['id']:x for x in items if x.get('kind')=='scan' and not x.get('registered_merge')}
    changed=set(changed_ids);retired=set();newly_retired=set()
    for merged in items:
        if not merged.get('registered_merge'):continue
        ids=set(merged.get('merge_source_ids',[]));available=ids<=sources.keys()
        mismatch,source_changes=_source_changes(merged,sources)
        conflict=bool(available and not source_changes and merged.get('visible') and any(sources[i].get('visible') for i in ids))
        if ids&changed or mismatch or conflict or merged.get('merge_stale'):
            first=not merged.get('merge_stale')
            if first:newly_retired.add(merged['id'])
            merged.pop('merge_source_changes',None)
            merged.update(visible=False,merge_archived=True,
                          merge_stale='Source setups changed or superseded this saved merged representation.',
                          position_status='Previous registered result — use current registered setups')
            retired.add(merged['id'])
            for ident in ids:
                child=sources.get(ident)
                if child is not None:
                    attached=child.get('merge_parent')==merged['id']
                    if attached:child.pop('merge_parent',None)
                    if first or attached or ids&changed:child['visible']=True
        else:
            if source_changes:
                merged['merge_source_changes']=source_changes
                reason=('source points edited and setups removed' if source_changes['point_edits'] and source_changes['removed']
                        else 'source points edited' if source_changes['point_edits'] else 'source setups removed')
                merged['position_status']='Saved registered cloud — '+reason
            elif merged.pop('merge_source_changes',None) is not None:
                merged['position_status']='Registration complete'
            if merged.get('merge_archived'):
                merged['visible']=False
            elif merged.get('visible'):
                # If source data are missing or edited, the saved merged cache
                # remains independent. Suppress overlapping visible originals.
                for ident in ids:
                    if ident in sources:sources[ident]['visible']=False
    for x in items:
        if x.get('merge_parent') in retired:x.pop('merge_parent',None)
    for link in state.get('scan_links',[]):
        if link.get('completed_merge_id') in retired:link.pop('completed_merge_id',None)
    if (state.get('last_registered_merge') or {}).get('id') in retired:state.pop('last_registered_merge',None)
    if newly_retired or state.get('active_job_scan') in retired or state.get('surface_source_item') in retired:
        _clear_aliases(state,retired)
    return retired


def set_visibility(items,item,visible):
    """A scan and a materialised copy containing it cannot both be active."""
    if visible:require_current(item)
    item['visible']=bool(visible)
    if not visible:return
    sources=set(item.get('merge_source_ids',[])) if item.get('registered_merge') else {item['id']}
    for other in items:
        if other is item:continue
        overlap=(sources & set(other.get('merge_source_ids',[]))) if other.get('registered_merge') else (other['id'] in sources)
        if overlap or other['id']==item.get('merge_parent') or other.get('merge_parent')==item['id']:
            other['visible']=False


def show_current(items,selected=None):
    """Show one representation per setup, atomically for Show all / Isolate."""
    chosen=set(selected) if selected is not None else None
    current=[x for x in items if not x.get('merge_archived') and not x.get('merge_stale')
             and (chosen is None or x['id'] in chosen)]
    covered=set();visible=set()
    # A materialised cloud keeps its own point edits. Prefer it when explicitly
    # selected or showing the whole project; children are a view alternative.
    for x in reversed(current):
        if not x.get('registered_merge'):continue
        sources=set(x.get('merge_source_ids',[]))
        if not sources&covered:visible.add(x['id']);covered.update(sources)
    for x in current:
        if not x.get('registered_merge') and (x.get('kind')!='scan' or x['id'] not in covered):visible.add(x['id'])
    for x in items:x['visible']=x['id'] in visible


def build(s, sources, registration_id, progress=lambda message: None):
    """No sampling, view clipping or overlap removal; deletion masks are respected."""
    total = sum(x['meta']['count'] for x in sources)
    jobdata.room(s.WORK, total * 40 + 8 * 1024 * 1024)
    ident = uuid.uuid4().hex
    xyz = asset_folder(s.WORK,'registered') / (ident + '.xyz64')
    attrs = asset_folder(s.WORK,'registered') / (ident + '.attr32')
    count = 0
    lo = np.full(3, np.inf); hi = -lo
    alo = np.full(4, np.inf); ahi = -alo
    counts = np.zeros(4, dtype=np.int64)
    ranges = []
    meta = dict(name='Registered cloud', file=str(xyz), attributes_file=str(attrs))
    if any(x['meta'].get('experimental_reconstruction') for x in sources):
        from rtc360_optics import NOTICE
        meta.update(experimental_reconstruction=True,warnings=[NOTICE])
    try:
        with xyz.open('wb') as pf, attrs.open('wb') as af:
            for ordinal, x in enumerate(sources, 1):
                m = x['meta']; n = m['count']; start_count = count
                maps = []
                def mapped(path, dtype, shape):
                    value = np.memmap(path, dtype, 'r', shape=shape); maps.append(value); return value
                try:
                    if not n: continue
                    points = mapped(m['file'], '<f8', (n, 3))
                    values = mapped(m['attributes_file'], '<f4', (n, 4)) if m.get('attributes_file') else None
                    excluded = mapped(m['mask_file'], 'u1', (n,)) if m.get('mask_file') else None
                    for start in range(0, n, 200000):
                        stop = min(n, start + 200000)
                        progress(f'Merging setup {ordinal}/{len(sources)} · {x["name"]}: {stop:,} / {n:,} points')
                        use = excluded[start:stop] == 0 if excluded is not None else slice(None)
                        q = points[start:stop][use]
                        if not len(q): continue
                        q = registration_pose.points(m,q)
                        if not np.isfinite(q).all(): raise ValueError('A setup contains non-finite coordinates. The merge was not saved.')
                        a = values[start:stop][use] if values is not None else np.full((len(q), 4), np.nan, dtype='<f4')
                        q.astype('<f8', copy=False).tofile(pf); a.astype('<f4', copy=False).tofile(af)
                        count += len(q); lo = np.minimum(lo, q.min(0)); hi = np.maximum(hi, q.max(0))
                        finite = np.isfinite(a); counts += finite.sum(0)
                        alo = np.minimum(alo, np.where(finite, a, np.inf).min(0))
                        ahi = np.maximum(ahi, np.where(finite, a, -np.inf).max(0))
                finally:
                    for value in maps: value._mmap.close()
                ranges.append(dict(scan_id=x['id'], start=start_count, count=count-start_count,
                                   source_file=m['file'], source_mask=m.get('mask_file')))
        if not count: raise ValueError('The registered setups contain no retained points to merge.')
        meta.update(count=count, minimum=lo.tolist(), maximum=hi.tolist(), source_format='registered_merge',
                    attribute_counts=counts.tolist(), attribute_min=[float(v) if np.isfinite(v) else None for v in alo],
                    attribute_max=[float(v) if np.isfinite(v) else None for v in ahi], invalid_points_skipped=0)
        jobdata.preview_cache(meta, xyz.parent, progress)
        progress(f'Merge ready · {len(sources)} setups · {count:,} full-density points')
        return dict(id=ident, kind='scan', name=meta['name'], meta=meta, visible=True, source_bytes=0,
                    position_status='Registration complete', registered_merge=True, merge_registration_id=registration_id,
                    merge_source_ids=[x['id'] for x in sources], merge_ranges=ranges,
                    merge_source_revisions={x['id']:source_state(x) for x in sources},
                    hidden_boxes=[], isolate_box=None, edit_history=[], redo_history=[])
    except BaseException:
        jobdata.remove_assets(meta)
        raise


def install(s, merged):
    """Hide originals below the new cloud; retain an edited previous result."""
    items = s.STATE['job_items']; ids = set(merged['merge_source_ids'])
    prior = [x for x in items if x.get('registered_merge') and not x.get('merge_archived')
             and ids.intersection(x.get('merge_source_ids', []))]
    if len(prior) == 1:
        merged['name'] = prior[0]['name']; merged['meta']['name'] = merged['name']
    for old in prior:
        full = set(old.get('merge_source_ids', [])) <= ids
        edited = bool(old['meta'].get('mask_file') or old.get('edit_history') or old.get('redo_history'))
        if edited or not full:
            old.update(visible=False, merge_parent=merged['id'], merge_archived=True)
            old['name'] = (old['name'] + ' · previous result')[:150]
        else:
            items.remove(old)
        for child in items:
            if child.get('merge_parent') == old['id']:
                if child.get('merge_archived'): child['merge_parent'] = merged['id']
                elif child['id'] not in ids:
                    child.pop('merge_parent', None); child['visible'] = True
    for x in items:
        if x['id'] in ids: x.update(merge_parent=merged['id'], visible=False)
    items.append(merged)
    retired = {x['id'] for x in prior}
    if s.STATE.get('active_job_scan') in retired:
        s.STATE.update(active_job_scan=None, cloud=None, derived=None, valid_points=0)
    if s.STATE.get('surface_source_item') in retired:
        s.STATE.update(surface_source_item=None, surface_source=None, surface_result=None)
    for link in s.STATE.get('scan_links', []):
        if link['a'] in ids and link['b'] in ids: link['completed_merge_id'] = merged['id']
    s.STATE['last_registered_merge'] = dict(id=merged['id'], registration_id=merged['merge_registration_id'],
                                           count=merged['meta']['count'], setups=len(ids))
