# Copyright (c) 2026 Damian Gulczynski.
"""Start empty while preserving one hidden last-session recovery manifest.

Recovery metadata is deliberately not exposed as a normal named project.
"""
from datetime import datetime
from pathlib import Path
import os
import shutil
import uuid
import projects


def has_work(data):
    if not isinstance(data,dict):return False
    keys=('gis_resources','axis','cloud','job_items','rings','structures','cad','surface_source',
          'surface_result','analysis_tool_result','report_settings','project_id')
    registration=data.get('registration') or {}
    return any(bool(data.get(key)) for key in keys) or (
        isinstance(registration,dict) and any(registration.get(key) for key in ('cloud','rows','candidates','transformed')))


def _prune_hidden_recovery(folder, keep):
    """Keep only the newest hidden startup recovery manifest.

    Recovery JSON is tiny, but every old manifest can protect gigabytes of historical
    caches from storage cleanup.  One last-session recovery is enough for startup
    rollback and keeps previous sessions out of the normal project model.
    """
    keep=Path(keep).resolve()
    for path in Path(folder).glob('workspace_*.json'):
        try:
            if path.resolve()!=keep:path.unlink(missing_ok=True)
        except OSError:pass


def begin(server, empty_state, save_named):
    """Called once after acquiring the process lock, before serving any requests.

    Never load the old project into the active STATE. A hidden recovery copy is
    written before changing project.json.  It is intentionally not added to Open
    Project. Existing named projects and cached survey data are never overwritten.
    """
    work=Path(server.WORK)
    previous=work/'project.json'
    warning=None
    if previous.is_file():
        read_error=None
        try:
            data=projects.read_json(previous)
            if not isinstance(data,dict):raise ValueError('Expected project object.')
        except (ValueError,UnicodeError,RecursionError) as error:
            data=None;read_error=str(error)
        if data is None or has_work(data):
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
            folder=work/'startup_recovery';folder.mkdir(parents=True,exist_ok=True)
            recovery=folder/('workspace_'+stamp+'_'+uuid.uuid4().hex[:8]+'.json')
            # Fail before clearing anything if the recovery copy cannot be saved.
            try:
                with previous.open('rb') as source,recovery.open('xb') as stream:
                    shutil.copyfileobj(source,stream,length=1024*1024)
                    stream.flush();os.fsync(stream.fileno())
                if recovery.stat().st_size!=previous.stat().st_size:
                    raise OSError('The previous workspace changed size while making its recovery copy.')
            except BaseException:
                recovery.unlink(missing_ok=True);raise
            _prune_hidden_recovery(folder,recovery)
            if data is None:
                warning=('The previous autosave could not be opened ('+str(previous.stat().st_size)+
                         ' bytes). '+str(read_error or '')+' Its complete hidden recovery copy was kept at '+
                         str(recovery)+'. A new empty project is open.')
            # Valid previous work is intentionally silent: the hidden recovery manifest
            # exists for crash recovery, but no duplicate named project is created.
    old_state=dict(server.STATE);old_selection=getattr(server,'SELECTION',None)
    server.STATE.clear();server.STATE.update(empty_state());server.SELECTION=None
    try:server.save()
    except BaseException:
        server.STATE.clear();server.STATE.update(old_state);server.SELECTION=old_selection;raise
    server.JOB.update(message='Ready — new empty project',error=warning)
