"""Review and quarantine specific generated workspace files without accepting client paths."""
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import threading
import uuid

import projects
import storage_inventory as si

CATEGORIES={'snapshots':'Old named project records (metadata only)','caches':'Unreferenced working caches',
            'exports':'Temporary exports','reports':'Superseded reports','demo':'Generated demonstration data',
            'analysis':'Detached analysis outputs','previews':'Unused previews'}
RECEIPTS='.generated-products'
LOCK=threading.Lock()
_applying=False
_writers=0


def committing():
    with LOCK:return _applying


@contextmanager
def incoming():
    global _writers
    with LOCK:
        if _applying:raise ValueError('Storage cleanup is committing. Try again when it finishes.')
        _writers+=1
    try:yield
    finally:
        with LOCK:_writers-=1


@contextmanager
def exclusive(server):
    global _applying
    with LOCK:
        if _applying or _writers>1 or server.JOB.get('busy'):
            raise ValueError('Wait until other workspace operations finish, then review storage again.')
        _applying=True
    try:yield
    finally:
        with LOCK:_applying=False


def _relative_digest(rel):
    return hashlib.sha256(os.fsencode(rel.as_posix())).hexdigest()


def _shape(rel,kind):
    if kind=='exports':
        return (rel.parent in (Path('.'),Path('Exports')) and
                bool(re.fullmatch(si.UUID+r'\.(?:e57|las|laz|pts|xyz|csv|ply|obj|json)',rel.name,re.I)))
    if kind=='reports':
        return rel in (Path('survey_report.html'),Path('Exports/survey_report.html'))
    if kind=='analysis':
        return rel.parent in (Path('.'),Path('Analysis')) and bool(re.fullmatch(si.UUID+r'\.analysis64',rel.name))
    if kind=='demo':
        return (rel in {Path('demo')/n for n in ('demo_alignment.csv','demo_scan.xyz','demo_rings.csv')}
                or rel in {Path('target_demo')/n for n in ('synthetic_targets.csv','control.csv')}
                or len(rel.parts)==2 and re.fullmatch('surface_demo_'+si.UUID,rel.parts[0]) and
                rel.name in ('synthetic_intersection.csv','synthetic_surface.truth8'))
    return False


def record_product(work,path,kind,state=None,recipe=None):
    """A failed provenance write leaves an otherwise successful product protected."""
    root=Path(work).resolve();path=Path(path).absolute()
    try:
        rel=path.relative_to(root)
        if not _shape(rel,kind) or not si.safe_path(path,root) or path.lstat().st_nlink!=1:return
        target=root/RECEIPTS
        si.normal_folder(target);target.mkdir(exist_ok=True)
        receipt=target/(_relative_digest(rel)+'.json')
        projects.atomic_json(receipt,dict(relative=rel.as_posix(),category=kind,
            stamp=si.deletion_stamp(path.lstat()),owner=str((state or {}).get('project_title') or '')[:180],recipe=recipe))
    except (OSError,ValueError,TypeError):return


def _recipe(meta,axis):
    if not isinstance(meta,dict) or not meta.get('file') or axis is None:return None
    try:
        rows=axis.get('rows') if isinstance(axis,dict) else getattr(axis,'a',None)
        if hasattr(rows,'tolist'):rows=rows.tolist()
        if not isinstance(rows,list) or not rows:return None
        paths=[Path(meta[k]) for k in ('file','mask_file') if meta.get(k)]
        data=[[(str(p),si.deletion_stamp(p.stat())) for p in paths],
              meta.get('registration_rotation'),meta.get('registration_translation'),rows]
        return hashlib.sha256(json.dumps(data,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    except (OSError,ValueError,TypeError):return None


def record_projection(meta,axis,path):
    p=Path(path).absolute();root=p.parent
    if root.name=='Analysis' and (root.parent/'.tunnelstudio-workspace.json').is_file():root=root.parent
    recipe=_recipe(meta,axis)
    if recipe:record_product(root,p,'analysis',recipe=recipe)


def _products(root):
    folder=root/RECEIPTS;si.normal_folder(folder);rows={}
    if not folder.is_dir():return rows
    for p in folder.glob('*.json'):
        try:
            if p.lstat().st_size>10000 or not si.safe_path(p,root):continue
            entry=projects.read_json(p);rel=Path(entry['relative'])
            if rel.is_absolute() or '..' in rel.parts or _relative_digest(rel)+'.json'!=p.name or not _shape(rel,entry['category']):continue
            file=root/rel
            if list(si.deletion_stamp(file.lstat()))==entry['stamp'] and si.safe_path(file,root):
                rows[file]=entry
        except (KeyError,OSError,ValueError,TypeError,AttributeError):continue
    return rows


# Canonical byte digests of the shipped deterministic demonstration generators.
# Verify legacy files by content as well as location. A project can reuse a demo
# filename for its own survey without that file ever becoming disposable.
LEGACY_DEMO_HASHES={
    'demo_alignment.csv':'f997a33cd0dd5c37c26d2739b3e9b03ade0a4d315c441722dced1d18154c3d06',
    'demo_rings.csv':'f85e5a7dae66a93a87bfce185b97bc029b9b01f52d0f49372e4ab058a0556a2f',
    'demo_scan.xyz':'49277e71cae8c8910b1cc8e346c813147761a999abe29c5dd63f2902aa80bb63',
    'synthetic_intersection.csv':'a76cb995336c2928fe12837ae386c33c0902ca49c82bb32387ad0f3a58bd0372',
    'synthetic_surface.truth8':'b9be3d34145a201252fa7c15fe00f2157c5305c76c80c986fb6310baa604ae5a',
    'synthetic_targets.csv':'c5f96391773c775f7889b29bac142871acb9279218df1d06030e6988d9aa8563',
    'control.csv':'e73cef7b56c94cb9426b8c159a54c8f60de7f436874bd3c3912a69de3cb77b84',
}


@lru_cache(maxsize=128)
def _legacy_demo(path,stamp):
    if stamp[4]>20_000_000:return False
    try:
        data=path.read_bytes()
        if path.suffix in ('.xyz','.csv'):data=data.replace(b'\r\n',b'\n')
        return hashlib.sha256(data).hexdigest()==LEGACY_DEMO_HASHES.get(path.name)
    except OSError:return False


def _legacy_product(root,path,info):
    rel=path.relative_to(root)
    if _shape(rel,'demo') and _legacy_demo(path,si.deletion_stamp(info)):return {'category':'demo','owner':'Tunnel Studio demo'}
    if _shape(rel,'reports'):
        try:
            with path.open('rb') as source:
                if source.read(128).startswith(b'<!doctype html><html lang="en"><meta charset="utf-8"><title>Tunnel Studio survey report</title>'):
                    return {'category':'reports','owner':'Tunnel Studio survey report'}
        except OSError:pass
    return {}


def _candidate_files(root,state,mode):
    if mode not in ('manual','close'):raise ValueError('Choose manual or on-close cleanup.')
    if root.is_relative_to(si.program_root()):raise ValueError('The program installation cannot be cleaned as a workspace.')
    named=projects._read_named_asset_sets(root,strict=True,maintain=False)
    recoveries=projects._hidden_recovery_entries(root,strict=True,prune=False)
    protected=projects._current_and_recovery_assets(root,state,strict=True,prune=False)
    for entry in named.values():protected.update(entry['assets'])
    documents=[state,*[entry['data'] for entry in named.values()]]
    autosave=root/'project.json'
    if autosave.is_file():documents.append(projects.read_json(autosave))
    documents.extend(projects.read_json(entry['path']) for entry in recoveries)
    current_id=state.get('project_id')
    snapshots={entry['path']:str(entry['data'].get('project_title') or 'Untitled project')
               for ident,entry in named.items() if ident!=current_id}
    products=_products(root)
    report_in_use=any(doc.get('report_ready') for doc in documents)
    demo_in_use=any(doc.get('demo') or (doc.get('registration') or {}).get('demo') for doc in documents)
    recipes=set()
    if mode=='close' and any(e['category']=='analysis' for e in products.values()):
        for doc in documents:
            clouds=[doc.get('cloud')]+[row.get('meta') for row in doc.get('job_items',[]) if isinstance(row,dict) and row.get('kind')=='scan']
            recipes.update(x for m in clouds if (x:=_recipe(m,doc.get('axis'))))
    found={};warnings=[]
    for p,info in si.files_in(root,warnings):
        if info.st_nlink!=1 or p.is_relative_to(si.program_root()) or p in protected:continue
        product=products.get(p) or (_legacy_product(root,p,info) if mode=='manual' else {})
        if p in snapshots and mode=='manual':
            category='snapshots';owners=[snapshots[p]]
        elif product and product['category'] in ('exports','reports','demo'):
            category=product['category'];owners=[product['owner']] if product.get('owner') else []
            if category=='reports' and report_in_use or category=='demo' and demo_in_use:continue
        elif si.owned_name(root,p):
            owners=[];suffix=p.suffix.lower()
            if suffix=='.preview64' or p.name.endswith('.xyz64.viewindex.npz'):category='previews'
            elif suffix in ('.analysis64','.design-results','.inspection-results','.surface_labels','.surface_ids'):category='analysis'
            else:category='caches'
            if mode=='close' and category=='analysis' and not (product.get('recipe') and product['recipe'] in recipes):continue
        else:continue
        if mode=='close' and category not in ('exports','analysis','previews'):continue
        ident=hashlib.sha256(os.fsencode(str(root)) + b'\0' + os.fsencode(p.relative_to(root))
                             + str(si.deletion_stamp(info)).encode() + category.encode()).hexdigest()
        found[ident]=dict(id=ident,name=p.name,path=str(p),bytes=info.st_size,category=category,owners=owners,
            protection=('Selected record only. All project caches remain on disk.' if category=='snapshots' else
                        'Verified generated file, unreferenced by current work, named projects or recovery.'),
            modified_at=datetime.fromtimestamp(info.st_mtime,timezone.utc).isoformat())
    return found


def _review(found,ids,mode):
    if not isinstance(ids,list) or not ids or len(ids)>3000 or any(not isinstance(x,str) or x not in found for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('File selection is missing, duplicate, changed or no longer eligible. Review the files again (up to 3,000 per operation).')
    rows=[found[x] for x in sorted(ids)]
    token=hashlib.sha256((mode+'\0'+'\n'.join(sorted(ids))).encode()).hexdigest()
    return dict(files=rows,file_count=len(rows),bytes=sum(r['bytes'] for r in rows),review_token=token)


def _apply(root,review):
    queue=root/projects._PURGE_ROOT;si.normal_folder(queue);queue.mkdir(exist_ok=True)
    stage=queue/('select_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex)
    stage.mkdir();moved=[]
    try:
        for row in review['files']:
            src=Path(row['path']);rel=src.relative_to(root);info=src.lstat()
            ident=hashlib.sha256(os.fsencode(str(root))+b'\0'+os.fsencode(rel)+str(si.deletion_stamp(info)).encode()+row['category'].encode()).hexdigest()
            if row['id']!=ident or info.st_nlink!=1 or not si.safe_path(src,root):raise ValueError('A selected file changed. Review storage again.')
            dst=stage/'assets'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dst);moved.append((src,dst))
        projects._write_purge_meta(stage,dict(bytes=review['bytes'],label='Selected generated files and project records',created_at=datetime.now(timezone.utc).isoformat()))
    except BaseException:
        for src,dst in reversed(moved):
            if dst.exists() and not src.exists():os.replace(dst,src)
        if not any(dst.exists() for _,dst in moved):shutil.rmtree(stage,ignore_errors=True)
        raise
    projects._schedule_purge(stage)
    return dict(deleted=True,files=len(moved),bytes=review['bytes'],purge_pending=True)


def request(s,body):
    import workspace_history
    root=Path(s.WORK).resolve();mode=body.get('mode','manual');action=body.get('action')
    if s.JOB.get('busy'):raise ValueError('Wait until the running workspace job finishes before reviewing cleanup.')
    def candidates():return _candidate_files(root,workspace_history.storage_state(s),mode)
    if action=='apply':
        if body.get('confirm') is not True:raise ValueError('Confirm the selected files before deletion.')
        with exclusive(s):
            review=_review(candidates(),body.get('ids'),mode)
            if review['review_token']!=body.get('expected_token'):raise ValueError('Review the current selection before cleanup.')
            return _apply(root,review)
    found=candidates()
    if action=='review':return _review(found,body.get('ids'),mode)
    if action!='list':raise ValueError('Unknown storage cleanup action.')
    category=body.get('category','all');offset=body.get('offset',0);limit=body.get('limit',100)
    if category not in {'all',*CATEGORIES} or type(offset)!=int or offset<0 or type(limit)!=int or not 1<=limit<=200:raise ValueError('Invalid cleanup page or category.')
    totals={key:dict(key=key,name=name,files=0,bytes=0) for key,name in CATEGORIES.items()}
    for row in found.values():
        entry=totals[row['category']];entry['files']+=1;entry['bytes']+=row['bytes']
    rows=sorted((r for r in found.values() if category=='all' or r['category']==category),key=lambda r:(-r['bytes'],r['path']))
    return dict(files=rows[offset:offset+limit],total_files=len(rows),total_bytes=sum(r['bytes'] for r in rows),
                offset=offset,limit=limit,categories=list(totals.values()),selection_limit=3000)
