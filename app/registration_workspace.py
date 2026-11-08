"""Persistent target work and reviewed pairwise registration of scan components."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy,hashlib,json,time,uuid
import numpy as np
import registration,jobdata,registration_pose

def scans(s):return [x for x in s.STATE.get('job_items',[]) if x['kind']=='scan' and not x.get('registered_merge')]
def scan(s,ident):
    x=next((x for x in scans(s) if x['id']==ident),None)
    if x is None:raise ValueError('Select an existing scan.')
    return x

def persist(s):
    ident=s.STATE.get('active_job_registration');r=s.STATE.get('registration')
    x=next((x for x in scans(s) if x['id']==ident),None)
    if x is not None and r and r.get('cloud',{}).get('file')==x['meta']['file']:
        include_candidates(r,controls=s.STATE.get('survey_controls',{}))
        x['registration_state']=copy.deepcopy(r)
    for link in s.STATE.get('scan_links',[]):
        if link.get('target_stamp') and link['target_stamp']!=stamp(s,[link['a'],link['b']]):
            link['status']='targets changed — review link'

def include_candidates(r, candidates=None, controls=None):
    """Make finite fitted centres usable, retaining explicit exclusions.

    The optional legacy pass only promotes pending suggestions; it never
    restores a rejected target or a row the user explicitly excluded.
    """
    selected = (candidates if candidates is not None else
                [c for c in r.get('candidates', []) if c.get('status', 'unreviewed') == 'unreviewed'])
    if not selected:
        return False
    rows = r.setdefault('rows', [])
    controls = controls or {}
    changed = False
    for candidate in selected:
        if candidate.get('status') == 'rejected':
            continue
        p = candidate.get('source')
        try:
            finite = np.asarray(p, float).shape == (3,) and np.isfinite(p).all()
        except (TypeError, ValueError):
            finite = False
        if not finite:
            candidate['usable'] = False
            continue
        row = next((v for v in rows if v.get('candidate_id') == candidate['id']), None)
        if row is None:
            name = str(candidate.get('name') or candidate['id']).strip()
            names = {v['id'] for v in rows}
            if name in names:
                name = candidate['id']
                suffix = 2
                while name in names:
                    name = f"{candidate['id']}_{suffix}"; suffix += 1
            candidate['name'] = name
            role = 'check' if candidate.get('role') == 'check' else 'fit'
            row = dict(id=name, candidate_id=candidate['id'], role=role,
                       source=list(p), control=list(controls.get(name, [None, None, None])))
            rows.append(row)
        candidate.update(status='accepted', usable=row.get('role') != 'off')
        changed = True
    if changed:
        r.update(solution=None, transformed=None, revision=uuid.uuid4().hex)
    return changed


def state_for(x):
    r=x.setdefault('registration_state',dict(cloud=copy.deepcopy(x['meta']),rows=[],candidates=[],solution=None,transformed=None,revision=uuid.uuid4().hex))
    include_candidates(r)
    return r

def activate(s,ident,isolate=False):
    persist(s);x=scan(s,ident);r=copy.deepcopy(state_for(x));r['cloud']=copy.deepcopy(x['meta'])
    s.STATE.update(registration=r,active_job_registration=ident)
    if isolate:
        for other in s.STATE.get('job_items',[]):
            if other['kind']=='scan':other['visible']=other['id']==ident
    return r

def save_rows(s,rows):
    r=s.STATE['registration'];out=[];seen=set();controls=s.STATE.setdefault('survey_controls',{})
    for row in rows:
        name=str(row.get('id','')).strip()
        if not name or len(name)>60 or name in seen:raise ValueError('Give every target a unique name within this scan (up to 60 characters).')
        seen.add(name);v=copy.deepcopy(row);v['id']=name
        if v.get('role') not in ('fit','check','off'):raise ValueError('Choose Fit, Check or Exclude.')
        for key in ('source','control'):
            a=v.get(key)
            if not isinstance(a,list) or len(a)!=3 or any(n is not None and (not isinstance(n,(int,float)) or not np.isfinite(n)) for n in a):raise ValueError('Target coordinates must be three numbers, or blank.')
        if all(n is None for n in v['control']) and name in controls:v['control']=list(controls[name])
        out.append(v)
    r.update(rows=out,solution=None,transformed=None,revision=uuid.uuid4().hex)
    for c in r.get('candidates',[]):
        row=next((v for v in out if v.get('candidate_id')==c['id']),None)
        if row:c['name']=row['id']
    persist(s)
    return out

def targets(x):
    # Target observations remain in the scan's stored frame. Matching consumes
    # world coordinates through the virtual registration pose, so accepting a
    # link never needs to rewrite target rows or the full XYZ cache.
    out={};state=state_for(x)
    rejected={c['id'] for c in state.get('candidates',[]) if c.get('status')=='rejected'}
    for row in state.get('rows',[]):
        if row.get('role')=='off':continue
        if row.get('candidate_id') in rejected:continue
        p=row.get('source',[])
        if len(p)!=3 or any(v is None for v in p) or not np.isfinite(p).all():continue
        name=row['id'].strip()
        if name in out:raise ValueError('Duplicate target name in '+x['name'])
        v=copy.deepcopy(row);v['source']=registration_pose.point(x['meta'],p).tolist();out[name]=v
    return out


def matched_targets(aa, bb, matches=None):
    """Resolve this pair afresh; check observations never become fit points."""
    ta,tb=targets(aa),targets(bb)
    if matches is None:
        matches=[dict(a=n,b=n,role='fit') for n in sorted(ta.keys()&tb.keys())]
    if not isinstance(matches,list) or len(matches)>200:
        raise ValueError('Invalid target matches.')
    clean=[];seen_a=set();seen_b=set()
    for raw in matches:
        if not isinstance(raw,dict):raise ValueError('Invalid target match.')
        m=copy.deepcopy(raw);role=m.get('role','fit')
        if role=='leica':role='fit'
        if role not in ('fit','check','off'):raise ValueError('Choose Fit, Check or Exclude for each match.')
        if role=='off':clean.append(m);continue
        na,nb=m.get('a'),m.get('b')
        if na not in ta or nb not in tb:raise ValueError('A matched target is missing or excluded; save target edits and refresh.')
        if na in seen_a or nb in seen_b:raise ValueError('Each target can appear only once in a link.')
        seen_a.add(na);seen_b.add(nb)
        if ta[na].get('role')=='check' or tb[nb].get('role')=='check':role='check'
        m['role']=role;clean.append(m)
    return clean

def stamp(s,ids):
    return hashlib.sha256(json.dumps([(i,scan(s,i)['meta']['file'],scan(s,i)['meta'].get('mask_file'),targets(scan(s,i))) for i in sorted(ids)],sort_keys=True,allow_nan=False).encode()).hexdigest()

def component(s,ident):
    ids={ident}
    while True:
        before=len(ids)
        for link in s.STATE.get('scan_links',[]):
            if link['a'] in ids or link['b'] in ids:ids.update([link['a'],link['b']])
        if len(ids)==before:return ids

def solve_pair(s,a,b,matches=None,tolerance=5,mode="rigid",anchor=None):
    persist(s)
    if a==b:raise ValueError('Choose two different scans.')
    aa,bb=scan(s,a),scan(s,b);matches=matched_targets(aa,bb,matches)
    left,right=component(s,a),component(s,b);anchor=anchor or a
    if anchor not in left|right:raise ValueError('The fixed setup must belong to one of these connected groups.')
    if left&right:raise ValueError('These scans are already connected. Use the connected-network registration for this link.')
    if anchor in right:
        a,b=b,a;aa,bb=bb,aa;left,right=right,left
        matches=[{**m,'a':m.get('b'),'b':m.get('a')} for m in matches]
    ta,tb=targets(aa),targets(bb);rows=[]
    for m in matches:
        if m.get('role')=='off':continue
        na,nb=m.get('a'),m.get('b')
        rows.append(dict(id=na+' ↔ '+nb,role=m.get('role','fit'),source=tb[nb]['source'],control=ta[na]['source']))
    solution=registration.solve(rows,tolerance,mode)
    solution.update(anchor=anchor,anchor_name=scan(s,anchor)['name'])
    moving=right;fixed=left
    draft=dict(id=uuid.uuid4().hex,a=a,b=b,matches=matches,solution=solution,mode=mode,moving=sorted(moving),fixed=sorted(fixed),stamp=stamp(s,moving|fixed))
    if mode=='vertical':draft['vertical_axis']=[0.,0.,1.]
    s.STATE['scan_link_draft']=draft;return draft

def positioned(meta,R,t,work,progress):
    # Compatibility entry point retained for callers outside registration. New
    # target-link acceptance is virtual and never calls this full-cache rewrite.
    jobdata.room(work,meta['count']*24+8*1024*1024)
    path=asset_folder(work,'registered')/(uuid.uuid4().hex+'.xyz64');p=np.memmap(meta['file'],'<f8','r',shape=(meta['count'],3));lo=np.full(3,np.inf);hi=-lo;out={}
    try:
        with path.open('wb') as f:
            for start in range(0,len(p),200000):
                q=np.asarray(p[start:start+200000])@R.T+t;q.astype('<f8').tofile(f);lo=np.minimum(lo,q.min(0));hi=np.maximum(hi,q.max(0));progress(f'Positioning {meta["name"]}: {min(start+len(q),len(p)):,} / {len(p):,}')
        out={**meta,'file':str(path),'minimum':lo.tolist(),'maximum':hi.tolist()}
        for k in ('preview_file','preview_count','preview_stride','retained_properties','retained_summary','registration_rotation','registration_translation','virtual_registration'):out.pop(k,None)
        jobdata.preview_cache(out,path.parent,progress);return out
    except BaseException:
        path.unlink(missing_ok=True)
        if out.get('preview_file'):Path(out['preview_file']).unlink(missing_ok=True)
        raise
    finally:p._mmap.close()

def move_targets(r,R,t):
    for c in r.get('candidates',[])+r.get('rows',[]):
        p=c.get('source')
        if p is not None and len(p)==3 and all(v is not None for v in p):c['source']=(R@np.asarray(p)+t).tolist()
        if c.get('normal') is not None:c['normal']=(R@np.asarray(c['normal'])).tolist()
    r.update(solution=None,transformed=None,revision=uuid.uuid4().hex)


def apply_control_solution(s,r,solution):
    """Position the existing connected component, retaining its source caches.

    Control solutions map stored target coordinates to the control frame. The
    corresponding world-space delta moves all connected setups together. A
    repeated Apply therefore cannot add a second transform or duplicate scan.
    The caller owns the transaction/save so analysis can be prepared atomically.
    """
    ident=s.STATE.get('active_job_registration');anchor=scan(s,ident)
    if anchor['meta']['file']!=r.get('cloud',{}).get('file'):
        raise ValueError('The active registration scan changed. Solve it again.')
    if r.get('applied_solution_id')==solution['id'] and r.get('transformed'):
        return copy.deepcopy(anchor['meta'])
    persist(s);ids=component(s,ident)
    targetR=np.asarray(solution['rotation'],float)
    targett=np.asarray(solution['control_centroid'],float)-targetR@np.asarray(solution['source_centroid'],float)
    oldR,oldt=registration_pose.get(anchor['meta'])
    R=targetR@oldR.T;t=targett-R@oldt
    for x in scans(s):
        if x['id'] not in ids:continue
        oldmeta=copy.deepcopy(x['meta']);bounds=registration_pose.world_bounds(oldmeta)
        origin=x.get('scan_origin',np.mean(bounds,axis=0))
        x.setdefault('original_meta',oldmeta)
        registration_pose.compose(x['meta'],R,t)
        x['scan_origin']=(R@np.asarray(origin)+t).tolist()
        x['scan_orientation']=(R@np.asarray(x.get('scan_orientation',np.eye(3)))).tolist()
        x.update(visible=True,position_status='Positioned by reviewed survey control',
                 hidden_boxes=[],isolate_box=None,limit_box=None)
        state=state_for(x);state.update(cloud=copy.deepcopy(x['meta']),solution=None,
                                       transformed=None,revision=uuid.uuid4().hex)
    import registered_cloud
    registered_cloud.reconcile_state(s.STATE,ids)
    registered_cloud._clear_aliases(s.STATE,ids)
    for link in s.STATE.get('scan_links',[]):
        if link['a'] in ids and link['b'] in ids:
            if (link.get('mode') or (link.get('solution') or {}).get('mode'))=='vertical':
                link['vertical_axis']=(R@np.asarray(link.get('vertical_axis',[0.,0.,1.]),float)).tolist()
            link['target_stamp']=stamp(s,[link['a'],link['b']])
    meta=copy.deepcopy(anchor['meta'])
    r.update(cloud=copy.deepcopy(meta),transformed=copy.deepcopy(meta),solution=solution,
             applied_solution_id=solution['id'],applied_at=time.time(),revision=uuid.uuid4().hex)
    anchor['registration_state']=copy.deepcopy(r)
    s.STATE['registration']=r
    return meta

def selected_merge_ids(s, selected):
    """Resolve exactly the selected setups, requiring a saved connection path.

    Intermediate setups may establish registration, but are not added to the
    output. A common bundle label alone does not establish an alignment.
    """
    if not isinstance(selected,list) or not 2<=len(selected)<=200:
        raise ValueError('Select between two and 200 registered setups to merge.')
    if any(not isinstance(v,str) or not v for v in selected) or len(set(selected))!=len(selected):
        raise ValueError('Choose different existing setups for the merge.')
    ids=set(selected)
    for ident in ids:scan(s,ident)
    available={x['id'] for x in scans(s)}
    connected={selected[0]}
    while True:
        previous=len(connected)
        for link in s.STATE.get('scan_links',[]):
            if link.get('a') not in available or link.get('b') not in available:continue
            # Drafts do not live in scan_links. Stored links are applied poses,
            # including manually reviewed and imported registration links.
            if link['a'] in connected or link['b'] in connected:
                connected.update((link['a'],link['b']))
        if len(connected)==previous:break
    if not ids<=connected:
        raise ValueError('These setups have no saved registration connection. Register them first, then merge.')
    return ids


def merge_estimate(s, ident, source_ids=None):
    ids=component(s,ident) if source_ids is None else selected_merge_ids(s,source_ids)
    selected=[x for x in scans(s) if x['id'] in ids]
    total=sum(int(x['meta'].get('count',0)) for x in selected)
    # Registered-cloud materialisation writes float64 XYZ (24 bytes) and four
    # float32 attributes (16 bytes) per retained point. Preview overhead is
    # bounded; RESERVE remains the general safety margin. Counts are an upper
    # bound when deletion masks exist, which is deliberately conservative.
    payload=total*40 + 8*1024*1024
    import shutil
    free=shutil.disk_usage(s.WORK).free
    return dict(scan_id=ident,setups=len(selected),points=total,payload_bytes=payload,
                reserve_bytes=jobdata.RESERVE,required_free_bytes=payload+jobdata.RESERVE,
                free_bytes=free,enough=free>=payload+jobdata.RESERVE)

def build_merge(s, ident, progress, registration_id=None, source_ids=None, name=None):
    ids=component(s,ident) if source_ids is None else selected_merge_ids(s,source_ids)
    selected=[x for x in scans(s) if x['id'] in ids]
    if len(selected)<2:raise ValueError('Connect at least two registered setups before building a merged cloud.')
    registration_id=registration_id or 'manual-merge-'+uuid.uuid4().hex
    import registered_cloud,visual_registration
    def source_assets():
        rows=[]
        for source_id in sorted(ids):
            meta=scan(s,source_id)['meta']
            for key in ('file','attributes_file','mask_file'):
                if not meta.get(key):continue
                path=Path(meta[key]).resolve();info=path.stat()
                rows.append((source_id,key,str(path),info.st_dev,info.st_ino,
                             info.st_size,info.st_mtime_ns,info.st_ctime_ns))
        return rows
    context=id(s.STATE)
    before=visual_registration.fingerprint(s,ids)
    assets=source_assets()
    merged=registered_cloud.build(s,copy.deepcopy(selected),registration_id,progress)
    try:
        if source_ids is not None:selected_merge_ids(s,source_ids)
        if name is not None:
            if not isinstance(name,str) or not name.strip() or len(name.strip())>120:
                raise ValueError('Enter a merged cloud name of 1–120 characters.')
            merged['name']=merged['meta']['name']=name.strip()
        if (context!=id(s.STATE) or before!=visual_registration.fingerprint(s,ids)
                or assets!=source_assets()):
            raise ValueError('Source setups changed while building the merged cloud. Build it again from the current sources.')
        # Final cancellable checkpoint; installation and the atomic save follow.
        progress('Saving merged cloud…')
        old=copy.deepcopy(s.STATE)
        try:
            registered_cloud.install(s,merged)
            # install preserves an old result's name when replacing it. An
            # explicitly named selected merge keeps the reviewed new name.
            if name is not None:merged['name']=merged['meta']['name']=name.strip()
            s.save()
        except BaseException:
            s.STATE.clear();s.STATE.update(old)
            raise
        return merged
    except BaseException:
        jobdata.remove_assets(merged['meta'])
        raise

def merge_caches(s):
    out=[]
    for x in s.STATE.get('job_items',[]):
        if not x.get('registered_merge'):continue
        m=x.get('meta') or {};files=[];size=0
        for key in ('file','attributes_file','mask_file','preview_file'):
            value=m.get(key)
            if value and Path(value).is_file():
                files.append(value);size+=Path(value).stat().st_size
        edited=bool(m.get('mask_file') or x.get('edit_history') or x.get('redo_history') or x.get('cleaning_provenance'))
        out.append(dict(id=x['id'],name=x.get('name','Registered cloud'),setups=len(x.get('merge_source_ids',[])),count=int(m.get('count',0)),cache_bytes=size,edited=edited,visible=bool(x.get('visible',True)),stale=bool(x.get('merge_stale')),archived=bool(x.get('merge_archived'))))
    return out

def release_merge(s, ident):
    x=next((x for x in s.STATE.get('job_items',[]) if x.get('registered_merge') and x['id']==ident),None)
    if x is None:raise ValueError('The selected merged cloud is no longer available.')
    m=x.get('meta') or {}
    if m.get('mask_file') or x.get('edit_history') or x.get('redo_history') or x.get('cleaning_provenance'):
        raise ValueError('This merged cloud has edits or derived work. Keep it, or explicitly export/backup it before removing it from the project.')
    import projects,workspace_history
    root=Path(s.WORK).resolve()
    # Establish readable ownership before changing the scene as well as before
    # removing files; unknown references in a damaged project block release.
    projects._protected_asset_paths(root,None,workspace_history.storage_state(s))
    old=copy.deepcopy(s.STATE)
    paths=[m.get(k) for k in ('file','attributes_file','preview_file') if m.get(k)]
    if m.get('file'):paths.append(str(m['file'])+'.viewindex.npz')
    s.STATE['job_items']=[item for item in s.STATE.get('job_items',[]) if item['id']!=ident]
    for child in s.STATE['job_items']:
        if child.get('merge_parent')==ident:
            child.pop('merge_parent',None);child['visible']=True
    for link in s.STATE.get('scan_links',[]):
        if link.get('completed_merge_id')==ident:link.pop('completed_merge_id',None)
    if (s.STATE.get('last_registered_merge') or {}).get('id')==ident:s.STATE.pop('last_registered_merge',None)
    import registered_cloud
    registered_cloud._clear_aliases(s.STATE,{ident})
    try:s.save()
    except Exception:
        s.STATE.clear();s.STATE.update(old);raise
    # Physical merges can be shared by a saved project, a scene copy, recovery
    # or Undo. Removing this scene entry does not transfer ownership of those
    # references. Only unreferenced internal generated caches may be erased.
    protected=projects._protected_asset_paths(root,None,workspace_history.storage_state(s))
    reclaimed=0;protected_bytes=0
    for value in paths:
        p=Path(value).resolve()
        try:
            if not p.is_relative_to(root) or not (p.suffix in ('.xyz64','.attr32','.preview64') or p.name.endswith('.xyz64.viewindex.npz')):continue
            if p.is_file():
                size=p.stat().st_size
                if p in protected:protected_bytes+=size;continue
                p.unlink();reclaimed+=size
        except OSError:pass
    return dict(id=ident,reclaimed_bytes=reclaimed,protected_bytes=protected_bytes)

def accept(s,ident,reviewed=True,merge=False):
    persist(s);d=s.STATE.get('scan_link_draft')
    if not d or ident!=d['id']:raise ValueError('This registration result is no longer current. Run registration again.')
    ids=set(d['moving'])|set(d['fixed'])
    if d.get('bundle'):
        from bundle_workflow import verify_draft
        verify_draft(s,d)
    if d.get('network'):
        from registration_network import topology
        changed=topology(s,ids)!=d['network_topology'] or (component(s,d['a'])|component(s,d['b']))!=ids
    else:changed=component(s,d['a'])!=set(d['fixed']) or component(s,d['b'])!=set(d['moving'])
    if stamp(s,ids)!=d['stamp'] or changed:raise ValueError('Scans, targets or links changed. Solve the geometry again.')
    old=copy.deepcopy(s.STATE);created=[]
    if not d.get('network'):R=np.asarray(d['solution']['rotation']);t=np.asarray(d['solution']['translation'])
    import job_api
    progress=lambda msg:job_api.progress(s,msg)
    try:
        # Transform-only acceptance: keep the one existing full-density cache
        # and compose a tiny rigid pose in metadata. This is effectively O(1)
        # disk growth per accepted link, regardless of scan point count.
        replacements=[]
        for ident in d['moving']:
            if d.get('network'):R=np.asarray(d['transforms'][ident]['rotation']);t=np.asarray(d['transforms'][ident]['translation'])
            x=scan(s,ident);oldmeta=copy.deepcopy(x['meta']);registration_pose.compose(x['meta'],R,t);replacements.append((x,oldmeta,R.copy(),t.copy()))
        for x,oldmeta,R,t in replacements:
            origin=x.get('scan_origin',np.mean(registration_pose.world_bounds(oldmeta),axis=0))
            x.setdefault('original_meta',copy.deepcopy(oldmeta))
            r=state_for(x);r['cloud']=copy.deepcopy(x['meta']);r.update(solution=None,transformed=None,revision=uuid.uuid4().hex)
            x['scan_origin']=(R@np.asarray(origin)+t).tolist()
            x['scan_orientation']=(R@np.asarray(x.get('scan_orientation',np.eye(3)))).tolist()
            position_status={'manual_visual':'Virtually positioned by manual visual alignment','cloud_to_cloud':'Virtually positioned by reviewed cloud-to-cloud refinement · not survey control','bundle_adjustment':'Virtually positioned by global bundle adjustment · review checks and loop links'}.get(d.get('kind'),'Virtually positioned by reviewed target link · no duplicate XYZ cache')
            x.update(position_status=position_status,hidden_boxes=[],isolate_box=None,limit_box=None)
        # A component can later be moved by a full 3D registration. Carry the
        # original keep-vertical direction with that component for future loop
        # adjustments; it is not necessarily global Z after such a move.
        moved_rotations={x['id']:R for x,_old,R,_t in replacements}
        for edge in s.STATE.get('scan_links',[]):
            edge_mode=edge.get('mode') or (edge.get('solution') or {}).get('mode')
            if edge_mode=='vertical' and edge['a'] in moved_rotations and edge['b'] in moved_rotations:
                Ra,Rb=moved_rotations[edge['a']],moved_rotations[edge['b']]
                axis=np.asarray(edge.get('vertical_axis',[0.,0.,1.]),float)
                new_axis=Ra@axis
                if np.linalg.norm(new_axis-Rb@axis)<1e-6:
                    edge['vertical_axis']=new_axis.tolist()
        for x in scans(s):
            if x['id'] in ids:x['visible']=True
        # A previous physical merge is a frozen copy of the old poses. It must
        # not remain visible or serve as the active analysis source after Apply.
        import registered_cloud
        registered_cloud.reconcile_state(s.STATE,ids)
        if d.get('network'):
            edges=copy.deepcopy(d['network_edges'])
            for edge in edges:
                if edge.get('solution'):edge.setdefault('original_solution',edge['solution'])
                edge.update(solution=copy.deepcopy(d['network_reports'][edge['id']]),status='network adjusted',network_adjustment_id=d['id'])
                if edge.get('mode')=='vertical':
                    R=moved_rotations.get(edge['a'],np.eye(3))
                    edge['vertical_axis']=(R@np.asarray(edge.get('vertical_axis',[0.,0.,1.]),float)).tolist()
            s.STATE['scan_links']=[l for l in s.STATE.get('scan_links',[]) if not (l['a'] in ids and l['b'] in ids)]+edges
            s.STATE['last_network_adjustment']=dict(id=d['id'],anchor=d['solution'].get('anchor_name'),coordinate_references=copy.deepcopy(d['solution'].get('coordinate_references',[])),scan_count=len(ids),link_count=len(edges),before=d['solution']['before'],after=d['solution']['fit'])
        else:s.STATE.setdefault('scan_links',[]).append({**copy.deepcopy(d),'status':'manually aligned' if d.get('kind')=='manual_visual' else 'accepted','kind':d.get('kind','target_match')})
        for link in s.STATE['scan_links']:
            if link['a'] in ids and link['b'] in ids:link['target_stamp']=stamp(s,[link['a'],link['b']])
        s.STATE.pop('scan_link_draft',None)
        s.STATE['last_registration_result']={k:copy.deepcopy(d[k]) for k in ('id','a','b','solution','moving','fixed','network','mode','request') if k in d}
        s.STATE['last_registration_result'].update(applied=True,applied_at=time.time())
        active=s.STATE.get('active_job_registration')
        if active in d['moving']:s.STATE['registration']=copy.deepcopy(state_for(scan(s,active)))
        if s.STATE.get('active_job_scan') in d['moving']:s.STATE.update(cloud=None,derived=None,valid_points=0,active_job_scan=None,joints=None,report_ready=False,clean_ready=False)
        if s.STATE.get('surface_source_item') in d['moving']:s.STATE.update(surface_source=None,surface_result=None,surface_source_item=None)
        if merge:
            import registered_cloud
            merged=registered_cloud.build(s,[x for x in scans(s) if x['id'] in ids],d['id'],progress)
            created.extend(merged['meta'][k] for k in ('file','attributes_file','preview_file'))
            registered_cloud.install(s,merged)
        s.STATE['export_ready']=None
        if d.get('bundle'):
            from bundle_workflow import record_applied
            record_applied(s,d)
        s.save()
    except BaseException:
        s.STATE.clear();s.STATE.update(old)
        for p in created:Path(p).unlink(missing_ok=True)
        raise

def public(s):
    persist(s)
    from registration_review import public as public_review
    available={x['id']:x for x in scans(s)}
    return dict(scans=[dict(id=x['id'],name=x['name'],targets=list(targets(x)),target_roles={r['id']:r.get('role','fit') for r in state_for(x).get('rows',[])},origin=x.get('scan_origin'),has_setup=__import__('setup_camera').has_pose(x),position_status=x.get('position_status')) for x in scans(s)],links=[{**l,'a_name':available.get(l['a'],{}).get('name','Removed scan'),'b_name':available.get(l['b'],{}).get('name','Removed scan')} for l in s.STATE.get('scan_links',[])],draft=s.STATE.get('scan_link_draft'),last_registration_result=s.STATE.get('last_registration_result'),match_review=public_review(s),last_adjustment=s.STATE.get('last_network_adjustment'),last_merge=s.STATE.get('last_registered_merge'),merge_caches=merge_caches(s),controls=s.STATE.get('survey_controls',{}),active=s.STATE.get('active_job_registration'))

def scan_summary(s,x):
    """Compact sidebar information; stored matches describe the accepted fit."""
    by_id={v['id']:v for v in scans(s)};links=[]
    for link in s.STATE.get('scan_links',[]):
        if x['id'] not in (link['a'],link['b']):continue
        forward=x['id']==link['a'];other=by_id.get(link['b'] if forward else link['a'])
        if other is None:continue
        solution=link.get('solution') or {};residuals={r['id']:r for r in solution.get('residuals',[])}
        matches=[]
        for m in link.get('matches',[]):
            err=residuals.get(m['a']+' ↔ '+m['b'],{})
            matches.append(dict(target=m['a'] if forward else m['b'],other_target=m['b'] if forward else m['a'],role=m.get('role','fit'),error_mm=err.get('error_mm')))
        links.append(dict(id=link['id'],other_id=other['id'],other_name=other['name'],kind=link.get('kind'),status=link.get('status'),method=solution.get('method') or link.get('source_method'),rms_mm=solution.get('fit',{}).get('rms_mm'),matches=matches,pairs_recorded='matches' in link,shared_names=[] if 'matches' in link else sorted(targets(x).keys()&targets(other).keys())))
    state=state_for(x);rows=state.get('rows',[]);markers=[];used=set()
    for c in state.get('candidates',[]):
        if c.get('status')=='rejected':continue
        row=next((r for r in rows if r.get('candidate_id')==c['id']),None)
        name=row['id'] if row else c.get('name',c['id']);used.add(name)
        markers.append(dict(id=name,key='candidate:'+c['id'],source=c.get('source'),status=c.get('status','unreviewed')))
    for row in rows:
        if row['id'] not in used:markers.append(dict(id=row['id'],key='row:'+row['id'],source=row.get('source'),status='accepted'))
    for marker in markers:
        p=marker.get('source')
        if p is not None and len(p)==3 and all(v is not None for v in p):
            marker['source']=registration_pose.point(x['meta'],p).tolist()
    return dict(targets=[dict(name=r['id'],role=r.get('role','fit')) for r in rows],links=links,markers=markers)

def api(s,h,path,body=None):
    if path!='/api/scan-registration':return False
    if body is None:
        value=public(s);value['last_vis_pose_result']=s.STATE.get('last_vis_pose_result')
        h.send(value);return True
    if body.get('action')=='cloud_status':
        import cloud_registration_workflow
        h.send(cloud_registration_workflow.status(s,body));return True
    if body.get('action')=='bundle_status':
        import bundle_workflow
        h.send(bundle_workflow.status(s,body));return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    action=body.get('action')
    if action in ('bundle_inspect','bundle_preview','bundle_apply'):
        import bundle_workflow
        handler={'bundle_inspect':bundle_workflow.inspect,'bundle_preview':bundle_workflow.start,'bundle_apply':bundle_workflow.apply}[action]
        h.send(handler(s,body));return True
    if action=='cloud_refine':
        import cloud_registration_workflow
        h.send(cloud_registration_workflow.start(s,body));return True
    if action=='vis_inspect':
        import rtc360_vis_pose
        h.send(rtc360_vis_pose.inspect_item(scan(s,body.get('scan_id'))));return True
    if action in ('vis_preview','vis_apply','vis_show'):
        import rtc360_vis_workflow
        handler={'vis_preview':rtc360_vis_workflow.preview,'vis_apply':rtc360_vis_workflow.start_apply,'vis_show':rtc360_vis_workflow.show}[action]
        h.send(handler(s,body));return True
    if action in ('visual_preview','visual_apply'):
        import visual_registration
        h.send(visual_registration.preview(s,body) if action=='visual_preview' else visual_registration.apply(s,body));return True
    if action=='activate':activate(s,body['id'],body.get('isolate',True));s.save()
    elif action=='review_matches':
        from registration_review import start
        ident=start(s,body);h.send({'started':True,'saved':True,'review_id':ident});return True
    elif action=='adopt_match_review':
        from registration_review import adopt
        h.send(adopt(s,body));return True
    elif action=='register_setups':
        request_id=uuid.uuid4().hex
        request=copy.deepcopy(body)
        def register_setups():
            import job_api
            # Solve and apply are one user action. The inner accept rollback
            # starts after solving, so protect the earlier draft/history state
            # too when solving, cancellation or saving prevents completion.
            previous=copy.deepcopy(s.STATE)
            selection=copy.deepcopy(getattr(s,'SELECTION',None))
            try:
                a,b=request['a'],request['b'];mode=request.get('mode','rigid');anchor=request.get('anchor') or a
                progress=lambda message:job_api.progress(s,message)
                s.STATE.pop('scan_link_draft',None)
                if component(s,a)&component(s,b):
                    from registration_network import solve as solve_network
                    draft=solve_network(s,a,b,request.get('matches'),request.get('tolerance',5),mode,anchor,progress)
                else:
                    draft=solve_pair(s,a,b,request.get('matches'),request.get('tolerance',5),mode,anchor)
                draft['id']=request_id
                draft['request']={k:request.get(k) for k in ('a','b','mode','anchor')}
                draft['request'].update(mode=mode,anchor=anchor)
                progress('Applying registration to the existing setups…')
                accept(s,request_id,merge=False)
            except BaseException:
                s.STATE.clear();s.STATE.update(previous);s.SELECTION=selection
                raise
        s.start_job(register_setups,kind='job',native_progress=True)
        h.send({'started':s.JOB['busy'],'saved':True,'registration_id':request_id});return True
    elif action=='solve':
        s.STATE.pop('scan_link_draft',None);solve_pair(s,body['a'],body['b'],body.get('matches'),body.get('tolerance',5),body.get('mode','rigid'),body.get('anchor'));s.save()
    elif action in ('solve_network','solve_targets'):
        from registration_network import solve
        s.STATE.pop('scan_link_draft',None)
        def adjust():
            import job_api
            draft=solve(s,body['a'],body['b'],body.get('matches'),body.get('tolerance',5),body.get('mode','rigid'),body.get('anchor'),lambda m:job_api.progress(s,m),references=body.get('references',[]) if action=='solve_targets' else None)
            if action=='solve_targets':
                from registration_review import attach_provenance
                attach_provenance(s,body,draft)
            s.save()
        s.start_job(adjust,kind='job')
    elif action=='accept':s.start_job(lambda:accept(s,body['id'],body.get('reviewed',False),body.get('merge',False)),kind='job',native_progress=True)
    elif action=='selection_merge_estimate':
        ids=body.get('ids')
        selected_merge_ids(s,ids)
        import visual_registration
        result=merge_estimate(s,ids[0],source_ids=ids)
        result['selection_token']=visual_registration.fingerprint(s,ids)
        h.send(result);return True
    elif action=='build_selection_merge':
        ids=body.get('ids')
        selected_merge_ids(s,ids)
        import visual_registration
        if body.get('expected_token')!=visual_registration.fingerprint(s,ids):
            raise ValueError('The selected setups changed. Close and review the merge again.')
        name=body.get('name','Registered cloud')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>120:
            raise ValueError('Enter a merged cloud name of 1–120 characters.')
        registration_id='selected-merge-'+uuid.uuid4().hex
        def materialise_selection():
            import job_api
            build_merge(s,ids[0],lambda m:job_api.progress(s,m),registration_id,
                        source_ids=ids,name=name)
        s.start_job(materialise_selection,kind='job',native_progress=True)
        h.send({'started':True,'registration_id':registration_id});return True
    elif action=='merge_estimate':
        h.send(merge_estimate(s,body['id']));return True
    elif action=='build_merge':
        target=body['id']
        registration_id='manual-merge-'+uuid.uuid4().hex
        def materialise():
            import job_api
            progress=lambda m:job_api.progress(s,m)
            build_merge(s,target,progress,registration_id=registration_id)
        s.start_job(materialise,kind='job',native_progress=True)
        h.send({'started':True,'registration_id':registration_id});return True
    elif action=='release_merge':
        h.send(release_merge(s,body['id']));return True
    elif action=='remove_link':
        s.STATE['scan_links']=[l for l in s.STATE.get('scan_links',[]) if l['id']!=body['id']];s.STATE.pop('scan_link_draft',None);s.save()
    elif action=='controls':
        controls=body.get('controls',{})
        if not isinstance(controls,dict) or len(controls)>2000:raise ValueError('Invalid control list.')
        clean={}
        for name,xyz in controls.items():
            if not name.strip() or len(name)>60 or not isinstance(xyz,list) or len(xyz)!=3 or any(v is None for v in xyz) or not np.isfinite(xyz).all():raise ValueError('Control needs a name and finite E, N, H.')
            clean[name.strip()]=xyz
        s.STATE.setdefault('survey_controls',{}).update(clean);s.save()
    else:raise ValueError('Unknown scan registration operation.')
    h.send({'started':s.JOB['busy'],'saved':True});return True
