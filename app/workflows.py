# Copyright (c) 2026 Damian Gulczynski.
"""Project, registration and exchange workflows for the local server."""
from project_data_layout import folder as asset_folder
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
import copy, csv, io, json, os, shutil, time, uuid, hashlib, workspace_tools
import numpy as np
import registration_workspace, leica_project, setup_view, setup_photos, setup_camera, scan_export, ring_creep
import projects, registration, pointio, exchange, surface_api, job_api, jobdata, scan_edits, target_ops, session_start, workspace_history
from core import Alignment, project_cloud


def empty():
    return {'axis':None,'cloud':None,'derived':None,'rings':[],'demo':False,'valid_points':0,'joints':None,'edits':[],'redo':[],'audit':[],'report_ready':False,'clean_ready':False,'report_settings':{},'structures':[],'registration':{},'project_id':None,'project_title':'Untitled project','export_ready':None,'ui':{}}


def serial(s, *, compact_history=False):
    # Preparing a save/open must not mutate the active workspace. In particular,
    # persisting target edits used to modify it before a failed open was known.
    staged=SimpleNamespace(STATE=copy.deepcopy(s.STATE))
    job_api.sync_active(staged);registration_workspace.persist(staged)
    if compact_history:scan_edits.compact_project_history(staged.STATE)
    if staged.STATE.get('inspection_result'):
        import inspection_api
        staged.STATE['inspection_result']=inspection_api.current(staged)
    d=staged.STATE;a=d.get('axis');d['axis']={'rows':a.a.tolist(),'name':a.name} if a else None
    return json.loads(json.dumps(d,allow_nan=False))


def project_location(s):
    work = Path(s.WORK).resolve()
    ident = s.STATE.get('project_id')
    project_file = None
    if isinstance(ident, str) and len(ident) == 32 and all(c in '0123456789abcdef' for c in ident):
        path = work / 'projects' / (ident + '.json')
        if path.is_file():
            project_file = str(path)
    try:
        free_bytes = shutil.disk_usage(work).free
    except OSError:
        free_bytes = None
    return {'work_folder': str(work), 'project_folder': str(work / 'projects'),
            'project_file': project_file, 'free_bytes': free_bytes,
            'last_copy': getattr(s, 'LAST_PROJECT_COPY', None)}


def save_portable_in(s, body):
    destination = scan_export.destination(s, body.get('destination', ''), 'tstudio')
    before = scan_export.fingerprint(destination)
    reviewed = body.get('replace_version')
    if before is not None and reviewed != before:
        return {'needs_overwrite': True, 'destination': str(destination), 'replace_version': before}
    if reviewed is not None and before is None:
        raise ValueError('The reviewed destination no longer exists. Choose the destination again.')
    copy_id = uuid.uuid4().hex

    def write():
        snapshot = serial(s, compact_history=True)
        if isinstance(body.get('ui'), dict):
            snapshot['ui'] = copy.deepcopy(body['ui'])
        name = str(body.get('name') or snapshot.get('project_title') or destination.stem).strip()[:120]
        snapshot['project_title'] = name or destination.stem
        projects.portable(s.WORK, snapshot, destination, s.progress,
                          publish=lambda temp, dest: scan_export.publish_file(temp, dest, before))
        s.LAST_PROJECT_COPY = {'id': copy_id, 'destination': str(destination)}
        try:
            projects.atomic_json(Path(s.WORK) / 'project_save_preferences.json',
                                 {'last_folder': str(destination.parent)})
        except OSError:
            pass  # The copy already committed. Remembering a folder is optional.

    s.start_job(write, 'project_copy')
    return {'started': True, 'id': copy_id, 'destination': str(destination)}


class _Recovery:
    """Validate immutable references without reading point assets into RAM.

    Missing current masks/geometry quarantine their owner; optional display
    assets can be removed with a visible warning. Original unavailable metadata
    stays in project_recovery, including in a subsequently saved portable copy.
    """
    def __init__(self,s,data):
        self.s=s;self.root=Path(s.WORK).resolve();self.memo={}
        previous=data.get('project_recovery') or {}
        if not isinstance(previous,dict):previous={}
        self.report=copy.deepcopy(previous)
        self.report.setdefault('warnings',[]);self.report.setdefault('unavailable',[])
        for text in (data.get('_project_import') or {}).get('warnings',[]):self.warn(text)

    def warn(self,text):
        text=str(text)
        if text not in self.report['warnings']:self.report['warnings'].append(text)

    def hold(self,kind,name,value,reason):
        if isinstance(reason,jobdata.Cancelled):raise reason
        message=str(name)+': '+str(reason)
        self.warn(message)
        self.report['unavailable'].append(dict(kind=kind,name=str(name),reason=str(reason),original_data_json=json.dumps(value,allow_nan=False)))

    def path(self,value):
        if not isinstance(value,str) or not value:raise ValueError('A linked cache path is missing.')
        if value in self.memo:return self.memo[value]
        p=Path(value).resolve()
        if not p.is_relative_to(self.root) or not p.is_file():
            # Named legacy snapshots may carry a previous computer's cache root.
            # Never follow that external path; only a same-name local asset is
            # eligible. Portable archives use their stricter unique-member map.
            name=PureWindowsPath(value).name if '\\' in value else Path(value).name
            alternate=self.root/name
            if not p.is_relative_to(self.root) and alternate.is_file():
                p=alternate.resolve();self.warn('Relinked a legacy local cache: '+name)
            else:raise ValueError('Missing linked cache: '+value)
        if not p.is_relative_to(self.root):raise ValueError('Project cache is outside the workspace: '+value)
        self.memo[value]=str(p);return str(p)

    def cache(self,value,n,width):
        if type(n) is not int or n<1:raise ValueError('Invalid cache point/face count.')
        p=self.path(value)
        if Path(p).stat().st_size!=n*width:raise ValueError('Damaged cache size: '+str(value))
        return p

    def paths(self,value):return projects.visit(value,self.path)

    def meta(self,value,mesh=False,name='Scan'):
        if not isinstance(value,dict):raise ValueError('Invalid scan/model metadata.')
        m=copy.deepcopy(value)
        required=[('vertices_file','vertices',24),('faces_file','triangles',12)] if mesh else [('file','count',24)]
        for key,count,width in required:m[key]=self.cache(m.get(key),m.get(count),width)
        try:
            low=np.asarray(m['minimum'],float);high=np.asarray(m['maximum'],float)
            bounds_ok=low.shape==(3,) and high.shape==(3,) and np.isfinite([low,high]).all() and bool(np.all(low<=high))
        except (KeyError,TypeError,ValueError):bounds_ok=False
        if not bounds_ok:
            # Older cache metadata can lack bounds. Rebuild them from every
            # preserved coordinate in fixed blocks, without a whole-file map.
            low=np.full(3,np.inf);high=-low;read=0;total=m['vertices'] if mesh else m['count']
            with open(m['vertices_file'] if mesh else m['file'],'rb') as source:
                while read<total:
                    points=np.fromfile(source,'<f8',count=min(100000,total-read)*3).reshape(-1,3)
                    if not len(points) or not np.isfinite(points).all():raise ValueError('Invalid coordinates while rebuilding missing geometry bounds.')
                    low=np.minimum(low,points.min(0));high=np.maximum(high,points.max(0));read+=len(points)
                    self.s.progress(f'Recovering saved geometry bounds · {read:,}/{total:,} points')
            m['minimum']=low.tolist();m['maximum']=high.tolist();self.warn(name+': rebuilt missing or invalid geometry bounds from all preserved coordinates.')
        n=m.get('triangles') if mesh else m.get('count')
        for key in ('mask_file','face_flags_file'):
            if m.get(key):m[key]=self.cache(m[key],n,1)
        optional=[('preview_file',m.get('preview_count'),24),('attributes_file',m.get('count'),16),('truth_file',m.get('count'),1),('vertex_colours_file',m.get('vertices'),12)]
        for key,count,width in optional:
            if not m.get(key):continue
            try:
                m[key]=self.cache(m[key],count,width)
                if key=='preview_file' and (type(m.get('preview_stride')) is not int or m['preview_stride']<1):raise ValueError('Invalid preview stride.')
            except (OSError,ValueError) as exc:
                self.hold('optional_asset',name+' · '+key,{key:m[key]},exc);m.pop(key,None)
                if key=='preview_file':m.pop('preview_count',None);m.pop('preview_stride',None)
                elif key=='attributes_file':
                    m['attribute_counts']=[0,0,0,0]
                    if isinstance(m.get('retained_properties'),dict):m['retained_properties']['attribute_counts']=[0,0,0,0]
                elif key=='vertex_colours_file':m.pop('coloured_vertices',None)
        for cache_key in ('retained_properties','retained_summary'):
            props=m.get(cache_key)
            if isinstance(props,dict):
                if props.get('revision')==(value.get('mask_file') or ''):props['revision']=m.get('mask_file') or ''
                elif props.get('revision')!=(m.get('mask_file') or ''):m.pop(cache_key,None);props=None
            if isinstance(props,dict) and props.get('derived'):
                try:props['derived']=self.cache(props['derived'],m.get('count'),32)
                except (OSError,ValueError):
                    for key in ('derived','valid_points','derived_signature','analysis_range'):props.pop(key,None)
        m.setdefault('name',str(name))
        return m

    def history(self,values,n,name):
        if not isinstance(values,list):
            self.hold('history',name,values,'Invalid edit history. Current point edits are retained.');return []
        out=[]
        for value in values:
            try:
                v=self.paths(value)
                def masks(x,current=n):
                    if isinstance(x,dict):
                        if x.get('kind') in ('scan','mesh') and isinstance(x.get('meta'),dict):
                            # A separation stores the child scene item inside
                            # its parent operation for redo. Child edits address
                            # the child's own coordinate cache and mask length.
                            child_mesh=x['kind']=='mesh';x['meta']=self.meta(x['meta'],child_mesh,name+' · separated item')
                            current=x['meta']['triangles' if child_mesh else 'count']
                        for key,y in x.items():
                            if key in ('before_mask','after_mask') and y:self.cache(y,current,1)
                            else:masks(y,current)
                        if 'group_change' in x:scan_edits.group_change(x)
                    elif isinstance(x,list):
                        for y in x:masks(y,current)
                masks(v)
                if isinstance(v,dict) and v.get('archive'):
                    a=v['archive'];a['source_meta']=self.meta(a['source_meta'],name=name+' · archived source')
                    self.cache(a.get('point_ids_file'),a.get('input_count'),8);self.cache(a.get('labels_file'),a.get('input_count'),4)
                out.append(v)
            except (OSError,ValueError,TypeError,KeyError) as exc:
                self.hold('history',name,value,str(exc)+' Current point edits are retained; this history operation is unavailable.')
                # Undo/redo is a chain. Do not skip over a broken transition and
                # offer an older jump that could replace unrelated later edits.
                if out and 'deleted_groups' not in name:
                    self.hold('history',name+' · earlier operations',out,'The later missing transition makes this part of the undo/redo chain unavailable.');out=[]
        return out

    def registration(self,value,name='Registration'):
        if not isinstance(value,dict):
            self.hold('registration',name,value,'Invalid registration metadata.');return {}
        out=copy.deepcopy(value)
        for key in ('cloud','transformed'):
            if out.get(key):
                try:out[key]=self.meta(out[key],name=name+' · '+key)
                except (OSError,ValueError,KeyError,TypeError) as exc:self.hold('registration_asset',name+' · '+key,out[key],exc);out.pop(key,None)
            else:out.pop(key,None)
        return out


def prepare_data(s,data):
    """Build a complete recoverable candidate before changing active state."""
    if not isinstance(data,dict):raise ValueError('Project metadata must be an object.')
    original=copy.deepcopy(data);r=_Recovery(s,original);d=empty();d.update(copy.deepcopy(original))
    d.pop('_project_import',None);d.pop('last_backup',None)
    expected=bool(d.get('axis') or d.get('cloud') or d.get('job_items') or d.get('surface_source') or d.get('surface_result') or (d.get('registration') or {}).get('cloud'))
    if d.get('axis'):
        try:
            a=d['axis'];rows=a['rows'];rows=[row if isinstance(row,dict) else dict(zip(['chainage','e','n','h','radius'],row)) for row in rows]
            d['axis']=Alignment(rows,str(a.get('name') or 'Imported alignment'))
        except (ValueError,KeyError,TypeError,IndexError) as exc:r.hold('alignment','Alignment',d['axis'],exc);d['axis']=None
    raw=d.get('job_items',[])
    if not isinstance(raw,list):raise ValueError('The project scan/model inventory is damaged.')
    entries=[];seen=set()
    for index,item in enumerate(raw):
        name=item.get('name','Item '+str(index+1)) if isinstance(item,dict) else 'Item '+str(index+1)
        try:
            if not isinstance(item,dict):raise ValueError('Invalid scene item.')
            x=copy.deepcopy(item);kind=x.get('kind') or ('mesh' if 'vertices_file' in x.get('meta',{}) else 'scan')
            if kind not in ('scan','mesh'):raise ValueError('Unknown scene item type.')
            x['kind']=kind;x['meta']=r.meta(x['meta'],kind=='mesh',name)
            if not x.get('id') or x['id'] in seen:
                x['id']=uuid.uuid4().hex;r.warn(name+': assigned a unique scene identity while upgrading the legacy inventory.')
            seen.add(x['id']);x.setdefault('name',name);x.setdefault('visible',True)
            x.setdefault('source_bytes',0);x.setdefault('hidden_boxes',[]);x.setdefault('isolate_box',None)
            for key in ('edit_history','redo_history','deleted_groups'):
                if key in x:x[key]=r.history(x[key],x['meta'].get('count',x['meta'].get('triangles')),name+' · '+key)
            if x.get('setup_photo'):
                try:x['setup_photo']=r.paths(x['setup_photo'])
                except (OSError,ValueError) as exc:r.hold('setup_photo',name+' · setup photo',x.pop('setup_photo'),exc);x['photo_notice']='The saved setup photo is unavailable; scan geometry remains available.'
            if x.get('registration_state'):x['registration_state']=r.registration(x['registration_state'],name+' · registration')
            # Other linked features such as per-item cleaning archives cannot be
            # left dangling: quarantine only that optional feature, not geometry.
            for key in list(x):
                if key in ('meta','edit_history','redo_history','deleted_groups','registration_state'):continue
                try:x[key]=r.paths(x[key])
                except (OSError,ValueError) as exc:r.hold('item_metadata',name+' · '+key,x.pop(key),exc)
            entries.append(x)
        except (OSError,ValueError,KeyError,TypeError) as exc:r.hold('scene_item',name,item,exc)
    d['job_items']=entries
    d['registration']=r.registration(d.get('registration') or {})
    for key in ('cloud','surface_source'):
        if d.get(key):
            try:d[key]=r.meta(d[key],name=key)
            except (OSError,ValueError,KeyError,TypeError) as exc:r.hold('scan',key,d[key],exc);d[key]=None
    if d.get('cloud'):
        d['edits']=r.history(d.get('edits',[]),d['cloud']['count'],'Survey edits')
        d['redo']=r.history(d.get('redo',[]),d['cloud']['count'],'Survey redo')
        try:
            if d['axis'] is None:raise ValueError('No usable alignment is available.')
            d['derived']=r.cache(d.get('derived'),d['cloud']['count'],32)
        except (OSError,ValueError) as exc:
            meta=d['cloud'];match=next((x for x in entries if x['kind']=='scan' and x['meta']['file']==meta['file'] and x['meta'].get('mask_file')==meta.get('mask_file')),None)
            if match is None:
                match=dict(id=uuid.uuid4().hex,kind='scan',name=meta.get('name','Recovered survey scan'),meta=copy.deepcopy(meta),visible=True,source_bytes=0,hidden_boxes=[],isolate_box=None,edit_history=d['edits'],redo_history=d['redo']);entries.append(match)
            elif d.get('active_job_scan')==match['id'] or not match.get('edit_history'):
                match['edit_history']=copy.deepcopy(d['edits']);match['redo_history']=copy.deepcopy(d['redo'])
            r.warn('Survey analysis is unavailable: '+str(exc)+' The full scan and current edits remain in Scans & Models; select an alignment and analyse it again.')
            if d.get('derived'):r.hold('analysis','Survey analysis cache',{'derived':d['derived']},exc)
            d.update(cloud=None,derived=None,active_job_scan=None,valid_points=0,joints=None,edits=[],redo=[])
    else:
        if d.get('edits') or d.get('redo'):r.hold('history','Survey edit history',{'edits':d.get('edits',[]),'redo':d.get('redo',[])},'The associated active survey scan is unavailable. Per-scan edits on recovered scans are retained.')
        d.update(derived=None,active_job_scan=None,valid_points=0,edits=[],redo=[])
    surf=d.get('surface_result')
    if surf:
        try:
            surf=copy.deepcopy(surf);surf['source_meta']=r.meta(surf['source_meta'],name='Cleaning source')
            n=surf['statistics']['input_points']
            if type(n) is not int or not 1<=n<=surf['source_meta']['count']:raise ValueError('Invalid cleaning result point count.')
            for key,width in (('point_ids_file',8),('labels_file',4)):surf[key]=r.cache(surf.get(key),n,width)
            from project_validation import validate_surface
            validate_surface(surf,s.WORK,s.progress)
            outputs={}
            for key,value in surf.get('outputs',{}).items():
                try:outputs[key]=r.meta(value,name='Cleaning export · '+key)
                except (OSError,ValueError,TypeError,KeyError) as exc:r.hold('cleaning_output',key,value,str(exc)+' This output can be exported again from the retained reviewed result.')
            surf['outputs']=outputs
            if surf.get('automatic'):
                try:
                    auto=surf['automatic'];auto['labels_file']=r.cache(auto.get('labels_file'),n,4)
                    valid={-1,*[c['id'] for c in auto['clusters']]}
                    if not set(auto['kept'])<=valid:raise ValueError('Invalid automatic cleaning cluster labels.')
                    if auto['labels_file']!=surf['labels_file'] or auto['clusters']!=surf['clusters']:validate_surface({**surf,**auto},s.WORK,s.progress)
                except (OSError,ValueError,TypeError,KeyError) as exc:r.hold('cleaning_reset','Automatic cleaning reset',surf.pop('automatic'),str(exc)+' Current reviewed labels remain available; automatic reset is disabled.')
            d['surface_result']=surf
        except (OSError,ValueError,KeyError,TypeError,OverflowError) as exc:r.hold('cleaning_result','Cleaning/classification result',d['surface_result'],exc);d['surface_result']=None
    valid_ids={x['id'] for x in entries}
    for key in ('active_job_scan','active_job_registration','surface_source_item'):
        if d.get(key) not in valid_ids:d[key]=None
    good_links=[]
    for link in d.get('scan_links',[]):
        if isinstance(link,dict) and link.get('a') in valid_ids and link.get('b') in valid_ids:good_links.append(link)
        else:r.hold('registration_link','Registration link',link,'A referenced scan is unavailable. Stored target coordinates are retained.')
    d['scan_links']=good_links
    if d.get('scan_link_draft') and not set(d['scan_link_draft'].get('moving',[])+d['scan_link_draft'].get('fixed',[]))<=valid_ids:
        r.hold('registration_draft','Registration draft',d.pop('scan_link_draft'),'A referenced scan is unavailable.')
    # PDF Drawing sources are legacy optional attachments. CAD geometry and
    # profile snapshots, including pdf_trace provenance, remain independent.
    for key in list(d):
        if key in ('axis','job_items','cloud','surface_source','surface_result','registration','project_recovery','edits','redo','derived'):continue
        if key in ('export_ready','last_backup'):d[key]=None;continue
        try:d[key]=r.paths(d[key])
        except (OSError,ValueError) as exc:
            r.hold('project_feature',key,d[key],exc)
            d[key]=[] if isinstance(d[key],list) else {} if isinstance(d[key],dict) else None
    usable=bool(d.get('axis') or entries or d.get('surface_source') or d.get('surface_result') or d.get('cad') or d.get('structures') or d.get('tunnel_design') or (d.get('registration') or {}).get('cloud') or (d.get('registration') or {}).get('rows'))
    if expected and not usable:raise ValueError('This project has no usable linked scans, models or alignment. The active project has been kept unchanged. Restore the missing caches or open a complete portable backup. '+('; '.join(r.report['warnings'][:3])))
    if r.report['warnings'] or r.report['unavailable']:
        r.report['opened_at']=time.time();d['project_recovery']=r.report
    if d.get('inspection_result'):
        import inspection_api
        inspection_api.restored(d,portable=bool(original.get('_project_import')))
    __import__('rtc360_names').normalize(d)
    __import__('registered_cloud').reconcile_state(d)
    d.update(report_ready=False,clean_ready=False,export_ready=None)
    return d


def _commit_project(s,candidate,reset_view=True):
    """Install a candidate and its disk index together, rolling back on failure."""
    encoded=dict(candidate);axis=encoded.get('axis');encoded['axis']={'rows':axis.a.tolist(),'name':axis.name} if axis else None
    projects.validate_metadata(encoded)
    previous=dict(s.STATE);selection=getattr(s,'SELECTION',None);cad=getattr(s,'_cad_context_key',None)
    index=Path(s.WORK)/'project.json';backup=Path(s.WORK)/('project_switch_'+uuid.uuid4().hex+'.bak')
    existed=index.is_file()
    if existed:shutil.copyfile(index,backup)
    try:
        s.STATE.clear();s.STATE.update(candidate)
        try:s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(previous);s.SELECTION=selection;s._cad_context_key=cad
            if existed:os.replace(backup,index)
            else:index.unlink(missing_ok=True)
            raise
        if reset_view:
            s._cad_context_key=None;s.SELECTION=None
            if hasattr(s,'JOB'):s.JOB['error']=None;s.JOB['cancelled']=False
    finally:backup.unlink(missing_ok=True)


def _save_previous(s,ui=None):
    if session_start.has_work(s.STATE):
        previous=serial(s)
        if isinstance(ui,dict):previous['ui']=copy.deepcopy(ui)
        # New/Open and portable imports preserve unsaved work as hidden recovery,
        # not as another normal named project. Explicit Save As owns new IDs.
        # Do not overwrite the named snapshot that the user is about to open.
        folder=Path(s.WORK)/'startup_recovery';folder.mkdir(parents=True,exist_ok=True)
        recovery=folder/('workspace_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.json')
        projects.atomic_json(recovery,previous)
        session_start._prune_hidden_recovery(folder,recovery)


def restore_data(s,data,save_current=False,ui=None):
    try:
        candidate=prepare_data(s,data)
        s.progress('Project checked · preparing the saved workspace')
        if save_current:_save_previous(s,ui)
        s.progress('Project checked · committing the saved workspace')
        _commit_project(s,candidate)
    except BaseException:
        projects.cleanup_import(s.WORK,data);raise
    projects.release_import(data)
    return candidate.get('project_recovery',{})


def load(s,kind,path,display_name=None):
    if job_api.load(s,kind,path,display_name):return True
    if surface_api.load(s,kind,path): return True
    if kind=='registration':
        source_path=Path(path)
        if source_path.suffix.lower() in ('.e57','.hspca'):
            # Keep E57/native reader work outside the desktop process. Some vendor
            # E57 files can spend minutes in native code and previously blanked the
            # WebView even though the Windows shell itself was still alive.
            parts=jobdata.scan_parts(source_path,s.progress);expected=sum(n for _,n in parts if n)
            meta=jobdata.import_scan(source_path,s.WORK,index=None,expected=expected or None,progress=s.progress)
        else:
            meta=pointio.import_cloud(source_path,asset_folder(s.WORK,'imported')/(uuid.uuid4().hex+'.xyz64'),s.progress)
        s.STATE['active_job_registration']=None;s.STATE['registration']={'cloud':meta,'candidates':[],'rows':[],'solution':None,'transformed':None,'revision':uuid.uuid4().hex}; s.save(); return True
    if kind=='project':
        data=projects.unpack(s.WORK,path,s.progress); restore_data(s,data,save_current=True); return True
    return False


def survey_registration_unavailable(s,r):
    if s.STATE['axis'] is None:
        return 'Import an alignment to use the positioned scan for survey analysis.'
    if s.STATE.get('demo') and not r.get('demo'):
        return 'Import your real alignment to use this scan for survey analysis.'
    return ''


def reg(s,cloud=True):
    r=s.STATE.setdefault('registration',{})
    if cloud and not r.get('cloud'): raise ValueError('Import a source scan in Registration first.')
    registration_workspace.include_candidates(r,controls=s.STATE.get('survey_controls',{}))
    return r


def get(s,h,path,args):
    if path=='/api/project-shapefiles':
        from project_shapefiles import listing
        h.send(listing(s));return True
    if path=='/api/workspace-folders':
        h.send(__import__('workspace_folders').request(s));return True
    if path=='/api/storage-inventory':
        from storage_catalog import catalog
        h.send(catalog.scan(s.WORK,workspace_history.storage_state(s)));return True
    if path=='/api/storage-inventory-files':
        from storage_catalog import catalog
        h.send(catalog.page(s.WORK,lambda:workspace_history.storage_state(s),args.get('token',[''])[0],
                            args.get('q',[''])[0],int(args.get('offset',['0'])[0]),int(args.get('limit',['50'])[0])));return True
    if path=='/api/project-location':
        h.send(project_location(s));return True
    if path=='/api/tunnel-monitoring' and __import__('tunnel_monitoring').api(s,h,path):return True
    if setup_camera.get(s,h,path,args):return True
    if setup_photos.get(s,h,path,args):return True
    if registration_workspace.api(s,h,path) or setup_view.api(s,h,path) or leica_project.api(s,h,path):return True
    if job_api.get(s,h,path,args):return True
    if surface_api.get(s,h,path,args): return True
    if path=='/api/projects':
        projects.resume_storage_purges(s.WORK)
        return h.send({'projects':projects.projects_storage_overview(s.WORK,workspace_history.storage_state(s)),'purge':projects.storage_purge_status(s.WORK)}) or True
    if path=='/api/workspace-storage':
        projects.resume_storage_purges(s.WORK)
        return h.send(projects.workspace_storage_overview(s.WORK,s.STATE)|{'purge':projects.storage_purge_status(s.WORK)}) or True
    if path=='/api/project-storage':
        raw=args.get('ids',[''])[0] or args.get('id',[''])[0]
        ids=[x for x in raw.split(',') if x]
        review=projects.projects_storage_review(s.WORK,ids,workspace_history.storage_state(s));review.pop('exclusive_paths',None);h.send(review);return True
    if path=='/api/project-orphans':
        review=projects.orphan_storage_review(s.WORK,workspace_history.storage_state(s));review.pop('paths',None);h.send(review);return True
    if path=='/api/project-purge':
        projects.resume_storage_purges(s.WORK);h.send(projects.storage_purge_status(s.WORK));return True
    if path=='/api/project-recovery':
        report=s.STATE.get('project_recovery') or {}
        warnings=report.get('warnings',[])
        unavailable=[{k:v for k,v in entry.items() if k!='original_data_json'} for entry in report.get('unavailable',[])]
        needs_review=bool(unavailable or any(note not in projects.PROJECT_FORMAT_NOTICES for note in warnings))
        h.send({'warnings':warnings,'unavailable':unavailable,'needs_review':needs_review});return True
    if path=='/api/registration':
        r=reg(s,False); result={k:v for k,v in r.items() if k not in ('cloud','transformed','target_undo')}
        result['cloud']={k:v for k,v in r['cloud'].items() if k not in ('file','attributes_file','mask_file')} if r.get('cloud') else None
        result['source_id']=Path(r['cloud']['file']).stem if r.get('cloud') else None
        ident=s.STATE.get('active_job_registration')
        if ident and r.get('cloud') and s.STATE.get('survey_controls'):
            _,result['control_matches'],_=registration_workspace.control_group(s,ident)
        result['survey_analysis_unavailable']=survey_registration_unavailable(s,r)
        result['source_item_id']=ident;result['can_undo_targets']=bool(r.get('target_undo')); result['transformed']=bool(r.get('transformed')); h.send(result); return True
    if path=='/api/reg-preview':
        if s.JOB['busy']: raise ValueError('Wait until the current operation finishes.')
        r=reg(s); h.send(registration.preview(r['cloud'],r.get('solution') if args.get('positioned',['0'])[0]=='1' else None,args.get('channel',['intensity'])[0])); return True
    if path=='/api/export-file':
        item=scan_export.current(s,args.get('id',[''])[0])
        h.send_file(Path(item['file']),'application/octet-stream',item['name']); return True
    if path=='/api/registration-report':
        r=reg(s); solution=r.get('solution')
        if not solution: raise ValueError('Solve registration first.')
        if args.get('format',['json'])[0]=='csv':
            out=io.StringIO(); w=csv.writer(out); w.writerow(['id','role','source_x_m','source_y_m','source_z_m','control_e_m','control_n_m','control_h_m','residual_e_mm','residual_n_mm','residual_h_mm','error_3d_mm'])
            for row in solution['residuals']: w.writerow([row['id'],row['role'],*row['source'],*row['control'],*row['residual_mm'],row['error_mm']])
            h.send(out.getvalue(),'text/csv',filename='target_residuals.csv')
        else: h.send(solution,filename='registration_transform.json')
        return True
    return False


def post(s,h,path,body):
    if path=='/api/project-browser':
        h.send(__import__('project_browser').request(s,body));return True
    if path=='/api/storage-cleanup':
        import storage_cleanup
        h.send(storage_cleanup.request(s,body));return True
    if path=='/api/project-shapefile':
        from project_shapefiles import post as archive_shapefile
        return archive_shapefile(s,h,body)
    if path=='/api/project-folder-copy':
        import project_folder_copy
        h.send(project_folder_copy.request(s,body));return True
    if path=='/api/workspace-folders':
        h.send(__import__('workspace_folders').request(s,body));return True
    if path=='/api/open-data-folder':
        if body != {}:
            raise ValueError('Open data folder only opens the active workspace.')
        from startup_support import reveal_data_folder
        h.send(reveal_data_folder(s.WORK))
        return True
    if path=='/api/tunnel-monitoring' and __import__('tunnel_monitoring').api(s,h,path,body):return True
    if ring_creep.api(s,h,path,body):return True
    if path=='/api/export-destination':
        dest=scan_export.destination(s,body.get('destination',''),body.get('format'))
        h.send({'destination':str(dest)});return True
    if path=='/api/export-save':
        h.send(scan_export.save(s,body));return True
    if setup_camera.api(s,h,path,body):return True
    if setup_photos.api(s,h,path,body):return True
    if registration_workspace.api(s,h,path,body) or leica_project.api(s,h,path,body) or setup_view.api(s,h,path,body):return True
    if job_api.post(s,h,path,body):return True
    if surface_api.post(s,h,path,body): return True
    if path not in ('/api/project-action','/api/registration-action','/api/export'): return False
    if s.JOB['busy']: raise ValueError('Wait until the current operation finishes.')
    if path=='/api/project-action':
        action=body['action']
        if action in ('save','save_as'):
            candidate=copy.deepcopy(s.STATE)
            staged=SimpleNamespace(STATE=candidate)
            job_api.sync_active(staged)
            scan_edits.compact_project_history(candidate)
            snapshot=serial(staged)
            if isinstance(body.get('ui'),dict):snapshot['ui']=copy.deepcopy(body['ui'])
            data=projects.save_named(s.WORK,snapshot,body.get('name'),action=='save_as')
            candidate.update({k:data[k] for k in ('project_id','project_title','saved_at','ui')})
            candidate['project_preview']=data.get('project_preview')
            _commit_project(s,candidate,reset_view=False);h.send({'saved':True,'project_file':project_location(s)['project_file']})
        elif action=='save_portable_in':
            h.send(save_portable_in(s, body))
        elif action=='new':
            _save_previous(s,body.get('ui'));_commit_project(s,empty());h.send({'created':True})
        elif action=='open':
            data=projects.read_named(s.WORK,body['id'])
            if callable(getattr(s,'start_job',None)):
                # Saved cleaning identities may contain hundreds of millions of
                # rows. Validate them with progress/cancellation in the normal
                # job worker, keeping concurrent scene mutation disabled.
                s.start_job(lambda:restore_data(s,data,save_current=True,ui=body.get('ui')),'project');h.send({'started':True})
            else:
                report=restore_data(s,data,save_current=True,ui=body.get('ui'));h.send({'opened':True,'warnings':report.get('warnings',[])})
        elif action in ('delete_project','delete_projects'):
            from storage_cleanup import exclusive
            with exclusive(s):
                old=copy.deepcopy(s.STATE);ids=body.get('ids') or [body.get('id')]
                storage=workspace_history.storage_state(s)
                review=projects.projects_storage_review(s.WORK,ids,storage)
                expected=body.get('expected_bytes')
                token=body.get('expected_token')
                if not isinstance(token,str) or token!=review['review_token']:raise ValueError('Project storage changed. Review the selection before deleting it.')
                if not body.get('confirm'):raise ValueError('Confirm permanent project deletion.')
                try:
                    if review.get('active'):_commit_project(s,empty())
                    result=projects.delete_projects_permanently(s.WORK,ids,storage,expected,token)
                except Exception:
                    if review.get('active'):
                        try:_commit_project(s,old,reset_view=False)
                        except Exception:pass
                    raise
                if review.get('active'):s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
                h.send({'deleted':True,**result})
        elif action=='clean_orphans':
            from storage_cleanup import exclusive
            with exclusive(s):
                if not body.get('confirm'):raise ValueError('Confirm cleanup of unreferenced project data.')
                token=body.get('expected_token')
                if not isinstance(token,str) or not token:raise ValueError('Review unreferenced files before cleaning.')
                result=projects.clean_orphan_storage(s.WORK,workspace_history.storage_state(s),body.get('expected_bytes'),token);h.send({'cleaned':True,**result})
        else: raise ValueError('Unknown project command.')
        return True
    if path=='/api/export':
        fmt=body['format']; source=body.get('source','survey')
        prefix={'surface':'Tunnel_Studio_Tunnel_Kept','surface_other':'Tunnel_Studio_Noise_Removed'}.get(source,'Tunnel_Studio_export') if fmt not in ('obj','tstudio') else 'Tunnel_Studio_export'
        name=prefix+'.'+fmt
        if fmt not in ('e57','las','laz','ply','pts','xyz','csv','tstudio','obj','json'): raise ValueError('Unsupported export format.')
        def write():
            dest=asset_folder(s.WORK,'export')/(uuid.uuid4().hex+'.'+fmt)
            extra={}
            if source=='highlighted':
                if fmt in ('obj','tstudio','json'):raise ValueError('Choose a point-cloud format for highlighted points.')
                request=body.get('highlight') or {}
                with workspace_tools.highlighted(s,request,s.progress) as (meta,revision):
                    result=scan_export.export_cloud(meta,dest,fmt,s.WORK,s.progress,exchange.export_cloud)
                if request.get('deleted_groups'):result.setdefault('warnings',[]).append('Exported only the selected deleted area. Source points and deleted groups are unchanged.')
                extra.update(highlight=request,highlight_revision=revision)
            elif source=='objects':
                if fmt!='json':raise ValueError('Selected geometry and metadata use JSON export.')
                import scene_objects
                ids=body.get('object_ids',[]);objects={o['id']:o for o in scene_objects.public(s)}
                if not isinstance(ids,list) or not ids or any(i not in objects for i in ids):raise ValueError('Highlight an available object first.')
                values=[objects[i] for i in ids]
                dest.write_text(json.dumps({'units':'metres','objects':values},indent=2),encoding='utf8')
                result={'count':None,'warnings':['Selected geometry and metadata in project coordinates.']}
                extra.update(object_ids=ids,object_revision=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest())
            elif fmt=='json':raise ValueError('Select geometry to export JSON.')
            elif fmt=='tstudio': projects.portable(s.WORK,serial(s),dest,s.progress); result={'count':None,'warnings':['Complete portable project. Large scan caches are included.']}
            elif fmt=='obj':
                if body.get('mesh_id') or body.get('mesh_ids'):
                    ids=body.get('mesh_ids') or [body['mesh_id']]
                    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):raise ValueError('Select meshes to export.')
                    entries=[job_api.item(s,i) for i in ids]
                    if any(x['kind']!='mesh' for x in entries):raise ValueError('Select meshes to export.')
                    result=workspace_tools.export_meshes(entries,dest,s.progress)
                    extra.update(mesh_ids=ids,mesh_revisions={x['id']:scan_export.signature(x) for x in entries})
                else:
                    import structures
                    if not s.STATE.get('structures'):raise ValueError('Select a mesh in Scans & models before exporting OBJ.')
                    dest.write_text(structures.obj(s.STATE['structures'],s.STATE['axis']),encoding='utf8');result={'count':None,'warnings':['Mesh geometry only.']}

            else:
                if source=='scene':
                    entry=job_api.item(s,body.get('source_id'))
                    if entry['kind']!='scan':raise ValueError('Choose a point-cloud scan in Scans & models.')
                    meta=entry['meta']
                elif source=='survey': meta=s.STATE.get('cloud')
                elif source=='registered': meta=reg(s).get('transformed')
                elif source=='source': meta=reg(s).get('cloud')
                elif source in ('surface','surface_other'): meta=surface_api.filtered_cloud(s,source=='surface')
                else: raise ValueError('Unknown export source.')
                if not meta: raise ValueError('Load a scan, or apply registration to prepare the positioned scan.')
                result=scan_export.export_cloud(meta,dest,fmt,s.WORK,s.progress,exchange.export_cloud)
            __import__('storage_cleanup').record_product(s.WORK,dest,'exports',s.STATE)
            s.STATE['export_ready']={**result,**extra,'id':export_id,'file':str(dest),'name':name,'format':fmt,'source':source,'source_id':body.get('source_id'),'source_revision':scan_export.signature(entry) if source=='scene' and fmt not in ('obj','tstudio') else None}; s.save()
        export_id=uuid.uuid4().hex
        s.STATE['export_ready']=None
        s.start_job(write); h.send({'started':True,'id':export_id}); return True
    action=body['action']
    if action=='demo':
        def demo():
            from demo_targets import generate
            path,rows=generate(s.WORK/'target_demo')
            for name in ('synthetic_targets.csv','control.csv'):
                __import__('storage_cleanup').record_product(s.WORK,s.WORK/'target_demo'/name,'demo',s.STATE)
            load(s,'registration',path); r=reg(s); r.update(rows=rows,demo=True); s.save()
        s.start_job(demo); h.send({'started':True}); return True
    r=reg(s)
    if body.get("source_id") and body["source_id"]!=Path(r["cloud"]["file"]).stem:raise ValueError("The active scan changed. Return to that scan before saving these edits.")
    if action=='detect':
        def detect():
            seed=body.get('seed')
            if seed is not None and body.get('seed_frame')=='world':
                seed=__import__('registration_pose').inverse_point(r['cloud'],seed).tolist()
            result=registration.detect(r['cloud'],body.get('diameter'),body.get('channel','auto'),seed,s.progress,body.get('target_type','bw_auto'))
            for c in result['candidates']: c['id']='T'+uuid.uuid4().hex[:6].upper()
            if body.get('seed') is not None: r.setdefault('candidates',[]).extend(result['candidates'])
            else:
                kept=[c for c in r.get('candidates',[]) if c.get('status') in ('accepted','rejected')]
                r['candidates']=kept+[c for c in result['candidates'] if not any(np.linalg.norm(np.array(c['source'])-k['source'])<.02 for k in kept)]
            registration_workspace.include_candidates(r,
                [c for c in result['candidates'] if c in r.get('candidates',[])],
                s.STATE.get('survey_controls',{}))
            registration_workspace.persist(s)
            r.pop('target_undo',None)
            r['detection']={k:v for k,v in result.items() if k!='candidates'}; r['revision']=uuid.uuid4().hex; s.save()
        s.start_job(detect)
    elif action=='rename_target':
        if body.get('scan_id') is not None and body['scan_id']!=s.STATE.get('active_job_registration'):
            raise ValueError('The active scan changed. Choose its target again.')
        c=next((c for c in r.get('candidates',[]) if c['id']==body.get('id')),None)
        row_id=body.get('row_id')
        name=str(body.get('name','')).strip()
        if not name or len(name)>60:raise ValueError('Choose a target and enter its name.')
        if any(v is not c and v.get('name',v['id'])==name for v in r.get('candidates',[])):raise ValueError('A target already has that name in this scan.')
        rows=copy.deepcopy(r.get('rows',[]))
        row=next((row for row in rows if (row.get('candidate_id')==c['id'] if c else
                  row['id']==row_id and not row.get('candidate_id'))),None)
        if c is None and row is None:raise ValueError('This target is no longer in the scan.')
        if any(other is not row and other['id']==name for other in rows):
            raise ValueError('A target already has that name in this scan.')
        if row is not None:row['id']=name
        previous=copy.deepcopy(s.STATE)
        try:
            if c is not None:c['name']=name
            registration_workspace.save_rows(s,rows)
            s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(previous);raise
    elif action=='review':
        c=next((c for c in r.get('candidates',[]) if c['id']==body['id']),None)
        if not c or body.get('status') not in ('accepted','rejected','unreviewed'): raise ValueError('Invalid target review.')
        rows=__import__('copy').deepcopy(r.get('rows',[]))
        if body['status']=='accepted' and not any(row.get('candidate_id')==c['id'] for row in rows):
            rows.append(dict(id=c.get('name',c['id']),candidate_id=c['id'],role='fit',source=list(c['source']),control=list(s.STATE.get('survey_controls',{}).get(c.get('name',c['id']),[None,None,None]))))
        if body['status']!='accepted':rows=[row for row in rows if row.get('candidate_id')!=c['id']]
        registration_workspace.save_rows(s,rows);c['status']=body['status'];r.pop('target_undo',None);s.save()
    elif action in ('remove_candidate','remove_targets','clear_candidates','undo_targets'):
        if body.get('source_id') and body['source_id']!=Path(r['cloud']['file']).stem:
            raise ValueError('Registration source changed. Refresh the Targets window.')
        if action=='undo_targets':target_ops.undo_target_removal(r)
        else:
            ids=([str(body.get('id',''))] if action=='remove_candidate' else [c['id'] for c in r.get('candidates',[])] if action=='clear_candidates' else body.get('ids',[]))
            target_ops.remove_targets(r,ids,body.get('row_ids',[]))
        s.STATE['export_ready']=None;s.save()
    elif action=='solve_group_control':
        ident=s.STATE.get('active_job_registration')
        if not ident or body.get('scan_id')!=ident:
            raise ValueError('Select an original setup in the linked group first.')
        solution=registration_workspace.solve_control(s,ident,body.get('tolerance_mm',5))
        previous=copy.deepcopy(s.STATE)
        try:
            r.update(solution=solution,revision=uuid.uuid4().hex)
            registration_workspace.persist(s);s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(previous);raise
    elif action in ('rows','solve'):
        rows=body['rows']
        if not isinstance(rows,list) or len(rows)>200: raise ValueError('Maximum 200 target rows.')
        solution=registration.solve(rows,body.get('tolerance_mm',5)) if action=='solve' else None
        r.pop('target_undo',None)
        rows=registration_workspace.save_rows(s,rows)
        r.update(rows=rows,solution=solution,transformed=None,revision=uuid.uuid4().hex); s.STATE['export_ready']=None; s.save()
    elif action=='apply':
        solution=r.get('solution')
        if not solution or body.get('solution_id')!=solution['id']: raise ValueError('Registration changed. Solve and review it again.')
        def apply():
            old=copy.deepcopy(s.STATE);created=[];selection=getattr(s,'SELECTION',None)
            try:
                group=solution.get('control_group')
                if group:
                    if body.get('reviewed') is not True:
                        raise ValueError('Review the survey-control fit before applying it.')
                    active=s.STATE.get('active_job_registration')
                    if active!=group['anchor']:
                        raise ValueError('The active setup changed. Calculate the survey-control fit again.')
                    if r.get('applied_solution_id')!=solution['id']:
                        _,_,fingerprint=registration_workspace.control_group(s,active)
                        if fingerprint!=group['input_stamp']:
                            raise ValueError('Control, targets or linked setup poses changed. Calculate the fit again.')
                if body.get('to_survey'):
                    reason=survey_registration_unavailable(s,r)
                    if reason:raise ValueError(reason)
                active=s.STATE.get('active_job_registration')
                if active:
                    meta=registration_workspace.apply_control_solution(s,r,solution)
                else:
                    destination=asset_folder(s.WORK,'registered')/(uuid.uuid4().hex+'.xyz64');created.append(destination)
                    meta=registration.transformed_cloud(r['cloud'],solution,destination,s.progress)
                    r['transformed']=meta;r['applied_at']=time.time()
                s.STATE['export_ready']=None
                if body.get('to_survey'):
                    destination=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.analysis64');created.append(destination)
                    jobdata.room(s.WORK,int(meta['count'])*32)
                    coverage={}
                    derived,count=project_cloud(meta,s.STATE['axis'],destination,s.progress,coverage=coverage)
                    if not count:raise ValueError('No positioned points lie within the alignment extent. Check the alignment and target control coordinates.')
                    __import__('scan_edits').record_projection(meta,derived,count,coverage)
                    source=registration_workspace.scan(s,active) if active else {}
                    s.STATE.update(cloud=copy.deepcopy(meta),derived=derived,valid_points=count,joints=None,
                                   active_job_scan=active,edits=copy.deepcopy(source.get('edit_history',[])),
                                   redo=copy.deepcopy(source.get('redo_history',[])),audit=[],report_ready=False,clean_ready=False)
                    s.SELECTION=None
                s.save()
            except BaseException:
                s.STATE.clear();s.STATE.update(old);s.SELECTION=selection
                for path in created:path.unlink(missing_ok=True)
                raise
        s.start_job(apply)
    else: raise ValueError('Unknown registration command.')
    h.send({'started':s.JOB['busy'],'saved':True}); return True
