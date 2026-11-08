"""Reviewed whole-item deletion with scoped ownership and recoverable commits.

Point exclusion is a separate reversible operation. External inputs are never
removed. Small metadata is journalled before same-volume cache moves; startup
rolls back an interrupted uncommitted transaction before opening any workspace.
"""
import copy
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import projects
import workspace_history

TRANSACTIONS='.explorer_delete_transactions'
CACHE_ENDINGS=('.xyz64','.attr32','.mask','.preview64','.analysis64','.vertices64',
    '.faces32','.vcolour32','.faceflags','.face_flags','.truth','.viewindex.npz',
    '.surface_ids','.surface_labels','.surface.ids64','.surface.labels32',
    '.patch_ids','.selection','.design-results','.inspection','.inspection64','.results64','.inspection-results','.inspection.csv',
    '.feature_xyz64','.feature_ids','.feature_labels','.feature_selected',
    '.setup-photo.dat','.section.csv','.section.preview64','.mesh.json','.ids','.labels',
    '.i64','.i32','.vertices','.faces')


def _json(value):
    if hasattr(value,'a') and hasattr(value,'name'):return {'rows':value.a.tolist(),'name':value.name}
    if hasattr(value,'tolist'):return value.tolist()
    raise TypeError('Unsupported project metadata: '+type(value).__name__)


def _serial(state):return json.loads(json.dumps(state,default=_json,allow_nan=False))
def _digest(value):return hashlib.sha256(json.dumps(value,default=_json,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _source_paths(value):
    """Explicit original imports remain protected even inside the data folder."""
    found=set()
    def walk(v,key=''):
        if isinstance(v,dict):
            for k,x in v.items():walk(x,k)
        elif isinstance(v,(list,tuple)):
            for x in v:walk(x,key)
        elif isinstance(v,str) and key=='original_data_json':
            try:walk(json.loads(v))
            except (ValueError,TypeError) as error:
                raise ValueError('A project recovery ownership record contains invalid JSON. Restore or repair the project metadata before deleting working files.') from error
        elif isinstance(v,str) and v and key in ('source_path','original_path','original_file','input_path','external_path'):
            try:found.add(Path(v).resolve())
            except (OSError,ValueError):pass
    walk(value);return found


def _refs(root,value):
    found=projects._local_asset_paths(root,value)
    # Original-source overviews are deterministic and not written into metadata.
    def walk(v):
        if isinstance(v,dict):
            if v.get('file') and isinstance(v.get('count'),int):
                try:
                    p=__import__('preview_store').overview_path(v).resolve()
                    if p.is_relative_to(root) and p.is_file():found.add(p)
                except (OSError,ValueError,KeyError):pass
            for k,x in v.items():
                if k=='original_data_json' and isinstance(x,str):
                    try:
                        decoded=json.loads(x)
                        found.update(projects._local_asset_paths(root,decoded))
                        walk(decoded)
                    except (ValueError,TypeError) as error:
                        raise ValueError('A project recovery ownership record contains invalid JSON. Restore or repair the project metadata before deleting working files.') from error
                else:walk(x)
        elif isinstance(v,(list,tuple)):
            for x in v:walk(x)
    walk(value)
    # New display-cache ownership records also cover old pose/colour variants.
    directories={p.parent/'display_cache' for p in found if p.name.endswith(('.xyz64','.vertices64'))}
    for directory in directories:
        if not directory.is_dir():continue
        for record in directory.glob('*.owner.json'):
            try:
                data=projects.read_json(record);source=Path(data['source_file']).resolve()
                cache=record.with_name(record.name[:-len('.owner.json')])
                if source in found and cache.is_file():found.update((cache.resolve(),record.resolve()))
            except (OSError,ValueError,KeyError,TypeError):continue
    return found


def _owned(root,paths,originals):
    return {p for p in paths if p.is_relative_to(root) and p not in originals
            and (p.name.endswith(CACHE_ENDINGS) or p.parent.name=='display_cache' and p.suffix in ('.npz','.npy','.json'))}


def _depends(value,ids,files):
    if not isinstance(value,dict):return False
    keys=('source_id','reference_id','source_item_id','scan_id')
    if any(value.get(k) in ids for k in keys):return True
    for k in ('source_ids','source_item_ids','reference_ids','scan_ids','ids'):
        if isinstance(value.get(k),list) and any(i in ids for i in value[k] if isinstance(i,str)):return True
    for key in ('source','reference','source_meta','reference_meta','meta'):
        nested=value.get(key)
        if isinstance(nested,dict):
            if _depends(nested,ids,files):return True
            if not any(value.get(k) for k in keys) and nested.get('file') in files:return True
    for key in ('cloud_parts','source_ranges','reference_ranges'):
        for row in value.get(key,[]) or []:
            if isinstance(row,dict) and (row.get('id') in ids or _depends(row,ids,files)):return True
    for key in ('source_revisions','reference_revisions'):
        if isinstance(value.get(key),dict) and ids&value[key].keys():return True
    return False


def transform_state(state,ids,rows):
    """Remove only requested identities/aliases, preserving unrelated saved edits."""
    data=copy.deepcopy(state);ids=set(ids);resources=[]
    removed=[x for x in data.get('job_items',[]) if x.get('id') in ids]
    resources.extend(removed);scan_ids={x['id'] for x in removed};files={x.get('meta',{}).get('file') for x in removed}-{None}
    data['job_items']=[x for x in data.get('job_items',[]) if x.get('id') not in ids]
    for entry in data['job_items']:
        if entry.get('merge_parent') in ids:entry.pop('merge_parent',None);entry['visible']=not entry.get('merge_archived',False)
        # These are historical descriptions, not dependencies of an independent
        # merged cloud. Keep names/IDs without pinning erased source cache paths.
        for row in entry.get('merge_ranges',[]):
            if row.get('scan_id') in ids:
                for key in ('source_file','source_mask'):
                    row.pop(key,None)
                row['source_removed']=True
        for ident,row in entry.get('merge_source_revisions',{}).items():
            if ident in ids:
                for key in ('file','mask_file'):
                    row.pop(key,None)
                row['source_removed']=True
    # Separation history belongs to the surviving source scan, but must not
    # recreate an explicitly deleted child on a later point-edit Redo.
    histories=[data.get('edits',[]),data.get('redo',[])]
    histories.extend(stack for entry in data['job_items'] for stack in (entry.get('edit_history',[]),entry.get('redo_history',[])))
    for stack in histories:
        for record in stack:
            child=record.get('separated_item')
            if isinstance(child,dict) and child.get('id') in ids:
                resources.append(child);record.pop('separated_item');record['separated_item_removed']=True
    data['scan_links']=[x for x in data.get('scan_links',[]) if x.get('a') not in ids and x.get('b') not in ids]
    for link in data['scan_links']:
        if link.get('completed_merge_id') in ids:link.pop('completed_merge_id',None)
    if scan_ids:data.pop('scan_link_draft',None)
    remaining_files={x.get('meta',{}).get('file') for x in data['job_items']}
    def clear(key,value=None):
        if data.get(key) is not None:resources.append(data[key])
        data[key]=copy.deepcopy(value)
    if data.get('active_job_scan') in ids or ((data.get('cloud') or {}).get('file') in files-remaining_files):
        for key,value in dict(active_job_scan=None,cloud=None,derived=None,valid_points=0,joints=None,edits=[],redo=[]).items():clear(key,value)
    if data.get('active_job_registration') in ids or ((data.get('registration') or {}).get('cloud') or {}).get('file') in files-remaining_files:
        clear('registration',{});data['active_job_registration']=None
    if data.get('surface_source_item') in ids or (data.get('surface_source') or {}).get('file') in files-remaining_files:
        clear('surface_source');data['surface_source_item']=None;data['surface_job_selection']=None
    for key in ('surface_result','inspection_result','design_comparison','analysis_tool_result'):
        if _depends(data.get(key),ids,files):clear(key)
    if (data.get('setup_view') or {}).get('id') in ids:data.pop('setup_view',None)
    cad=data.get('cad') or {};oldcad=cad.get('objects',[])
    resources.extend(x for x in oldcad if x.get('id') in ids)
    if any(x.get('id') in ids for x in oldcad):
        cad['objects']=[x for x in oldcad if x.get('id') not in ids];cad['revision']=uuid.uuid4().hex
        cad['last_created']=[i for i in cad.get('last_created',[]) if i not in ids]
    # Generated references can be removed without changing the measured points.
    import scene_objects
    staged=SimpleNamespace(STATE=data)
    for row in rows:
        if row['id'] not in ids or row['kind'] in ('scan','mesh','cad'):continue
        resources.extend(x for x in data.get('scene_objects',[]) if x.get('id')==row['id'])
        if row['kind']=='inspection' and data.get('inspection_result'):resources.append(data['inspection_result'])
        if row['kind']=='section' and data.get('free_section'):resources.append(data['free_section'])
        if row['kind']=='alignment' and data.get('estimated_alignment'):resources.append(data['estimated_alignment'])
        if row['kind']=='structure':resources.extend(x for x in data.get('structures',[]) if x.get('id')==row.get('structure_id'))
        if row['kind']=='limit_box' and row.get('source_id') in ids:continue
        try:scene_objects.remove_row(staged,row)
        except StopIteration:continue  # The matching saved state may predate the owner.
    bundles=[]
    for bundle in data.get('bundles',[]):
        bundle['scan_ids']=[i for i in bundle.get('scan_ids',[]) if i not in ids]
        if len(bundle['scan_ids'])>=2:bundles.append(bundle)
    data['bundles']=bundles
    if removed:data.update(report_ready=False,clean_ready=False,export_ready=None)
    return data,resources


def _request(s,body):
    if not isinstance(body,dict):raise ValueError('Select items in Project Explorer.')
    ids=body.get('ids',[]);bundle_ids=body.get('bundle_ids',[])
    if not isinstance(ids,list) or not isinstance(bundle_ids,list) or any(not isinstance(i,str) or not i for i in ids+bundle_ids):raise ValueError('Select valid Project Explorer items.')
    if not ids and not bundle_ids:raise ValueError('Select at least one item.')
    import scene_objects,workspace_tools
    state=copy.deepcopy(s.STATE);staged=SimpleNamespace(STATE=state)
    bundles={b['id']:b for b in workspace_tools.bundles(staged)}
    if not set(bundle_ids)<=bundles.keys():raise ValueError('A selected bundle changed. Select it again.')
    expanded=set(ids)
    for ident in bundle_ids:
        expanded.update(bundles[ident]['scan_ids'])
        expanded.update(x['id'] for x in state.get('job_items',[])+(state.get('cad') or {}).get('objects',[]) if x.get('bundle_id')==ident and not x.get('registered_merge'))
    rows={x['id']:{'id':x['id'],'name':x.get('name','Item'),'kind':x['kind'],'removable':True} for x in state.get('job_items',[])}
    rows.update({x['id']:x for x in __import__('cad_api').public_objects(staged)})
    # Removing a scan must remain possible when an unrelated old result cannot
    # render. Only reference deletion needs the full generated-reference list.
    if not expanded<=rows.keys():rows.update({x['id']:x for x in scene_objects.public(staged)})
    if not expanded<=rows.keys() or any(not rows[i].get('removable') for i in expanded):raise ValueError('A selected item is unavailable or protected. Refresh Project Explorer.')
    return sorted(expanded),[rows[i] for i in sorted(expanded)]


def _stat(path):
    v=path.stat();return [str(path),v.st_size,v.st_mtime_ns,v.st_ctime_ns]


def _plan(s,body):
    if s.JOB.get('busy') and not body.get('_in_worker'):raise ValueError('Wait for the current operation to finish.')
    root=Path(s.WORK).resolve();ids,rows=_request(s,body)
    current,resources=transform_state(s.STATE,ids,rows);updates={};inputs=[]
    named=projects._read_named_asset_sets(root,strict=True)
    recoveries=projects._hidden_recovery_entries(root,strict=True)
    current_id=s.STATE.get('project_id');protected=_refs(root,current);originals=_source_paths(s.STATE)
    originals.update(_source_paths(getattr(s,'WORKSPACE_HISTORY',{})))
    for ident,entry in named.items():
        data=entry['data'];originals.update(_source_paths(data));inputs.append(_stat(entry['path']))
        if current_id and ident==current_id:
            next_data,removed=transform_state(data,ids,rows);resources.extend(removed)
            if _digest(data)!=_digest(next_data):updates[entry['path']]=next_data
            protected.update(_refs(root,next_data))
        else:protected.update(_refs(root,data))
    for entry in recoveries:
        path=entry['path'];data=projects.read_json(path)
        if data.get('format') or isinstance(data.get('state'),dict):data,_=projects.normalize_manifest(data)
        originals.update(_source_paths(data));inputs.append(_stat(path))
        # Unnamed historical work has no reliable identity and stays protected.
        if current_id and entry['project_id']==current_id:
            next_data,removed=transform_state(data,ids,rows);resources.extend(removed)
            if _digest(data)!=_digest(next_data):updates[path]=next_data
            protected.update(_refs(root,next_data))
        else:protected.update(_refs(root,data))
    history=getattr(s,'WORKSPACE_HISTORY',{})
    selected_files={x.get('meta',{}).get('file') for x in s.STATE.get('job_items',[]) if x.get('id') in ids}-{None}
    for name in ('FEATURE_SELECTION','GEOMETRY_PREVIEW','MESH_TO_CLOUD_ESTIMATE','HIGHLIGHT_STATS','_setup_cloud_pick','_setup_photo_pick'):
        value=getattr(s,name,None)
        if not isinstance(value,dict):continue
        originals.update(_source_paths(value))
        if _depends(value,set(ids),selected_files) or _depends(value.get('request'),set(ids),selected_files):resources.append(value)
        else:protected.update(_refs(root,value))
    for stack in history.values():
        if not isinstance(stack,list):continue
        for record in stack:
            if not isinstance(record,dict):continue
            for side in ('before','after'):
                if isinstance(record.get(side),dict):
                    _,removed=transform_state(record[side],ids,rows);resources.extend(removed)
    candidates=_owned(root,_refs(root,resources),originals)
    # Pre-0.18.3 view variants lack owner records. When their directory has no
    # surviving source owner at all, all disposable previews can be reclaimed.
    directories={p.parent/'display_cache' for p in candidates if p.name.endswith(('.xyz64','.vertices64'))}
    for directory in directories:
        if directory.is_dir() and not any(p.parent==directory.parent and p.name.endswith(('.xyz64','.vertices64')) for p in protected):
            candidates.update(p.resolve() for p in directory.iterdir() if p.is_file() and (p.suffix in ('.npy','.npz') or p.name.endswith('.owner.json')) and p.resolve().is_relative_to(root))
    # Legacy display-cache discovery is deliberately broad, but an original
    # import stays protected even when it was placed inside that directory.
    candidates.difference_update(originals)
    exclusive=candidates-protected;shared=candidates&protected
    clears=bool(exclusive)
    if not clears:
        protected.update(_refs(root,history));exclusive=candidates-protected;shared=candidates&protected
    autosave=root/'project.json'
    if autosave.exists():inputs.append(_stat(autosave))
    # Bind confirmation to every state/ownership/file version, not just bytes.
    revision=_digest([ids,body.get('bundle_ids',[]),s.STATE,history,sorted(inputs),
                      sorted(_stat(p) for p in candidates),sorted(str(p) for p in protected)])
    preserved=[dict(id=x['id'],name=x.get('name','Registered cloud')) for x in s.STATE.get('job_items',[])
               if x.get('registered_merge') and x['id'] not in ids
               and set(x.get('merge_source_ids',[])).intersection(ids)]
    public=dict(preserved_registered_clouds=preserved,revision=revision,item_count=len(ids),items=[{k:r[k] for k in ('id','name','kind')} for r in rows],
                exclusive_files=len(exclusive),exclusive_bytes=projects._asset_bytes(exclusive),
                shared_files=len(shared),shared_bytes=projects._asset_bytes(shared),
                external_files=len({p for p in _source_paths(resources) if p.is_file()}),
                history_cleared=clears,updates_saved_project=bool(updates),original_imports_preserved=True)
    return dict(root=root,ids=ids,rows=rows,state=current,updates=updates,paths=sorted(exclusive),public=public)


def review(s,body):
    recover(s.WORK)
    return _plan(s,body)['public']


def _relative(root,raw):
    path=Path(raw)
    if path.is_absolute() or '..' in path.parts:raise ValueError('Invalid item-deletion recovery path.')
    result=(root/path).resolve()
    if not result.is_relative_to(root):raise ValueError('Item-deletion recovery path leaves the workspace.')
    return result


def _rollback(root,stage,journal):
    # Restore moved data first, then put the original manifests back byte for byte.
    for relative in reversed(journal['assets']):
        original=_relative(root,relative);held=stage/'assets'/relative
        if held.exists():
            if original.exists():raise OSError('Cannot restore a deletion transaction over a changed cache: '+original.name)
            original.parent.mkdir(parents=True,exist_ok=True);os.replace(held,original)
    for row in journal['metadata']:
        original=_relative(root,row['path']);backup=stage/'before'/row['path']
        if backup.exists():original.parent.mkdir(parents=True,exist_ok=True);os.replace(backup,original)
        elif not row['existed']:original.unlink(missing_ok=True)
    shutil.rmtree(stage)


def _queue(root,stage,journal):
    # Old metadata backups are no longer needed after the commit marker.
    shutil.rmtree(stage/'before',ignore_errors=True)
    queue=root/projects._PURGE_ROOT;queue.mkdir(exist_ok=True)
    target=queue/stage.name
    projects._write_purge_meta(stage,{'bytes':journal['bytes'],'label':'Deleted Project Explorer items',
        'created_at':journal['created_at'],'explorer_delete':True})
    os.replace(stage,target);projects._schedule_purge(target)
    return target


def recover(work):
    root=Path(work).resolve();directory=root/TRANSACTIONS
    if not directory.is_dir():return
    for stage in directory.iterdir():
        if not stage.is_dir():continue
        journal_file=stage/'journal.json'
        if not journal_file.exists():
            # No moves can begin before this journal exists.
            shutil.rmtree(stage);continue
        try:
            journal=projects.read_json(journal_file)
            if journal['status']=='committed':_queue(root,stage,journal)
            else:_rollback(root,stage,journal)
        except Exception as error:
            raise ValueError('An interrupted item deletion needs recovery before this workspace can be changed: '+str(error)) from error


def delete(s,body):
    if body.get('confirmed') is not True:raise ValueError('Confirm deletion of the selected items and their unused working files.')
    plan=_plan(s,body);public=plan['public']
    if not body.get('revision') or body['revision']!=public['revision']:raise ValueError('Project storage or selection changed. Review the deletion again.')
    root=plan['root'];parent=root/TRANSACTIONS;parent.mkdir(exist_ok=True)
    stage=parent/('items_'+uuid.uuid4().hex);stage.mkdir()
    paths=[root/'project.json',*plan['updates']]
    journal={'status':'prepared','assets':[str(p.relative_to(root)) for p in plan['paths']],
             'metadata':[{'path':str(p.relative_to(root)),'existed':p.exists()} for p in paths],
             'bytes':public['exclusive_bytes'],'created_at':datetime.now(timezone.utc).isoformat()}
    old_state=copy.deepcopy(s.STATE);old_selection=getattr(s,'SELECTION',None)
    try:
        for path in paths:
            if path.exists():
                backup=stage/'before'/path.relative_to(root);backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
        projects.atomic_json(stage/'journal.json',journal)
        for path in plan['paths']:
            target=stage/'assets'/path.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);os.replace(path,target)
        s.STATE.clear();s.STATE.update(plan['state']);s.SELECTION=None
        s.STATE['last_explorer_delete']={**public,'transaction_id':stage.name,'removed_count':public['item_count'],
            'freed_bytes':0,'pending_bytes':public['exclusive_bytes'],'purge_pending':bool(public['exclusive_bytes'])}
        # s.save is the application's canonical current-workspace persistence.
        s.save()
        for path,data in plan['updates'].items():projects.atomic_json(path,_serial(data))
        journal['status']='committed';projects.atomic_json(stage/'journal.json',journal)
    except BaseException:
        s.STATE.clear();s.STATE.update(old_state);s.SELECTION=old_selection
        try:
            if (stage/'journal.json').exists():_rollback(root,stage,journal)
            else:shutil.rmtree(stage)
        except Exception as error:raise RuntimeError('Deletion did not complete; recovery data was retained. Restart before editing: '+str(error)) from error
        raise
    if public['history_cleared']:
        s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
        ctx=getattr(workspace_history.LOCAL,'context',None)
        if ctx:ctx['before']=None;ctx['label']=None;ctx['reset']=False
    # Result/pick buffers are disposable, not file ownership for deleted items.
    for name in ('FEATURE_SELECTION','GEOMETRY_PREVIEW','MESH_TO_CLOUD_ESTIMATE','HIGHLIGHT_STATS','_setup_cloud_pick','_setup_photo_pick'):
        value=getattr(s,name,None)
        if name in ('MESH_TO_CLOUD_ESTIMATE','HIGHLIGHT_STATS') or _depends(value,set(plan['ids']),set()) or _depends((value or {}).get('request'),set(plan['ids']),set()):setattr(s,name,None)
    pending=0
    try:
        queued=_queue(root,stage,journal)
        pending=projects._asset_bytes(p for p in (queued/'assets').rglob('*') if p.is_file()) if queued.exists() else 0
    except OSError:
        # The committed journal is durable and startup will retry the purge.
        pending=public['exclusive_bytes']
    result={**public,'transaction_id':stage.name,'removed_count':public['item_count'],'freed_bytes':max(0,public['exclusive_bytes']-pending),
            'pending_bytes':pending,'purge_pending':bool(pending)}
    s.STATE['last_explorer_delete']=result
    s.LAST_EXPLORER_DELETE=result
    return result


def report(s):
    result=copy.deepcopy(getattr(s,'LAST_EXPLORER_DELETE',None) or s.STATE.get('last_explorer_delete') or {})
    ident=result.get('transaction_id','')
    if ident.startswith('items_') and '/' not in ident and '\\' not in ident:
        root=Path(s.WORK).resolve();pending=0
        for base in (root/projects._PURGE_ROOT/ident,root/TRANSACTIONS/ident):
            if base.is_dir():pending+=projects._asset_bytes(p for p in (base/'assets').rglob('*') if p.is_file())
        result.update(pending_bytes=pending,purge_pending=bool(pending),freed_bytes=max(0,result['exclusive_bytes']-pending))
    return result
