# Copyright (c) 2026 Damian Gulczynski.
"""Local-only application server. Start with python server.py."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import argparse, csv, io, json, math, mimetypes, os, secrets, subprocess, threading, time, traceback, uuid, webbrowser
import workspace_history,large_sections,cad_api
import runtime_diagnostics
runtime_diagnostics.install('server')
import numpy as np
import sys, shutil
import editing, reporting, heatmap, structures, workflows, jobdata, meshing, analysis_tools, session_start, design_api, inspection_api
from release import VERSION, COPYRIGHT
from core import Alignment, CHUNK, generate_demo, import_cloud, project_cloud, ring_checks, slice_analysis, suggest_joints

ROOT=Path(__file__).resolve().parent
import workspace_folders
WORKSPACE_CATALOGUE,WORK,WORKSPACE_NOTICE=workspace_folders.startup()
if WORK == WORKSPACE_CATALOGUE:
    WORK.mkdir(parents=True,exist_ok=True)
elif not WORK.is_dir():
    raise OSError('The chosen project drive or project folder is unavailable.')
__import__('explorer_deletion').recover(WORK)
# Finish physical cleanup from earlier project/orphan deletions without blocking
# startup. The queue contains only files already removed from live project metadata.
workflows.projects.resume_storage_purges(WORK)
TOKEN=secrets.token_urlsafe(32)
STATE=workflows.empty()
JOB={'busy':False,'message':'Ready','error':None,'started_at':None,'kind':'general'}
JOB_STATUS_FILE=WORK/'job_status.json'
JOB_CANCEL_FILE=WORK/'job_cancel.request'
JOB_WINDOW_APPEARANCE=()
LOCK=threading.Lock()
SELECTION=None

def save():
    import job_api
    job_api.sync_active(sys.modules[__name__])
    a=STATE['axis']; data={**STATE,'axis':{'rows':a.a.tolist(),'name':a.name} if a else None}
    workflows.projects.atomic_json(WORK/'project.json',data)

def start_empty():
    # Complete any interrupted teaching against the saved project head before
    # session_start preserves that autosave and publishes an empty workspace.
    __import__('shape_library').recover_pending(sys.modules[__name__])
    initial=workflows.empty
    if WORK!=WORKSPACE_CATALOGUE:
        def initial():
            state=workflows.empty()
            state['project_title']=WORK.name
            return state
    session_start.begin(sys.modules[__name__],initial,workflows.projects.save_named)

def restore():
    try:
        data=json.loads((WORK/'project.json').read_text(encoding='utf8'))
        if data['axis']:
            data['axis']=Alignment([dict(zip(['chainage','e','n','h','radius'],r)) for r in data['axis']['rows']],data['axis']['name'])
        if data['cloud'] and (not Path(data['cloud']['file']).exists() or not Path(data['derived']).exists()): return
        __import__('rtc360_names').normalize(data)
        STATE.update(data)
    except (OSError,ValueError,KeyError,TypeError): pass

_LAST_PROGRESS_WRITE=0.0

def _write_job_status(throttle=False):
    global _LAST_PROGRESS_WRITE
    now=time.monotonic()
    # UI/native windows refresh at human rates; inner point loops can report
    # thousands of checkpoints per second. Cancellation is still checked on
    # every progress() call, and start/stop/error writes are never throttled.
    if throttle and now-_LAST_PROGRESS_WRITE<.2:return
    try:
        payload={**JOB,'updated_at':time.time(),'pid':os.getpid()}
        temp=JOB_STATUS_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(payload,separators=(',',':')),encoding='utf8')
        os.replace(temp,JOB_STATUS_FILE)
        _LAST_PROGRESS_WRITE=now
    except Exception:
        pass

class OperationCancelled(Exception):pass

def _cancel_requested():
    if JOB_CANCEL_FILE.exists():return True
    return any(getattr(sys.modules.get(name),'CANCEL',None) is not None and
               sys.modules[name].CANCEL.is_set() for name in ('job_api','surface_api'))

def _clear_cancel_events():
    for name in ('job_api','surface_api'):
        event=getattr(sys.modules.get(name),'CANCEL',None)
        if event is not None:event.clear()

def progress(message):
    # Native and in-app Stop buttons both create the same cancellation request.
    if JOB.get('busy') and _cancel_requested():
        try:
            import job_api, surface_api
            job_api.CANCEL.set();surface_api.CANCEL.set()
        except Exception:pass
        raise OperationCancelled('Operation cancelled safely. Original survey data was not changed.')
    JOB['message']=message
    _write_job_status(throttle=True)

def set_progress_appearance(theme,palette):
    global JOB_WINDOW_APPEARANCE
    JOB_WINDOW_APPEARANCE=(theme,palette)

def _start_native_progress():
    """Open a progress window in a separate process so WebView2 cannot hide it."""
    try:
        # start_job cleared the preceding job's request before marking this
        # job busy. Do not erase a new stop arriving while the window opens.
        helper=ROOT/'native_progress.py'
        pythonw=Path(sys.executable).with_name('pythonw.exe') if os.name=='nt' else Path(sys.executable)
        if not pythonw.is_file():pythonw=Path(sys.executable)
        flags=0x08000000 if os.name=='nt' else 0
        subprocess.Popen([str(pythonw),str(helper),str(JOB_STATUS_FILE),str(JOB_CANCEL_FILE),*JOB_WINDOW_APPEARANCE],cwd=str(ROOT),creationflags=flags)
    except Exception:
        pass

def load(kind,path,demo=False,display_name=None):
    global SELECTION
    path=Path(str(path).strip().strip('"')).expanduser()
    if workflows.load(sys.modules[__name__],kind,path,display_name): return
    if not path.is_file(): raise ValueError('File not found. Use Choose file, or paste the full local path including its extension.')
    if kind=='axis':
        axis=Alignment.read(path)
        # Loading a design starts a fresh project: never mix old/demo measurements with a new design.
        STATE.update(axis=axis,axis_provenance={'kind':'imported_design'},cloud=None,derived=None,rings=[],demo=demo,valid_points=0,joints=None,structures=[])
    elif kind=='cloud':
        if STATE['axis'] is None: raise ValueError('Load your alignment CSV before loading a point cloud.')
        if STATE['demo'] and not demo: raise ValueError('Load your own alignment first. A real scan cannot be analysed against the demonstration alignment.')
        name=uuid.uuid4().hex
        if path.suffix.lower() in ('.e57','.hspca'):
            parts=jobdata.scan_parts(path,progress);expected=sum(n for _,n in parts if n)
            meta=jobdata.import_scan(path,WORK,index=None,expected=expected or None,progress=progress)
        else:
            meta=import_cloud(path,asset_folder(WORK,'imported')/(name+'.xyz64'),progress)
        try: derived,count=project_cloud(meta,STATE['axis'],asset_folder(WORK,'analysis')/(name+'.analysis64'),progress)
        except Exception:
            Path(meta['file']).unlink(missing_ok=True); (asset_folder(WORK,'analysis')/(name+'.analysis64')).unlink(missing_ok=True); raise
        if count==0:
            Path(meta['file']).unlink(missing_ok=True); Path(derived).unlink(missing_ok=True)
            raise ValueError('No scan points project inside the alignment. Check coordinate system, units and alignment extent.')
        __import__('scan_edits').record_projection(meta,derived,count)
        STATE.update(cloud=meta,derived=derived,valid_points=count,joints=None)
    elif kind=='rings':
        import ring_creep
        rows=ring_creep.read_chainages(path)
        if rows is None:
            if STATE['axis'] is None:raise ValueError('XYZ ring observations need an alignment. For chainages, use ring_id, design_chainage_m, measured_chainage_m columns.')
            if STATE['demo'] and not demo:raise ValueError('Load your own alignment before importing project ring coordinates.')
            rows=ring_checks(path,STATE['axis'])
        STATE['rings']=rows; STATE['report_ready']=False
    else: raise ValueError('Unknown import type.')
    if kind in ('axis','cloud'):
        STATE.update(edits=[],redo=[],audit=[],report_ready=False,clean_ready=False,export_ready=None)
        SELECTION=None
    save()

def start_job(action,kind='general',native_progress=False):
    action=workspace_history.background(sys.modules[__name__],action)
    with LOCK:
        if JOB['busy']: raise ValueError('Another operation is running. Wait for it to finish.')
        JOB_CANCEL_FILE.unlink(missing_ok=True)
        import job_api
        _clear_cancel_events()
        JOB.update(busy=True,message='Starting…',error=None,cancelled=False,kind=kind,started_at=time.time())
        _write_job_status()
        if native_progress:_start_native_progress()
    def worker():
        try:
            action(); JOB['message']='Ready — saved on this laptop'
        except (OperationCancelled,jobdata.Cancelled,__import__('compute_runtime').ProcessingCancelled) as e:
            JOB['error']=None; JOB['cancelled']=True; JOB['message']=str(e)
        except Exception as e:
            # Older numerical modules signal cooperative cancellation with
            # ValueError. A user stop plus an explicit cancellation message is
            # distinct from a geometry/disk failure, which remains an error.
            if isinstance(e,ValueError) and _cancel_requested() and 'cancel' in str(e).lower():
                JOB['error']=None;JOB['cancelled']=True;JOB['message']=str(e)
            else:
                runtime_diagnostics.exception('Background operation failed: '+str(kind), e)
                JOB['error']=str(e); JOB['message']='Operation stopped'; traceback.print_exc()
        finally:
            # Release cancellation state before another job can start. The
            # numerical caller has already joined its workers at this point.
            with LOCK:
                _clear_cancel_events()
                try:JOB_CANCEL_FILE.unlink(missing_ok=True)
                except OSError as e:runtime_diagnostics.exception('Could not clear completed cancellation request',e)
                JOB['busy']=False
                _write_job_status()
    threading.Thread(target=worker,daemon=True,name='TunnelStudioWorker').start()

def demo():
    folder=generate_demo(WORK/'demo')
    for name in ('demo_alignment.csv','demo_scan.xyz','demo_rings.csv'):
        __import__('storage_cleanup').record_product(WORK,folder/name,'demo',STATE)
    load('axis',folder/'demo_alignment.csv',True)
    load('cloud',folder/'demo_scan.xyz',True)
    load('rings',folder/'demo_rings.csv',True)

def ready(require_cloud=False):
    if JOB['busy']: raise ValueError('Import in progress. Wait for Ready before running an analysis.')
    if STATE['axis'] is None: raise ValueError('Load an alignment first.')
    if require_cloud and STATE['cloud'] is None: raise ValueError('Load a scan first.')

def status(job=None):
    a=STATE['axis']; c=STATE['cloud']
    return {'history':workspace_history.status(sys.modules[__name__]),'job_item_count':len(STATE.get('job_items',[])),'project_id':STATE.get('project_id'),'project_title':STATE.get('project_title','Untitled project'),'saved_at':STATE.get('saved_at'),'ui':STATE.get('ui',{}),'registration_ready':bool(STATE.get('registration',{}).get('cloud')),'export_ready':{k:v for k,v in (STATE.get('export_ready') or {}).items() if k!='file'},'job':JOB.copy() if job is None else job,'demo':STATE['demo'],'axis':None if a is None else {'name':a.name,'start':float(a.a[0,0]),'end':float(a.a[-1,0]),'nodes':len(a.a),'estimated':STATE.get('axis_provenance',{}).get('kind')=='scan_estimate'},
        'cloud':None if c is None else {k:v for k,v in c.items() if k not in ('file','mask_file','attributes_file')},'valid_points':STATE['valid_points'],'rings':STATE['rings'],'joints':STATE.get('joints'),
        'last_backup':STATE.get('last_backup'),'structures':STATE.get('structures',[]),'selection_count':SELECTION['count'] if SELECTION else 0,'edit_count':len(STATE.get('edits',[])),'redo_count':len(STATE.get('redo',[])),'removed_points':sum(e['count'] for e in STATE.get('edits',[])), 'report_ready':STATE.get('report_ready',False),'clean_ready':STATE.get('clean_ready',False),'report_settings':STATE.get('report_settings',{}),'copyright':COPYRIGHT,'version':VERSION,'formats':['E57','LAS','LAZ','PLY','PTS','XYZ','TXT','CSV'],'mesh_capabilities':meshing.capabilities(),'analysis_tool_result':STATE.get('analysis_tool_result')}

def overview():
    ready(True); a=STATE['axis']; meta=STATE['cloud']; n=meta['count']
    p=np.memmap(meta['file'],dtype='<f8',mode='r',shape=(n,3)); d=np.memmap(STATE['derived'],dtype='<f8',mode='r',shape=(n,4))
    step=max(1,math.ceil(n/100000)); sample=__import__('registration_pose').points(meta,p[::step]); info=d[::step]
    origin=a.a[len(a.a)//2,1:4]; keep=np.isfinite(info[:,0])
    mask=editing.mask_for(meta)
    if mask is not None: keep &= mask[::step]==0
    sample=sample[keep]; info=info[keep]
    return {'origin':origin.tolist(),'points':np.column_stack((sample-origin,info[:,3]*1000,info[:,0])).tolist(),
        'axis':np.column_stack((a.a[:,1:4]-origin,a.a[:,0],a.a[:,4])).tolist(),'stride':step,'sample_count':len(sample),'source_count':n,'removed_points':sum(e['count'] for e in STATE.get('edits',[]))}

def section(params):
    ready(True)
    def get(k,d): return float(params.get(k,[d])[0])
    return large_sections.section(sys.modules[__name__],slice_analysis,get('station',STATE['axis'].a[0,0]),get('width',.30),get('band',.30),get('tolerance',25))

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def send(self,data,ctype='application/json',code=200,filename=None):
        if ctype=='application/json': data=json.dumps(data,allow_nan=False,separators=(',',':')).encode()
        if isinstance(data,str): data=data.encode()
        self.send_response(code); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if filename: self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers(); self.wfile.write(data)
    def send_file(self,path,ctype,filename=None):
        self.send_response(200); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(path.stat().st_size)); self.send_header('Cache-Control','no-store')
        if filename: self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        with path.open('rb') as source: shutil.copyfileobj(source,self.wfile)
    def allowed_host(self):
        host=self.headers.get('Host','')
        return host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.allowed_host(): return self.send({'error':'Invalid local host'},code=403)
        parsed=urlparse(self.path); path=parsed.path; args=parse_qs(parsed.query)
        try:
            if path=='/api/status':
                job=JOB.copy()
                if args.get('job_only')==['1'] or (job['busy'] and args.get('during_job')==['1']):
                    return self.send({'job':job})
                return self.send(status(job))
            if path=='/':
                from appearance_palettes import controls_markup, style_markup
                html=(ROOT/'web/index.html').read_text(encoding='utf8').replace('__TOKEN__',TOKEN).replace('__VERSION__',VERSION).replace('__COPYRIGHT__',COPYRIGHT)
                html=html.replace('__PALETTE_STYLES__',style_markup()).replace('__PALETTE_CHOICES__',controls_markup())
                return self.send(html,'text/html; charset=utf-8')
            if path=='/api/tunnel-design' and __import__('tunnel_designer').get(sys.modules[__name__],self,path,args):return
            if path=='/api/mesh-repair' and __import__('mesh_repair').get(sys.modules[__name__],self,path,args):return
            if __import__('geometry_tools').get(sys.modules[__name__],self,path,args):return
            if path=='/api/shape-selection' and __import__('shape_selection').get(sys.modules[__name__],self,path,args):return
            if path=='/api/shape-inspection' and __import__('shape_inspection').get(sys.modules[__name__],self,path,args):return
            if path=='/api/shape-library' and __import__('shape_library_api').get(sys.modules[__name__],self,path,args):return
            if __import__('feature_selection').get(sys.modules[__name__],self,path,args):return
            if __import__('measure_tools').get(sys.modules[__name__],self,path,args):return
            if path.startswith('/api/cad') and cad_api.get(sys.modules[__name__],self,path,args):return
            if path in ('/window-chrome.js','/window-chrome.css','/project-browser-ui.js','/project-browser.css','/selected-merge-ui.js','/workspace-modern.css','/scan-appearance-ui.js','/circle-clean-ui.js','/shape-selection-ui.js','/desktop-close.js','/storage-cleanup-ui.js','/storage-inventory-ui.js','/project-folder-copy.js','/workspace-folders.js','/project-save-location.js','/project-shapefiles.js','/bundle-adjustment-ui.js','/analysis-workspaces.css','/tunnel-monitoring-ui.js','/visual-registration-ui.js','/visual-registration-ui.css','/display-buffers.js','/explorer-delete-ui.js','/explorer-delete-ui.css','/mesh-results-ui.js','/mesh-results-ui.css','/explorer-polish.js','/explorer-polish.css','/theme-ui.css','/theme-ui.js','/permanent-points-ui.js','/permanent-points-ui.css','/ribbon-layout.js','/ribbon-modern.css','/parameter-help.js','/geometry-tools-ui.js','/geometry-tools-ui.css','/feature-selection-ui.js','/feature-selection-ui.css','/measure-tools-ui.js','/measure-tools-ui.css','/inspection-ui.js','/inspection-ui.css','/file-drop-ui.js','/mesh-repair-ui.js','/mesh-to-cloud-ui.js','/tunnel-designer-ui.js','/cleaning-help.js','/alignment-hub.js','/ui-icons.js','/cad-ui.js','/cad-math.js','/cad.css','/workspace-ui.js','/detached-ui.js','/detached.html','/detached-client.js','/leica-bridge-ui.js','/export-ui.js','/ring-creep-ui.js','/setup-backdrop.js','/setup-camera-ui.js','/scan-registration-ui.js','/app.js','/style.css','/tunnel.ico','/ribbon.js','/ribbon.css','/registration-ui.js','/surface-ui.js','/segment-review.js','/surface.css','/job-ui.js','/limit-box-ui.js','/section-locator-ui.js','/section-plot-ui.js','/alignment-editor-ui.js','/history-ui.js','/scene-objects-ui.js','/work-status-ui.js','/joint-ui.js','/heatmap-ui.js','/scan-tools-ui.js','/job.css','/section-research-ui.js','/design-ui.js'):
                p=ROOT/'web'/path[1:]; return self.send(p.read_bytes(),'text/javascript' if path.endswith('.js') else 'text/html; charset=utf-8' if path.endswith('.html') else 'image/x-icon' if path.endswith('.ico') else 'text/css')
            if path in ('/api/report-html','/api/download-report'):
                ready(True)
                if not STATE.get('report_ready'): raise ValueError('Generate a current report first.')
                return self.send_file(asset_folder(WORK,'export')/'survey_report.html','text/html; charset=utf-8','Tunnel_Survey_Report.html' if path.endswith('download-report') else None)
            if path=='/api/cleaned-cloud':
                ready(True)
                if not STATE.get('clean_ready'): raise ValueError('Prepare the cleaned export first.')
                return self.send_file(asset_folder(WORK,'export')/'cleaned_scan.csv','text/csv','cleaned_scan.csv')
            if path=='/api/alignment-data':
                axis=STATE.get('axis')
                return self.send({'name':axis.name,'rows':axis.a.tolist()} if axis is not None else {'name':'','rows':[]})
            if path=='/api/structures':
                ready()
                a=STATE['axis']; origin=a.a[len(a.a)//2,1:4].tolist()
                return self.send({'origin':origin,'objects':[{'id':o['id'],'name':o['name'],'visible':o['visible'],'wire':structures.wire(o,a,origin)[::max(1,int(o.get('summary',{}).get('triangles',0)/40000))] if o['visible'] else []} for o in STATE.get('structures',[])]})
            if path=='/api/structures-obj':
                ready(); return self.send(structures.obj(STATE.get('structures',[]),STATE['axis']),'text/plain',filename='reference_structures.obj')
            if path=='/api/heatmap':
                ready(True)
                return self.send(heatmap.calculate(STATE['cloud'],STATE['derived'],STATE['axis'],*[float(args.get(k,[default])[0] or default) for k,default in [('start',STATE['axis'].a[0,0]),('end',STATE['axis'].a[-1,0]),('step',.25),('degrees',5),('band',.3)]],args.get('mode',['extreme'])[0]))
            if path=='/api/support-info':
                import platform
                return self.send({'application':'Tunnel Studio','version':VERSION,'copyright':COPYRIGHT,'os':platform.system(),'os_release':platform.release(),'architecture':platform.machine(),'python':platform.python_version(),'point_count':(STATE['cloud'] or {}).get('count',0),'last_error':JOB.get('error')})
            if inspection_api.get(sys.modules[__name__],self,path,args):return
            if design_api.get(sys.modules[__name__],self,path,args):return
            if workflows.get(sys.modules[__name__],self,path,args): return
            if path=='/api/overview': return self.send(overview())
            import section_api
            if section_api.get(sys.modules[__name__],self,path,args):return
            if path=='/api/section': return self.send(section(args)[0])
            if path=='/api/export-section':
                report,uv,dev=section(args); out=asset_folder(WORK,'working')/(uuid.uuid4().hex+'.section-export.csv')
                try:
                    jobdata.room(WORK,len(dev)*100)
                    with out.open('w',encoding='utf8',newline='') as stream:
                        w=csv.writer(stream);w.writerow(['chainage_m','local_right_m','local_up_m','radial_deviation_mm'])
                        for row,d in zip(uv,dev):w.writerow([report['chainage'],*row,d])
                    return self.send_file(out,'text/csv; charset=utf-8','section_points.csv')
                finally:out.unlink(missing_ok=True)
            if path=='/api/export-rings':
                if JOB['busy']:raise ValueError('Wait until the current operation finishes.')
                if not STATE['rings']:raise ValueError('Calculate ring creep before exporting.')
                out=io.StringIO(); w=csv.writer(out); w.writerow(['ring_id','design_chainage_m','measured_chainage_m','creep_mm','reference','source'])
                for r in STATE['rings']: w.writerow([r[k] for k in ['ring_id','design','measured','creep_mm','reference','source']])
                return self.send(out.getvalue(),'text/csv; charset=utf-8',filename='ring_creep.csv')
            if path=='/api/export-joints':
                ready(); out=io.StringIO(); w=csv.writer(out); w.writerow(['candidate_id','suggested_chainage_m','review_status','signal_mm','supported_sectors_of_24','bin_width_m','type','note'])
                for r in (STATE.get('joints') or {}).get('candidates',[]): w.writerow([r['id'],r['chainage'],r['status'],r['signal_mm'],r['supported_sectors'],r['resolution_m'],r['type'],'Geometric suggestion; not a surveyed ring face'])
                return self.send(out.getvalue(),'text/csv; charset=utf-8',filename='joint_suggestions.csv')
            if path=='/api/report':
                r=section(args)[0]; r.pop('points',None); r['prototype']=f'Tunnel Studio {VERSION} — project validation pending'
                r['axis_file']=STATE['axis'].name; r['scan_file']=STATE['cloud']['name']; r['synthetic_demo']=STATE['demo']
                return self.send(r,'application/json',filename='section_report.json')
            if path.startswith('/templates/'):
                filename=path.split('/')[-1]
                if filename not in ('alignment_template.csv','ring_template.csv','points_template.csv','control_template.csv'): raise ValueError('Unknown template.')
                return self.send((ROOT/'examples'/filename).read_bytes(),'text/csv',filename=filename)
            return self.send({'error':'Not found'},code=404)
        except (BrokenPipeError,ConnectionResetError): return
        except (ValueError,KeyError,OSError) as e: return self.send({'error':str(e)},code=400)
        except Exception as e:
            traceback.print_exc(); return self.send({'error':str(e)},code=500)
    def do_POST(self):
        import storage_cleanup
        path = urlparse(self.path).path
        guard = LOCK if path == '/api/tunnel-monitoring' else nullcontext()
        try:
            with storage_cleanup.incoming(), guard, workspace_history.request(sys.modules[__name__], path):
                return self._post()
        except ValueError as error:
            return self.send({'error':str(error)},code=409)

    def _post(self):
        if not self.allowed_host() or self.headers.get('X-Tunnel-Token')!=TOKEN:
            return self.send({'error':'Local session token required'},code=403)
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
            return self.send({'error':'Invalid origin'},code=403)
        parsed=urlparse(self.path); args=parse_qs(parsed.query); length=int(self.headers.get('Content-Length',0))
        try:
            if parsed.path=='/api/upload':
                if JOB['busy']: raise ValueError('Wait for the current import to finish.')
                name=Path(args.get('name',['scan.xyz'])[0]).name; kind=args.get('kind',['cloud'])[0]
                bundle_id=args.get('bundle_id',[None])[0]
                if kind in ('job_scan','job_mesh'):
                    import workspace_tools
                    bundle_id=workspace_tools.destination_bundle(sys.modules[__name__],bundle_id)
                if kind=='project':workspace_history.configure('/api/load',{'kind':'project'})
                if length<0 or length>shutil.disk_usage(WORK).free-512*1024*1024:raise ValueError('Not enough free project-drive space for this upload. Use the local file path option for large files.')
                dest=asset_folder(WORK,'working')/('upload_'+uuid.uuid4().hex+Path(name).suffix.lower())
                import upload_io
                upload_io.receive(self.rfile,dest,length)
                def process():
                    try:
                        if kind in ('job_scan','job_mesh'):
                            import job_api
                            job_api.load(sys.modules[__name__],kind,dest,display_name=name,bundle_id=bundle_id)
                        else:load(kind,dest,display_name=name)
                        if kind=='cloud': STATE['cloud']['name']=name
                        if kind=='registration': STATE['registration']['cloud']['name']=name
                        if kind=='surface': STATE['surface_source']['name']=name
                        if kind=='axis': STATE['axis'].name=name
                        if kind!='project':save()
                    finally: dest.unlink(missing_ok=True)
                try:start_job(process,'job' if kind in ('job_scan','job_mesh') else 'general')
                except BaseException:dest.unlink(missing_ok=True);raise
                return self.send({'started':True})
            if length>(4000000 if parsed.path in ('/api/tunnel-design-preview','/api/tunnel-design-action','/api/surface-action','/api/cad-action','/api/cad-preview','/api/deleted-preview','/api/scene-object') else 250000): raise ValueError('Unexpected request size.')
            body=json.loads(self.rfile.read(length) or b'{}')
            workspace_history.configure(parsed.path,body)
            if parsed.path=='/api/history':
                if body.get('action') not in ('undo','redo'):raise ValueError('Choose Undo or Redo.')
                with LOCK:return self.send(workspace_history.apply(sys.modules[__name__],body['action']=='redo'))
            if parsed.path=='/api/cancel-operation':
                if not JOB.get('busy'):raise ValueError('No long operation is currently running.')
                JOB_CANCEL_FILE.write_text('cancel',encoding='utf8')
                try:
                    import job_api, surface_api
                    job_api.CANCEL.set();surface_api.CANCEL.set()
                except Exception:pass
                JOB['message']='Cancellation requested — stopping at the next safe checkpoint…';_write_job_status()
                return self.send({'cancelling':True})
            if parsed.path.startswith('/api/tunnel-design-') and __import__('tunnel_designer').post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path=='/api/mesh-repair' and __import__('mesh_repair').post(sys.modules[__name__],self,parsed.path,body):return
            if __import__('geometry_tools').post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path=='/api/shape-selection' and __import__('shape_selection').post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path=='/api/shape-library' and __import__('shape_library_api').post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path=='/api/scan-appearance' and __import__('scan_appearance').post(sys.modules[__name__],self,parsed.path,body):return
            if __import__('feature_selection').post(sys.modules[__name__],self,parsed.path,body):return
            if __import__('measure_tools').post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path.startswith('/api/cad') and cad_api.post(sys.modules[__name__],self,parsed.path,body):return
            if inspection_api.post(sys.modules[__name__],self,parsed.path,body):return
            if design_api.post(sys.modules[__name__],self,parsed.path,body):return
            if workflows.post(sys.modules[__name__],self,parsed.path,body): return
            import section_api
            if section_api.post(sys.modules[__name__],self,parsed.path,body):return
            if parsed.path=='/api/backup-project':
                ready()
                def backup():
                    from datetime import datetime
                    save()
                    dest=WORK.parent/'TunnelStudio_Backups'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
                    shutil.copytree(WORK,dest,ignore=shutil.ignore_patterns('session.lock','*.tmp'))
                    STATE['last_backup']=str(dest); save()
                start_job(backup)
            elif parsed.path=='/api/analysis-tool':
                if JOB['busy']: raise ValueError('Wait for the current operation to finish.')
                def run_analysis_tool():
                    import job_api
                    action=str(body.get('action',''))
                    source=job_api.item(sys.modules[__name__],body.get('source_id'))
                    if source.get('kind')!='scan': raise ValueError('Choose a point-cloud scan as the analysis source.')
                    if action=='cloud_compare':
                        reference=job_api.item(sys.modules[__name__],body.get('reference_id'))
                        if reference.get('kind')!='scan': raise ValueError('Choose a point-cloud reference scan.')
                        result=analysis_tools.cloud_compare(source['meta'],reference['meta'],body.get('max_points',200000),body.get('cutoff',0),progress)
                    elif action=='geometry_qa':
                        result=analysis_tools.geometry_qa(source['meta'],body.get('max_points',30000),body.get('k',12),progress)
                    else: raise ValueError('Unknown analysis tool.')
                    STATE['analysis_tool_result']=result;save();progress('Analysis complete.')
                start_job(run_analysis_tool,'analysis',native_progress=True)
            elif parsed.path=='/api/scan-mesh':
                if JOB['busy']: raise ValueError('Wait for the current operation to finish.')
                import mesh_workflows
                start_job(lambda:mesh_workflows.create_from_scan(sys.modules[__name__],body),'mesh',native_progress=True)
            elif parsed.path=='/api/mesh-refine':
                if JOB['busy']: raise ValueError('Wait for the current operation to finish.')
                import mesh_workflows
                start_job(lambda:mesh_workflows.refine_mesh(sys.modules[__name__],body),'mesh',native_progress=True)
            elif parsed.path=='/api/transform-mesh':
                ready()
                def transform_mesh():
                    item=next((o for o in STATE.get('structures',[]) if o['id']==body['id'] and o['kind']=='scan_mesh'),None)
                    if not item: raise ValueError('Select a scan mesh to transform.')
                    updated={**item,**{k:float(body[k]) for k in ['tx','ty','tz','rotation','scale']}}
                    structures.mesh(updated,STATE['axis']); item.update(updated); save()
                start_job(transform_mesh)
            elif parsed.path=='/api/save-structure':
                ready()
                def store_structure():
                    item={k:body[k] for k in ['kind','name','e','n','h','length','width','height','radius','yaw','start','end','step']}
                    structures.mesh(item,STATE['axis'])
                    objects=STATE.setdefault('structures',[])
                    existing=next((o for o in objects if o['id']==body.get('id')),None)
                    if existing:
                        item.update(id=existing['id'],visible=existing['visible']); objects[objects.index(existing)]=item
                    else:
                        if len(objects)>=30: raise ValueError('Maximum 30 reference structures per project.')
                        item.update(id=uuid.uuid4().hex,name=str(item['name'])[:100],visible=True);objects.append(item)
                    save()
                start_job(store_structure)
            elif parsed.path=='/api/structure-action':
                ready()
                def structure_action():
                    objects=STATE.setdefault('structures',[]);item=next((o for o in objects if o['id']==body['id']),None)
                    if item is None: raise ValueError('Structure not found.')
                    if body['action']=='delete': objects.remove(item)
                    elif body['action']=='visibility': item['visible']=not item['visible']
                    else: raise ValueError('Unknown structure action.')
                    save()
                start_job(structure_action)
            elif parsed.path=='/api/select':
                ready(True)
                def select_points():
                    global SELECTION
                    path=asset_folder(WORK,'working')/(uuid.uuid4().hex+'.selection')
                    selected=editing.select(STATE['cloud'],STATE['derived'],body,path)
                    if SELECTION: Path(SELECTION['file']).unlink(missing_ok=True)
                    SELECTION=selected
                start_job(select_points)
            elif parsed.path in ('/api/delete-selection','/api/undo-edit','/api/redo-edit'):
                ready(True)
                def edit_points():
                    global SELECTION
                    action=parsed.path
                    active=next((x for x in STATE.get('job_items',[]) if x['id']==STATE.get('active_job_scan')),None)
                    if active:
                        import scan_edits, job_api
                        job_api.sync_active(sys.modules[__name__])
                        scan_edits.apply(sys.modules[__name__],active,{'/api/delete-selection':'delete_selection','/api/undo-edit':'undo_edit','/api/redo-edit':'redo_edit'}[action],reason=body.get('reason') or 'Survey selection',selected_file=SELECTION)
                        SELECTION=None
                        return
                    if action=='/api/delete-selection':
                        entry=editing.apply_edit(STATE['cloud'],SELECTION,WORK,STATE['edits'],body.get('reason',''))
                        STATE['redo']=[]
                    elif action=='/api/undo-edit':
                        if not STATE['edits']: raise ValueError('No deletion to undo.')
                        entry=STATE['edits'].pop(); STATE['cloud']['mask_file']=entry['before_mask']; STATE['redo'].append(entry)
                    else:
                        if not STATE['redo']: raise ValueError('No deletion to redo.')
                        entry=STATE['redo'].pop(); STATE['cloud']['mask_file']=entry['after_mask']; STATE['edits'].append(entry)
                    STATE['audit'].append({'action':action,'time':time.time(),'count':entry['count'],'reason':entry['reason']})
                    STATE.update(joints=None,report_ready=False,clean_ready=False,export_ready=None)
                    if SELECTION: Path(SELECTION['file']).unlink(missing_ok=True)
                    SELECTION=None; save()
                start_job(edit_points)
            elif parsed.path=='/api/build-report':
                ready(True)
                def build_report():
                    STATE['report_ready']=False
                    report,rows=reporting.create(STATE,body,progress)
                    tmp=asset_folder(WORK,'export')/'report.tmp'; tmp.write_text(report,encoding='utf8'); tmp.replace(asset_folder(WORK,'export')/'survey_report.html')
                    __import__('storage_cleanup').record_product(WORK,asset_folder(WORK,'export')/'survey_report.html','reports',STATE)
                    STATE.update(report_ready=True,report_settings=body); save()
                start_job(build_report)
            elif parsed.path=='/api/open-report':
                ready(True)
                if not STATE.get('report_ready'): raise ValueError('Generate a current report first.')
                webbrowser.open(f'http://127.0.0.1:{self.server.server_port}/api/report-html')
                return self.send({'opened':True})
            elif parsed.path=='/api/export-clean':
                ready(True)
                def clean():
                    STATE['clean_ready']=False
                    editing.export_clean(STATE['cloud'],asset_folder(WORK,'export')/'cleaned_scan.tmp')
                    (asset_folder(WORK,'export')/'cleaned_scan.tmp').replace(asset_folder(WORK,'export')/'cleaned_scan.csv'); STATE['clean_ready']=True; save()
                start_job(clean)
            elif parsed.path=='/api/demo': start_job(demo)
            elif parsed.path=='/api/load':
                k=body['kind']; start_job(lambda:load(k,body['path']),'job' if k in ('job_scan','job_mesh') else 'surface' if k=='surface' else 'registration' if k=='registration' else 'general',native_progress=k in ('job_scan','job_mesh','surface','registration','cloud'))
            elif parsed.path=='/api/detect-joints':
                if JOB['busy']:raise ValueError('Wait for the current operation to finish.')
                import joint_scan
                joint_scan.source(sys.modules[__name__],body.get('scan_id'))
                start_job(lambda:joint_scan.run(sys.modules[__name__],body),'job',native_progress=True)
            elif parsed.path=='/api/review-joint':
                ready(True)
                if body['status'] not in ('kept','rejected','unreviewed'): raise ValueError('Unknown review state.')
                matches=[r for r in (STATE.get('joints') or {}).get('candidates',[]) if r['id']==body['id']]
                if not matches: raise ValueError('Candidate not found.')
                matches[0]['status']=body['status']; save(); return self.send({'saved':True})
            else: return self.send({'error':'Not found'},code=404)
            return self.send({'started':True})
        except (BrokenPipeError,ConnectionResetError): return
        except (ValueError,KeyError,OSError) as e: return self.send({'error':str(e)},code=400)

_PROJECT_LOCK=None
def acquire_project_lock():
    global _PROJECT_LOCK
    _PROJECT_LOCK=open(WORK/'session.lock','a+b')
    try:
        if sys.platform=='win32':
            import msvcrt
            _PROJECT_LOCK.seek(0)
            msvcrt.locking(_PROJECT_LOCK.fileno(),msvcrt.LK_NBLCK,1)
            _PROJECT_LOCK.seek(0); _PROJECT_LOCK.write(b'0'); _PROJECT_LOCK.flush()
        else:
            import fcntl
            fcntl.flock(_PROJECT_LOCK.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        _PROJECT_LOCK.close(); _PROJECT_LOCK=None
        raise ValueError('Tunnel Studio is already using this project. Close its other window first.')

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--no-browser',action='store_true'); args=parser.parse_args()
    acquire_project_lock()
    start_empty()
    try: server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}'
    print(f'Tunnel Studio {VERSION}\nOpen {url}\nFiles stay on this laptop. Keep this window open. Ctrl+C stops the app.',flush=True)
    if not args.no_browser: threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped. Project cache retained in .work.')
    finally: server.server_close()

if __name__=='__main__': main()
