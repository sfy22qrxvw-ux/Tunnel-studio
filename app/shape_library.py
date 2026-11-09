# Copyright (c) 2026 Damian Gulczynski.
"""An owned library of confirmed measured shapes, independent of source scans.

Examples contain their own immutable object/context observations and compact
descriptors. SQLite records classes and positive/negative labels. A pending
operation is written before the project save; recovery checks the saved head
before activating labels. Undo reverses only that action's label changes, never
another project's examples. Source deletion and emptying Deleted points cannot
remove these independent library assets. Matching remains geometric, not an
accuracy guarantee or automatic semantic/neural training.
"""
from pathlib import Path
from collections import OrderedDict
import copy,json,os,re,shutil,sqlite3,threading,time,uuid,zipfile
import numpy as np
import projects
import shape_examples

LOCK=threading.RLock()
IDENT=re.compile(r'[a-f0-9]{32}')
_DESCRIPTORS=OrderedDict()


class _Connection(sqlite3.Connection):
    """Hold an OS lock across pending-label recovery and the project save."""
    guard=None
    def close(self):
        try:super().close()
        finally:
            if self.guard is not None:
                guard,self.guard=self.guard,None
                try:
                    if os.name=='nt':
                        import msvcrt
                        guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(guard.fileno(),fcntl.LOCK_UN)
                finally:guard.close()


def _guard(path):
    handle=path.open('a+b');handle.seek(0,2)
    if handle.tell()==0:handle.write(b'0');handle.flush()
    deadline=time.monotonic()+3
    while True:
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return handle
        except OSError:
            if time.monotonic()>=deadline:
                handle.close();raise ValueError('Another Tunnel Studio window is saving the shape library. Retry when it finishes.')
            time.sleep(.05)


def root(s):
    return Path(getattr(s,'WORKSPACE_CATALOGUE',s.WORK))/'ShapeLibrary'


def recover_pending(s):
    """Resolve interrupted teaching before startup retires the old autosave."""
    if not (root(s)/'library.sqlite').exists():return
    with LOCK:
        db=_connect(s)
        try:_recover(db)
        finally:db.close()


def _project(s):
    return str(Path(s.WORK).resolve())+'|'+str(s.STATE.get('project_id') or '@unnamed')


def _id(value):
    if not isinstance(value,str) or not IDENT.fullmatch(value):raise ValueError('Choose an existing shape-library item.')
    return value


def _name(value):
    if not isinstance(value,str) or not 1<=len(value.strip())<=80 or any(ord(c)<32 for c in value):raise ValueError('Enter a shape type name of 1–80 characters.')
    return value.strip()


def _connect(s):
    p=root(s);p.mkdir(exist_ok=True,parents=True)
    from project_data_layout import is_link
    if is_link(p):raise ValueError('The shape library must be an owned folder, not a linked directory.')
    for child in (p/'library.sqlite',p/'library.lock',p/'examples'):
        if child.exists() and is_link(child):raise ValueError('The shape library contains an unexpected linked record or directory. Keep its owned files in place.')
    guard=_guard(p/'library.lock')
    try:db=sqlite3.connect(p/'library.sqlite',timeout=30,factory=_Connection);db.guard=guard
    except BaseException:guard.close();raise
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS examples(id TEXT PRIMARY KEY,class_id TEXT NOT NULL,label TEXT NOT NULL,digest TEXT NOT NULL,meta TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,UNIQUE(class_id,label,digest),FOREIGN KEY(class_id) REFERENCES classes(id));
      CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY,example_id TEXT NOT NULL,project TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 0,UNIQUE(example_id,project),FOREIGN KEY(example_id) REFERENCES examples(id));
      CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,project TEXT NOT NULL,before_head TEXT NOT NULL,after_head TEXT NOT NULL,changes TEXT NOT NULL);''')
    except BaseException:db.close();raise
    return db


def _recover(db):
    for ident,path,before,after,raw in db.execute('SELECT * FROM pending').fetchall():
        p=Path(path)
        try:
            if p.stat().st_size>projects.MAX_MANIFEST_BYTES:raise ValueError('Project metadata exceeds its supported size.')
            state=json.loads(p.read_text(encoding='utf8'))
        except (OSError,ValueError) as e:
            raise ValueError('A pending shape-library action needs its project record to recover. No uncertain training labels were activated.') from e
        head=state.get('shape_library_head','')
        if head not in (before,after):raise ValueError('The project changed before a teaching action was recovered. Keep the library and project records for recovery.')
        for claim,old,new in json.loads(raw):db.execute('UPDATE claims SET active=? WHERE id=?',(new if head==after else old,claim))
        db.execute('DELETE FROM pending WHERE id=?',(ident,))
    db.execute('UPDATE examples SET active=EXISTS(SELECT 1 FROM claims WHERE claims.example_id=examples.id AND claims.active=1)')
    db.commit()


def _class(db,ident):
    _id(ident);row=db.execute('SELECT name FROM classes WHERE id=? AND active=1',(ident,)).fetchone()
    if not row:raise ValueError('Select an available saved shape type.')
    return row[0]


def catalogue(s):
    with LOCK:
        db=_connect(s)
        try:
            _recover(db);rows=[]
            for ident,name,active in db.execute('SELECT id,name,active FROM classes ORDER BY name COLLATE NOCASE,id'):
                examples=[dict(id=i,label=lab,created=stamp,**json.loads(meta)) for i,lab,meta,stamp in db.execute('SELECT id,label,meta,created FROM examples WHERE class_id=? AND active=1 ORDER BY created DESC LIMIT 50',(ident,))]
                positive,negative=db.execute("SELECT coalesce(sum(label='positive'),0),coalesce(sum(label='negative'),0) FROM examples WHERE class_id=? AND active=1",(ident,)).fetchone()
                rows.append(dict(id=ident,name=name,active=bool(active),positive=positive,negative=negative,examples=examples,example_count=positive+negative))
            size=sum(p.stat().st_size for p in root(s).rglob('*') if p.is_file())
            return dict(classes=rows,folder=str(root(s)),bytes=size,method='Confirmed measured-example library; geometric matching')
        finally:db.close()


def class_action(s,body):
    with LOCK:
        db=_connect(s)
        try:
            _recover(db);action=body['action']
            if action=='create_type':
                name=_name(body.get('name'));ident=uuid.uuid4().hex
                if db.execute('SELECT 1 FROM classes WHERE lower(name)=lower(?) AND active=1',(name,)).fetchone():raise ValueError('That shape type already exists. Add another example to it, or choose a different name.')
                db.execute('INSERT INTO classes VALUES(?,?,1)',(ident,name))
            else:
                ident=_id(body.get('class_id'))
                if not db.execute('SELECT 1 FROM classes WHERE id=?',(ident,)).fetchone():raise ValueError('The shape type is unavailable.')
                if action=='rename_type':db.execute('UPDATE classes SET name=? WHERE id=?',(_name(body.get('name')),ident))
                elif action in ('archive_type','restore_type'):db.execute('UPDATE classes SET active=? WHERE id=?',(int(action=='restore_type'),ident))
                else:raise ValueError('Unknown shape type action.')
            db.commit();return dict(class_id=ident,saved=True)
        finally:db.close()


def _metadata(folder):
    p=Path(folder);meta=json.loads((p/'example.json').read_text(encoding='utf8'))
    for file,key in [('object.xyz64','points'),('context.xyz64','context_points')]:
        n=meta.get(key)
        if type(n) is not int or n<0 or (p/file).stat().st_size!=n*24:raise ValueError('A teaching example has an incomplete measured-point cache.')
    if meta['points']<8 or not re.fullmatch(r'[a-f0-9]{64}',str(meta.get('digest',''))):raise ValueError('A teaching example has invalid geometry or identity.')
    return meta


def _insert(s,db,folder,class_id,label):
    _class(db,class_id)
    if label not in ('positive','negative'):raise ValueError('Confirm this shape or mark it as an incorrect match.')
    meta=_metadata(folder)
    old=db.execute('SELECT id FROM examples WHERE class_id=? AND label=? AND digest=?',(class_id,label,meta['digest'])).fetchone()
    if old:return old[0],False
    ident=uuid.uuid4().hex;dest=root(s)/'examples'/ident;dest.parent.mkdir(exist_ok=True)
    try:
        shutil.copytree(folder,dest)
        # Library records carry provenance, never a source-cache path dependency.
        db.execute('INSERT INTO examples VALUES(?,?,?,?,?,0,?)',(ident,class_id,label,meta['digest'],json.dumps(meta,allow_nan=False,separators=(',',':')),time.time()))
    except BaseException:
        shutil.rmtree(dest,ignore_errors=True);raise
    return ident,True


def _stage(s,db,changes,target):
    before=s.STATE.get('shape_library_head','');head=uuid.uuid4().hex
    target['shape_library_head']=head
    ident=uuid.uuid4().hex
    db.execute('INSERT INTO pending VALUES(?,?,?,?,?)',(ident,str(Path(s.WORK)/'project.json'),before,head,json.dumps(changes)))
    db.commit();return ident


def transaction(s,folders,class_id,label='positive',mutate=None,deactivate=None,*,review=None):
    """Save teaching and an optional point edit with crash-recoverable labels."""
    with LOCK:
        db=_connect(s);before=copy.deepcopy(s.STATE);created=[];created_claims=[];pending=None
        try:
            _recover(db);_class(db,class_id);changes=[];saved=[]
            for serial,folder in enumerate(folders,1):
                s.progress(f'Copying independent teaching samples · {serial}/{len(folders)} objects')
                ident,new=_insert(s,db,folder,class_id,label);created.extend([ident] if new else [])
                claim=db.execute('SELECT id,active FROM claims WHERE example_id=? AND project=?',(ident,_project(s))).fetchone()
                if not claim:
                    claim=(uuid.uuid4().hex,0);created_claims.append(claim[0])
                    db.execute('INSERT INTO claims VALUES(?,?,?,0)',(claim[0],ident,_project(s)))
                if not claim[1]:changes.append([claim[0],0,1])
                saved.append(ident)
            for ident in deactivate or []:
                row=db.execute('SELECT active,class_id FROM examples WHERE id=?',(_id(ident),)).fetchone()
                if not row or row[1]!=class_id:raise ValueError('Choose an example from the selected shape type.')
                if row[0]:changes.extend([claim,1,0] for (claim,) in db.execute('SELECT id FROM claims WHERE example_id=? AND active=1',(ident,)))
            event=dict(id=uuid.uuid4().hex,changes=changes)
            if review:
                event['review']=review
            s.progress('Saving confirmed teaching and reviewed point changes')
            s.STATE.setdefault('shape_library_events',[]).append(event)
            pending=_stage(s,db,changes,s.STATE)
            if mutate:mutate()
            s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(before)
            if pending:db.execute('DELETE FROM pending WHERE id=?',(pending,))
            for ident in created_claims:db.execute('DELETE FROM claims WHERE id=?',(ident,))
            for ident in created:
                db.execute('DELETE FROM examples WHERE id=?',(ident,));shutil.rmtree(root(s)/'examples'/ident,ignore_errors=True)
            db.commit();db.close();raise
        try:
            _recover(db)
            return dict(saved=True,examples=saved,added=len(created),class_id=class_id)
        finally:db.close()


def history_begin(s,target):
    """Journal only label deltas belonging to this workspace Undo/Redo."""
    old=s.STATE.get('shape_library_events',[]);new=target.get('shape_library_events',[])
    old_ids={e['id'] for e in old};new_ids={e['id'] for e in new};desired={}
    for event in reversed(old):
        if event['id'] not in new_ids:
            for ident,before,after in event['changes']:desired[ident]=before
    for event in new:
        if event['id'] not in old_ids:
            for ident,before,after in event['changes']:desired[ident]=after
    if not desired:return None
    with LOCK:
        db=_connect(s)
        try:
            _recover(db);changes=[]
            for ident,wanted in desired.items():
                row=db.execute('SELECT active FROM claims WHERE id=?',(ident,)).fetchone()
                if row:changes.append([ident,row[0],wanted])
            return _stage(s,db,changes,target)
        finally:db.close()


def history_finish(s,ident,success):
    if not ident:return
    with LOCK:
        db=_connect(s)
        try:
            if success:_recover(db)
            else:db.execute('DELETE FROM pending WHERE id=?',(ident,));db.commit()
        finally:db.close()


def bank(s,class_ids):
    if not isinstance(class_ids,list) or not class_ids:raise ValueError('Choose at least one saved shape type.')
    with LOCK:
        db=_connect(s)
        try:
            _recover(db);entries=[];names={}
            for ident in dict.fromkeys(class_ids):
                names[ident]=_class(db,ident);positive=False
                for key,lab,meta in db.execute('SELECT id,label,meta FROM examples WHERE class_id=? AND active=1 ORDER BY created,id',(ident,)):
                    data=json.loads(meta);positive |= lab=='positive'
                    entries.append(dict(id=key,class_id=ident,label=lab,path=str(root(s)/'examples'/_id(key)/'descriptor.npz'),grid_mm=data['grid_mm'],side=data['side'],size_m=data['size_m'],footprint_cells=data['footprint_cells']))
                if not positive:raise ValueError('Add a confirmed example to '+names[ident]+' before searching.')
            return dict(entries=entries,names=names)
        finally:db.close()


def load_template(entry,grid_mm):
    if not np.isfinite(grid_mm) or not 2<=grid_mm<=100:raise ValueError('Use a finite shape grid from 2 to 100 mm.')
    key=(entry['path'],float(grid_mm));p=Path(entry['path']);stamp=p.stat().st_mtime_ns
    cached=_DESCRIPTORS.get(key)
    if cached and cached[0]==stamp:_DESCRIPTORS.move_to_end(key);return cached[1]
    if p.stat().st_size>10_000_000:raise ValueError('A saved shape descriptor exceeds its supported size.')
    with zipfile.ZipFile(p) as z:
        if sum(i.file_size for i in z.infolist())>6_000_000:raise ValueError('A saved shape descriptor is invalid or too large.')
    with np.load(p,allow_pickle=False) as raw:
        mask=np.asarray(raw['mask'],bool);depth=np.asarray(raw['depth'],float);original=float(raw['grid_mm'])
    if mask.ndim!=2 or depth.shape!=mask.shape or not mask.any() or max(mask.shape)>512 or not np.isfinite(depth).all() or not np.isfinite(original) or original<=0:raise ValueError('A saved shape descriptor is damaged.')
    if abs(original-grid_mm)>1e-9:
        from scipy.ndimage import zoom,distance_transform_edt
        ratio=original/grid_mm
        if max(mask.shape)*ratio>512:raise ValueError('Use a coarser search grid for this saved shape.')
        observed=zoom(mask.astype('u1'),ratio,order=0)>0;weight=zoom(mask.astype(float),ratio,order=1)
        d=zoom(depth*mask,ratio,order=1);np.divide(d,weight,out=d,where=weight>0)
        if not observed.any():raise ValueError('The search grid is too coarse to represent this saved object.')
        near=distance_transform_edt(~observed,return_distances=False,return_indices=True)
        mask=observed;depth=np.where(mask,d[tuple(near)],0)
    t=dict(mask=mask,depth=depth,long=False,grid_mm=grid_mm,example_sign=1 if entry['side']=='recess' else -1,
           footprint_cells=int(mask.sum()),example_points=int(mask.sum()),components=1,size_m=(np.asarray(mask.shape)*grid_mm/1000).tolist())
    _DESCRIPTORS[key]=(stamp,t)
    while len(_DESCRIPTORS)>32:_DESCRIPTORS.popitem(last=False)
    return t


def preview_example(s,ident):
    _id(ident)
    with LOCK:
        db=_connect(s)
        try:
            _recover(db);row=db.execute('SELECT meta FROM examples WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('This saved example is unavailable.')
            m=json.loads(row[0]);p=root(s)/'examples'/ident
            from bounded_rows import RowFile
            arrays=[]
            for file,key,colour in [('context.xyz64','context_points',[.2,.8,.55]),('object.xyz64','points',[1.,.55,.12])]:
                with RowFile(p/file,'<f8',m[key],3) as rows:
                    points=rows.take(np.linspace(0,max(0,m[key]-1),min(5000,m[key])).astype('<i8'))
                arrays.append(np.c_[points,np.tile(colour,(len(points),1))])
            q=np.vstack(arrays);origin=np.median(q[:,:3],axis=0);q[:,:3]-=origin
            return dict(origin=origin.tolist(),points=q.tolist(),preview_count=len(q),count=m['points']+m['context_points'],sampled=True)
        finally:db.close()
