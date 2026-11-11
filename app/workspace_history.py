# Copyright (c) 2026 Damian Gulczynski.
"""Session undo of workspace metadata; immutable point caches stay on disk.

One HTTP action or background job is one checkpoint. Snapshot restoration never
replays an import or deletes an external file. Draft node moves have local UI
history until saved. Named project switches start a new history.
"""
import copy,json,threading
from pathlib import Path
from contextlib import contextmanager

LOCAL=threading.local()
CAD_HISTORY_ROWS=2000000
PRESERVE={'project_preview','project_id','project_title','saved_at','ui','last_backup'}
VOLATILE={'report_ready','clean_ready','export_ready'}
FILES={'results_file','vertices_file','faces_file','face_flags_file','file','mask_file','attributes_file','mesh_file','point_ids_file','labels_file','preview_file','derived','before_mask','after_mask','ids_file','truth_file'}
LABELS={'add_measurement':'Measure two-point distance','delete_patches':'Delete detected points','separate_patches':'Separate detected points','limit_box':'Limit box','limit_visibility':'Hide outside','delete_selection':'Delete points','delete_box':'Delete points','separate_selection':'Separate points','separate_box':'Separate points','copy_selection':'Copy points','restore_deleted':'Restore deleted points','restore_deleted_selection':'Restore selected deleted points','remove_many':'Remove scans / models','remove':'Remove item','duplicate':'Duplicate item','rename':'Rename item','visibility':'Item visibility','hide_selection':'Hide points','isolate_selection':'Isolate points','clear_view':'Show all points','review':'Review decision','review_selection':'Cleaning point decision','reset_review':'Restore automatic cleaning','add_clean_scan':'Create cleaned tunnel','run':'Clean tunnel','estimate':'Scan-derived alignment','save_estimate':'Save alignment nodes','use_estimate':'Use estimated alignment','section':'Create cross-section','import_paths':'Import scans / models','undo_edit':'Undo point edit','redo_edit':'Redo point edit','apply':'Apply result','analyse':'Analyse scan'}

def _history(s):
    if not hasattr(s,'WORKSPACE_HISTORY'):s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
    return s.WORKSPACE_HISTORY

def snapshot(s):return copy.deepcopy({k:v for k,v in s.STATE.items() if k not in PRESERVE|VOLATILE})

def _json(value):
    if hasattr(value,'a') and hasattr(value,'name'):return {'rows':value.a.tolist(),'name':value.name}
    if hasattr(value,'tolist'):return value.tolist()
    raise TypeError('Unsupported workspace history value: '+type(value).__name__)

def equal(a,b):
    # Point edits append to audit, and object creation changes a top-level list.
    # Different list lengths prove a change without encoding two potentially
    # very large cleaning results. This is only a fast rejection: same-shape
    # metadata, Alignment and NumPy values retain the original exact comparison.
    if isinstance(a,dict) and isinstance(b,dict):
        for key,value in a.items():
            other=b.get(key)
            if isinstance(value,(list,tuple)) and isinstance(other,(list,tuple)) and len(value)!=len(other):return False
    return json.dumps(a,default=_json,sort_keys=True)==json.dumps(b,default=_json,sort_keys=True)

def record(s,before,label,group=None):
    after=snapshot(s)
    if equal(before,after):return
    h=_history(s)
    if group and h['undo'] and h['undo'][-1].get('group')==group:h['undo'][-1]['after']=after
    else:h['undo'].append(dict(before=before,after=after,label=label,group=group))
    h['undo']=h['undo'][-30:];h['redo']=[]
    # Geometry lives in metadata; bound retained CAD snapshots as well as count.
    def rows(entry):
        return sum(len(o.get('vertices',[]))+len(o.get('faces',[])) for key in ('before','after') for o in (entry[key].get('cad') or {}).get('objects',[]))
    total=sum(rows(entry) for entry in h['undo'])
    while total>CAD_HISTORY_ROWS and len(h['undo'])>1:total-=rows(h['undo'].pop(0))

def status(s):
    h=_history(s)
    return dict(undo_count=len(h['undo']),redo_count=len(h['redo']),undo_label=h['undo'][-1]['label'] if h['undo'] else '',redo_label=h['redo'][-1]['label'] if h['redo'] else '')

def storage_state(s):
    """Protect session Undo/Redo caches without persisting transient history.

    The shallow view only feeds reference-aware storage review/cleanup.  Point
    data and history snapshots are not copied or added to the saved project.
    """
    current=dict(s.STATE);history=getattr(s,'WORKSPACE_HISTORY',{})
    if any(history.get(side) for side in ('undo','redo')):
        current['_session_undo_assets']=history
    if getattr(s,'SHAPE_SELECTION',None):
        current['_shape_review_assets']=s.SHAPE_SELECTION
    return current

def configure(path,body):
    ctx=getattr(LOCAL,'context',None)
    if not ctx:return
    action=body.get('action','');ctx['reset']=path=='/api/job-action' and action in ('compact_points','compact_points_batch') or path=='/api/project-action' and action in ('new','open') or path=='/api/load' and body.get('kind')=='project'
    group=body.get('history_group');ctx['group']=str(group)[:100] if group else None
    if path=='/api/cad-action':
        import cad_api
        ctx['server']=cad_api.workspace(ctx['server'])
    if path=='/api/tunnel-design-action' and action in ('profile_begin','profile_finish','profile_cancel'):
        ctx['label']=None
    elif path in ('/api/project-browser','/api/project-folder-copy','/api/open-data-folder','/api/storage-cleanup','/api/workspace-folders','/api/measure-plane','/api/surface-preview','/api/explorer-delete-review','/api/geometry-preview','/api/geometry-discard','/api/measure-preview','/api/measure-export','/api/tunnel-design-preview','/api/cad-preview','/api/cad-export','/api/cad-pick','/api/history','/api/cancel-operation','/api/export','/api/backup-project','/api/project-action','/api/select') or action=='cancel' or path=='/api/job-action' and action in ('surface','register') or path=='/api/scan-registration' and action=='release_merge':
        ctx['label']=None
    elif path=='/api/scan-registration' and action in ('activate_setup','visual_preview','cloud_refine','cloud_status','vis_inspect','vis_preview','bundle_inspect','bundle_preview','bundle_status','bundle_control_status','bundle_control_solve','preview_link_targets'):ctx['label']=None
    elif path in ('/api/setup-camera','/api/setup-photos') and action=='open':ctx['label']=None
    elif path=='/api/tunnel-monitoring':ctx['label']=None if action=='export' else 'Update monitoring epochs and references'
    elif path=='/api/scan-registration' and action=='build_selection_merge':ctx['label']='Merge selected registered scans'
    elif path=='/api/scan-registration' and action=='bundle_control_apply':ctx['label']='Register bundle to survey control'
    elif path=='/api/scan-registration' and action=='bundle_apply':ctx['label']='Global bundle adjustment and loop closure'
    elif path=='/api/scan-registration' and action=='visual_apply':ctx['label']='Cloud-to-cloud registration' if body.get('refinement_id') else 'Manual visual registration'
    elif path=='/api/scan-registration' and action=='vis_apply':ctx['label']='RTC360 initial positions'
    elif path=='/api/scan-registration' and action=='vis_show':ctx['label']='Show reviewed RTC360 setups'
    elif path=='/api/explorer-delete':ctx['label']='Delete Project Explorer items'
    elif path=='/api/job-action' and action=='delete_selection_many':ctx['label']='Delete selected points'
    elif path=='/api/shape-library':ctx['label']={'capture':'Save a confirmed shape example','capture_scan':'Teach confirmed separated pocket scans','teach_selected':'Teach confirmed shape matches','reject_selected':'Teach incorrect shape matches','forget_example':'Remove a teaching example'}.get(action)
    elif path=='/api/scan-appearance':ctx['label']='Change scan colour'
    elif path in ('/api/feature-selection','/api/shape-selection') and action not in ('delete','separate'):ctx['label']=None
    elif path=='/api/geometry-apply':ctx['label']='Create fitted geometry'
    elif path in ('/api/feature-selection','/api/shape-selection') and action in ('delete','separate'):ctx['label']='Separate reviewed feature points' if action=='separate' else 'Remove reviewed feature points'
    elif path=='/api/measure-action':ctx['label']={'save':'Save measurement','update':'Edit measurement','remove':'Remove measurement','visibility':'Measurement visibility'}.get(action,'Measurement')
    else:ctx['label']=LABELS.get(action, (action or path.removeprefix('/api/')).replace('_',' ').replace('-',' ').capitalize())[:80]
    # JSON routes configure after authentication/body validation and before any
    # mutation. Read-only previews/picks, cancellation and history restoration
    # need no speculative full-workspace copy. Project resets still capture the
    # previous state so a failed open does not discard the current undo stack.
    if (ctx['label'] or ctx['reset']) and ctx['before'] is None and not ctx['server'].JOB.get('busy'):
        ctx['before']=snapshot(ctx['server'])

@contextmanager
def request(s,path):
    old=getattr(LOCAL,'context',None)
    ctx=dict(server=s,before=None,label='Import file' if path=='/api/upload' else None,queued=False,reset=False)
    # Multipart upload has no JSON configure call and can create its destination
    # bundle before queueing the import; preserve its original capture boundary.
    if ctx['label'] and not s.JOB.get('busy'):ctx['before']=snapshot(s)
    LOCAL.context=ctx
    try:yield
    finally:
        LOCAL.context=old
        s=ctx['server']
        if not ctx['queued'] and ctx['before'] is not None:
            if ctx['reset']:
                if not equal(ctx['before'],snapshot(s)):_history(s).update(undo=[],redo=[])
            elif ctx['label']:record(s,ctx['before'],ctx['label'],ctx.get('group'))

def background(s,action):
    ctx=getattr(LOCAL,'context',None)
    if not ctx:return action
    s=ctx['server']
    ctx['queued']=True
    def run():
        try:return action()
        finally:
            if ctx['reset']:
                if ctx['before'] is not None and not equal(ctx['before'],snapshot(s)):_history(s).update(undo=[],redo=[])
            elif ctx['label'] and ctx['before'] is not None:record(s,ctx['before'],ctx['label'],ctx.get('group'))
    return run

def _check_files(value,key=''):
    if isinstance(value,dict):
        for k,v in value.items():_check_files(v,k)
    elif isinstance(value,(list,tuple)):
        for v in value:_check_files(v,key)
    elif key in FILES and isinstance(value,str) and value and not Path(value).is_file():raise ValueError('An undo cache is missing. The current workspace is unchanged.')

def apply(s,redo=False):
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation before Undo or Redo.')
    h=_history(s);src=h['redo' if redo else 'undo'];dst=h['undo' if redo else 'redo']
    if not src:raise ValueError('Nothing to '+('redo.' if redo else 'undo.'))
    entry=src[-1];target=copy.deepcopy(entry['after' if redo else 'before']);_check_files(target)
    old=copy.deepcopy(s.STATE);selection=getattr(s,'SELECTION',None)
    target.update({k:v for k,v in old.items() if k in PRESERVE});target.update(report_ready=False,clean_ready=False,export_ready=None)
    import shape_library
    pending=shape_library.history_begin(s,target)
    try:
        s.STATE.clear();s.STATE.update(target);s.SELECTION=None;s.save()
    except Exception:
        s.STATE.clear();s.STATE.update(old);s.SELECTION=selection
        shape_library.history_finish(s,pending,False);raise
    shape_library.history_finish(s,pending,True)
    src.pop();dst.append(entry)
    return dict(saved=True,history=status(s),label=entry['label'])
