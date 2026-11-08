# Copyright (c) 2026 Damian Gulczynski.
"""Explicit, irreversible compaction of a scan's excluded-point cache.

Writes retained float64 rows unchanged, commits replacement metadata first,
then removes only old assets unreferenced by any current/saved/recovery state.
External inputs are never removed. No cloud-sized arrays are allocated.
"""
import copy
import hashlib
import json
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
import uuid
import numpy as np
import projects

BLOCK=1000000
RESERVE=512*1024*1024
CACHE_SUFFIXES=('.xyz64','.attr32','.mask','.preview64','.analysis64','.truth','.viewindex.npz','.surface_ids','.surface_labels','.patch_ids','.surface.ids64','.surface.labels32')

def revision(s,item):
    """Bind a confirmation to immutable source files and current workspace Undo."""
    meta=item['meta'];n=meta.get('count')
    if type(n) is not int or n<1:raise ValueError('The selected scan has no valid point cache.')
    sources={}
    for key,width in [('file',24),('attributes_file',16),('mask_file',1),('truth_file',1)]:
        if not meta.get(key):continue
        p=Path(meta[key]).resolve();stat=p.stat()
        if not p.is_file() or stat.st_size!=n*width:raise ValueError('A source cache is incomplete or changed. Permanent removal was not started.')
        sources[key]=[str(p),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]
    if s.STATE.get('active_job_scan')==item['id'] and s.STATE.get('derived'):
        p=Path(s.STATE['derived']).resolve();stat=p.stat()
        if not p.is_file() or stat.st_size!=n*32:raise ValueError('The active analysis cache is incomplete. Permanent removal was not started.')
        sources['derived']=[str(p),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]
    groups=[[g.get('id'),g.get('count'),g.get('active'),(g.get('archive') or {}).get('result_revision')] for g in item.get('deleted_groups',[])]
    history=getattr(s,'WORKSPACE_HISTORY',{})
    history_key={side:[[id(row),row.get('label')] for row in history.get(side,[])] for side in ('undo','redo')}
    value=[str(Path(s.WORK).resolve()),s.STATE.get('project_id'),item['id'],item.get('name'),n,sources,groups,history_key,
           [s.STATE.get(k) for k in ('active_job_scan','active_job_registration','surface_source_item')]]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def preview(s,item):
    if item.get('kind')!='scan':raise ValueError('Choose a point-cloud scan.')
    before=revision(s,item);meta=item['meta'];count=meta['count'];kept=count;mask=meta.get('mask_file')
    props=meta.get('retained_properties') or {}
    if mask:
        if props.get('revision')==mask and type(props.get('count')) is int and 0<=props['count']<=count:kept=props['count']
        else:
            kept=0
            with Path(mask).open('rb') as stream:
                for start in range(0,count,BLOCK):
                    size=min(BLOCK,count-start);part=np.fromfile(stream,'u1',count=size)
                    if len(part)!=size:raise ValueError('The deletion mask changed. Review permanent removal again.')
                    kept+=int(np.count_nonzero(part==0))
    archives=[g for g in item.get('deleted_groups',[]) if g.get('archive') and g.get('active',True) and g.get('kind')=='delete']
    archived=sum(max(0,int(g.get('count') or 0)) for g in archives);removed=count-kept
    if not removed and not archives:raise ValueError('This scan has no deleted points or cleaning restoration archives to remove permanently.')
    width=24+(16 if meta.get('attributes_file') else 0)+(1 if meta.get('truth_file') else 0)
    if s.STATE.get('active_job_scan')==item['id'] and s.STATE.get('derived'):width+=32
    stride=max(1,(kept+249999)//250000)
    estimated=kept*width+((kept+stride-1)//stride)*24 if removed else 0
    metadata_file=Path(s.WORK)/'project.json'
    metadata_space=(metadata_file.stat().st_size if metadata_file.is_file() else 0)+65536
    required=estimated+metadata_space+(RESERVE if removed and kept else 0)
    history=getattr(s,'WORKSPACE_HISTORY',{})
    result=dict(id=item['id'],name=item.get('name','Scan'),revision=before,original_points=count,
                remaining_points=kept,removed_points=removed,archived_points=archived,archived_group_count=len(archives),
                total_deleted_points=removed+archived,estimated_replacement_bytes=estimated,required_free_bytes=required,
                free_bytes=shutil.disk_usage(s.WORK).free,workspace_undo_count=len(history.get('undo',[])),
                workspace_redo_count=len(history.get('redo',[])),clears_workspace_history=True,rewrites_cache=bool(removed),
                removes_scan=kept==0,original_imports_preserved=True,saved_copies_preserved=True)
    if before!=revision(s,item):raise ValueError('The scan changed while preparing the review. Review permanent removal again.')
    return result

def forget_archive_provenance(item):
    provenance=item.get('cleaning_provenance') or {}
    forgotten={g['archive'].get('result_revision') for g in item.get('deleted_groups',[]) if g.get('archive')}
    if provenance.get('revision') in forgotten:item['cleaning_provenance']={**provenance,'restoration_archive_removed':True}

def invalidate_results(s,item,oldmeta,candidates,removed=False):
    """Remove active ID-based results; retain independent stored geometry."""
    ident=item['id'];active=s.STATE.get('active_job_scan')==ident
    for key in ('inspection_result','design_comparison'):
        result=s.STATE.get(key) or {}
        if ident in (result.get('source_id'),result.get('reference_id')):candidates.update(paths(result));s.STATE[key]=None
    result=s.STATE.get('surface_result') or {}
    if result.get('source_item_id')==ident or not result.get('source_item_id') and active and result.get('source_meta',{}).get('file')==oldmeta['file']:
        candidates.update(paths(result));s.STATE['surface_result']=None
    s.STATE['analysis_tool_result']=None
    if removed:
        if active:s.STATE.update(cloud=None,derived=None,valid_points=0,edits=[],redo=[],joints=None)
        if s.STATE.get('surface_source_item')==ident:s.STATE['surface_source']=None
    if (s.STATE.get('setup_view') or {}).get('id')==ident:s.STATE.pop('setup_view',None)
    s.STATE.update(report_ready=False,clean_ready=False,export_ready=None)

def clear_transients(s,ident):
    feature=getattr(s,'FEATURE_SELECTION',None)
    if feature and feature.get('source_id')==ident:
        __import__('feature_selection')._remove_cache(feature);s.FEATURE_SELECTION=None
    geometry=getattr(s,'GEOMETRY_PREVIEW',None) or {};request=geometry.get('request') or {}
    if ident in request.get('ids',[]) or request.get('source_id')==ident:s.GEOMETRY_PREVIEW=None
    if (getattr(s,'_setup_cloud_pick',None) or {}).get('scan_id')==ident:s._setup_cloud_pick=None
    s.HIGHLIGHT_STATS=None

def paths(value):
    found=set()
    projects.visit(value,lambda p:found.add(str(Path(p).resolve())) or p)
    return found

def protect_saved(work,current):
    protected=paths(current)
    for folder in ('projects','startup_recovery'):
        for file in (Path(work)/folder).glob('*.json'):
            try:protected.update(paths(projects.read_json(file)))
            except (OSError,ValueError,TypeError) as error:
                # A damaged snapshot may still own caches. Do not guess which.
                raise ValueError('Cache reclamation skipped because a saved project cannot be checked: '+file.name) from error
    protected.update(p+'.viewindex.npz' for p in list(protected))
    return protected

def compact(s,item,confirmed=False,expected_revision=None):
    if confirmed is not True:raise ValueError('Confirm permanent deletion and loss of point-edit/workspace undo history.')
    if item.get('kind')!='scan':raise ValueError('Choose a point-cloud scan.')
    review=preview(s,item)
    if not expected_revision or expected_revision!=review['revision']:raise ValueError('The permanent removal review is missing or stale. Review permanent removal again.')
    if review['free_bytes']<review['required_free_bytes']:raise ValueError('Not enough free project-drive space for a safe replacement cache. The current scan is unchanged.')
    oldmeta=copy.deepcopy(item['meta']);count=oldmeta['count'];mask_path=oldmeta.get('mask_file')
    if not review['rewrites_cache']:
        return forget_archives(s,item,review)
    root=Path(s.WORK).resolve();ident=uuid.uuid4().hex
    created=[];newmeta={k:copy.deepcopy(v) for k,v in oldmeta.items() if k not in ('file','mask_file','attributes_file','preview_file','preview_count','preview_stride','retained_properties','retained_summary','truth_file')}
    from project_data_layout import folder
    data=folder(root,'registered' if item.get('registered_merge') else 'derived')
    xyz=data/(ident+'.xyz64');attr=data/(ident+'.attr32');derived=folder(root,'analysis')/(ident+'.analysis64');truth=data/(ident+'.truth');preview_path=data/(ident+'.preview64')
    active=s.STATE.get('active_job_scan')==item['id'];source_derived=s.STATE.get('derived') if active else None
    source_truth=oldmeta.get('truth_file');written=valid=preview_count=0;lo=np.full(3,np.inf);hi=-lo
    stride=max(1,int(np.ceil(review['remaining_points']/250000)))
    acount=np.zeros(4,np.int64);amin=np.full(4,np.inf);amax=-amin
    def progress(message):
        # Shares the job cancellation token; every read/copy is bounded.
        import job_api
        job_api.progress(s,message)
    try:
        with ExitStack() as stack:
            inputs={key:stack.enter_context(open(path,'rb')) for key,path in [('xyz',oldmeta['file']),('mask',mask_path),('attr',oldmeta.get('attributes_file')),('derived',source_derived),('truth',source_truth)] if path}
            output_paths={'xyz':xyz,'preview':preview_path,**({'attr':attr} if 'attr' in inputs else {}),**({'derived':derived} if source_derived else {}),**({'truth':truth} if source_truth else {})}
            outputs={}
            for key,path in output_paths.items():outputs[key]=stack.enter_context(path.open('xb'));created.append(path)
            for start in range(0,count,BLOCK):
                n=min(BLOCK,count-start);progress(f'Permanently removing excluded points · {start+n:,}/{count:,} read')
                excluded=np.fromfile(inputs['mask'],'u1',count=n);p=np.fromfile(inputs['xyz'],'<f8',count=n*3)
                if len(excluded)!=n or len(p)!=n*3:raise ValueError('Incomplete source cache; current cloud is unchanged.')
                keep=excluded==0;q=p.reshape(n,3)[keep]
                before_written=written;q.tofile(outputs['xyz'])
                if len(q):
                    first=(-before_written)%stride;sample=q[first::stride]
                    if len(sample):sample.tofile(outputs['preview']);preview_count+=len(sample)
                    lo=np.minimum(lo,q.min(0));hi=np.maximum(hi,q.max(0))
                written+=len(q)
                if 'attr' in inputs:
                    a=np.fromfile(inputs['attr'],'<f4',count=n*4)
                    if len(a)!=n*4:raise ValueError('Incomplete attributes cache; current cloud is unchanged.')
                    a=a.reshape(n,4)[keep];a.tofile(outputs['attr'])
                    for j in range(4):
                        v=a[np.isfinite(a[:,j]),j];acount[j]+=len(v)
                        if len(v):amin[j]=min(amin[j],float(v.min()));amax[j]=max(amax[j],float(v.max()))
                if source_derived:
                    d=np.fromfile(inputs['derived'],'<f8',count=n*4)
                    if len(d)!=n*4:raise ValueError('Incomplete analysis cache; current cloud is unchanged.')
                    d=d.reshape(n,4)[keep];d.tofile(outputs['derived']);valid+=int(np.isfinite(d[:,0]).sum())
                if source_truth:
                    t=np.fromfile(inputs['truth'],'u1',count=n)
                    if len(t)!=n:raise ValueError('Incomplete synthetic truth cache; current cloud is unchanged.')
                    t[keep].tofile(outputs['truth'])
            for output in outputs.values():output.flush();os.fsync(output.fileno())
        if written!=review['remaining_points']:raise ValueError('The deletion count changed. Review permanent removal again.')
        if not written:
            candidates=paths(item)|({str(Path(source_derived).resolve())} if source_derived else set())
            candidates.add(str(Path(oldmeta['file']).resolve())+'.viewindex.npz')
            progress('Committing permanent removal · final safe cancellation check')
            if revision(s,item)!=expected_revision:raise ValueError('The scan or project changed during removal. Review permanent removal again.')
            for path in created:path.unlink(missing_ok=True)
            created.clear();before=copy.deepcopy(s.STATE);old_history=getattr(s,'WORKSPACE_HISTORY',{'undo':[],'redo':[]});old_selection=getattr(s,'SELECTION',None)
            try:
                import job_api
                invalidate_results(s,item,oldmeta,candidates,removed=True)
                s.SELECTION=None;s.WORKSPACE_HISTORY={'undo':[],'redo':[]};job_api.remove_items(s,[item['id']])
            except BaseException:s.STATE.clear();s.STATE.update(before);s.WORKSPACE_HISTORY=old_history;s.SELECTION=old_selection;raise
            return finish(s,candidates,created,count,0,review)
        if written==count:raise ValueError('This cloud has no excluded points to remove permanently.')
        newmeta.update(file=str(xyz),count=written,minimum=lo.tolist(),maximum=hi.tolist())
        if oldmeta.get('attributes_file'):newmeta.update(attributes_file=str(attr),attribute_counts=acount.tolist(),attribute_min=[float(v) if np.isfinite(v) else None for v in amin],attribute_max=[float(v) if np.isfinite(v) else None for v in amax])
        if source_truth:newmeta['truth_file']=str(truth)
        # Preview samples are written during the compaction pass, avoiding a
        # second full read of the newly-created cloud on large RTC360 datasets.
        newmeta.update(preview_file=str(preview_path),preview_count=preview_count,preview_stride=stride)
        progress('Committing permanent removal · final safe cancellation check')
        if revision(s,item)!=expected_revision:raise ValueError('The scan or project changed during removal. Review permanent removal again.')
        before=copy.deepcopy(s.STATE);old_selection=getattr(s,'SELECTION',None);old_history=getattr(s,'WORKSPACE_HISTORY',{'undo':[],'redo':[]})
        candidates=paths(item)|({str(Path(source_derived).resolve())} if source_derived else set())
        candidates.add(str(Path(oldmeta['file']).resolve())+'.viewindex.npz')
        try:
            forget_archive_provenance(item)
            item.update(meta=newmeta,edit_history=[],redo_history=[],deleted_groups=[])
            for key in ('setup_view','setup_cache','setup_view_cache','merge_ranges'):item.pop(key,None)
            registration=item.get('registration_state')
            if registration:registration.update(cloud=copy.deepcopy(newmeta),candidates=[],transformed=None,revision=uuid.uuid4().hex)
            if active:s.STATE.update(cloud=copy.deepcopy(newmeta),derived=str(derived) if source_derived else None,valid_points=valid if source_derived else written,edits=[],redo=[],joints=None)
            if s.STATE.get('active_job_registration')==item['id']:
                s.STATE.setdefault('registration',{}).update(cloud=copy.deepcopy(newmeta),candidates=[],transformed=None,revision=uuid.uuid4().hex)
            if s.STATE.get('surface_source_item')==item['id']:s.STATE['surface_source']=copy.deepcopy(newmeta)
            result=s.STATE.get('surface_result') or {}
            if result.get('source_meta',{}).get('file')==oldmeta['file'] and (result.get('source_item_id')==item['id'] or not result.get('source_item_id') and active):
                candidates.update(paths(result));s.STATE['surface_result']=None
            comparison=s.STATE.get('design_comparison') or {}
            if comparison.get('source_id')==item['id']:s.STATE['design_comparison']=None
            invalidate_results(s,item,oldmeta,candidates)
            s.STATE.update(report_ready=False,clean_ready=False,export_ready=None);s.SELECTION=None;s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
            s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(before);s.SELECTION=old_selection;s.WORKSPACE_HISTORY=old_history;raise
    except BaseException:
        for path in created:path.unlink(missing_ok=True)
        raise
    return finish(s,candidates,created,count,written,review)



def compact_many(s,selected,confirmed=False):
    """Permanently compact several scans sequentially with one confirmation.

    Every scan is validated before the first commit. Completed scans remain
    committed if the user cancels later in the batch; the current scan is still
    atomic. Sequential processing bounds temporary disk usage and avoids
    competing large reads/writes on the same project drive.
    """
    if confirmed is not True:raise ValueError('Confirm permanent deletion and loss of point-edit/workspace undo history.')
    if not isinstance(selected,list) or not selected:raise ValueError('Choose at least one scan.')
    if len(selected)>200:raise ValueError('Choose no more than 200 scans in one permanent-removal batch.')
    seen=set();prepared=[]
    for row in selected:
        if not isinstance(row,dict):raise ValueError('The permanent-removal selection is invalid.')
        ident=str(row.get('id') or '')
        if not ident or ident in seen:raise ValueError('The permanent-removal selection contains a duplicate or missing scan.')
        seen.add(ident)
        target=next((x for x in s.STATE.get('job_items',[]) if x.get('id')==ident),None)
        if target is None:
            # Job storage changed names across old releases; use job_api's canonical lookup.
            import job_api
            target=job_api.item(s,ident)
        review=preview(s,target)
        if row.get('revision')!=review['revision']:raise ValueError('One or more scans changed after the batch review. Refresh the review before permanently removing points.')
        prepared.append((target,review))
    reports=[];total=len(prepared)
    for index,(target,_) in enumerate(prepared,1):
        import job_api
        job_api.progress(s,f'Permanent removal batch · scan {index:,}/{total:,} · {target.get("name","Scan")}')
        # Earlier commits intentionally clear workspace history. Re-review this
        # untouched scan immediately before its own atomic compaction.
        current=preview(s,target)
        reports.append(compact(s,target,True,current['revision']))
    aggregate={
        'batch':True,'scan_count':len(reports),'reports':reports,
        'removed_points':sum(int(r.get('removed_points') or 0) for r in reports),
        'remaining_points':sum(int(r.get('remaining_points') or 0) for r in reports),
        'forgotten_archived_points':sum(int(r.get('forgotten_archived_points') or 0) for r in reports),
        'net_reclaimed_bytes':sum(int(r.get('net_reclaimed_bytes') or 0) for r in reports if r.get('net_reclaimed_bytes') is not None),
        'reclamation_complete':all(r.get('reclamation_complete') is not False for r in reports),
        'warnings':[w for r in reports for w in (r.get('warnings') or [])],
        'clears_workspace_history':True,
    }
    s.STATE['last_compaction']=aggregate
    try:s.save()
    except Exception as error:aggregate['warnings'].append('Batch completion summary could not be saved: '+str(error))
    if hasattr(s,'JOB'):s.JOB['message']=f'Permanent removal complete · {len(reports):,} scan(s) processed'
    return aggregate

def forget_archives(s,item,review):
    """Cleaned copies already contain no removed rows; discard restoration links."""
    import job_api
    job_api.progress(s,'Committing permanent removal of cleaning restoration archives')
    if revision(s,item)!=review['revision']:raise ValueError('The scan changed. Review permanent removal again.')
    before=copy.deepcopy(s.STATE);old_history=getattr(s,'WORKSPACE_HISTORY',{'undo':[],'redo':[]});old_selection=getattr(s,'SELECTION',None)
    candidates=paths(item)
    try:
        forget_archive_provenance(item)
        item.update(edit_history=[],redo_history=[],deleted_groups=[])
        if s.STATE.get('active_job_scan')==item['id']:s.STATE.update(edits=[],redo=[])
        s.SELECTION=None;s.WORKSPACE_HISTORY={'undo':[],'redo':[]};s.save()
    except BaseException:s.STATE.clear();s.STATE.update(before);s.WORKSPACE_HISTORY=old_history;s.SELECTION=old_selection;raise
    return finish(s,candidates,[],review['original_points'],review['remaining_points'],review)


def finish(s,candidates,created,count,written,review):
    """Post-commit housekeeping must never report an already-saved edit as lost."""
    warnings=[]
    if review['rewrites_cache']:
        try:clear_transients(s,review['id'])
        except Exception as error:warnings.append('Permanent removal saved; an obsolete preview could not be cleared: '+str(error))
    try:report=reclaim(s,candidates,created,count,written,review)
    except Exception as error:
        report=dict(id=review['id'],name=review['name'],removed_points=count-written,remaining_points=written,
                    forgotten_archived_points=review['archived_points'],forgotten_archive_groups=review['archived_group_count'],
                    removed_scan=review['removes_scan'],clears_workspace_history=True,reclamation_complete=False,
                    reclaimed_old_cache_bytes=None,replacement_cache_bytes=None,net_reclaimed_bytes=None,
                    shared_or_in_use_bytes=None,warnings=['Permanent removal saved; cache-space cleanup could not finish: '+str(error)])
        s.STATE['last_compaction']=report
        try:s.save()
        except Exception as save_error:report['warnings'].append('The completion summary could not be saved: '+str(save_error))
    if warnings:
        report['warnings'].extend(warnings)
        try:s.save()
        except Exception as error:report['warnings'].append('The cleanup warning could not be saved: '+str(error))
    return report


def reclaim(s,candidates,created,count,written,review):
    root=Path(s.WORK).resolve()
    # The replacement is now committed. A failed purge leaves an extra old
    # file, never a missing source or half-written active cache.
    warnings=[]
    if s.STATE.get('project_id'):
        try:
            import workflows
            projects.save_named(s.WORK,workflows.serial(s))
        except Exception as error:warnings.append('The named snapshot was not updated; its original caches were retained: '+str(error))
    removed=retained=0
    try:protected=protect_saved(root,s.STATE)
    except ValueError as error:protected=candidates;warnings.append(str(error))
    for value in candidates:
        path=Path(value)
        if value in protected or not path.is_relative_to(root) or path.is_symlink() or not path.name.endswith(CACHE_SUFFIXES):
            if path.is_file():retained+=path.stat().st_size
            continue
        try:
            size=path.stat().st_size;path.unlink();removed+=size
        except FileNotFoundError:pass
        except OSError as error:
            if path.is_file():retained+=path.stat().st_size
            warnings.append('A cache is in use and was retained: '+path.name+' ('+str(error)+')')
    replacement_bytes=sum(p.stat().st_size for p in created)
    report=dict(id=review['id'],name=review['name'],removed_points=count-written,remaining_points=written,
                forgotten_archived_points=review['archived_points'],forgotten_archive_groups=review['archived_group_count'],
                removed_scan=review['removes_scan'],clears_workspace_history=True,reclamation_complete=True,
                reclaimed_old_cache_bytes=removed,replacement_cache_bytes=replacement_bytes,net_reclaimed_bytes=removed-replacement_bytes,shared_or_in_use_bytes=retained,warnings=warnings)
    s.STATE['last_compaction']=report
    try:s.save()
    except Exception as error:report['warnings'].append('Compaction committed, but the summary could not be saved: '+str(error))
    # Cancellation is checked before the commit. Afterwards a late Stop cannot
    # truthfully be reported as an aborted/unchanged operation.
    if hasattr(s,'JOB'):s.JOB['message']=f'Permanent removal complete · {count-written:,} cache points removed'
    return report
