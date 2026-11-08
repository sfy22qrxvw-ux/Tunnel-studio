# Copyright (c) 2026 Damian Gulczynski.
"""Teach only reviewed compact objects and retain independent measured crops.

One streamed pass gathers confirmed complete match groups into owned temporary
files. Their descriptors and nearby wall context are copied to the persistent
library before a point edit commits. Library labels share the project's atomic
save and workspace Undo. No unreviewed candidate becomes a training example.
This is geometric example reuse, not a guarantee of object recognition.
"""
from contextlib import contextmanager,ExitStack
from pathlib import Path
import copy
import json
import shutil
import sqlite3
import uuid
import numpy as np
from bounded_rows import RowFile
import feature_selection
import registration_pose
import shape_examples as examples
import shape_library as library
import shape_matching
import shape_selection
from project_data_layout import folder as asset_folder


@contextmanager
def staging(s):
    folder=asset_folder(s.WORK,'working')/('shape-teaching-'+uuid.uuid4().hex)
    folder.mkdir(parents=True)
    try:yield folder
    finally:shutil.rmtree(folder,ignore_errors=True)


@contextmanager
def selected_examples(s,row):
    """Complete reviewed groups, exact float64 world observations, bounded RAM."""
    if row.get('mode')!='shape' or row['statistics'].get('continuing_service'):
        raise ValueError('Teach a compact reviewed object. Continuing services and circle-departure groups are not compact examples.')
    db=sqlite3.connect(row['review_file'])
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='group_examples'").fetchone():raise ValueError('Find compact shapes again before teaching this result.')
        rows=db.execute('SELECT m.id,m.points,m.selected_points,e.path FROM matches m LEFT JOIN group_examples e ON m.id=e.id WHERE m.selected_points>0 ORDER BY m.id').fetchall()
    finally:db.close()
    if not rows:raise ValueError('Select confirmed object groups before teaching.')
    if any(n!=selected for _,n,selected,_ in rows):raise ValueError('Teach complete object groups. Clear partial refinements with Select all, or draw a new manual example.')
    if any(not p or n>examples.MAX_CAPTURE for _,n,_,p in rows):raise ValueError('A selected group is too large for one compact teaching example. Draw one object more tightly; whole-cloud searches have no input-point cap.')
    item=shape_selection.source(s,row['source_id']);meta=item['meta'];n=row['count']
    with staging(s) as folder:
        targets={ident:folder/str(ident) for ident,_,_,_ in rows}
        for p in targets.values():p.mkdir()
        counts={k:0 for k in targets}
        with ExitStack() as stack:
            ids=stack.enter_context(RowFile(row['point_ids_file'],'<i8',n))
            labels=stack.enter_context(RowFile(row['labels_file'],'<i4',n))
            flags=stack.enter_context(RowFile(row['selected_file'],'u1',n))
            xyz=stack.enter_context(RowFile(meta['file'],'<f8',meta['count'],3))
            for start in range(0,n,shape_matching.BLOCK):
                size=min(shape_matching.BLOCK,n-start);use=flags.read(start,size)!=0;lab=labels.read(start,size)[use]
                q=registration_pose.points(meta,xyz.take(ids.read(start,size)[use]))
                for ident in np.unique(lab):
                    points=q[lab==ident]
                    with (targets[int(ident)]/'object.xyz64').open('ab') as out:points.astype('<f8',copy=False).tofile(out)
                    counts[int(ident)]+=len(points)
                s.progress(f'Storing confirmed original observations · {start+size:,}/{n:,} candidate IDs')
        for serial,(ident,expected,_,descriptor) in enumerate(rows,1):
            if counts[ident]!=expected:raise ValueError('The reviewed object changed before teaching. Find shapes again.')
            p=np.fromfile(targets[ident]/'object.xyz64','<f8').reshape(-1,3)
            wall=examples.context(item,p.min(0),p.max(0),s.progress)
            with np.load(descriptor,allow_pickle=False) as raw:
                mask=raw['mask'].astype(bool);depth=raw['depth'];cell=float(raw['grid_mm']);side=int(raw['side'])
            t=dict(mask=mask,depth=depth,grid_mm=cell,example_sign=side,size_m=(np.asarray(mask.shape)*cell/1000).tolist())
            examples.write(targets[ident],p,wall,t,dict(kind='reviewed_match',source_name=item['name'],source_item_id=item['id'],method='Confirmed complete measured component and separate wall context'))
            s.progress(f'Preparing library examples · {serial}/{len(rows)} objects')
        yield list(targets.values())


def capture(s,body):
    if body.get('confirmed') is not True:raise ValueError('Confirm the drawn object before teaching the library.')
    item=shape_selection.source(s,body.get('source_id'));before=feature_selection.fingerprint(item)
    cfg=shape_matching.settings(body.get('settings'))
    if cfg['continuation']=='long':raise ValueError('Saved types currently teach compact pockets and fittings. Search continuing services with a drawn example.')
    with staging(s) as folder:
        examples.capture(item,body.get('example'),folder/'example',cfg,s.progress)
        if feature_selection.fingerprint(shape_selection.source(s,item['id']))!=before:raise ValueError('The source changed while the example was captured. No teaching was saved.')
        s.progress('Saving confirmed measured shape with independent wall context')
        return library.transaction(s,[folder/'example'],body.get('class_id'))


def teach(s,body):
    if body.get('confirmed') is not True:raise ValueError('Review the selected objects before confirming their teaching label.')
    row=shape_selection.current(s,body)
    with selected_examples(s,row) as folders:
        shape_selection.current(s,body);s.progress('Saving reviewed shape examples')
        result=library.transaction(s,folders,body.get('class_id'),'negative' if body['action']=='reject_selected' else 'positive')
    row['last_teaching']=result
    return result


def get(s,h,path,args):
    if path!='/api/shape-library':return False
    if args.get('example'):
        h.send(library.preview_example(s,args['example'][0]))
    else:h.send({**library.catalogue(s),'last_import':copy.deepcopy(getattr(s,'SHAPE_LIBRARY_IMPORT',None))})
    return True


def post(s,h,path,body):
    if path!='/api/shape-library':return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    action=body.get('action')
    if action in ('create_type','rename_type','archive_type','restore_type'):
        h.send(library.class_action(s,body));return True
    if action not in ('capture','capture_scan','teach_selected','reject_selected','forget_example'):raise ValueError('Unknown saved-shape action.')
    request=copy.deepcopy(body)
    if action=='forget_example':
        if request.get('confirmed') is not True:raise ValueError('Confirm removal of this teaching example.')
        operation=lambda:library.transaction(s,[],request.get('class_id'),deactivate=[request.get('example_id')])
    elif action=='capture_scan':operation=lambda:__import__('shape_separated').capture(s,request)
    else:operation=lambda:(capture if action=='capture' else teach)(s,request)
    s.start_job(operation,'confirmed shape library',native_progress=True);h.send({'started':True});return True
