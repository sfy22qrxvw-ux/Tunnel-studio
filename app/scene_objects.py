# Copyright (c) 2026 Damian Gulczynski.
"""Persistent created geometry and references for the Scans & models inventory."""
import copy,uuid
import numpy as np
import registration_pose

def source_revision(item):
    meta=item.get('meta',{})
    return registration_pose.revision(meta)

def alignment_outdated(s,result):
    revisions=result.get('source_revisions') or {result.get('source_id'):result.get('source_revision')}
    items={x['id']:x for x in s.STATE.get('job_items',[])}
    return any(revision and (ident not in items or source_revision(items[ident])!=revision) for ident,revision in revisions.items())

def remember(s,kind,result):
    objects=s.STATE.setdefault('scene_objects',[]);ident=result.get('scene_object_id')
    existing=next((o for o in objects if o['id']==ident),None)
    if existing is None:
        ident='geometry:'+uuid.uuid4().hex;result['scene_object_id']=ident
        name=result.get('name') or ('Scan-derived alignment' if kind=='alignment' else 'Cross-section '+str(round(result.get('position',0),3))+' m')+' · '+result['source_name']
        existing=dict(id=ident,kind=kind,name=name,visible=True);objects.append(existing)
    elif result.get('name'):existing['name']=result['name']
    value=copy.deepcopy(result)
    if value.get('preview'):
        p=value['preview']['points'];value['preview']['points']=p[::max(1,int(np.ceil(len(p)/2000))) ]
    existing['data']=value
    return ident

def public(s):
    out=[];options=s.STATE.get('scene_object_options',{})
    def add(ident,kind,name,points=None,**extra):
        if options.get(ident,{}).get('removed'):return
        row=dict(id=ident,kind=kind,name=name,visible=True,points=points or []);row.update(extra);row.update(options.get(ident,{}));out.append(row)
    objects=list(s.STATE.get('scene_objects',[]))
    legacy=s.STATE.get('estimated_alignment')
    if legacy and not any(o['id']==legacy.get('scene_object_id') for o in objects):objects.append(dict(id='estimate:'+legacy['id'],kind='alignment',name='Scan-derived alignment · '+legacy['source_name'],visible=True,data=legacy))
    section=s.STATE.get('free_section')
    if section and not any(o['id']==section.get('scene_object_id') for o in objects):objects.append(dict(id='section:'+section['id'],kind='section',name='Cross-section · '+section['source_name'],visible=True,data=section))
    for o in objects:
        if o['kind']=='measurement_v2':
            if not options.get(o['id'],{}).get('removed'):
                row=__import__('measure_tools').public_record(o)
                row.update(options.get(o['id'],{}));out.append(row)
            continue
        if o['kind']=='measurement':
            d=o['data'];add(o['id'],'measurement',o['name'],d['points'],visible=o.get('visible',True),source_id=d.get('source_id'),source_name=d.get('source_name'),editable=False,removable=True,length_m=d['length_m'],horizontal_m=d['horizontal_m'],vertical_m=d['vertical_m'],summary=f"3D {d['length_m']:.4f} m · horizontal {d['horizontal_m']:.4f} m · ΔH {d['vertical_m']:+.4f} m · fixed picked coordinates")
            continue
        d=o['data'];kind=o['kind'];points=[r[1:4] for r in d['rows']] if kind=='alignment' else d.get('outline',[])
        add(o['id'],kind,o['name'],points,visible=o.get('visible',True),source_id=d.get('source_id'),source_name=d.get('source_name'),editable=True,removable=True,summary=(str(len(points))+' nodes · '+('source points changed · stored alignment' if alignment_outdated(s,d) else 'tentative' if d.get('tentative') else 'scan-derived')) if kind=='alignment' else str(d['count'])+' full-density points · '+str(d['width'])+' m thickness')
    axis=s.STATE.get('axis')
    if axis is not None:add('axis:active','active_alignment','Active chainage alignment · '+axis.name,axis.a[:,1:4].tolist(),summary='Estimated reference for chainage and radial comparison' if s.STATE.get('axis_provenance',{}).get('kind')=='scan_estimate' else 'Current alignment used for chainage and design analysis',editable=False,removable=False)
    for x in s.STATE.get('job_items',[]):
        if x.get('limit_box'):add('limit:'+x['id'],'limit_box','Limit box · '+x['name'],source_id=x['id'],source_name=x['name'],box=copy.deepcopy(x['limit_box']),summary='Hide outside '+('on' if x.get('limit_hide_outside',True) else 'off')+' · eye controls box outline',editable=True,removable=True)
    for o in s.STATE.get('structures',[]):
        edges=[]
        if axis is not None:
            import structures
            wire=structures.wire(o,axis,[0,0,0]);edges=wire[::max(1,int(np.ceil(len(wire)/1500)))]
            if hasattr(edges,'tolist'):edges=edges.tolist()
            edges=[[e[:3],e[3:6]] for e in edges]
        add('structure:'+o['id'],'structure',o['name'],visible=o.get('visible',True),edges=edges,structure_id=o['id'],summary=o.get('kind','Reference structure'),editable=True,removable=True)
    for r in (s.STATE.get('joints') or {}).get('candidates',[]):
        if r.get('status')=='rejected':continue
        c=np.asarray(r['centre']);right=np.asarray(r['right']);up=np.asarray(r['up']);points=[(c+r['radius']*(np.cos(t)*right+np.sin(t)*up)).tolist() for t in np.linspace(0,2*np.pi,97)]
        add('joint:'+r['id'],'ring_joint','Ring candidate · '+r['id'],points,joint_id=r['id'],removable=True,summary='Ch. '+str(round(r['chainage'],3))+' m · '+r['status'],editable=True)
    result=s.STATE.get('surface_result') or {}
    if result.get('settings',{}).get('method')=='lining_features':
        meta=result.get('source_meta',{});source_id=result.get('source_item_id')
        item=next((i for i in s.STATE.get('job_items',[]) if i['id']==source_id),None)
        current=(item and source_revision(item)==source_revision({'meta':meta})) or (not source_id and (s.STATE.get('cloud') or {}).get('file')==meta.get('file'))
        if current:
            for f in result.get('features',[]):
                if f['kind']=='segment':continue
                edges=[];points=f.get('points',[])
                if f['kind']=='bolt_hole':
                    c=np.asarray(f['centre']);edges=[[(c-np.eye(3)[i]*.05).tolist(),(c+np.eye(3)[i]*.05).tolist()] for i in range(3)]
                recess=f['kind']=='bolt_hole'
                add('lining:'+result['id']+':'+str(f['id']),'lining_feature',f['name'],points,edges=edges,source_id=source_id,source_name=meta.get('name'),feature=copy.deepcopy(f),result_id=result['id'],cluster_id=f['id'],point_count=int(f.get('original_points',0)),summary=(f"{int(f.get('original_points',0)):,} measured recess points · select to review or delete from the scan" if recess else 'Candidate · '+f['kind'].replace('_',' ')+' · fitted guide; inspect measured support'),editable=recess,removable=True)
    d=s.STATE.get('design_comparison')
    if d:add('deviation:'+d['id'],'deviation','3D deviation · '+d.get('source_name','Scan'),summary=str(d['used'])+' compared points · '+d.get('reference_label','Reference'),editable=True,removable=False)
    d=s.STATE.get('inspection_result')
    if d:
        source=next((x for x in s.STATE.get('job_items',[]) if x['id']==d.get('source_id')),None)
        meta=source.get('meta',{}) if source else {};points=[]
        if meta.get('minimum') is not None and meta.get('maximum') is not None:
            points=[[meta['maximum' if bit&(1<<axis) else 'minimum'][axis] for axis in range(3)] for bit in range(8)]
        add('inspection:'+d['id'],'inspection',d.get('mode_label','Inspection')+' · '+d.get('source_name','Source'),
            points,source_id=d.get('source_id'),source_name=d.get('source_name'),result_id=d['id'],
            summary=f"{d['used']:,} included "+('surface samples' if d.get('source_kind')=='mesh' else 'points')+
                    ' · '+d.get('reference_name','Reference'),editable=True,removable=True)
    scan_map={x['id']:x for x in s.STATE.get('job_items',[]) if x['kind']=='scan'}
    for link in s.STATE.get('scan_links',[]):
        a,b=scan_map.get(link['a']),scan_map.get(link['b'])
        if a and b:
            points=[(x.get('scan_origin') or np.mean([x['meta']['minimum'],x['meta']['maximum']],axis=0).tolist()) for x in (a,b)]
            add('link:'+link['id'],'scan_link',a['name']+' ↔ '+b['name'],points,link_id=link['id'],connection=copy.deepcopy(link),visible=link.get('completed_merge_id') not in scan_map,summary='Scan registration link · '+link['status'],editable=True,removable=False)
    registration=s.STATE.get('registration',{}) or {};seen=set()
    for category,values in [('candidate',registration.get('candidates',[])),('row',registration.get('rows',[]))]:
        for r in values:
            p=r.get('source');key=category+':'+str(r.get('id',''));ident='target:'+key
            if p is None or len(p)!=3 or any(v is None for v in p) or not np.isfinite(p).all() or r.get('status')=='rejected' or str(r.get('candidate_id',r.get('id'))) in seen:continue
            seen.add(str(r.get('id')));p=registration_pose.point(registration.get('cloud') or {},p)
            add(ident,'target','Target · '+str(r.get('name',r.get('id'))),points=[p.tolist()],marker_key=key,summary=r.get('status','accepted'),editable=True,removable=False)
    out.extend(__import__('cad_api').public_objects(s))
    return out

def remove_row(s,row):
    if not row.get('removable'):raise ValueError('This reference can be hidden, but cannot be removed here.')
    ident=row['id'];objects=s.STATE.setdefault('scene_objects',[]);obj=next((o for o in objects if o['id']==ident),None)
    if obj:objects.remove(obj)
    if row['kind'] in ('alignment','section'):
        key='estimated_alignment' if row['kind']=='alignment' else 'free_section';current=s.STATE.get(key)
        if current and (current.get('scene_object_id')==ident or not obj):s.STATE[key]=None
    elif row['kind']=='limit_box':
        owner=next(x for x in s.STATE['job_items'] if x['id']==row['source_id']);owner['limit_box']=None;owner['limit_hide_outside']=False
    elif row['kind']=='structure':s.STATE['structures']=[o for o in s.STATE['structures'] if o['id']!=row['structure_id']]
    elif row['kind']=='inspection':
        current=s.STATE.get('inspection_result')
        if current and 'inspection:'+current['id']==ident:s.STATE['inspection_result']=None
    s.STATE.get('scene_object_options',{}).pop(ident,None)
    if row['kind'] in ('lining_feature','ring_joint'):
        # Remove the displayed guide, retaining analysis labels and original
        # scan coordinates. Workspace Undo restores this metadata change.
        s.STATE.setdefault('scene_object_options',{})[ident]={'removed':True}


def post(s,h,path,body):
    if path!='/api/scene-object':return False
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    if body.get('action')=='remove_many':
        ids=body.get('ids');available={o['id']:o for o in public(s)}
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Select available geometry items.')
        rows=[available.get(i) for i in ids]
        if any(r is None or not r.get('removable') or r['kind']=='cad' for r in rows):raise ValueError('A selected geometry item is unavailable or protected. Refresh the selection.')
        old=copy.deepcopy(s.STATE)
        try:
            for row in rows:remove_row(s,row)
            s.save()
        except Exception:s.STATE.clear();s.STATE.update(old);raise
        h.send(dict(saved=True,removed=len(rows)));return True
    if body.get('action')=='add_measurement':
        try:points=np.asarray(body.get('points'),dtype=float)
        except (ValueError,TypeError):raise ValueError('Pick two finite scan points.')
        if points.shape!=(2,3) or not np.isfinite(points).all():raise ValueError('Pick two finite scan points.')
        delta=points[1]-points[0];length=float(np.linalg.norm(delta))
        if length<1e-8:raise ValueError('Pick two different points for a distance.')
        source=next((x for x in s.STATE.get('job_items',[]) if x['id']==body.get('source_id')),None)
        old=copy.deepcopy(s.STATE);ident='measurement:'+uuid.uuid4().hex
        try:
            number=1+sum(o['kind']=='measurement' for o in s.STATE.get('scene_objects',[]))
            data=dict(points=points.tolist(),length_m=length,horizontal_m=float(np.hypot(*delta[:2])),vertical_m=float(delta[2]),source_id=source['id'] if source else None,source_name=source['name'] if source else 'Picked scan points')
            s.STATE.setdefault('scene_objects',[]).append(dict(id=ident,kind='measurement',name=f'Distance {number} · {length:.4f} m',visible=True,data=data));s.save()
        except Exception:s.STATE.clear();s.STATE.update(old);raise
        h.send(dict(saved=True,id=ident));return True
    ident=str(body.get('id',''));action=body.get('action')
    if ident.startswith('cad:'):
        import cad_api
        mapped={'rename':'properties'}.get(action,action)
        return cad_api.post(s,h,'/api/cad-action',dict(body,action=mapped,ids=[ident],revision=cad_api.current(s)['revision'],context=cad_api.context(s)))
    row=next((o for o in public(s) if o['id']==ident),None)
    if row is None:raise ValueError('This geometry is no longer in the scene.')
    old=copy.deepcopy(s.STATE)
    try:
        objects=s.STATE.setdefault('scene_objects',[]);obj=next((o for o in objects if o['id']==ident),None)
        if action in ('visibility','rename'):
            changes={'visible':not row['visible']} if action=='visibility' else {'name':str(body.get('name','')).strip()[:150]}
            if action=='rename' and not changes['name']:raise ValueError('Enter a name.')
            if obj:obj.update(changes)
            elif ident.startswith('structure:'):next(o for o in s.STATE['structures'] if o['id']==row['structure_id']).update(changes)
            else:s.STATE.setdefault('scene_object_options',{}).setdefault(ident,{}).update(changes)
        elif action=='open':
            if row['kind'] in ('alignment','section'):
                key='estimated_alignment' if row['kind']=='alignment' else 'free_section'
                if obj:
                    s.STATE[key]=copy.deepcopy(obj['data'])
                    if row['kind']=='alignment':s.STATE[key]['name']=obj['name']
                elif not s.STATE.get(key):raise ValueError('Geometry is no longer available.')
        elif action=='remove':remove_row(s,row)
        else:raise ValueError('Unknown scene geometry action.')
        s.save()
    except Exception:s.STATE.clear();s.STATE.update(old);raise
    h.send(dict(saved=True,source_id=row.get('source_id'),kind=row['kind']));return True
