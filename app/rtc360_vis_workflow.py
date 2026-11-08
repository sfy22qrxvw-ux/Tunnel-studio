"""Review and atomically apply RTC360 initial poses with workspace Undo.

Only unlinked raw setups or unchanged poses owned by this feature are eligible.
Target/manual/cloud links protect even an identity-pose anchor from accidental
repositioning. No scanner files or point caches are rewritten.
"""
import copy
from pathlib import Path
import uuid

import job_api
import registered_cloud
import registration_workspace as rw
import rtc360_vis_pose as vis
import visual_registration


def _folder(item):
    meta = item['meta']
    if meta.get('source_path'):
        return meta['source_path']
    if meta.get('source_file'):
        return str(Path(meta['source_file']).parent)
    raise ValueError('The original scanner setup folder is unavailable.')


def preview(s, body):
    rw.persist(s)
    chosen = body.get('scan_ids', [])
    if not isinstance(chosen, list) or len(chosen) > 200 or any(not isinstance(v, str) for v in chosen):
        raise ValueError('Choose up to 200 RTC360 setups.')
    scope = body.get('scope', 'selected' if chosen else 'all')
    if scope not in ('all', 'selected'):raise ValueError('Choose all raw setups or selected setups.')
    if scope == 'selected' and not chosen:raise ValueError('Select RTC360 setups in the Object Explorer, or choose All imported raw setups.')
    items = [rw.scan(s, ident) for ident in dict.fromkeys(chosen)] if scope == 'selected' else [i for i in rw.scans(s) if i['meta'].get('source_format') == 'rtc360_raw_experimental']
    if not items:
        raise ValueError('Import original RTC360 setups first, then inspect their saved positions.')
    if len(items) > 200:
        raise ValueError('Select at most 200 setups in the Object Explorer.')
    ids = [i['id'] for i in items]
    before = visual_registration.fingerprint(s, ids)
    connected = {ident for edge in s.STATE.get('scan_links', []) for ident in (edge['a'], edge['b'])}
    rows = []
    for item in items:
        out = None
        if item['id'] in connected:
            out = vis._failure('Existing registration link protects this setup and its group.', protected=True)
        else:
            try:
                out = vis.apply_pose(copy.deepcopy(item['meta']), _folder(item))
            except (OSError, ValueError, TypeError) as exc:
                out = vis._failure(exc)
        status = 'ready' if out.get('applied') else 'positioned' if out.get('already_applied') else 'protected' if out.get('protected') else 'unavailable'
        rows.append(dict(id=item['id'], name=item['name'], eligible=out.get('applied', False), status=status, visible=item.get('visible', True), result=out))
    if before != visual_registration.fingerprint(s, ids):
        raise ValueError('The workspace changed during inspection. Inspect again.')
    value = dict(token=uuid.uuid4().hex, context=id(s.STATE), stamp=before, ids=ids, rows=rows)
    s._rtc360_vis = value
    return dict(token=value['token'], rows=rows, warnings=[vis.NOTICE],
                method=vis.METHOD, eligible_count=sum(r['eligible'] for r in rows), applied=False, scope=scope,
                counts={key:sum(r['status']==key for r in rows) for key in ('ready','positioned','protected','unavailable')})


def show(s, body):
    """Reveal reviewed setups and return current world bounds, without reapplying poses."""
    value = getattr(s, '_rtc360_vis', None)
    if not value or value['token'] != body.get('token') or value['context'] != id(s.STATE):
        raise ValueError('Inspect the setups again before showing them.')
    if value['stamp'] != visual_registration.fingerprint(s, value['ids']):
        raise ValueError('The setups changed. Inspect them again.')
    import registration_pose
    old=copy.deepcopy(s.STATE)
    try:
        bounds=[]
        for ident in value['ids']:
            item=rw.scan(s,ident)
            registered_cloud.set_visibility(s.STATE.get('job_items',[]),item,True)
            bounds.extend(v.tolist() for v in registration_pose.world_bounds(item['meta']))
        s.save()
    except BaseException:
        s.STATE.clear();s.STATE.update(old);raise
    # Visibility can be part of the geometry fingerprint.
    value['stamp']=visual_registration.fingerprint(s,value['ids'])
    return dict(shown=value['ids'],bounds=bounds)


def start_apply(s, body):
    value = getattr(s, '_rtc360_vis', None)
    if not value or value['token'] != body.get('token'):
        raise ValueError('Inspect the RTC360 saved positions again before applying.')
    eligible = [r for r in value['rows'] if r['eligible']]
    if not eligible:
        raise ValueError('No eligible initial poses were found.')
    request_id = uuid.uuid4().hex

    def run():
        progress = lambda message: job_api.progress(s, message)
        if value['context'] != id(s.STATE) or value['stamp'] != visual_registration.fingerprint(s, value['ids']):
            raise ValueError('The scans or links changed after inspection. Inspect again.')
        prepared = []
        for row in eligible:
            item = rw.scan(s, row['id'])
            meta = copy.deepcopy(item['meta'])
            progress('Checking saved position for ' + item['name'] + '…')
            out = vis.apply_pose(meta, _folder(item), progress,
                                 lambda: job_api.CANCEL.is_set() or s.JOB_CANCEL_FILE.exists())
            if not out.get('applied') or any(out.get(k) != row['result'].get(k) for k in ('source_sha256', 'setup_sha256', 'job_sha256')):
                raise ValueError('RTC360 metadata changed or became unavailable: ' + out.get('reason', item['name']) + '. Inspect again.')
            prepared.append((item, meta, out))
        progress('Saving initial poses without rewriting point caches…')
        old = copy.deepcopy(s.STATE)
        selection = copy.deepcopy(getattr(s, 'SELECTION', None))
        try:
            for item, meta, out in prepared:
                progress('Applying initial position for ' + item['name'] + '…')
                item.setdefault('original_meta', copy.deepcopy(item['meta']))
                item.update(meta=meta, scan_origin=out['translation'], scan_orientation=out['scanner_rotation'], visible=True,
                            position_status=meta['position_status'], hidden_boxes=[], isolate_box=None, limit_box=None)
                state = rw.state_for(item)
                state.update(cloud=copy.deepcopy(meta), solution=None, transformed=None, revision=uuid.uuid4().hex)
            moved = [item['id'] for item, _, _ in prepared]
            registered_cloud.reconcile_state(s.STATE, moved)
            registered_cloud._clear_aliases(s.STATE, moved)
            active = s.STATE.get('active_job_registration')
            if active in moved:
                s.STATE['registration'] = copy.deepcopy(rw.state_for(rw.scan(s, active)))
            s.STATE['last_vis_pose_result'] = dict(id=request_id, applied=True, scan_ids=moved, count=len(moved), method=vis.METHOD, warnings=[vis.NOTICE])
            s.STATE['export_ready'] = None
            s.SELECTION = None
            progress('Committing reviewed initial positions…')
            s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(old);s.SELECTION = selection
            raise
        s._rtc360_vis = None
        s._visual_registration = None

    s.start_job(run, kind='job', native_progress=True)
    return dict(started=True, request_id=request_id)
