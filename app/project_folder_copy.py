"""Copy one open named project into a new, organised working folder."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import uuid

import project_data_layout as layout
import projects
import workspace_folders


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _asset(work, value):
    path = Path(value)
    if not path.is_absolute():
        path = work/path
    if '..' in path.parts or not path.is_relative_to(work):
        raise ValueError(f'Project asset is outside the active workspace: {value}')
    for component in (path, *path.parents):
        if component == work:
            break
        if layout.is_link(component):
            raise ValueError(f'Linked project assets cannot be copied automatically: {path}')
    if not stat.S_ISREG(path.stat().st_mode) or path.suffix.lower() in ('.exe','.dll','.bat','.cmd','.ps1','.py','.pyc'):
        raise ValueError(f'This project record references a non-data file: {path}')
    return path


def _plan(s, body):
    import workflows
    if not s.STATE.get('project_id'):
        raise ValueError('Open the named project you want to copy, or save the current project first.')
    work = Path(s.WORK).resolve(strict=True)
    name = body.get('name')
    destination = workspace_folders.new_path(s.WORKSPACE_CATALOGUE,body.get('parent'),name)
    state = workflows.serial(s)
    for key in ('last_backup','_project_import'):
        state.pop(key,None)
    roles = layout.category_for_state_asset_map(state)
    files = {}
    aliases = {}
    locations = {}
    def collect(raw):
        path = _asset(work,raw)
        aliases[raw]=path
        if path in files:
            return raw
        category=Path(layout.CATEGORIES[roles.get(raw,'working')])
        files[path]=_signature(path.stat())
        # Keep dataset family basenames and their common directory together.
        # Separate source directories with equal basenames also remain separate.
        parent=path.parent.relative_to(work)
        if parent.is_relative_to(category):
            parent=parent.relative_to(category)
        locations[path]=category/parent/path.name
        return raw
    projects.visit(state,collect)
    # Historical recovery JSON can hold the last reference to a surviving scan
    # or result. Copy the surviving local assets without reactivating a damaged
    # item. Keep missing source paths as information for a later recovery.
    for held in (state.get('project_recovery') or {}).get('unavailable',[]):
        if 'original_data_json' not in held:continue
        record=json.loads(held['original_data_json'])
        def recovery_asset(raw):
            candidate=Path(raw)
            if not candidate.is_absolute():candidate=work/candidate
            if not candidate.is_file():return raw
            if not candidate.is_relative_to(work):
                raise ValueError('A recovery record still needs an external asset: '+raw)
            return collect(raw)
        projects.visit(record,recovery_asset)
    # Shapefile companions must retain the original common basename.
    gis_rel=layout.gis_member_names(state)
    for raw,family_name in gis_rel.items():
        if raw in aliases:locations[aliases[raw]]=Path(layout.CATEGORIES['gis'])/family_name
    if len({str(rel).casefold() for rel in locations.values()}) != len(files):
        raise ValueError('Two linked assets would have the same destination. Review their paths before copying.')
    digest=hashlib.sha256()
    for block in projects._json_chunks(state):
        digest.update(block)
    digest.update(str(destination).encode())
    for path,sig in sorted(files.items()):
        digest.update(repr((str(path),sig)).encode())
    needed=sum(sig[2] for sig in files.values())
    return dict(state=state, files=files, aliases=aliases, paths=locations, dest=destination, name=name,
                token=digest.hexdigest(), bytes=needed, free=shutil.disk_usage(destination.parent).free)


def _rename_new(source,dest):
    # Windows rename refuses an existing target. On Linux use the equivalent
    # NOREPLACE flag so even an empty user directory created mid-copy survives.
    if os.name=='nt':
        os.rename(source,dest)
    elif sys.platform.startswith('linux'):
        fn=getattr(ctypes.CDLL(None,use_errno=True),'renameat2',None)
        if fn is None:raise OSError('Exclusive directory publication is unavailable on this system.')
        if fn(-100,os.fsencode(source),-100,os.fsencode(dest),1)!=0:
            error=ctypes.get_errno()
            raise OSError(error,os.strerror(error),str(dest))
    else:
        raise OSError('Organised project copying requires Windows or Linux.')


def _copy_verified(path, target, signature, progress, label):
    target.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256()
    with path.open('rb') as src,target.open('xb') as dst:
        if _signature(os.fstat(src.fileno()))!=signature:raise ValueError('An asset changed. Review the copy again.')
        projects._copy_stream(src,dst,signature[2],progress,label,digest)
        dst.flush();os.fsync(dst.fileno())
        if _signature(os.fstat(src.fileno()))!=signature:raise ValueError('An asset changed during copying.')
    second=hashlib.sha256()
    with target.open('rb') as stream:
        amount=0
        while chunk:=stream.read(4*1024*1024):
            second.update(chunk);amount+=len(chunk)
            progress(f'Verifying {target.name} · {amount:,}/{signature[2]:,} bytes')
    if digest.digest()!=second.digest():raise OSError('Destination verification failed: '+str(target))


def _mapped(plan,root):
    replacements={raw:str(root/plan['paths'][path]) for raw,path in plan['aliases'].items()}
    # A source cache can also be mentioned by a legacy provenance key, an
    # edit fingerprint or a signature list outside the normal asset fields.
    def walk(value):
        if isinstance(value,dict):
            return {k:(v if k=='project_recovery' else walk(v)) for k,v in value.items()}
        if isinstance(value,list):return [walk(v) for v in value]
        if isinstance(value,str):return replacements.get(value,value)
        return value
    state=walk(plan['state'])
    if state.get('project_recovery'):
        import copy
        recovery=copy.deepcopy(state['project_recovery'])
        for held in recovery.get('unavailable',[]):
            if 'original_data_json' in held:
                held['original_data_json']=json.dumps(walk(json.loads(held['original_data_json'])),allow_nan=False)
        state['project_recovery']=recovery
    # A retained count signature includes the old inode and timestamps.
    # Restored full-density sources are authoritative on the new drive.
    projects._unpack_retained_caches(state,{})
    return state


def _copy(s,plan,job_id):
    import workflows
    dest=plan['dest']
    if plan['bytes']+64_000_000>shutil.disk_usage(dest.parent).free:
        raise ValueError('The destination needs space for the project copy plus 64 MB reserve.')
    with tempfile.TemporaryDirectory(prefix='.ts-project-copy-',dir=dest.parent) as temporary:
        staging=Path(temporary)/'project'
        entry=workspace_folders.initialize_root(staging,plan['name'])
        for i,(path,signature) in enumerate(plan['files'].items(),1):
            label=f'Copying project asset {i}/{len(plan["files"])}'
            s.progress(label)
            _copy_verified(path,staging/plan['paths'][path],signature,s.progress,label)
        s.progress('Checking the copied scans, meshes, outputs and edit records…')
        staging_state=_mapped(plan,staging)
        checked=workflows.prepare_data(SimpleNamespace(WORK=staging,progress=s.progress),staging_state)
        old=staging_state.get('project_recovery') or {}
        new=checked.get('project_recovery') or {}
        if len(new.get('unavailable',[]))>len(old.get('unavailable',[])):
            raise ValueError('A copied asset failed the project opening checks. The original project remains available.')
        final_state=_mapped(plan,dest)
        saved=projects.save_named(staging,final_state,plan['name'],as_new=True)
        for path,sig in plan['files'].items():
            if _signature(path.stat())!=sig:raise ValueError('The source project changed while copying. Review the copy again.')
        s.progress('Publishing verified project folder…')
        _rename_new(staging,dest)
    entry['path']=str(dest)
    try:
        workspace_folders.register_root(s.WORKSPACE_CATALOGUE,entry,select=True)
    except (OSError,ValueError) as error:
        raise ValueError(f'The verified copy is at {dest}, but its folder could not be activated: {error}') from error
    s.PROJECT_FOLDER_COPY=dict(id=job_id,destination=str(dest),project_id=saved['project_id'],
                               project_title=saved['project_title'],bytes=plan['bytes'],files=len(plan['files']))


def request(s,body):
    if body.get('action')=='receipt':
        return getattr(s,'PROJECT_FOLDER_COPY',{}) or {}
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    plan=_plan(s,body)
    if body.get('action')=='review':
        return dict(token=plan['token'],bytes=plan['bytes'],files=len(plan['files']),destination=str(plan['dest']),
                    free=plan['free'],enough=plan['bytes']+64_000_000<=plan['free'])
    if body.get('action')!='copy' or not body.get('token') or body.get('token')!=plan['token']:
        raise ValueError('This project or destination changed. Review the copy again.')
    job_id=uuid.uuid4().hex
    s.start_job(lambda:_copy(s,plan,job_id),'project-folder-copy',native_progress=True)
    return dict(started=True,id=job_id)
