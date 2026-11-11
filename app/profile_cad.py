# Copyright (c) 2026 Damian Gulczynski.
"""Transfer tunnel profiles into the Model CAD workspace and back."""
import copy
import json
from types import SimpleNamespace
import uuid

import numpy as np

import cad_api
import cad_geometry as g
import tunnel_designer as designer
import workspace_history


def profile_geometry(profile, frame, offset=(0., 0.)):
    """Seed CAD with native circles and analytic line/arc control vertices."""
    kind=profile['kind']
    xy=np.asarray(offset)+[designer.finite(profile.get(k,0),k) for k in ('x','y')]
    origin,basis=g.matrix(frame)
    if kind=='circle':
        return g.build(dict(kind='circle',centre=(origin+basis[:,:2]@xy).tolist(),
                            radius=profile.get('radius'),frame=frame,segments=96))
    if kind in ('custom','polyline'):
        rows=np.asarray(profile.get('nodes'),float)
    elif kind in ('rectangle','arch'):
        width=g.number(profile.get('width'),'Profile width',True)
        height=g.number(profile.get('height'),'Profile height',True)
        r=width/2
        rows=np.array([[-r,-height/2,0],[r,-height/2,0],[r,height/2,0],[-r,height/2,0]])
        if kind=='arch':
            if height<r:raise ValueError('An arched profile height must be at least half its width.')
            rows=np.array([[-r,-height/2,0],[r,-height/2,0],[r,height/2-r,r],
                           [0,height/2,r],[-r,height/2-r,0]])
            if height==r:rows=rows[2:].copy()
    elif kind=='cad':
        obj=profile.get('geometry')
        if not obj:raise ValueError('Choose a linked CAD outline first.')
        frame0=profile.get('profile_frame') or g.closed_profile_frame(obj)
        rows=g.curve_nodes(obj,frame0)
    else:
        raise ValueError('Choose a profile before editing its outline.')
    if rows.ndim!=2 or rows.shape[1]!=3:
        raise ValueError('Enter X, Y and the radius to the next node.')
    rows=rows+[xy[0],xy[1],0.]
    return g.planar_curve(rows,frame,kind!='polyline')


def profile_shape(obj, frame, offset=(0., 0.)):
    """Carry the CAD definition back as a circle or signed-radius node list."""
    if obj.get('faces') or len(obj['vertices'])<2:
        raise ValueError('Use curves to define the profile boundary.')
    if obj.get('shape')=='circle' and obj.get('radius') is not None:
        x,y=g.plane_coordinates([obj['centre']],frame)[0]-offset
        return dict(kind='circle',radius=obj['radius'],x=float(x),y=float(y))
    rows=g.curve_nodes(obj,frame)-[offset[0],offset[1],0.]
    return dict(kind='custom' if obj['closed'] else 'polyline',nodes=rows.tolist(),x=0.,y=0.)


def begin(s,body):
    cad_api.check(s,body)
    if cad_api.workspace(s) is not s:
        raise ValueError('Finish or cancel the profile that is open in Model space.')
    design=designer.resolve_profiles(s,body.get('design',{}),allow_incomplete=True)
    json.dumps(design,allow_nan=False)
    profile=next((p for p in design['profiles'] if p['id']==body.get('profile_id')),None)
    if profile is None:raise ValueError('Choose a tunnel profile.')
    previous=copy.deepcopy(design)
    target=None
    working_frame=g.frame({'name':'Profile X/Y'})
    if profile['kind']=='cad' and profile.get('object_id'):
        target=cad_api.get_object(cad_api.current(s),profile['object_id'])
        working_frame=profile.get('profile_frame') or g.closed_profile_frame(target)

    data=cad_api.empty();data['active_frame']=working_frame
    seeds={}
    if target:
        obj=copy.deepcopy(target)
        obj.update(visible=True,layer='Model',bundle_id='')
        data['objects'].append(obj)
        seeds[obj['id']]=(None,copy.deepcopy(obj))
    else:
        parts=(designer.with_drawing_parts(profile,design.get('drawing_objects',[]))['parts']
               if profile['kind']=='composite' else [profile])
        shift=(profile.get('x',0),profile.get('y',0)) if profile['kind']=='composite' else (0.,0.)
        for part in parts:
            try:
                geometry=profile_geometry(part,working_frame,shift)
            except ValueError:
                if not body.get('draw'):raise
                continue
            obj=cad_api.object_record(geometry,dict(name=part['name']))
            data['objects'].append(obj)
            seeds[obj['id']]=(part['id'] if profile['kind']=='composite' else None,copy.deepcopy(obj))
        # Saved construction curves remain available through the CAD visibility
        # controls. Only this profile's existing parts are visible initially.
        active_ids={p['id'] for p in parts} if profile['kind']=='composite' else set()
        for part in design.get('drawing_objects',[]):
            if part['id'] in active_ids:continue
            try:geometry=profile_geometry(part,working_frame,shift)
            except ValueError:continue
            obj=cad_api.object_record(geometry,dict(name=part['name']))
            obj['visible']=False
            data['objects'].append(obj);seeds[obj['id']]=(part['id'],copy.deepcopy(obj))
    session=SimpleNamespace(
        STATE={'cad':data,'project_id':s.STATE.get('project_id')},JOB=s.JOB,save=lambda:None,
        id=uuid.uuid4().hex,name=profile['name'],base=(cad_api.context(s),cad_api.current(s)['revision']),
        design=design,previous=previous,model_id=body.get('model_id'),profile_id=profile['id'],
        frame=working_frame,linked_id=target['id'] if target else None,seeds=seeds,
    )
    s.PROFILE_CAD_EDIT=session
    ids=[o['id'] for o in data['objects'] if o['visible']]
    return dict(edit_id=session.id,ids=ids)


def result(s,session,body):
    data=cad_api.current(session)
    selected=body.get('ids')
    if selected is not None and (not isinstance(selected,list) or not selected):
        raise ValueError('Select the curves to use in the profile.')
    visible_layers={l['name'] for l in data['layers'] if l.get('visible',True)}
    objects=[o for o in data['objects'] if o.get('visible',True) and o['layer'] in visible_layers
             and len(o['vertices'])>=2 and (selected is None or o['id'] in selected)]
    if not objects or selected is not None and {o['id'] for o in objects}!=set(selected):
        raise ValueError('Draw a closed outline or select connected curves to form the profile.')
    design=copy.deepcopy(session.design)
    profile=next(p for p in design['profiles'] if p['id']==session.profile_id)
    frame=session.frame
    project_cad=None
    if session.linked_id:
        if len(objects)!=1 or not objects[0]['closed'] or objects[0].get('faces'):
            raise ValueError('Choose one closed outline to update the linked CAD profile. Join the boundary curves first.')
        obj=objects[0]
        designer.outline({**profile,'geometry':obj,'profile_frame':frame},designer.curve_step(design.get('detail')))
        original=session.seeds[session.linked_id][1]
        if any(obj.get(key)!=original.get(key) for key in cad_api.GEOMETRY_KEYS):
            project=cad_api.current(s)
            target=cad_api.get_object(project,session.linked_id).copy()
            project_cad={**project,'objects':[target if o['id']==session.linked_id else o for o in project['objects']]}
            cad_api.replace_geometry(target,{k:copy.deepcopy(obj[k]) for k in cad_api.GEOMETRY_KEYS if k in obj})
            if designer.available_vertices(project_cad)<0:
                raise ValueError('The linked outline exceeds the project’s editable-vertex capacity. Reduce the curve detail or free space in Model.')
            project_cad.update(revision=uuid.uuid4().hex,last_created=[target['id']])
            profile['profile_frame']=frame
            profile['geometry']={k:target[k] for k in ('vertices','closed','definition','transform') if k in target}
    else:
        existing=design.get('drawing_objects',[])
        old_ids=profile.get('drawing_ids',[]) if profile['kind']=='composite' else []
        shared={ident for p in design['profiles'] if p is not profile and p['kind']=='composite' for ident in p['drawing_ids']}
        parts=[]
        composite=bool(old_ids) or len(objects)>1
        shift=(profile.get('x',0),profile.get('y',0)) if old_ids else (0.,0.)
        for obj in objects:
            old_id,original=session.seeds.get(obj['id'],(None,None))
            same=original and all(obj.get(k)==original.get(k) for k in cad_api.GEOMETRY_KEYS|{'name'})
            if same and not composite and old_id is None:
                parts.append(profile.copy())
                continue
            part=next((p.copy() for p in existing if same and p['id']==old_id),None)
            if part is None:
                part=profile_shape(obj,frame,shift)
                part.update(id=old_id if old_id and old_id not in shared else 'drawing-'+uuid.uuid4().hex,name=obj['name'])
            parts.append(part)
        ident,name=profile['id'],profile['name']
        profile.clear()
        if composite:
            if len(parts)>128:raise ValueError('Use up to 128 curves in one profile.')
            ids=[p['id'] for p in parts]
            remaining=[p for p in existing if p['id'] not in ids and (p['id'] not in old_ids or p['id'] in shared)]
            design['drawing_objects']=remaining+parts
            profile.update(kind='composite',drawing_ids=ids,x=shift[0],y=shift[1])
        elif parts[0]['kind']=='polyline':
            raise ValueError('Close the polyline or join the boundary curves before finishing the profile.')
        else:profile.update(parts[0])
        profile.update(id=ident,name=name)
    shape=designer.with_drawing_parts(profile,design.get('drawing_objects',[]))
    designer.outline(shape,designer.curve_step(design.get('detail')))
    return design,project_cad


def edit(s,body):
    action=body['action']
    if action=='profile_begin':return begin(s,body)
    session=cad_api.workspace(s)
    if session is s or session.id!=body.get('edit_id'):
        raise ValueError('The profile editing workspace changed. Reopen the profile to continue.')
    cad_api.check(session,body)
    if action=='profile_cancel':
        s.PROFILE_CAD_EDIT=None
        return dict(cancelled=True,design=session.previous,model_id=session.model_id,
                    profile_id=session.profile_id,context=cad_api.context(s),revision=cad_api.current(s)['revision'],
                    history=workspace_history.status(s))
    design,data=result(s,session,body)
    before=workspace_history.snapshot(s)
    old={**s.STATE,**before}
    try:
        if data is not None:s.STATE['cad']=data
        s.STATE['tunnel_design']=dict(design=design,model_id=session.model_id)
        s.save()
    except Exception:
        s.STATE.clear();s.STATE.update(old)
        raise
    workspace_history.record(s,before,'Edit tunnel profile')
    s.PROFILE_CAD_EDIT=None
    return dict(design=design,model_id=session.model_id,profile_id=session.profile_id,context=cad_api.context(s),
                revision=cad_api.current(s)['revision'],history=workspace_history.status(s))
