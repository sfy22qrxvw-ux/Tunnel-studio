# Copyright (c) 2026 Damian Gulczynski.
"""Teach existing separated pocket scans using independent lining context.

All retained observations are streamed twice. A disk-backed occupied-voxel graph
separates disconnected objects; only one compact object and its nearby wall
sample occupy RAM during descriptor creation. The user explicitly confirms the
separated scan's type. Unusable or connected large groups are reported, not
invented or silently taught. Original scans and coordinates are never edited.
This is confirmed example capture, not autonomous object classification.
"""
from pathlib import Path
import sqlite3
import numpy as np
import feature_selection
import shape_examples
import shape_library
import shape_matching
import shape_selection


def split_scan(s,source,folder,cfg):
    """Partition retained world observations without teaching or changing scans."""
    folder=Path(folder)
    cell=max(.01,min(.05,cfg['grid_mm']/500))
    db=sqlite3.connect(folder/'groups.sqlite');db.execute('PRAGMA cache_size=-16384');db.execute('PRAGMA temp_store=FILE')
    db.execute('CREATE TABLE cells(x INTEGER,y INTEGER,z INTEGER,n INTEGER,gid INTEGER DEFAULT 0,PRIMARY KEY(x,y,z)) WITHOUT ROWID')
    no_filters={**cfg,'respect_view':False};total=0;serial=0;skipped=[]
    try:
        for ids,p,_ in shape_selection.batches(source,no_filters,s.progress):
            keys,counts=np.unique(np.floor(p/cell).astype(np.int64),axis=0,return_counts=True)
            db.executemany('INSERT INTO cells(x,y,z,n) VALUES(?,?,?,?) ON CONFLICT(x,y,z) DO UPDATE SET n=n+excluded.n',((*map(int,k),int(n)) for k,n in zip(keys,counts)))
            total+=len(ids);db.commit()
        if not total:raise ValueError('The separated example scan has no retained measured points.')
        db.execute('CREATE INDEX unassigned ON cells(gid,x,y,z)')
        import job_api
        db.set_progress_handler(lambda:int(job_api.CANCEL.is_set()),10000)
        while True:
            seed=db.execute('SELECT x,y,z FROM cells WHERE gid=0 LIMIT 1').fetchone()
            if seed is None:break
            serial+=1;s.progress(f'Separating saved pocket examples · {serial:,} connected groups')
            db.execute('''WITH RECURSIVE connected(x,y,z) AS (
                VALUES(?,?,?) UNION SELECT b.x,b.y,b.z FROM cells b JOIN connected a
                ON b.x BETWEEN a.x-1 AND a.x+1 AND b.y BETWEEN a.y-1 AND a.y+1 AND b.z BETWEEN a.z-1 AND a.z+1 WHERE b.gid=0)
                UPDATE cells SET gid=? WHERE (x,y,z) IN connected''',(*seed,serial));db.commit()
        rows=db.execute('SELECT gid,sum(n),min(x),max(x),min(y),max(y),min(z),max(z) FROM cells GROUP BY gid ORDER BY gid').fetchall()
        targets={}
        for ident,n,x0,x1,y0,y1,z0,z1 in rows:
            extent=cell*np.linalg.norm([x1-x0+1,y1-y0+1,z1-z0+1])
            if n<8 or n>shape_examples.MAX_CAPTURE or extent>3:
                skipped.append(dict(group=ident,points=n,reason='Not one compact supported object; separate it more tightly.'));continue
            p=folder/('object-'+str(ident));p.mkdir();targets[ident]=p
        for ids,p,_ in shape_selection.batches(source,no_filters,s.progress):
            keys,inverse=np.unique(np.floor(p/cell).astype(np.int64),axis=0,return_inverse=True)
            groups=np.asarray([db.execute('SELECT gid FROM cells WHERE x=? AND y=? AND z=?',tuple(map(int,k))).fetchone()[0] for k in keys],np.int64)[inverse]
            for ident in np.unique(groups):
                if int(ident) not in targets:continue
                with (targets[int(ident)]/'input.xyz64').open('ab') as out:p[groups==ident].astype('<f8',copy=False).tofile(out)
        return targets,total,serial,skipped,cell
    except sqlite3.OperationalError:
        if __import__('job_api').CANCEL.is_set():raise ValueError('Separated example capture stopped. Source scans and teaching labels are unchanged.')
        raise
    finally:db.close()


def capture(s,body):
    if body.get('confirmed') is not True:raise ValueError('Confirm that the separated scan contains examples of the selected shape type.')
    reference=shape_selection.source(s,body.get('source_id'));source=shape_selection.source(s,body.get('example_source_id'))
    if reference['id']==source['id']:raise ValueError('Choose the main measured lining scan as the wall reference and a different separated pocket scan as the examples.')
    cfg=shape_matching.settings(body.get('settings'))
    if cfg['continuation']=='long':raise ValueError('Teach compact separated pockets or fittings; use a drawn example to follow continuing services.')
    before=(feature_selection.fingerprint(reference),feature_selection.fingerprint(source))
    from shape_library_api import staging
    with staging(s) as folder:
        targets,total,serial,skipped,cell=split_scan(s,source,folder,cfg)
        import job_api
        folders=[]
        for at,(ident,path) in enumerate(targets.items(),1):
            s.progress(f'Fitting separate wall context · object {at}/{len(targets)}')
            p=np.fromfile(path/'input.xyz64','<f8').reshape(-1,3)
            try:
                shape_examples.capture_points(reference,p,path,cfg,s.progress,provenance=dict(kind='separated_scan',example_source_name=source['name']))
            except ValueError as e:
                if job_api.CANCEL.is_set():raise
                skipped.append(dict(group=ident,points=len(p),reason=str(e)));continue
            (path/'input.xyz64').unlink();folders.append(path)
        if not folders:raise ValueError('No separated objects had a usable measured wall reference. Choose the corresponding lining scan and check object separation, side and depth. '+(skipped[0]['reason'] if skipped else ''))
        if before!=(feature_selection.fingerprint(shape_selection.source(s,reference['id'])),feature_selection.fingerprint(shape_selection.source(s,source['id']))):raise ValueError('An example or lining scan changed during teaching. No library examples were saved.')
        s.progress('Saving confirmed separated pockets independently')
        result=shape_library.transaction(s,folders,body.get('class_id'))
        s.SHAPE_LIBRARY_IMPORT=dict(added=result['added'],usable=len(folders),groups=serial,input_points=total,skipped=skipped,source_name=source['name'],connection_mm=cell*1000,warning='Disconnected groups are geometric pieces; check the saved examples. Touching pockets may need separate scans or tighter manual crops.')
        return result
