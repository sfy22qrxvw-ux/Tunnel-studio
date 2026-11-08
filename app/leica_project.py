"""Read-only import of the observed Register 360 project/database layout."""
from pathlib import Path,PureWindowsPath
import copy,hashlib,sqlite3,uuid,os
import numpy as np
from scipy.spatial.transform import Rotation
import jobdata,registration_workspace as rw,leica_catalog

def inside(root,name):
    rel=str(name).replace('\\','/')
    if PureWindowsPath(rel).is_absolute() or '..' in Path(rel).parts:raise ValueError('Project references a file outside its folder.')
    p=(root/rel).resolve()
    if not p.is_relative_to(root):raise ValueError('Project file escapes its folder.')
    return p

def deliverable_databases(root,max_depth=5,max_entries=20000):
    """Bounded, read-only folder discovery; never choose by filename/date."""
    root=Path(root).resolve();pending=[(root,0)];found=[];visited=0;truncated=False
    while pending:
        folder,depth=pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    visited+=1
                    if visited>max_entries:return sorted(found),True
                    path=Path(entry.path)
                    if entry.is_symlink() or not path.resolve().is_relative_to(root):continue
                    name=entry.name.lower()
                    if entry.is_file(follow_symlinks=False) and name.startswith('project_deliv_') and name.endswith('.db'):found.append(path)
                    elif entry.is_dir(follow_symlinks=False) and name not in ('setupsfolder','pointcloud','cache','caches','.git'):
                        if depth<max_depth:pending.append((path,depth+1))
                        else:truncated=True
        except (PermissionError,FileNotFoundError):truncated=True
    return sorted(found),truncated


def inspect(path,database=None):
    p=Path(str(path).strip().strip('"')).expanduser().resolve();root=p.parent if p.is_file() else p
    if not root.is_dir():raise ValueError('Choose the project folder containing SetupsFolder and the project databases.')
    if root.name.lower()=='setupsfolder' and p.is_dir():root=root.parent
    dbs=sorted(x for x in root.iterdir() if x.is_file() and x.name.lower().startswith('project_deliv_') and x.suffix.lower()=='.db')
    # Original scanner jobs use the independent experimental SCV6 reader.
    if not dbs and not database and (not p.is_file() or p.name in ('VGFibGVPZkNvbnRlbnQ=','project.rtc360','SetupPositions')):
        import leica_bridge,rtc360_source
        try:
            raw=rtc360_source.inspect(root)
        except ValueError as exc:
            if str(exc).startswith('No RTC360 setup metadata found.'):
                raw=None
            else:raise
        if raw is not None:
            raw['stamp']=leica_bridge.raw_stamp(raw)
            raw['converters']=leica_bridge.converter_candidates()
            return raw
    metadata_infos=None
    if not dbs and not database and not p.is_file():
        dbs,truncated=deliverable_databases(root)
        if not dbs:
            metadata_infos,incomplete=leica_catalog.discover(root)
            dbs=[Path(d['path']) for info in metadata_infos for d in info['databases']]
            truncated=truncated or incomplete
    else:truncated=False
    if p.is_file():db=p
    elif database:db=inside(root,database)
    elif len(dbs)==1 and not truncated:db=dbs[0]
    elif dbs:
        descriptions={v['path']:v for v in (metadata_infos if metadata_infos is not None else leica_catalog.discover(root)[0])}
        choices=[]
        for db in dbs:
            info=descriptions.get(str(db.parent)) or leica_catalog.describe(db.parent)
            choices.append(dict(path=str(db),relative=str(db.relative_to(root)),
                                project_title=info['project_title'] if info else 'REGISTER 360 project'))
        return dict(format='leica_database_choices',root=str(root),databases=choices,search_incomplete=truncated)
    else:
        raise ValueError('No REGISTER 360 deliverable database (project_deliv_*.db) was found in '+str(root)+'. '+('The bounded folder search was incomplete; choose a closer project folder. ' if truncated else '')+'Select the saved project folder containing the database and SetupsFolder, or choose its database directly. A backup/archive or another REGISTER database layout needs separate support.')
    if db.suffix.lower()!='.db' or not db.is_file():raise ValueError('Choose a Register 360 project database.')
    root=db.parent
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('The Leica database failed its integrity check.')
        try:
            setups=[dict(r) for r in c.execute('SELECT mId,mGuid,mName,mCloudURI,mIsUnstructured FROM Hds_OdbSetup WHERE mIsDeleted=0 AND mIsPhantom=0')]
            refs=[dict(r) for r in c.execute('SELECT * FROM Hds_OdbSetupRef')]
            targets=[dict(r) for r in c.execute('SELECT mId,mGuid,mSetupId,mLabel,m3dPosX,m3dPosY,m3dPosZ,mNormalX,mNormalY,mNormalZ FROM Hds_OdbTarget WHERE mIsDeleted=0')]
            links=[dict(r) for r in c.execute('SELECT mId,mGuid,mpLeftSetup,mpRightSetup,mUseTargetCorrespondences,mTransformToUse FROM Hds_OdbLink WHERE mIsDeleted=0')]
        except sqlite3.DatabaseError as exc:raise ValueError('This Leica project database schema is not supported.') from exc
        try:
            correspondences=[dict(r) for r in c.execute('SELECT mLinkId,mEnabled,mpTarget1,mpTarget2 FROM Hds_OdbCorrespondence')]
        except sqlite3.DatabaseError:correspondences=None
        try:photos={r['mId']:dict(r) for r in c.execute('SELECT mId,mPanoURI,mMetadata FROM Hds_OdbSetup')}
        except sqlite3.DatabaseError:photos={}
        by_id={t['mId']:t for t in targets}
        for link in links:
            if correspondences is None:continue
            link['matches']=[]
            for match in correspondences:
                if match['mLinkId']!=link['mId']:continue
                a,b=by_id.get(match['mpTarget1']),by_id.get(match['mpTarget2'])
                if not a or not b:continue
                if a['mSetupId']==link['mpRightSetup'] and b['mSetupId']==link['mpLeftSetup']:a,b=b,a
                if a['mSetupId']!=link['mpLeftSetup'] or b['mSetupId']!=link['mpRightSetup']:continue
                link['matches'].append(dict(a=a['mLabel'],b=b['mLabel'],role='leica' if match['mEnabled'] else 'off'))
    if not 1<=len(setups)<=200:raise ValueError('Choose a project with 1–200 setups.')
    out=[]
    for x in setups:
        if x['mIsUnstructured']:raise ValueError('Unstructured Leica setups are not supported yet.')
        path=inside(root,x['mCloudURI'])
        if path.suffix.lower()!='.hspca':raise ValueError('This project does not reference supported HSPCA setups.')
        poses=[r for r in refs if r['mSetupId']==x['mId']]
        if len(poses)!=1:raise ValueError('Setup '+x['mName']+' has missing or ambiguous saved poses.')
        pose=poses[0];q=np.array([pose['mRotation'+k] for k in 'XYZW'],float);t=np.array([pose['mPosition'+k] for k in 'XYZ'],float)
        if not np.isfinite(q).all() or not np.isfinite(t).all() or abs(np.linalg.norm(q)-1)>.001:raise ValueError('Invalid stored setup pose.')
        R=Rotation.from_quat(q).as_matrix();ts=[]
        for target in targets:
            if target['mSetupId']!=x['mId']:continue
            xyz=[target['m3dPos'+k] for k in 'XYZ']
            if any(v is None for v in xyz) or not np.isfinite(xyz).all():continue
            ts.append(dict(id=target['mGuid'],name=target['mLabel'],source=xyz,normal=[target['mNormal'+k] for k in 'XYZ']))
        photograph=None
        if x['mId'] in photos and photos[x['mId']].get('mPanoURI'):
            from setup_photo_codec import frame_is_setup
            record=photos[x['mId']];photo_path=inside(root,record['mPanoURI'])
            photograph=dict(path=str(photo_path),available=photo_path.is_file(),in_setup_frame=frame_is_setup(record['mMetadata']))
        out.append(dict(photograph=photograph,setup_id=x['mId'],guid=x['mGuid'],name=x['mName'],path=str(path),available=path.is_file(),bytes=path.stat().st_size if path.is_file() else 0,rotation=R.tolist(),translation=t.tolist(),targets=ts))
    metadata=leica_catalog.describe(root) or {}
    return dict(project_title=metadata.get('project_title'),title_source=metadata.get('title_source'),name_note=metadata.get('name_note'),root=str(root),database=str(db),database_sha256=hashlib.sha256(db.read_bytes()).hexdigest(),setups=out,links=links,warnings=['Experimental import of the observed Leica project layout. Saved setup poses are read from Hds_OdbSetupRef; no national-grid datum or survey accuracy is inferred.','Supported setup JPEG photographs are imported for viewing and picking. External Leica cleaning masks, point RGB and HDR decoding are not imported. Source XYZ/intensity points are retained.','The combined PointCloud file is not imported, avoiding duplicate scans.'])

def import_project(s,body):
    import job_api
    info=inspect(body['path'],body.get('database'));chosen=body.get('setups',[]);mode=body.get('mode')
    if info.get('format')=='rtc360_raw':raise ValueError(info['message'])
    if info.get('format')=='leica_database_choices':raise ValueError('Choose a listed project database and inspect it before importing.')
    if mode not in ('registered','unregistered'):raise ValueError('Choose existing Leica positions or unregistered setups.')
    if info['database_sha256']!=body.get('database_sha256'):raise ValueError('The database changed. Inspect it again.')
    selected=[x for x in info['setups'] if x['guid'] in chosen]
    if not selected or len(selected)!=len(set(chosen)) or any(not x['available'] for x in selected):raise ValueError('Choose available setups from the inspected project.')
    if any(x.get('leica_project_hash')==info['database_sha256'] and x.get('leica_setup_guid') in chosen for x in rw.scans(s)):raise ValueError('One of these setups is already imported from this project. Remove its scene item before reimporting.')
    progress=lambda msg:job_api.progress(s,msg)
    old=copy.deepcopy(s.STATE);created=[];pending=[]
    try:
        for setup in selected:
            progress('Importing Leica setup: '+setup['name'])
            _,expected=jobdata.scan_parts(setup['path'],progress)[0]
            m=jobdata.import_scan(setup['path'],s.WORK,expected=expected,progress=progress)
            created.extend(m[k] for k in ('file','attributes_file','preview_file') if m.get(k));m['name']=setup['name'];original=copy.deepcopy(m)
            R=np.asarray(setup['rotation']) if mode=='registered' else np.eye(3);t=np.asarray(setup['translation']) if mode=='registered' else np.zeros(3)
            if mode=='registered':
                m=rw.positioned(m,R,t,s.WORK,progress);created.extend([m['file'],m['preview_file']]);m['coordinate_frame']='Leica project';m['position_status']='Imported Register 360 saved setup position — independent validation required'
            x=dict(id=uuid.uuid4().hex,kind='scan',name=setup['name'],meta=m,original_meta=original,visible=True,source_bytes=setup['bytes'],position_status=m['position_status'],hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[],leica_project_hash=info['database_sha256'],leica_setup_guid=setup['guid'],leica_setup_id=setup['setup_id'],scan_origin=t.tolist(),scan_orientation=R.tolist())
            from setup_photos import copy_photo
            try:
                photo=copy_photo(setup,s.WORK)
                if photo:x['setup_photo']=photo;created.append(photo['file'])
            except (ValueError,OSError) as e:
                x['photo_notice']=str(e);info['warnings'].append(setup['name']+': photographs unavailable: '+str(e))
            r=rw.state_for(x);seen=set()
            for target in setup['targets']:
                name=target['name'].strip()
                if not name or name in seen:raise ValueError('Duplicate/blank Leica target labels in '+setup['name']+'. Resolve these in the source project first.')
                seen.add(name);xyz=(R@np.asarray(target['source'])+t).tolist()
                r['candidates'].append(dict(id=target['id'],name=name,source=xyz,status='accepted',method='Imported Leica target observation',channel='intensity',score=None,plane_rms_mm=None))
                r['rows'].append(dict(id=name,candidate_id=target['id'],role='fit',source=xyz,control=[None,None,None],method='Imported Leica target observation'))
            pending.append(x)
        s.STATE.setdefault('job_items',[]).extend(pending)
        mapping={x['leica_setup_id']:x['id'] for x in pending}
        if mode=='registered':
            for l in info['links']:
                if l['mpLeftSetup'] in mapping and l['mpRightSetup'] in mapping:
                    link=dict(id=uuid.uuid4().hex,a=mapping[l['mpLeftSetup']],b=mapping[l['mpRightSetup']],status='imported',kind='leica',source_link_guid=l['mGuid'],source_method=l['mTransformToUse'],uses_target_correspondences=bool(l['mUseTargetCorrespondences']),solution=None)
                    if 'matches' in l:link['matches']=l['matches']
                    link['target_stamp']=rw.stamp(s,[link['a'],link['b']]);s.STATE.setdefault('scan_links',[]).append(link)
        s.STATE['last_leica_import']=dict(id=body.get('import_id') or uuid.uuid4().hex,project_title=info.get('project_title'),title_source=info.get('title_source'),database_name=Path(info['database']).name,mode=mode,count=len(pending),point_count=sum(x['meta']['count'] for x in pending),items=[dict(id=x['id'],name=x['name'],count=x['meta']['count']) for x in pending],warnings=info['warnings'])
        if (not old.get('project_id') and not any(old.get(k) for k in ('job_items','cloud','axis','structures'))
                and not (old.get('cad') or {}).get('objects')
                and old.get('project_title','Untitled project') in ('','Untitled project','New project')
                and info.get('title_source') in ('project_metadata','site_map')):
            s.STATE['project_title']=info['project_title']
        progress('Saving Leica project: '+str(len(pending))+' setups.');rw.activate(s,pending[0]['id']);s.save()
    except BaseException:
        s.STATE.clear();s.STATE.update(old)
        for p in set(created):Path(p).unlink(missing_ok=True)
        raise

def api(s,h,path,body=None):
    if path!='/api/leica-project':return False
    if body is None:h.send({'last_import':s.STATE.get('last_leica_import')});return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    if body.get('action')=='native_import':
        import rtc360_native
        rtc360_native.api(s,h,body);return True
    if str(body.get('action','')).startswith('bridge_'):
        import leica_bridge
        leica_bridge.api(s,h,body);return True
    if body.get('action')=='inspect':
        found=inspect(body['path'],body.get('database'));cached=s.STATE.get('last_leica_conversion') or {}
        if found.get('format')=='rtc360_raw' and cached.get('raw_stamp')==found.get('stamp'):
            found['cached_export_folder']=cached['folder']
        h.send(found)
    elif body.get('action')=='import':
        import job_api
        request={**body,'import_id':uuid.uuid4().hex}
        job_api.CANCEL.clear();s.start_job(lambda:import_project(s,request),kind='job',native_progress=True);h.send({'started':True,'import_id':request['import_id']})
    else:raise ValueError('Unknown Leica project operation.')
    return True
