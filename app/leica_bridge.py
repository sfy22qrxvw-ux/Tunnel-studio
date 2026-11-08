# Copyright (c) 2026 Damian Gulczynski.
"""Local REGISTER 360 CLI conversion, plus reuse of completed E57 exports.

No SDK, no remote service and no speculative raw coordinates. The proprietary
converter is user-installed and requires Leica's WORKFLOW licence. Original
scanner files are copied to a new local staging directory before it is run.
"""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy, csv, hashlib, io, json, os, queue, shutil, subprocess, sys, threading, time, uuid

import rtc360_folder

LICENCE_NOTICE='Automatic conversion requires installed Cyclone REGISTER 360 PLUS (not BLK Edition) and a Cyclone WORKFLOW licence. Tunnel Studio cannot check or supply that licence.'


def converter_candidates():
    candidates=[]
    if os.name!='nt':return candidates
    for key in ('ProgramFiles','ProgramFiles(x86)'):
        root=Path(os.environ.get(key,'C:/Program Files'))
        for pattern in ('Leica Geosystems/*/register360.exe','Leica Geosystems/*/*/register360.exe','Leica Geosystems/Cyclone REGISTER 360*/register360.exe'):
            candidates.extend(str(p.resolve()) for p in root.glob(pattern) if p.is_file())
    return sorted(set(candidates))


def validate_converter(value):
    if os.name!='nt':raise ValueError('Automatic Leica conversion runs on Windows. You can still import existing E57 exports.')
    path=Path(str(value).strip().strip('"')).expanduser().resolve()
    if path.name.lower()!='register360.exe' or not path.is_file():raise ValueError('Choose register360.exe in your Cyclone REGISTER 360 PLUS installation folder.')
    with path.open('rb') as stream:
        if stream.read(2)!=b'MZ':raise ValueError('The selected converter is not a Windows executable.')
    check=subprocess.run(['tasklist','/FI','IMAGENAME eq register360.exe','/FO','CSV','/NH'],capture_output=True,text=True,timeout=15,creationflags=0x08000000)
    if check.returncode:raise ValueError('Could not check whether REGISTER 360 is running. Close it and retry.')
    for row in csv.reader(io.StringIO(check.stdout)):
        if row and row[0].lower()=='register360.exe':raise ValueError('Close REGISTER 360 before starting automatic conversion. Its running session will not be closed by Tunnel Studio.')
    return path


def stop_process(proc):
    if proc.poll() is not None:return
    if os.name=='nt':
        try:subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=8,creationflags=0x08000000)
        except (OSError,subprocess.TimeoutExpired):pass
    if proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=2)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=2)


def run_process(command, progress, label, *, cwd=None, json_result=False, log=None):
    """Poll cancellation even while a native converter/reader is silent."""
    progress(label)
    proc=subprocess.Popen([str(x) for x in command],cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',
        creationflags=0x08000000 if os.name=='nt' else 0)
    messages=queue.Queue(maxsize=16);stopped=threading.Event()
    def read_output():
        try:
            while not stopped.is_set():
                line=proc.stdout.readline(2*1024*1024)
                if not line:break
                while not stopped.is_set():
                    try:messages.put(line,timeout=.2);break
                    except queue.Full:pass
        finally:
            while not stopped.is_set():
                try:messages.put(None,timeout=.2);break
                except queue.Full:pass
    thread=threading.Thread(target=read_output,daemon=True);thread.start()
    started=time.monotonic();last=started;result=None;tail=[];eof=False;log_bytes=0
    sink=Path(log).open('w',encoding='utf8') if log else None
    try:
        while not (eof and proc.poll() is not None):
            try:line=messages.get(timeout=.2)
            except queue.Empty:line=''
            if line is None:eof=True
            elif line:
                if sink and log_bytes<20*1024**2:sink.write(line);sink.flush();log_bytes+=len(line.encode('utf8'))
                line=line.rstrip('\r\n')
                if json_result and line.startswith('RESULT\t'):result=json.loads(line.split('\t',1)[1])
                elif json_result and line.startswith('PROGRESS\t'):
                    progress(line.split('\t',1)[1]);last=time.monotonic()
                else:tail=(tail+[line[:4000]])[-12:]
            if time.monotonic()-last>=1:
                seconds=int(time.monotonic()-started)
                progress(f'{label} · {seconds//60}:{seconds%60:02d} elapsed');last=time.monotonic()
        code=proc.wait()
        progress(label+' · checking result')
        if code or (json_result and result is None):
            detail='\n'.join(tail[-6:])[-3000:]
            raise ValueError(f'{label} stopped (exit {code}).'+('\n'+detail if detail else '')+(('\nLog: '+str(log)) if log else ''))
        return result
    except BaseException:
        stop_process(proc);raise
    finally:
        stopped.set();thread.join(timeout=1)
        if proc.stdout:proc.stdout.close()
        if sink:sink.close()


def worker(args,progress):
    exe=Path(sys.executable);console=exe.with_name('python.exe')
    return run_process([console if console.is_file() else exe,Path(__file__).with_name('leica_e57_worker.py'),*args],progress,'Reading E57',json_result=True)


def export_files(path):
    p=Path(str(path).strip().strip('"')).expanduser().resolve()
    if p.is_file():
        if p.suffix.lower()!='.e57':raise ValueError('Choose an E57 file or the folder containing completed E57 exports.')
        return [p]
    if not p.is_dir():raise ValueError('The E57 export folder was not found.')
    files=[];visited=0
    for root,dirs,names in os.walk(p,followlinks=False):
        visited+=1
        if visited>1000:raise ValueError('Choose a smaller folder containing only your completed exports.')
        rel=Path(root).relative_to(p)
        dirs[:]=sorted(d for d in dirs if not (Path(root)/d).is_symlink()) if len(rel.parts)<2 else []
        for name in sorted(names):
            child=Path(root)/name
            if child.suffix.lower()=='.e57' and child.is_file() and not child.is_symlink():files.append(child)
            if len(files)>200:raise ValueError('Choose at most 200 E57 files.')
    if not files:raise ValueError('No completed E57 files were found. Choose the export file or its containing folder.')
    return files


def inspect_exports(path,progress=lambda message:None):
    scans=[]
    for file in export_files(path):
        found=worker(['describe',str(file)],progress)
        for scan in found['scans']:
            ident=hashlib.sha256((found['stamp']+':'+str(scan['index'])).encode()).hexdigest()
            scans.append({**scan,'id':ident,'path':str(file),'stamp':found['stamp']})
            if len(scans)>200:raise ValueError('Choose exports containing at most 200 setups.')
    return dict(path=str(Path(path).resolve()),scans=scans)


def raw_stamp(info):
    return hashlib.sha256(json.dumps([info['root'],[(x.get('id'),x.get('metadata_sha256'),x.get('source_bytes')) for x in info['setups']]],sort_keys=True).encode()).hexdigest()


def inspect_raw(path):
    info=rtc360_folder.discover(path)
    return {**info,'stamp':raw_stamp(info),'converters':converter_candidates(),'licence_notice':LICENCE_NOTICE}


def source_inventory(root):
    root=Path(root).resolve();files=[];total=0
    for folder,dirs,names in os.walk(root,followlinks=False):
        for name in dirs+names:
            p=Path(folder)/name
            if p.is_symlink():raise ValueError('The scanner folder contains a linked file/folder. Use a complete local copy.')
        for name in sorted(names):
            p=Path(folder)/name
            # Completed exports are not scanner input and must never be mistaken
            # for freshly converted output in the new staging directory.
            if p.suffix.lower()=='.e57':continue
            if not p.is_file():raise ValueError('Unsupported file in scanner folder: '+str(p))
            st=p.stat();files.append((p,p.relative_to(root),st.st_size,st.st_mtime_ns));total+=st.st_size
            if len(files)>200000:raise ValueError('Too many files. Choose one scanner job folder.')
    if not files:raise ValueError('Scanner folder is empty.')
    return files,total


def convert_raw(s,body,progress):
    info=inspect_raw(body['path'])
    if info['stamp']!=body.get('raw_stamp'):raise ValueError('The scanner folder changed. Inspect it again.')
    if any(x.get('error') or x.get('missing') for x in info['setups']):raise ValueError('Some scanner files are missing or invalid. Download/copy the complete folder first.')
    exe=validate_converter(body.get('converter',''))
    root=Path(info['root']);base=asset_folder(s.WORK,'export').resolve()/'leica_conversions'
    if base.is_relative_to(root):raise ValueError('Choose the scanner job folder, not the Tunnel Studio project/cache folder.')
    base.mkdir(parents=True,exist_ok=True)
    if os.name=='nt' and str(base).startswith('\\\\'):raise ValueError('Leica conversion needs a local project drive, not a network/NAS path.')
    files,total=source_inventory(root)
    if shutil.disk_usage(base).free < total+2*1024**3:raise ValueError('Not enough local space for a scanner copy plus conversion. Free at least the input size plus 2 GiB; large jobs need more.')
    stage=base/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]);stage.mkdir()
    manifest=stage/'tunnel_conversion.json'
    record=dict(version=1,status='copying',source=str(root),raw_stamp=info['stamp'],expected_setups=len(info['setups']),converter=str(exe))
    def save_record():
        temp=manifest.with_suffix('.tmp');temp.write_text(json.dumps(record,indent=2),encoding='utf8');os.replace(temp,manifest)
    save_record();copied=0
    try:
        for source,relative,size,mtime in files:
            destination=stage/root.name/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            before=source.stat()
            if (before.st_size,before.st_mtime_ns)!=(size,mtime):raise ValueError('A scanner source changed while copying. Retry with a completed scan folder.')
            with source.open('rb') as src,destination.open('xb') as out:
                while block:=src.read(4*1024**2):
                    progress(f'Copying scanner data: {copied/1024**2:,.0f} / {total/1024**2:,.0f} MiB');out.write(block);copied+=len(block)
            after=source.stat()
            if (after.st_size,after.st_mtime_ns)!=(size,mtime):raise ValueError('A scanner source changed while copying.')
        record['status']='converting';save_record()
        command=[str(exe),'-regexp2e57',str(stage),'deleteproject=N','panores=2048']
        record['command']=command;save_record()
        run_process(command,progress,'REGISTER 360 is converting and registering the folder',cwd=exe.parent,log=stage/'converter.log')
        result=inspect_exports(stage,progress)
        if len(result['scans'])!=len(info['setups']):
            raise ValueError(f'Leica produced {len(result["scans"])} E57 setups for {len(info["setups"])} scanner setups. Review its result before import. Exports are kept at {stage}')
        record.update(status='complete',scans=result['scans']);save_record()
        s.STATE['last_leica_conversion']=dict(folder=str(stage),count=len(result['scans']),source=str(root),raw_stamp=info['stamp']);s.save()
        return result,stage
    except BaseException as exc:
        record.update(status='incomplete',error=str(exc));save_record()
        # Retain partial diagnostics but never treat this conversion as complete.
        import jobdata
        if isinstance(exc,(jobdata.Cancelled,getattr(s,'OperationCancelled',jobdata.Cancelled),KeyboardInterrupt,SystemExit)):raise
        raise ValueError(str(exc)+'\nConversion working folder: '+str(stage)) from exc


def import_exports(s,found,chosen,progress,import_id,conversion_folder=None,raw_source_stamp=None):
    import registration_workspace as rw
    if not isinstance(chosen,list) or not chosen or len(chosen)!=len(set(chosen)):raise ValueError('Select one or more E57 setups.')
    selected=[x for x in found['scans'] if x['id'] in chosen]
    if len(selected)!=len(chosen):raise ValueError('The selected E57 files changed. Inspect the exports again.')
    existing={x.get('leica_e57_source_id') for x in s.STATE.get('job_items',[])}
    if existing.intersection(chosen):raise ValueError('One of these E57 setups is already imported. Deselect it before importing again.')
    old=copy.deepcopy(s.STATE);created=[];pending=[];warnings=[]
    try:
        for ordinal,scan in enumerate(selected,1):
            progress(f'Importing {scan["name"]} · setup {ordinal}/{len(selected)}')
            dest=asset_folder(s.WORK,'imported')/(uuid.uuid4().hex+'.xyz64')
            created.extend([dest,dest.with_suffix('.attr32'),dest.with_suffix('.preview64'),dest.with_suffix('.setup-photo.dat'),Path(str(dest)+'.viewindex.npz')])
            result=worker(['import',scan['path'],str(dest),str(scan['index']),scan['stamp']],progress)
            m=result['meta'];m['name']=scan['name'];warnings.extend(m.get('warnings',[]))
            entry=dict(id=uuid.uuid4().hex,kind='scan',name=m['name'],meta=m,original_meta=copy.deepcopy(m),visible=True,
                source_bytes=Path(scan['path']).stat().st_size,position_status=m['position_status'],
                hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[],leica_e57_source_id=scan['id'],
                scan_origin=result['scan_origin'],scan_orientation=result['scan_orientation'],e57_pose=result['e57_pose'])
            if result.get('setup_photo'):entry['setup_photo']=result['setup_photo']
            if conversion_folder:entry['leica_conversion_folder']=str(conversion_folder)
            if raw_source_stamp:entry['leica_raw_stamp']=raw_source_stamp
            pending.append(entry)
        progress('Saving imported E57 setups…')
        s.STATE.setdefault('job_items',[]).extend(pending)
        s.STATE['last_leica_import']=dict(id=import_id,count=len(pending),point_count=sum(x['meta']['count'] for x in pending),
            items=[dict(id=x['id'],name=x['name'],count=x['meta']['count']) for x in pending],warnings=warnings,
            conversion_folder=str(conversion_folder) if conversion_folder else None,mode='e57_bridge')
        rw.activate(s,pending[0]['id']);s.save()
    except BaseException:
        s.STATE.clear();s.STATE.update(old)
        for path in created:path.unlink(missing_ok=True)
        raise


def run_import(s,body):
    import job_api
    progress=lambda message:job_api.progress(s,message)
    if body.get('route')=='convert':
        if any(x.get('leica_raw_stamp')==body.get('raw_stamp') and x.get('leica_raw_stamp') for x in s.STATE.get('job_items',[])):
            raise ValueError('This scanner job is already imported through conversion. Use its existing scans, or remove them before converting again.')
        found,folder=convert_raw(s,body,progress)
        return import_exports(s,found,[x['id'] for x in found['scans']],progress,body['import_id'],folder,body['raw_stamp'])
    if body.get('route')=='existing':
        found=inspect_exports(body['exports_path'],progress)
        return import_exports(s,found,body.get('scans'),progress,body['import_id'])
    raise ValueError('Choose automatic conversion or existing E57 exports.')


def api(s,h,body):
    import job_api
    action=body.get('action')
    if action=='bridge_status':h.send(dict(converters=converter_candidates(),licence_notice=LICENCE_NOTICE,last_conversion=s.STATE.get('last_leica_conversion')))
    elif action=='bridge_inspect_exports':h.send(inspect_exports(body.get('exports_path','')))
    elif action=='bridge_import':
        request={**body,'import_id':uuid.uuid4().hex};job_api.CANCEL.clear()
        s.start_job(lambda:run_import(s,request),kind='job',native_progress=True);h.send(dict(started=True,import_id=request['import_id']))
    else:raise ValueError('Unknown Leica bridge operation.')
