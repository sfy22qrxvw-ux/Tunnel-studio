# Copyright (c) 2026 Damian Gulczynski.
"""Independent setup inventory, editing and reference meshes for large local jobs."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy,json,shutil,threading,uuid
import jobdata,pointio,editing,scan_edits,scene_objects,workspace_tools,registration_pose,registered_cloud
from core import project_cloud

CANCEL=threading.Event()
def items(s):return s.STATE.setdefault('job_items',[])
def sync_active(s):
    ident=s.STATE.get('active_job_scan');cloud=s.STATE.get('cloud')
    for item in items(s):
        if item['kind']=='scan' and item['id']==ident and cloud and item['meta']['file']==cloud['file']:
            meta=item['meta'];changed=meta.get('mask_file')!=cloud.get('mask_file')
            same_source=all((meta.get(k) or None)==(cloud.get(k) or None) for k in ('file','count','attributes_file','mask_file'))
            local_properties=scan_edits.cached_properties(meta) if same_source else None
            active_properties=scan_edits.cached_properties(cloud)
            local_summary=scan_edits.cached_summary(meta) if same_source else None
            active_summary=scan_edits.cached_summary(cloud)
            incoming=copy.deepcopy(cloud);incoming.pop('retained_properties',None);incoming.pop('retained_summary',None)
            meta.update(incoming)
            if changed:meta['mask_file']=cloud.get('mask_file')
            retained=local_properties if local_properties is not None else active_properties
            if retained is not None:
                meta['retained_properties']=copy.deepcopy(retained)
                cloud['retained_properties']=copy.deepcopy(retained)
            else:
                meta.pop('retained_properties',None);cloud.pop('retained_properties',None)
            summary=local_summary if local_summary is not None else active_summary
            if summary is not None:
                meta['retained_summary']=copy.deepcopy(summary)
                cloud['retained_summary']=copy.deepcopy(summary)
            else:
                meta.pop('retained_summary',None);cloud.pop('retained_summary',None)
            if changed:
                item['edit_history']=copy.deepcopy(s.STATE.get('edits',[]));item['redo_history']=copy.deepcopy(s.STATE.get('redo',[]));item.pop('deleted_groups',None)
        registration=s.STATE.get('registration',{})
        if item['id']==s.STATE.get('active_job_registration') and item['kind']=='scan' and registration.get('cloud',{}).get('file')==item['meta']['file']:item['registration_state']=copy.deepcopy(registration)
def progress(s,message):
    if getattr(s,'JOB_CANCEL_FILE',None) and s.JOB_CANCEL_FILE.exists():CANCEL.set()
    if CANCEL.is_set():raise jobdata.Cancelled('Operation cancelled. Completed imports remain in the job; the incomplete asset was discarded.')
    s.progress(message)
def item(s,ident):
    result=next((i for i in items(s) if i['id']==ident),None)
    if result is None:raise ValueError('Select an item in the job first.')
    return result
def remove_items(s,identifiers):
    """Remove scene entries atomically; immutable source and cache files remain."""
    if not isinstance(identifiers,list) or not identifiers or any(not isinstance(i,str) for i in identifiers):
        raise ValueError('Select at least one scan or model.')
    ids=set(identifiers);old=items(s)
    if not ids <= {x['id'] for x in old}:raise ValueError('A selected scene item is no longer available. Refresh the list.')
    saved=copy.deepcopy(s.STATE)
    # Compatibility metadata-only removal still needs complete alias cleanup;
    # a stale cloud alias otherwise recreates the removed scan on reopening.
    rows=[{'id':x['id'],'kind':x['kind']} for x in old if x['id'] in ids]
    candidate,_=__import__('explorer_deletion').transform_state(s.STATE,ids,rows)
    s.STATE.clear();s.STATE.update(candidate)
    try:s.save()
    except Exception:s.STATE.clear();s.STATE.update(saved);raise
def append(s,kind,meta,source_bytes=0,bundle_id=None,commit=True):
    bundle_id=workspace_tools.destination_bundle(s,bundle_id)
    result={'id':uuid.uuid4().hex,'bundle_id':bundle_id,'kind':kind,'name':meta['name'],'meta':meta,'visible':True,'source_bytes':source_bytes,
            'position_status':meta.get('position_status','Position from file — not verified') if kind=='scan' else 'Reference mesh — units unconfirmed',
            'hidden_boxes':[],'isolate_box':None,'edit_history':[],'redo_history':[]}
    items(s).append(result)
    if commit:s.save()
    return result
def load(s,kind,path,display_name=None,bundle_id=None):
    if kind not in ('job_scan','job_mesh'):return False
    bundle_id=workspace_tools.destination_bundle(s,bundle_id)
    name=display_name or Path(path).name
    if not Path(path).is_file():raise ValueError('File not found. Choose it with Browse files or use its full local path.')
    if kind=='job_mesh':
        meta=jobdata.import_obj(path,s.WORK,lambda m:progress(s,m));meta['name']=name if Path(name).suffix.lower()!='.zip' else meta['name'];append(s,'mesh',meta,Path(path).stat().st_size,bundle_id)
    else:
        if Path(path).suffix.lower() not in jobdata.POINT_EXTS:raise ValueError('Use E57, Leica setup HSPCA, LAS/LAZ, PLY, PTS, XYZ, TXT or CSV.')
        setups=jobdata.scan_parts(path,lambda m:progress(s,m))
        total=len(setups)
        for ordinal,(index,expected) in enumerate(setups,1):
            progress(s,f'Importing {name} · setup {ordinal}/{total}')
            meta=jobdata.import_scan(path,s.WORK,index,expected,lambda m:progress(s,m))
            meta['name']=name+(f' · setup {index+1}' if total>1 else '')
            if index is not None:meta['e57_setup_index']=index
            append(s,'scan',meta,Path(path).stat().st_size if ordinal==1 else 0,bundle_id)
    return True
def snapshot(s):
    import registration_workspace as rw
    registered_cloud.reconcile_state(s.STATE)
    sync_active(s);rw.persist(s)
    candidates=[s.STATE.get('surface_result')]
    for stack in getattr(s,'WORKSPACE_HISTORY',{}).values():
        if isinstance(stack,list):
            for change in stack:
                if isinstance(change,dict):
                    candidates.extend(change.get(side,{}).get('surface_result') for side in ('before','after'))
    results={r.get('revision'):r for r in candidates if r}
    for entry in items(s):
        provenance=entry.get('cleaning_provenance') or {};result=results.get(provenance.get('revision'))
        if result and all(Path(result[k]).is_file() for k in ('point_ids_file','labels_file')):
            scan_edits.archive_cleaning(entry,result)
    groups=workspace_tools.inventory(s);entries=[];size=0;seen=set()
    for x in items(s):
        m=x['meta'];cache=0
        for k in ('file','attributes_file','mask_file','vertices_file','faces_file','vertex_colours_file','face_flags_file','preview_file'):
            if m.get(k) and m[k] not in seen and Path(m[k]).is_file():seen.add(m[k]);cache+=Path(m[k]).stat().st_size
        size+=cache
        props=scan_edits.display_properties(m) if x['kind']=='scan' else m
        if x['kind']=='scan' and props.get('count') and props.get('minimum') is not None:
            lo,hi=registration_pose.world_bounds({**m,**props})
            props={**props,'minimum':lo.tolist(),'maximum':hi.tolist()}
        groups=scan_edits.records(x) if x['kind']=='scan' else []
        counts=props.get('attribute_counts') or [0,0,0,0]
        entries.append({k:x.get(k) for k in ('id','kind','name','bundle_id','visible','display_mode','display_colour','source_bytes','position_status','registered_merge','merge_parent','merge_archived','merge_stale','merge_source_ids','merge_source_changes','locked')}|{
            'registration_rotation':m.get('registration_rotation'),'registration_translation':m.get('registration_translation'),
            'cleaned':bool(x.get('cleaning_provenance')),'count':props.get('count'),'original_count':m.get('count'),'excluded_count':m.get('count',0)-props.get('count',0) if props.get('count_exact',True) else None,'deleted_groups':[{**{k:g.get(k) for k in ('id','name','count','created')},'archived':bool(g.get('archive'))} for g in groups if g['active'] and g['kind']=='delete'],'limit_box':x.get('limit_box'),'limit_hide_outside':bool(x.get('limit_box') and x.get('limit_hide_outside',True)),'geometry_revision':scene_objects.source_revision(x) if x['kind']=='scan' else str(m.get('vertices_file') or '')+'|'+str(m.get('faces_file') or '')+'|'+str(m.get('mask_file') or ''),'vertices':m.get('vertices'),'triangles':m.get('triangles'),'degenerate_triangles':m.get('degenerate_triangles',0),'cache_bytes':cache,
            'count_exact':props.get('count_exact',True),'bounds_exact':props.get('bounds_exact',True),
            'metadata_pending':props.get('metadata_pending',False),'metadata_error':props.get('metadata_error'),
            'has_setup_view':__import__('setup_camera').has_pose(x),'has_setup_photos':bool(x.get('setup_photo')),
            'experimental_reconstruction':bool(m.get('experimental_reconstruction')),
            'rtc360_original_name':(m.get('raw_import') or {}).get('source_setup_name',((m.get('raw_import') or {}).get('acquisition_metadata') or {}).get('Name')),
            'rtc360_setup_id':(m.get('raw_import') or {}).get('setup_id'),
            'scan_origin':x.get('scan_origin'),
            'setup_revision':__import__('setup_camera').source_key(x) if __import__('setup_camera').has_pose(x) else None,
            'registration_summary':rw.scan_summary(s,x) if x['kind']=='scan' and not x.get('registered_merge') else None,
            'rgb_available':bool(len(counts)>=4 and min(counts[1:4])>0),'intensity_available':bool(counts and counts[0]>0),
            'hidden_regions':len(x.get('hidden_boxes',[])),'isolated':bool(x.get('isolate_box')),'edit_count':len(x.get('edit_history',[])),'redo_count':len(x.get('redo_history',[])),
            'minimum':props.get('minimum'),'maximum':props.get('maximum'),'mesh_summary':m.get('mesh_summary')})
    return {'cad_revision':__import__('cad_api').current(s)['revision'],'cad_context':__import__('cad_api').context(s),'bundles':workspace_tools.inventory(s),'items':entries,'objects':scene_objects.public(s),'cache_bytes':size,'free_bytes':shutil.disk_usage(s.WORK).free,'active_scan':s.STATE.get('active_job_scan'),'active_registration':s.STATE.get('active_job_registration'),'scan_count':sum(i['kind']=='scan' for i in entries),'mesh_count':sum(i['kind']=='mesh' for i in entries),'source_bytes':sum(i['source_bytes'] or 0 for i in entries),'data_folder':str(s.WORK)}

def _valid_selection(body):
    raw=body.get('selection',body.get('box'))
    return jobdata.selection_spec(raw)

def get(s,h,path,args):
    if path=='/api/explorer-delete-report':
        return h.send(__import__('explorer_deletion').report(s)) or True
    if path=='/api/compaction-preview':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        sync_active(s)
        return h.send(__import__('scan_compaction').preview(s,item(s,args.get('id',[''])[0]))) or True
    if path=='/api/mesh-to-cloud-estimate':
        if s.JOB['busy']:raise ValueError('Wait for the mesh operation to finish.')
        estimate=getattr(s,'MESH_TO_CLOUD_ESTIMATE',None)
        if estimate is None:raise ValueError('Estimate the sampled cloud first.')
        return h.send(estimate) or True
    if path=='/api/highlight-statistics':
        result=getattr(s,'HIGHLIGHT_STATS',None)
        if s.JOB['busy'] or result is None:raise ValueError('Count the highlighted points first.')
        return h.send(result) or True
    if path=='/api/compaction-report':return h.send(s.STATE.get('last_compaction') or {}) or True
    if path=='/api/deleted-preview':
        if s.JOB['busy']:raise ValueError('Wait for the operation to finish.')
        if args.get('all',['0'])[0]=='1':return h.send(scan_edits.deleted_preview_many(items(s))) or True
        if args.get('groups'):
            chosen=json.loads(args['groups'][0])
            if not isinstance(chosen,list) or any(not isinstance(p,list) or len(p)!=2 for p in chosen):raise ValueError('Select valid deleted groups.')
            return h.send(scan_edits.deleted_preview_many(items(s),chosen)) or True
        return h.send(scan_edits.deleted_preview(item(s,args.get('id',[''])[0]),args.get('group',[''])[0])) or True
    if __import__('scan_geometry').get(s,h,path,args):return True
    if path=='/api/job':return h.send(snapshot(s)) or True
    if path=='/api/job-preview':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        sync_active(s);return h.send(jobdata.preview(items(s),args.get('budget',[100000])[0])) or True
    if path=='/api/job-mesh-preview-bin':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        return h.send(jobdata.mesh_preview_binary(items(s)), 'application/octet-stream') or True
    if path=='/api/job-preview-bin':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        sync_active(s);preview_items=items(s)
        if args.get('setup_id'):
            preview_items=__import__('setup_camera').preview_items(s,args['setup_id'][0],args.get('setup_key',[''])[0])
        if args.get('live_limit',['0'])[0]=='1':
            preview_items=[{**x,'limit_hide_outside':False} for x in preview_items if x['kind']=='scan']
        view=json.loads(args['view'][0]) if args.get('view') else None
        import detail_preview
        if view is not None:detail_preview.validate_view(view)
        try:
            blob,_=jobdata.preview_binary(preview_items,args.get('detail',[25])[0],args.get('mode',['rgb'])[0],view,owners=args.get('owners',['0'])[0]=='1',quick=args.get('quick',['0'])[0]=='1',cancel=lambda:detail_preview.request_cancelled(h))
        except InterruptedError:
            h.close_connection=True
            return True
        return h.send(blob,'application/octet-stream') or True
    return False

def post(s,h,path,body):
    if path in ('/api/explorer-delete-review','/api/explorer-delete'):
        import explorer_deletion,workspace_history
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        sync_active(s)
        if path=='/api/explorer-delete-review':
            return h.send(explorer_deletion.review(s,body)) or True
        if body.get('confirmed') is not True:raise ValueError('Confirm deletion of the selected items.')
        checked=explorer_deletion.review(s,body)
        if body.get('revision')!=checked['revision']:raise ValueError('Project storage or selection changed. Review the deletion again.')
        if checked['history_cleared']:
            ctx=getattr(workspace_history.LOCAL,'context',None)
            if ctx:ctx['before']=None;ctx['label']=None;ctx['reset']=False
        CANCEL.clear()
        s.start_job(lambda:explorer_deletion.delete(s,{**body,'_in_worker':True}),'job')
        return h.send({'started':True}) or True
    if path=='/api/deleted-preview':
        if s.JOB['busy']:raise ValueError('Wait for the operation to finish.')
        chosen=None if body.get('all') is True else body.get('groups',[])
        if chosen is not None and (not isinstance(chosen,list) or any(not isinstance(p,list) or len(p)!=2 or any(not isinstance(v,str) for v in p) for p in chosen)):raise ValueError('Select valid deleted groups.')
        return h.send(scan_edits.deleted_preview_many(items(s),chosen)) or True
    if workspace_tools.post(s,h,path,body):return True
    if scene_objects.post(s,h,path,body):return True
    if __import__('scan_geometry').post(s,h,path,body):return True
    if path!='/api/job-action':return False
    action=body.get('action')
    if action=='cancel':
        if s.JOB.get('kind')!='job' or not s.JOB['busy']:raise ValueError('No job import or operation is running.')
        CANCEL.set()
        request=getattr(s,'JOB_CANCEL_FILE',None)
        if request is not None:request.write_text('cancel',encoding='utf8')
        h.send({'cancelling':True});return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
    CANCEL.clear();sync_active(s)
    def perform():
        if action=='import_paths':
            paths=body.get('paths',[]);bundle_id=workspace_tools.destination_bundle(s,body.get('bundle_id'))
            if not isinstance(paths,list) or not 1<=len(paths)<=200:raise ValueError('Choose 1–200 files.')
            for path in paths:
                p=Path(str(path).strip().strip('"')).expanduser();load(s,'job_mesh' if p.suffix.lower() in ('.obj','.zip','.3dr') else 'job_scan',p,bundle_id=bundle_id)
            s.progress('Import complete. File coordinates were preserved; registration was not changed.')
        elif action in ('limit_box','limit_visibility'):
            value=body.get('box');spec=jobdata.selection_spec({'mode':'box',**value}) if value else None
            enabled=body.get('hide_outside',True)
            if type(enabled) is not bool:raise ValueError('Hide outside must be on or off.')
            if spec and spec['mode'] not in ('box','oriented_box'):raise ValueError('Choose a 3D limit box.')
            if spec:spec.pop('clip_box',None);spec['invert']=False
            ids=body.get('ids',[]);targets=[item(s,i) for i in ids]
            old=copy.deepcopy(s.STATE)
            try:
                for x in targets:
                    if x['kind']=='scan':
                        if action=='limit_box':x['limit_box']=spec
                        x['limit_hide_outside']=enabled if x.get('limit_box') else False
                s.save()
            except Exception:s.STATE.clear();s.STATE.update(old);raise
        elif action=='remove_many':remove_items(s,body.get('ids'))
        elif action=='restore_deleted_selection':
            __import__('deleted_points').restore_selection(s,body.get('groups'),_valid_selection(body),lambda message:progress(s,message))
            return
        elif action=='delete_selection_many':
            result=scan_edits.delete_many(s,body.get('ids'),_valid_selection(body),str(body.get('reason') or 'Selected points'),lambda message:progress(s,message))
            s.LAST_POINT_DELETE=result
            return
        elif action in ('show_all','isolate_items'):
            registered_cloud.reconcile_state(s.STATE)
            ids=body.get('ids') if action=='isolate_items' else None
            if action=='isolate_items' and (not isinstance(ids,list) or not ids or not set(ids)<={x['id'] for x in items(s)}):
                raise ValueError('Select available scans or models to isolate.')
            registered_cloud.show_current(items(s),ids)
        elif action=='keep_active':
            source=body.get('source','survey')
            m=s.STATE.get('cloud') if source=='survey' else s.STATE.get('registration',{}).get('transformed') if source=='positioned' else s.STATE.get('registration',{}).get('cloud')
            if not m:raise ValueError('No scan is available from that workspace.')
            if any(i['kind']=='scan' and i['meta']['file']==m['file'] for i in items(s)):raise ValueError('This scan is already in the job.')
            meta=copy.deepcopy(m);jobdata.preview_cache(meta,s.WORK);x=append(s,'scan',meta)
            if source=='positioned':x['position_status']='Positioned using reviewed target transform'
            s.save()
        elif action=='compact_points_batch':
            import scan_compaction
            scan_compaction.compact_many(s,body.get('items'),body.get('confirmed') is True);return
        else:
            x=item(s,body.get('id'));m=x['meta']
            if action=='visibility':
                registered_cloud.set_visibility(items(s),x,body.get('visible',not x['visible']))
            elif action=='rename':
                name=str(body.get('name','')).strip()[:150]
                if not name:raise ValueError('Enter an item name.')
                x['name']=name;m['name']=name
                if m.get('source_format')=='rtc360_raw_experimental':x['rtc360_name_custom']=True
            elif action=='remove':
                remove_items(s,[x['id']]);return
            elif action=='duplicate':
                clone=copy.deepcopy(x);clone['id']=uuid.uuid4().hex;clone['name']=(str(x.get('name','Item'))+' — copy')[:150];clone['source_bytes']=0
                for key in ('merge_parent','merge_archived','merge_stale','registered_merge','merge_source_ids','merge_ranges','merge_registration_id','merge_source_revisions','merge_source_changes'):clone.pop(key,None)
                # Immutable full-density caches are shared; edit/view state is copied independently.
                for entry in clone.get('edit_history',[])+clone.get('redo_history',[]):entry.pop('separated_item',None)
                items(s).append(clone)
            elif action=='register':
                if x['kind']!='scan':raise ValueError('Choose a scan for registration.')
                __import__('registration_workspace').activate(s,x['id'],body.get('isolate',False))
            elif action=='surface':
                if x['kind']!='scan':raise ValueError('Choose a scan for surface extraction.')
                registered_cloud.require_current(x)
                s.STATE['surface_source']=copy.deepcopy(m)
                s.STATE['surface_source_item']=x['id']
                s.STATE['surface_job_selection']=jobdata.selection_spec(body['selection']) if body.get('selection') else None
            elif action=='analyse':
                if x['kind']!='scan' or s.STATE['axis'] is None:raise ValueError('Survey analysis needs a design alignment. Import the alignment CSV first, then try again.')
                if s.STATE.get('demo'):raise ValueError('Load your own alignment before analysing a real job scan.')
                if body.get('position_confirmed') is not True:raise ValueError('Confirm that this scan is in the alignment coordinate system and metres.')
                jobdata.room(s.WORK,m['count']*32);dest=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.analysis64')
                try:
                    progress(s,f"Preparing {x['name']} for analysis · {m['count']:,} source points")
                    derived,count=project_cloud(m,s.STATE['axis'],dest,lambda msg:progress(s,msg))
                    if not count:raise ValueError('No points fall within the alignment. Check units, positioning and extent.')
                    scan_edits.record_projection(m,derived,count)
                except Exception:dest.unlink(missing_ok=True);raise
                s.STATE.update(cloud=copy.deepcopy(m),derived=derived,valid_points=count,joints=None,active_job_scan=x['id'],edits=copy.deepcopy(x.get('edit_history',[])),redo=copy.deepcopy(x.get('redo_history',[])),audit=[],report_ready=False,clean_ready=False,export_ready=None);s.SELECTION=None
            elif action=='compact_points':
                import scan_compaction
                scan_compaction.compact(s,x,body.get('confirmed') is True,body.get('revision'));return
            elif action in ('mesh_to_cloud_estimate','mesh_to_cloud'):
                if x['kind']!='mesh':raise ValueError('Select a triangle mesh to sample.')
                import mesh_to_cloud
                if action=='mesh_to_cloud_estimate':
                    estimate=mesh_to_cloud.estimate(m,body,lambda message:progress(s,message))
                    s.MESH_TO_CLOUD_ESTIMATE={**estimate,'source_id':x['id'],'source_name':x['name']}
                    return
                prepared=getattr(s,'MESH_TO_CLOUD_ESTIMATE',None)
                if not prepared or prepared.get('source_id')!=x['id'] or body.get('estimate_revision')!=prepared.get('revision'):
                    raise ValueError('Estimate this mesh and its sampling settings before creating the cloud.')
                previous=copy.deepcopy(s.STATE);newmeta=None
                try:
                    newmeta=mesh_to_cloud.sample(m,s.WORK,body,body.get('name') or (x['name']+' · sampled cloud'),lambda message:progress(s,message),prepared)
                    newmeta['source_mesh_id']=x['id']
                    created=append(s,'scan',newmeta,bundle_id=x.get('bundle_id'))
                except Exception:
                    s.STATE.clear();s.STATE.update(previous)
                    if newmeta:jobdata.remove_assets(newmeta)
                    raise
                s.MESH_TO_CLOUD_ESTIMATE={**prepared,'created_item_id':created['id'],'created_points':newmeta['count']}
                return
            elif action in ('delete_box','separate_box','delete_selection','separate_selection','restore_deleted','undo_edit','redo_edit','copy_selection'):
                if x['kind']!='scan':raise ValueError('Select a scan item first.')
                op={'delete_box':'delete_selection','separate_box':'separate_selection'}.get(action,action)
                selection=_valid_selection(body) if op in ('delete_selection','separate_selection','copy_selection') else None
                if selection and x.get('limit_box') and x.get('limit_hide_outside',True):selection['clip_box']=x['limit_box']
                if op=='restore_deleted' and scan_edits.record(x,body.get('group_id')).get('archive'):
                    scan_edits.restore_archive(s,x,scan_edits.record(x,body.get('group_id')),lambda msg:progress(s,msg));return
                if op=='copy_selection':
                    newmeta=jobdata.extract_selection(m,selection,s.WORK,x['name']+' · copy',lambda msg:progress(s,msg))
                    old=copy.deepcopy(s.STATE)
                    try:append(s,'scan',newmeta)
                    except Exception:s.STATE.clear();s.STATE.update(old);jobdata.remove_assets(newmeta);raise
                else:
                    try:scan_edits.apply(s,x,op,selection,body.get('group_id'),str(body.get('reason') or ('Moved to separated scan' if op=='separate_selection' else 'Selected points')),progress=lambda msg:progress(s,msg))
                    except ValueError as error:
                        if op in ('delete_selection','separate_selection') and str(error) in ('No retained points in this selection.','The selected area contains no retained full-density points.'):
                            s.progress('No retained points inside this selection for '+x['name']+'; scan unchanged.')
                        else:raise
                return
            elif action in ('hide_box','isolate_box','clear_view','hide_selection','isolate_selection'):
                if x['kind']!='scan':raise ValueError('Select a scan item first.')
                selection=_valid_selection(body) if action!='clear_view' else None
                if action in ('hide_box','hide_selection'):
                    if len(x.setdefault('hidden_boxes',[]))>=50:raise ValueError('Too many hidden areas. Use Show all before hiding more regions.')
                    x['hidden_boxes'].append(selection);x['isolate_box']=None
                elif action in ('isolate_box','isolate_selection'):x['isolate_box']=selection
                else:x['hidden_boxes']=[];x['isolate_box']=None
            else:raise ValueError('Unknown job action.')
            s.save()
    native=action in ('compact_points','compact_points_batch','analyse','import_paths','separate_box','separate_selection','restore_deleted','restore_deleted_selection','copy_selection','mesh_to_cloud_estimate','mesh_to_cloud')
    # Keep compatibility with lightweight/test Session shims that implement the
    # original two-argument start_job signature.
    if native:s.start_job(perform,'job',native_progress=True)
    else:s.start_job(perform,'job')
    h.send({'started':True});return True
