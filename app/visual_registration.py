"""Bounded visual previews and explicit, reversible manual rigid registration."""
import copy,hashlib,json,uuid
import numpy as np
import registration_workspace as rw,registration_pose,jobdata,scan_export

GROUP_PREVIEW_POINTS=40000

def groups(s,a,b):
    rw.scan(s,a);rw.scan(s,b)
    if a==b:raise ValueError('Choose two different setups.')
    direct=[v for v in s.STATE.get('scan_links',[]) if {v['a'],v['b']}=={a,b}]
    if len(direct)>1:raise ValueError('Review duplicate links for this pair before visual alignment.')
    edges=[v for v in s.STATE.get('scan_links',[]) if v not in direct]
    def component(ident):
        ids={ident}
        while True:
            before=len(ids)
            for edge in edges:
                if edge['a'] in ids or edge['b'] in ids:ids.update([edge['a'],edge['b']])
            if len(ids)==before:return ids
    fixed,moving=component(a),component(b)
    if fixed&moving:raise ValueError('These setups are connected by another route. Review or remove the loop link before moving one group manually.')
    return sorted(fixed),sorted(moving),direct[0]['id'] if direct else None

def fingerprint(s,ids):
    rows=[]
    for ident in sorted(ids):
        item=rw.scan(s,ident)
        rows.append([ident,scan_export.signature(item),item.get('hidden_boxes'),item.get('isolate_box'),item.get('limit_box'),item.get('limit_hide_outside'),rw.targets(item)])
    links=[l for l in s.STATE.get('scan_links',[]) if l['a'] in ids or l['b'] in ids]
    return hashlib.sha256(json.dumps([s.STATE.get('project_id'),rows,links],sort_keys=True,allow_nan=False).encode()).hexdigest()

def preview(s,body):
    rw.persist(s)
    a,b=body.get('fixed'),body.get('moving');fixed,moving,replaced=groups(s,a,b);ids=fixed+moving
    if len(ids)>200:raise ValueError('Choose groups of at most 200 setups for visual alignment.')
    before=fingerprint(s,ids);context=id(s.STATE)
    bounds=[registration_pose.world_bounds(rw.scan(s,ident)['meta']) for ident in ids]
    origin=(np.min([p[0] for p in bounds],axis=0)+np.max([p[1] for p in bounds],axis=0))/2
    clouds=[]
    for members,colour in ((fixed,[.12,.65,1.]),(moving,[1.,.5,.15])):
        per=max(1,GROUP_PREVIEW_POINTS//len(members));parts=[]
        for ident in members:
            # Reuse cached overview or bounded reads; never materialise full XYZ.
            sample=jobdata._item_sample(rw.scan(s,ident),per,origin,colour,mode='bundle',quick=True)
            parts.append(sample[:,:3])
        points=np.concatenate(parts)
        if not len(points):raise ValueError('One group has no visible sampled points. Restore its view or retained points first.')
        clouds.append(points.tolist())
    if context!=id(s.STATE) or before!=fingerprint(s,ids):raise ValueError('The workspace changed while loading. Reload the pair.')
    session=dict(token=uuid.uuid4().hex,fixed=a,moving=b,fixed_ids=fixed,moving_ids=moving,replaced_link=replaced,origin=origin.tolist(),stamp=before,context=context)
    s._visual_registration=session
    return {k:v for k,v in session.items() if k not in ('stamp','context')}|dict(clouds=clouds,sampled=True,total_points=[sum(int(rw.scan(s,i)['meta']['count']) for i in members) for members in (fixed,moving)])

def apply(s,body):
    session=getattr(s,'_visual_registration',None)
    if not session or body.get('token')!=session['token']:raise ValueError('This visual alignment is no longer current. Reload the pair.')
    ids=session['fixed_ids']+session['moving_ids']
    if id(s.STATE)!=session['context'] or fingerprint(s,ids)!=session['stamp']:raise ValueError('The scans, positions or links changed. Reload the pair before applying.')
    R=np.asarray(body.get('rotation'),dtype=float);local_t=np.asarray(body.get('translation'),dtype=float)
    if R.shape!=(3,3) or local_t.shape!=(3,) or not np.isfinite(R).all() or not np.isfinite(local_t).all():raise ValueError('Enter a finite rigid transform.')
    if not np.allclose(R.T@R,np.eye(3),atol=1e-8,rtol=0) or abs(np.linalg.det(R)-1)>1e-8:raise ValueError('Manual alignment must be a rotation and translation; scale and reflection are not allowed.')
    if body.get('correct_tilt') is not True and not np.allclose(R@[0.,0.,1.],[0.,0.,1.],atol=1e-8,rtol=0):raise ValueError('Enable Correct tilt before changing roll or pitch.')
    origin=np.asarray(session['origin']);t=origin+local_t-R@origin
    cloud_solution=None
    if body.get('refinement_id'):
        from cloud_registration_workflow import accepted_solution
        cloud_solution=accepted_solution(s,session,body,R,local_t)
    old=copy.deepcopy(s.STATE)
    try:
        if session['replaced_link']:
            s.STATE['scan_links']=[v for v in s.STATE.get('scan_links',[]) if v['id']!=session['replaced_link']]
        ident=session['replaced_link'] or uuid.uuid4().hex
        solution=cloud_solution or dict(id=ident,method='Manual visual alignment',rotation=R.tolist(),translation=t.tolist(),scale=1.,anchor=session['fixed'],anchor_name=rw.scan(s,session['fixed'])['name'],manual=True,
                      fit=dict(count=0,rms_mm=None,max_mm=None),check=dict(count=0,rms_mm=None,max_mm=None),residuals=[],warnings=['Visual alignment only; no independent target residual or cloud-to-cloud optimisation has been calculated.'])
        solution.update(id=ident,applied=True)
        draft=dict(id=ident,a=session['fixed'],b=session['moving'],matches=[],solution=solution,mode='rigid',kind='cloud_to_cloud' if cloud_solution else 'manual_visual',moving=session['moving_ids'],fixed=session['fixed_ids'],stamp=rw.stamp(s,ids))
        s.STATE['scan_link_draft']=draft
        # A new explicit Apply must not inherit a cancelled preview job's flag.
        import job_api
        job_api.CANCEL.clear();s.JOB_CANCEL_FILE.unlink(missing_ok=True)
        rw.accept(s,ident,reviewed=True,merge=False)
    except BaseException:
        s.STATE.clear();s.STATE.update(old);raise
    s._visual_registration=None
    return dict(applied=True,id=ident,moving=session['moving_ids'],fixed=session['fixed_ids'])
