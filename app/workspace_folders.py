# Copyright (c) 2026 Damian Gulczynski.
"""Select an owned project data root once, before starting the local server."""
import contextlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

MARKER = '.tunnelstudio-workspace.json'
CATALOGUE = 'workspace-folders.json'
_CATALOGUE_LOCK = threading.Lock()
_LIMIT = 256 * 1024
_APP = Path(__file__).resolve().parent
_ID = re.compile(r'[a-f0-9]{32}')


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError('Enter a project folder name of 1–100 characters.')
    value = value.strip()
    if (value in ('.','..') or value.endswith('.') or
            any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value) or
            value.partition('.')[0].upper() in {'CON','PRN','AUX','NUL','CONIN$','CONOUT$',
              *(f'{k}{n}' for k in ('COM','LPT') for n in range(1,10))}):
        raise ValueError('Choose a valid Windows folder name, without path separators.')
    return value


def _read(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise ValueError('The storage settings path is a link. Review the folder in Explorer.')
    with path.open('rb') as stream:
        value = stream.read(_LIMIT + 1)
    if len(value)>_LIMIT:
        raise ValueError('The project folder settings exceed their size limit.')
    data = json.loads(value)
    if not isinstance(data,dict):
        raise ValueError('The project folder settings are invalid.')
    return data


def _record(data):
    if not _ID.fullmatch(str(data.get('id',''))):
        raise ValueError('The project folder identity is invalid.')
    return dict(id=data['id'], name=_name(data.get('name')))


def _marker(root):
    m = _read(root/MARKER)
    if m.get('format')!='TunnelStudioWorkspace' or m.get('version')!=1:
        raise ValueError('This folder has no supported Tunnel Studio workspace marker.')
    return _record(m)


def managed_root(path):
    try:
        _marker(Path(path))
        return True
    except (OSError, TypeError, ValueError, RecursionError):
        return False


def settings_folder(shared_root):
    # Keep the settings separate from every data workspace. A non-default
    # TUNNEL_STUDIO_DATA (including installer smoke fixtures) gets its own
    # sibling settings namespace. Merely starting the app creates no settings.
    shared_root=Path(shared_root).resolve()
    name='TunnelStudioSettings' if shared_root.name.casefold()=='tunnelstudio' else shared_root.name+'.TunnelStudioSettings'
    base=shared_root.parent/name
    if base.is_symlink() or getattr(base,'is_junction',lambda:False)():
        raise ValueError('The Tunnel Studio settings folder must be a real directory.')
    return base


def _registry(catalogue):
    try:
        raw = _read(settings_folder(catalogue)/CATALOGUE)
    except FileNotFoundError:
        return {'version':1,'selected':'shared','folders':[]}
    if raw.get('version')!=1 or not isinstance(raw.get('folders'),list) or len(raw['folders'])>256:
        raise ValueError('The project folder catalogue is invalid.')
    names=set()
    for entry in raw['folders']:
        if not isinstance(entry,dict):
            raise ValueError('The project folder catalogue contains an invalid entry.')
        ident=_record(entry)['id']
        if ident in names or not isinstance(entry.get('path'),str) or not Path(entry['path']).is_absolute():
            raise ValueError('The project folder catalogue contains an invalid path or duplicate.')
        names.add(ident)
    if raw.get('selected')!='shared' and raw.get('selected') not in names:
        raise ValueError('The selected project folder is missing from the catalogue.')
    return raw


def _nested(a,b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def _locations(catalogue):
    # Protect the whole installation tree, including older versions and runtime.
    release = _APP.parent
    install = release.parent.parent if release.parent.name.lower()=='releases' else release
    protected=[install.resolve(),Path(catalogue).resolve(),settings_folder(catalogue)]
    if os.name=='nt':
        for key in ('SystemRoot','ProgramFiles','ProgramFiles(x86)','ProgramData'):
            if os.environ.get(key):protected.append(Path(os.environ[key]).resolve())
    return protected


def _validate_location(path,catalogue):
    root=Path(path)
    if not root.is_absolute() or root.is_symlink() or getattr(root,'is_junction',lambda:False)():
        raise ValueError('Choose a real absolute project folder, outside the installation and shared data folder.')
    candidate=root.resolve()
    if candidate==Path(candidate.anchor) or any(_nested(candidate,q) for q in _locations(catalogue)):
        raise ValueError('Choose a separate project folder outside Tunnel Studio’s installation and shared data folder.')
    return candidate


def _active(entry,catalogue):
    root=_validate_location(entry['path'],catalogue)
    if _marker(root)['id']!=entry['id']:
        raise ValueError('The folder is on a different drive or its project identity changed.')
    return root


def startup():
    """Return (catalogue, active root, notice). No missing external folders are created."""
    base=Path(os.environ.get('TUNNEL_STUDIO_DATA') or
              (Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'TunnelStudio')).resolve()
    # Child jobs and browser recovery inherit the process's selected root. If
    # a tool overrides DATA itself (e.g. startup self-check), honour that root.
    if str(base)==os.environ.get('TUNNEL_STUDIO_SESSION_ROOT') and os.environ.get('TUNNEL_STUDIO_PROFILE_ROOT'):
        catalogue=Path(os.environ['TUNNEL_STUDIO_PROFILE_ROOT']).resolve()
    else:
        catalogue=base
    active=catalogue
    warning=''
    try:
        settings=_registry(catalogue)
        if settings['selected']!='shared':
            entry=next(e for e in settings['folders'] if e['id']==settings['selected'])
            active=_active(entry,catalogue)
    except (OSError,ValueError,RecursionError) as error:
        warning=('The chosen project folder is unavailable. This session uses the shared data folder. '
                 'Check the project drive under Project folders. '+str(error))
    os.environ['TUNNEL_STUDIO_PROFILE_ROOT']=str(catalogue)
    os.environ['TUNNEL_STUDIO_SESSION_ROOT']=str(active)
    os.environ['TUNNEL_STUDIO_DATA']=str(active)
    return catalogue,active,warning


@contextlib.contextmanager
def _locked(catalogue):
    with _CATALOGUE_LOCK:
        directory=settings_folder(catalogue)
        directory.mkdir(exist_ok=True)
        lock=directory/'workspace-folders.lock'
        if lock.is_symlink() or getattr(lock,'is_junction',lambda:False)():
            raise ValueError('The project folder settings lock must be a regular file.')
        with lock.open('a+b') as f:
            if os.name=='nt':
                import msvcrt
                if f.seek(0,os.SEEK_END)==0:
                    f.write(b'0');f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                try:yield
                finally:
                    f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def _save(catalogue,data):
    import projects
    if len(json.dumps(data).encode())>_LIMIT-2048:
        raise ValueError('The project folder catalogue is full.')
    projects.atomic_json(settings_folder(catalogue)/CATALOGUE,data)


def _no_overlap(root,registry):
    for e in registry['folders']:
        if _nested(root,Path(e['path']).resolve()):
            raise ValueError('Registered project folders must be separate. Choose a different destination.')


def new_path(catalogue,parent,name):
    if not isinstance(parent,(Path,str)) or not str(parent) or not Path(parent).is_absolute():
        raise ValueError('Choose an absolute parent folder on the destination drive.')
    base=Path(parent).resolve(strict=True)
    if not base.is_dir():
        raise ValueError('The destination parent must be an existing folder.')
    dest=_validate_location(base/_name(name),catalogue)
    # Reject nesting even within a separately owned, unregistered workspace.
    if any((ancestor/MARKER).exists() for ancestor in (base,*base.parents)):
        raise ValueError('Choose a parent outside other project folders.')
    _no_overlap(dest,_registry(catalogue))
    if os.path.lexists(dest):
        raise ValueError('This project folder already exists. Choose a new name or select its existing entry.')
    return dest


def initialize_root(root,name):
    root=Path(root)
    name=_name(name)
    entry=dict(id=uuid.uuid4().hex,name=name,path=str(root.resolve()))
    root.mkdir(exist_ok=False)
    try:
        marker=dict(format='TunnelStudioWorkspace',version=1,id=entry['id'],name=entry['name'])
        with (root/MARKER).open('x',encoding='utf8') as f:
            json.dump(marker,f);f.flush();os.fsync(f.fileno())
        from project_data_layout import create_categories
        create_categories(root)
    except BaseException:
        _undo_new(root,entry)
        raise
    return entry


def _undo_new(root,entry):
    # Remove only the known empty category tree. Never follow reparse points or
    # traverse unrelated folders that appeared during publication.
    from project_data_layout import CATEGORIES,is_link
    try:
        if is_link(root) or _marker(root)['id']!=entry['id']:return
        paths=set()
        for category in CATEGORIES.values():
            relative=Path(category)
            paths.add(relative)
            paths.update(p for p in relative.parents if p.parts)
        for relative in sorted(paths,key=lambda p:len(p.parts),reverse=True):
            components=[root/relative, *(root/p for p in relative.parents if p.parts)]
            try:
                if not any(is_link(q) for q in components):
                    (root/relative).rmdir()
            except OSError:pass
        marker=root/MARKER
        if set(root.iterdir())=={marker}:
            marker.unlink()
            root.rmdir()
    except (OSError,ValueError):pass


def register_root(catalogue,entry,select=True):
    root=_active(entry,catalogue)
    with _locked(catalogue):
        registry=_registry(catalogue)
        _no_overlap(root,registry)
        if len(registry['folders'])>=256:
            raise ValueError('The project folder list is full.')
        if any(e['id']==entry['id'] for e in registry['folders']):
            raise ValueError('This project folder identity is already registered.')
        registry['folders'].append(dict(**_marker(root),path=str(root)))
        if select:registry['selected']=entry['id']
        _save(catalogue,registry)
    return entry


def select(catalogue,ident):
    with _locked(catalogue):
        data=_registry(catalogue)
        if ident!='shared':
            entry=next((a for a in data['folders'] if a['id']==ident),None)
            if entry is None:raise ValueError('Choose a project folder from the list.')
            _active(entry,catalogue)
        data['selected']=ident
        _save(catalogue,data)


def overview(s):
    catalogue=Path(s.WORKSPACE_CATALOGUE)
    active=Path(s.WORK).resolve()
    warning=getattr(s,'WORKSPACE_NOTICE','')
    try:
        registry=_registry(catalogue)
    except (ValueError,OSError,RecursionError) as err:
        registry={'folders':[],'selected':'shared'}
        warning=warning or 'The project folder settings cannot be read: '+str(err)
    from project_data_layout import CATEGORIES
    return dict(configuration_file=str(settings_folder(catalogue)/CATALOGUE),
                categories=list(CATEGORIES.values()),current_root=str(active), shared_root=str(catalogue), dedicated=active!=catalogue,
                folders=registry['folders'], next_id=registry['selected'],
                notice=warning)


def request(s,body=None):
    if body is not None:
        if s.JOB.get('busy'):raise ValueError('Wait for the current job to finish before choosing a project folder.')
        catalogue=s.WORKSPACE_CATALOGUE
        if body.get('action')=='create':
            root=new_path(catalogue,body.get('parent'),body.get('name'))
            entry=initialize_root(root,body['name'])
            try:register_root(catalogue,entry)
            except BaseException:
                _undo_new(root,entry)
                raise
        elif body.get('action')=='select':
            select(catalogue,body.get('id'))
        else:raise ValueError('Choose Create or Use after restart.')
    return overview(s)
