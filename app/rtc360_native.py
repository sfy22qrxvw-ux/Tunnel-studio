# Copyright (c) 2026 Damian Gulczynski.
"""Transactional direct raw import; no Leica executable, SDK, or E57 required."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy, shutil, sys, uuid
from rtc360_source import inspect
from rtc360_optics import NOTICE

POSITION_NOTICE = ('Setups imported at saved scanner positions. These are initial odometry positions, '
                   'not survey control. Refine alignment with cloud-to-cloud or target registration.')
LOCAL_POSITION_WARNING = ('Setups are imported separately at their own scanner origins with recorded tilt. '
                          'No VIS/world position or registration is inferred.')


def position_mode(body):
    # Match the existing saved-project API's mode key. Omitted mode retains the
    # legacy raw-import behaviour for older clients and scripted callers.
    mode = body.get('mode', 'unregistered')
    if mode not in ('registered', 'unregistered'):
        raise ValueError('Choose saved scanner positions or unregistered setups.')
    return mode


def worker(folder,dest,stamp,progress):
    from leica_bridge import run_process
    exe=Path(sys.executable);console=exe.with_name('python.exe')
    return run_process([console if console.is_file() else exe,Path(__file__).with_name('rtc360_native_worker.py'),
                        'import',folder,dest,stamp],progress,'Converting RTC360 raw data',json_result=True)


def run_import(s,body):
    import job_api,registration_workspace as rw
    import rtc360_vis_pose as vis
    mode=position_mode(body)
    progress=lambda message:job_api.progress(s,message)
    cancel=lambda:job_api.CANCEL.is_set() or bool(getattr(s,'JOB_CANCEL_FILE',None) and s.JOB_CANCEL_FILE.exists())
    info=inspect(body['path']);chosen=body.get('setups');stamps=body.get('setup_stamps',{})
    if not isinstance(chosen,list) or not chosen or any(not isinstance(x,str) for x in chosen) or len(chosen)!=len(set(chosen)):
        raise ValueError('Select one or more scanner setups.')
    selected=[x for x in info['setups'] if x.get('id') in chosen]
    if len(selected)!=len(chosen) or any(not x.get('native_available') or stamps.get(x['id'])!=x.get('stamp') for x in selected):
        raise ValueError('The selected scanner setups changed or are unsupported. Inspect the folder again.')
    existing={x.get('rtc360_setup_guid') for x in s.STATE.get('job_items',[]) if x.get('rtc360_setup_guid')}
    if existing.intersection(chosen):raise ValueError('A selected raw setup is already imported. Deselect it or remove its scene item before reimporting.')
    needed=sum(x['cache_bytes_estimate'] for x in selected)+512*1024**2
    if shutil.disk_usage(s.WORK).free<needed:
        raise ValueError(f'The selected full-resolution setups need approximately {needed/1024**3:.2f} GiB free in the working-cache drive.')
    old=copy.deepcopy(s.STATE);created=[];pending=[];warnings=[];positions=[]
    try:
        for index,setup in enumerate(selected,1):
            progress(f'Converting {setup["name"]} · setup {index}/{len(selected)}')
            dest=asset_folder(s.WORK,'imported')/(uuid.uuid4().hex+'.xyz64')
            created.extend((dest,dest.with_suffix('.attr32'),dest.with_suffix('.preview64'),dest.with_suffix('.setup-photo.dat'),Path(str(dest)+'.viewindex.npz')))
            result=worker(setup['folder'],dest,setup['stamp'],progress);m=result['meta']
            m.setdefault('source_name',Path(setup['folder']).name);m.setdefault('source_path',str(Path(setup['folder']).resolve()))
            m.setdefault('source_format','rtc360_raw_experimental');m.setdefault('source_bytes',int(setup.get('source_bytes') or 0))
            progress(f'{m["name"]}: preparing reusable intensity overview…')
            __import__('jobdata')._source_overview(m,build=True,progress=progress)
            entry=dict(id=uuid.uuid4().hex,kind='scan',name=m['name'],meta=m,original_meta=copy.deepcopy(m),visible=True,
                       source_bytes=setup['source_bytes'],position_status=m['position_status'],hidden_boxes=[],isolate_box=None,
                       edit_history=[],redo_history=[],rtc360_setup_guid=setup['id'],rtc360_source_stamp=setup['stamp'],
                       scan_origin=result['scan_origin'],scan_orientation=result['scan_orientation'],
                       photo_notice='Experimental raw reconstruction. Photo alignment and factory calibration remain unverified.')
            if result.get('setup_photo'):entry['setup_photo']=result['setup_photo']
            if mode=='registered':
                progress(f'Positioning {m["name"]} at its saved scanner position…')
                out=vis.apply_pose(m,setup['folder'],progress,cancel)
                # Never turn a cancellation into an unavailable-pose warning.
                progress(f'Checking saved position result for {m["name"]}…')
                positioned=bool(out.get('applied'))
                if positioned:
                    m['warnings']=[w for w in m.get('warnings',[]) if w!=LOCAL_POSITION_WARNING]
                    entry.update(scan_origin=out['translation'],scan_orientation=out['scanner_rotation'],
                                 position_status=m['position_status'])
                else:
                    reason=out.get('reason','Saved scanner position unavailable.')
                    m.setdefault('warnings',[]).append(f'{m["name"]}: saved position was not applied; imported at its scanner origin. {reason}')
                positions.append(dict(id=entry['id'],name=m['name'],applied=positioned,
                                      reason=out.get('reason'),method=out.get('method')))
            warnings.extend(m.get('warnings',[]))
            pending.append(entry)
        progress('Saving directly imported RTC360 setups…')
        s.STATE.setdefault('job_items',[]).extend(pending)
        positioned=sum(x['applied'] for x in positions)
        notice=(POSITION_NOTICE if positioned==len(pending) else
                f'{positioned} of {len(pending)} setups imported at saved scanner positions; '
                f'{len(pending)-positioned} remain at their scanner origins. Review the warnings and use RTC360 starting positions to retry. '
                'Saved positions are initial odometry, not survey control. Refine with cloud-to-cloud or target registration.') if mode=='registered' else 'Setups imported unregistered at their scanner origins.'
        s.STATE['last_leica_import']=dict(id=body['import_id'],mode='rtc360_native_experimental',count=len(pending),
            position_mode=mode,positioned_count=positioned,position_results=positions,position_notice=notice,
            point_count=sum(x['meta']['count'] for x in pending),items=[dict(id=x['id'],name=x['name'],count=x['meta']['count']) for x in pending],
            warnings=list(dict.fromkeys(warnings)))
        rw.activate(s,pending[0]['id']);s.save()
    except BaseException:
        s.STATE.clear();s.STATE.update(old)
        for path in created:path.unlink(missing_ok=True)
        # Temporary EXR files live only under a per-worker temporary directory.
        # Worker termination is handled by the shared cancellable process runner.
        raise
    finally:
        for path in created:
            if path.suffix=='.dat':
                for temp in path.parent.glob(path.stem+'_photos_*'):
                    if temp.is_dir():shutil.rmtree(temp,ignore_errors=True)


def api(s,h,body):
    import job_api
    if body.get('action')!='native_import':raise ValueError('Unknown direct raw operation.')
    request={**body,'mode':position_mode(body),'import_id':uuid.uuid4().hex};job_api.CANCEL.clear()
    s.start_job(lambda:run_import(s,request),kind='job',native_progress=True)
    h.send(dict(started=True,import_id=request['import_id']))
