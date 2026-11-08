# Copyright (c) 2026 Damian Gulczynski.
"""Persistent CAD objects, transactions, precise snapping and interchange."""
from project_data_layout import folder as asset_folder
import copy, io, json, re, time, uuid
from pathlib import Path
import numpy as np
import cad_geometry as g

GEOMETRY_KEYS={'vertices','faces','closed','solid','minimum','maximum','area_m2','volume_m3','shape','definition','transform','centre','radius','design_summary'}
MAX_PROJECT_VERTICES=300000

def empty():
    return dict(revision='empty',objects=[],frames=[],active_frame=g.frame({'name':'World'}),layers=[dict(name='Model',visible=True)],last_created=[])

def current(s):return s.STATE.get('cad') or empty()

def context(s):
    key=(id(s.STATE.get('cad')),s.STATE.get('project_id'))
    if getattr(s,'_cad_context_key',None)!=key:
        s._cad_context_key=key;s._cad_context_token=uuid.uuid4().hex
    return s._cad_context_token

def check(s,body):
    if body.get('context')!=context(s) or body.get('revision')!=current(s)['revision']:
        raise ValueError('The modelling workspace changed. Refresh the command and try again.')

def get_object(data,ident):
    found=next((o for o in data['objects'] if o['id']==ident),None)
    if found is None:raise ValueError('The selected model no longer exists.')
    return found

def public_objects(s):
    data=current(s);layers={x['name']:x.get('visible',True) for x in data['layers']};out=[]
    for o in data['objects']:
        count=len(o['vertices']);faces=len(o.get('faces',[]));visible=o.get('visible',True) and layers.get(o['layer'],True)
        corners=[[o['maximum' if k&(1<<i) else 'minimum'][i] for i in range(3)] for k in range(8)]
        out.append(dict(id=o['id'],kind='cad',shape=o['shape'],name=o['name'],bundle_id=o.get('bundle_id'),layer=o['layer'],visible=visible,colour=o['colour'],points=corners,minimum=o['minimum'],maximum=o['maximum'],editable=True,removable=True,summary=f"{o['shape'].title()} · {'solid' if o.get('solid') else 'surface' if faces else 'profile' if o.get('closed') else 'geometry'} · {faces if faces else count:,} {'triangles' if faces else 'vertices'}",area_m2=o['area_m2'],volume_m3=o['volume_m3']))
    return out

def state(s):
    data=copy.deepcopy(current(s));data['context']=context(s);data['project_id']=s.STATE.get('project_id')
    axis=s.STATE.get('axis');data['alignment']={'name':axis.name,'start':float(axis.a[0,0]),'end':float(axis.a[-1,0])} if axis is not None else None
    try:g.engines();data['engine_ready']=True
    except ValueError:data['engine_ready']=False
    return data

def object_record(geometry,body,index=0):
    name=str(body.get('name') or geometry.get('shape','Model').replace('_',' ').title()).strip()[:120]
    colour=str(body.get('colour','#62bdcf'))
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',colour):raise ValueError('Choose a valid model colour.')
    return dict(**geometry,id='cad:'+uuid.uuid4().hex,name=name+(f' {index+1}' if index else ''),visible=True,
                layer=str(body.get('layer','Model')).strip()[:80] or 'Model',colour=colour,bundle_id=str(body.get('bundle_id') or '')[:100])

def replace_geometry(obj,value):
    for key in GEOMETRY_KEYS:obj.pop(key,None)
    obj.update(value)

def execute(data,body,progress=lambda message:None):
    """Compute in an isolated CAD snapshot. Caller commits only after success."""
    action=body.get('action');ids=list(dict.fromkeys(body.get('ids',[])))
    selected=[get_object(data,i) for i in ids];created=[];progress('Modelling · validating input…')
    def add(value,default_name=None):
        value.setdefault('shape',action);meta={**body}
        if default_name and not meta.get('name'):meta['name']=default_name
        record=object_record(value,meta,len(created));data['objects'].append(record);created.append(record['id']);return record
    if action=='create':add(g.build(body.get('spec',{}),lambda ident:get_object(data,ident)))
    elif action in ('move','copy','rotate','scale','mirror','align'):
        if not selected:raise ValueError('Select model geometry first.')
        for o in selected:
            progress('Modelling · '+action+' '+o['name']);value=g.transformed(o,action,body)
            if action=='copy':add(value,o['name']+' copy')
            else:replace_geometry(o,value);created.append(o['id'])
    elif action=='parameters':
        if len(selected)!=1:raise ValueError('Select one model to edit its construction parameters.')
        replace_geometry(selected[0],g.update_parameters(selected[0],body.get('values',{})));created=ids
    elif action=='vertex':
        if len(selected)!=1 or selected[0].get('faces'):raise ValueError('Select one drawn curve or point to edit its vertices.')
        o=selected[0];p=copy.deepcopy(o['vertices']);index=int(body.get('index',-1))
        if not 0<=index<len(p):raise ValueError('Choose a valid vertex.')
        p[index]=g.vector(body.get('point')).tolist();kind='point' if len(p)==1 else 'polyline'
        replace_geometry(o,g.build(dict(kind=kind,points=p,closed=o['closed'])));created=ids
    elif action in ('union','subtract','intersect'):
        add(g.boolean(selected,action));
        for o in selected:o['visible']=False
    elif action in ('cut','break','offset','trim','extend'):
        if not selected:raise ValueError('Select model geometry first.')
        o=selected[0]
        if action=='cut':values=g.cut(o,body.get('point'),body.get('normal'),body.get('both',True))
        elif action=='break':values=g.break_curve(o,body.get('point'))
        elif action=='offset':values=g.offset(o,body.get('distance'),body.get('frame'))
        else:
            if len(selected)!=2:raise ValueError('Select the target first, then Ctrl-select the straight boundary line.')
            values=[g.trim_extend(o,selected[1],body.get('frame'),action=='extend',body.get('end','end'),body.get('keep'))]
        for value in values:add(value,o['name']+' '+action)
        if action!='offset':o['visible']=False
    elif action=='join':
        add(g.join(selected))
        for o in selected:o['visible']=False
    elif action in ('remove','visibility','properties'):
        if not selected:raise ValueError('Select model geometry first.')
        if action=='remove':data['objects']=[o for o in data['objects'] if o['id'] not in ids]
        for o in selected:
            if action=='visibility':o['visible']=bool(body.get('visible',not o['visible']))
            if action=='properties':
                for key in ('name','layer','bundle_id'):
                    if key in body:
                        value=str(body[key]).strip()[:120]
                        if not value:raise ValueError(key.title()+' cannot be empty.')
                        o[key]=value
                if 'colour' in body:
                    if not re.fullmatch(r'#[0-9a-fA-F]{6}',str(body['colour'])):raise ValueError('Invalid colour.')
                    o['colour']=body['colour']
        if action!='remove':created=ids
    elif action=='frame':
        f=g.frame(body.get('frame'));data['active_frame']=f
        if body.get('save_named'):
            data['frames']=[x for x in data['frames'] if x['name']!=f['name']]+[f]
    elif action=='layer':
        name=str(body.get('name','')).strip()[:80]
        if not name:raise ValueError('Enter a layer name.')
        row=next((x for x in data['layers'] if x['name']==name),None)
        if row is None:data['layers'].append(dict(name=name,visible=bool(body.get('visible',True))))
        else:row['visible']=bool(body.get('visible',True))
    else:raise ValueError('Unknown modelling action.')
    if sum(len(o['vertices']) for o in data['objects'])>MAX_PROJECT_VERTICES:
        raise ValueError('This model exceeds the 300,000 editable-vertex budget. Reduce curve detail or split the model into projects.')
    for o in data['objects']:
        if not any(x['name']==o['layer'] for x in data['layers']):data['layers'].append(dict(name=o['layer'],visible=True))
    data['last_created']=created;data['revision']=uuid.uuid4().hex
    return data

def commit(s,data):
    old=s.STATE.get('cad');s.STATE['cad']=data
    try:s.save()
    except BaseException:
        if old is None:s.STATE.pop('cad',None)
        else:s.STATE['cad']=old
        raise

def snap_cloud(s,body,progress):
    import preview_index
    import jobdata
    import registration_pose
    import registered_cloud
    from contextlib import ExitStack
    item=next((x for x in s.STATE.get('job_items',[]) if x['id']==body.get('source_id') and x['kind']=='scan' and x.get('visible',True)),None)
    if item is None:raise ValueError('Choose a retained point in a visible scan.')
    registered_cloud.require_current(item)
    meta=item['meta'];revision=registration_pose.revision(meta)
    if body.get('source_revision')!=revision:raise ValueError('This scan changed. Refresh its preview and pick again.')
    seed=g.vector(body.get('point'));lo,hi=registration_pose.world_bounds(meta)
    tolerance=max(.0001,float(np.max(hi-lo))*np.finfo(np.float32).eps*3)
    # Bounds are stored in the original source frame. Rigid inverse-transform
    # the picked world point for pruning, then test retained points in world
    # coordinates so limit boxes and hidden areas match the displayed scene.
    local_seed=registration_pose.inverse_point(meta,seed)
    bounds=preview_index.bounds(meta,progress)
    delta=np.maximum(np.maximum(bounds[:,0]-local_seed,local_seed-bounds[:,1]),0)
    chosen=np.flatnonzero(np.linalg.norm(delta,axis=1)<=tolerance)
    n=int(meta['count']);best=None
    if Path(meta['file']).stat().st_size!=n*24:raise ValueError('The source point cache is incomplete. Reimport the scan.')
    if meta.get('mask_file') and Path(meta['mask_file']).stat().st_size!=n:raise ValueError('The source exclusion mask is incomplete.')
    with ExitStack() as files:
        points=files.enter_context(open(meta['file'],'rb'))
        mask=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        for block in chosen:
            start=int(block)*preview_index.BLOCK;count=min(preview_index.BLOCK,n-start)
            points.seek(start*24);q=np.fromfile(points,'<f8',count=count*3)
            if q.size!=count*3:raise ValueError('The source point cache changed while picking.')
            q=registration_pose.points(meta,q.reshape(-1,3));valid=np.isfinite(q).all(1)
            if mask is not None:
                mask.seek(start);excluded=np.fromfile(mask,'u1',count=count)
                if len(excluded)!=count:raise ValueError('The source exclusion mask changed while picking.')
                valid &= excluded==0
            if item.get('limit_box') and item.get('limit_hide_outside',True):valid &= jobdata.selection_mask(q,{'mode':'box',**item['limit_box']})
            if item.get('isolate_box'):valid &= jobdata.selection_mask(q,item['isolate_box'])
            for hidden in item.get('hidden_boxes',[]):valid &= ~jobdata.selection_mask(q,hidden)
            dist=np.linalg.norm(q-seed,axis=1);ids=np.flatnonzero(valid&(dist<=tolerance))
            if len(ids):
                i=ids[np.argmin(dist[ids])];rank=(float(dist[i]),start+int(i))
                if best is None or rank<best[0]:best=(rank,q[i].tolist())
            progress('Snapping · recovering original measured XYZ in the registered frame…')
    if best is None:raise ValueError('The displayed sample could not be matched to a retained measured point. Refresh and pick again.')
    if registration_pose.revision(item['meta'])!=revision:raise ValueError('This scan changed while picking. Refresh its preview and pick again.')
    return dict(point=best[1],source_id=item['id'],index=best[0][1],source_revision=revision)

def export_bytes(objects,fmt):
    if fmt=='json':return json.dumps(dict(format='Tunnel Studio model geometry',version=1,units='metres',coordinate_frame='project/world',objects=objects),indent=2).encode('utf8')
    if fmt=='obj':
        out=['# Tunnel Studio model geometry; project/world coordinates; metres'];offset=1
        for o in objects:
            out.append('o '+re.sub(r'[^\w.-]+','_',o['name']))
            out.extend('v '+' '.join(format(float(v),'.17g') for v in p) for p in o['vertices'])
            if o['faces']:out.extend('f '+' '.join(str(i+offset) for i in f) for f in o['faces'])
            elif len(o['vertices'])==1:out.append('p '+str(offset))
            else:out.append('l '+' '.join(str(i+offset) for i in range(len(o['vertices'])))+(' '+str(offset) if o['closed'] else ''))
            offset+=len(o['vertices'])
        return ('\n'.join(out)+'\n').encode('utf8')
    if fmt=='dxf':
        import ezdxf
        from ezdxf.math import OCS
        doc=ezdxf.new('R2013');doc.units=6;msp=doc.modelspace()
        for o in objects:
            layer=re.sub(r'[<>/\\":;?*|=]','_',o.get('layer','Model')) or 'Model'
            if layer not in doc.layers:doc.layers.new(layer)
            attrs={'layer':layer,'true_color':int(o.get('colour','#62bdcf')[1:],16)};p=np.asarray(o['vertices'])
            if o['faces']:
                for face in o['faces']:msp.add_3dface(p[face].tolist(),dxfattribs=attrs)
            elif len(p)==1:msp.add_point(p[0],dxfattribs=attrs)
            elif o.get('centre') is not None and o.get('radius') and o['shape'] in ('circle','arc'):
                normal=g.plane(p)[1][:,2];ocs=OCS(normal);centre=ocs.from_wcs(o['centre']);da={**attrs,'extrusion':normal.tolist()}
                if o['shape']=='circle':msp.add_circle(centre,o['radius'],dxfattribs=da)
                else:
                    a=np.asarray(ocs.from_wcs(p[0]))-centre;b=np.asarray(ocs.from_wcs(p[-1]))-centre
                    msp.add_arc(centre,o['radius'],math_degrees(a),math_degrees(b),dxfattribs=da)
            elif len(p)==2:msp.add_line(p[0],p[1],dxfattribs=attrs)
            else:msp.add_polyline3d(p.tolist(),close=bool(o['closed']),dxfattribs=attrs)
        stream=io.StringIO();doc.write(stream);return stream.getvalue().encode('utf8')
    raise ValueError('Choose DXF, OBJ or model JSON.')

def math_degrees(v):return float(np.degrees(np.arctan2(v[1],v[0]))%360)

def import_model_json(path,progress=lambda message:None):
    """Read our portable editable geometry, validating before any project change."""
    def invalid_constant(value):raise ValueError('Model JSON contains a non-finite number.')
    with Path(path).open(encoding='utf-8-sig') as source:
        document=json.load(source,parse_constant=invalid_constant)
    if not isinstance(document,dict) or document.get('format')!='Tunnel Studio model geometry' or document.get('version')!=1:
        raise ValueError('Choose a Tunnel Studio Model JSON export, not a metadata-only JSON file.')
    if document.get('units')!='metres' or document.get('coordinate_frame')!='project/world':
        raise ValueError('Model JSON must use project/world coordinates in metres.')
    rows=document.get('objects')
    if not isinstance(rows,list) or not rows or len(rows)>MAX_PROJECT_VERTICES:
        raise ValueError('Model JSON must contain editable model objects within the project budget.')
    out=[];count=0
    for index,raw in enumerate(rows):
        progress(f'Importing editable models · {index+1:,} / {len(rows):,}')
        if not isinstance(raw,dict):raise ValueError('Invalid model object.')
        vertices=g.points(raw.get('vertices'),1,g.MAX_VERTICES);count+=len(vertices)
        if count>MAX_PROJECT_VERTICES:raise ValueError('Model exceeds the editable vertex budget.')
        faces=raw.get('faces',[])
        if not isinstance(faces,list) or len(faces)>g.MAX_FACES or any(not isinstance(f,list) or len(f)!=3 or any(type(i) is not int for i in f) for f in faces):
            raise ValueError('Model faces must contain three integer vertex indices.')
        for key in ('closed','solid','visible'):
            if key in raw and type(raw[key]) is not bool:raise ValueError('Invalid model '+key+' flag.')
        value=g.geometry(vertices,faces,closed=raw.get('closed',False),solid=raw.get('solid',False),shape=str(raw.get('shape','geometry'))[:40])
        if raw.get('transform') is not None:
            transform=np.asarray(raw['transform'],dtype=float)
            if transform.shape!=(4,4) or not np.isfinite(transform).all() or not np.allclose(transform[3],[0,0,0,1],rtol=0,atol=1e-12) or abs(np.linalg.det(transform[:3,:3]))<1e-18:
                raise ValueError('Invalid model construction transform.')
            value['transform']=transform.tolist()
        if raw.get('centre') is not None:value['centre']=g.vector(raw['centre']).tolist()
        if raw.get('radius') is not None:value['radius']=g.number(raw['radius'],'Radius',True)
        if raw.get('definition') is not None:
            if not isinstance(raw['definition'],dict):raise ValueError('Invalid construction parameters.')
            value['definition']=copy.deepcopy(raw['definition'])
            rebuilt=g.update_parameters(value,{})
            if np.shape(rebuilt['vertices'])!=vertices.shape or not np.allclose(rebuilt['vertices'],vertices,rtol=0,atol=1e-7):
                raise ValueError('Construction parameters do not match the stored model geometry.')
        # New identities prevent collisions when the same file is imported twice.
        out.append(object_record(value,raw))
    return out,{},'metres'

def import_dxf(path,scale=1.,progress=lambda message:None):
    import ezdxf
    doc=ezdxf.readfile(path);out=[];skipped={};scale=g.number(scale,'Import scale',True)
    for index,e in enumerate(doc.modelspace()):
        if index%100==0:
            progress(f'Importing DXF · {index:,} entities checked…')
            if sum(len(o['vertices']) for o in out)>MAX_PROJECT_VERTICES:raise ValueError('DXF exceeds the editable model vertex budget.')
        kind=e.dxftype();spec=None
        if kind=='POINT':spec=dict(kind='point',points=[np.asarray(e.dxf.location)*scale])
        elif kind=='LINE':spec=dict(kind='line',points=np.asarray([e.dxf.start,e.dxf.end])*scale)
        elif kind in ('CIRCLE','ARC'):
            ocs=e.ocs();f=g.frame(dict(x=list(ocs.ux),y=list(ocs.uy)));spec=dict(kind=kind.lower(),centre=(np.asarray(ocs.to_wcs(e.dxf.center))*scale).tolist(),radius=e.dxf.radius*scale,frame=f)
            if kind=='ARC':spec.update(start_angle=e.dxf.start_angle,end_angle=e.dxf.start_angle+(e.dxf.end_angle-e.dxf.start_angle)%360)
        elif kind in ('LWPOLYLINE','POLYLINE'):
            if kind=='POLYLINE' and not (e.is_2d_polyline or e.is_3d_polyline):skipped[kind]=skipped.get(kind,0)+1;continue
            if kind=='LWPOLYLINE' and e.has_arc or kind=='POLYLINE' and e.has_arc:
                from ezdxf.path import make_path
                p=list(make_path(e).flattening(.001/scale,segments=8))
            else:p=list(e.vertices_in_wcs()) if kind=='LWPOLYLINE' else list(e.points_in_wcs())
            spec=dict(kind='polyline',points=np.asarray(p)*scale,closed=bool(e.closed if kind=='LWPOLYLINE' else e.is_closed))
        elif kind=='3DFACE':
            p=np.asarray(list(e.wcs_vertices()))*scale
            if len(p)==4 and np.linalg.norm(p[2]-p[3])<g.EPS:p=p[:3]
            value=g.geometry(p,[[0,1,2]]+([[0,2,3]] if len(p)==4 else []),shape='surface')
            out.append(object_record(value,{'name':'DXF face','layer':e.dxf.layer}));continue
        else:skipped[kind]=skipped.get(kind,0)+1;continue
        if spec is not None:
            # Convert NumPy construction arrays before preserving the definition.
            spec=json.loads(json.dumps(spec,default=lambda a:a.tolist()))
            out.append(object_record(g.build(spec),{'name':kind.title(),'layer':e.dxf.layer}))
    if not out:raise ValueError('No supported POINT, LINE, ARC, CIRCLE, POLYLINE or 3DFACE entities were found.')
    return out,skipped,doc.units

def get(s,h,path,args):
    if path=='/api/cad':h.send(state(s));return True
    if path=='/api/cad-pick':h.send(getattr(s,'_cad_pick',{}));return True
    if path=='/api/cad-download':
        value=getattr(s,'_cad_export',None)
        if not value or args.get('token',[''])[0]!=value['token']:raise ValueError('Prepare this model export again.')
        if value['revision']!=current(s)['revision'] or value['context']!=context(s):raise ValueError('Model changed after export preparation. Prepare it again.')
        h.send(Path(value['file']).read_bytes(),'application/octet-stream');return True
    return False

def post(s,h,path,body):
    if path not in ('/api/cad-action','/api/cad-preview','/api/cad-export','/api/cad-import','/api/cad-pick'):return False
    check(s,body)
    if body.get('spec',{}).get('kind') in ('alignment_profile','alignment_loft'):
        axis=s.STATE.get('axis')
        if axis is None:raise ValueError('Import an alignment for Along alignment. Free draw, Extrude, Sweep and Loft work without one.')
        body=copy.deepcopy(body);body['spec']['alignment']=axis.a.tolist()
    if s.JOB['busy']:raise ValueError('Wait for the current operation, or cancel it first.')
    if path=='/api/cad-preview':
        started=time.perf_counter();data=current(s);value=g.build(body.get('spec',{}),lambda ident:get_object(data,ident))
        h.send(dict(geometry=value,milliseconds=(time.perf_counter()-started)*1000,revision=data['revision']));return True
    if path=='/api/cad-export':
        data=current(s);ids=list(dict.fromkeys(body.get('ids',[])))
        if not ids:raise ValueError('Select the model objects to export.')
        fmt=body.get('format');destination=body.get('path');dest=None
        if fmt not in ('json','obj','dxf'):raise ValueError('Choose DXF, OBJ or model JSON.')
        if destination:
            import scan_export
            dest=scan_export.destination(s,destination,fmt)
            if dest.is_relative_to(Path(__file__).resolve().parents[1]):raise ValueError('Save outside the Tunnel Studio program folder.')
        objects=copy.deepcopy([get_object(data,i) for i in ids]);value=export_bytes(objects,fmt)
        check(s,body)
        token=uuid.uuid4().hex;path=asset_folder(s.WORK,'cad')/('model_export_'+token+'.'+fmt);path.write_bytes(value)
        old=getattr(s,'_cad_export',None)
        s._cad_export=dict(token=token,file=str(path),revision=data['revision'],context=context(s))
        if old:Path(old['file']).unlink(missing_ok=True)
        if dest:
            # The desktop Save As dialogue supplies the destination. Atomic file replacement.
            temp=dest.with_name(dest.name+'.'+token+'.tmp')
            try:temp.write_bytes(value);temp.replace(dest)
            finally:temp.unlink(missing_ok=True)
        h.send(dict(token=token,filename='Tunnel_Model.'+fmt,bytes=len(value),saved=bool(destination),objects=len(objects)));return True
    captured=current(s)['revision'];captured_context=context(s)
    def run():
        progress=lambda msg:s.progress(msg)
        if path=='/api/cad-pick':
            value=snap_cloud(s,body,progress)
            if context(s)!=captured_context:raise ValueError('Workspace changed while snapping.')
            s._cad_pick=value;return
        data=copy.deepcopy(current(s))
        if path=='/api/cad-import':
            file=Path(str(body.get('path','')).strip().strip('"')).expanduser();suffix=file.suffix.lower()
            if not file.is_file() or suffix not in ('.dxf','.json'):raise ValueError('Choose an existing DXF or Tunnel Studio model JSON file.')
            if file.stat().st_size>100*1024*1024:raise ValueError('Split this model into files smaller than 100 MB before importing.')
            from workspace_tools import destination_bundle
            destination=destination_bundle(s,body.get('bundle_id'))
            values,skipped,units=import_model_json(file,progress) if suffix=='.json' else import_dxf(file,body.get('scale',1),progress)
            for o in values:o['bundle_id']=destination
            data['objects'].extend(values);data['last_created']=[o['id'] for o in values];data['revision']=uuid.uuid4().hex
            if sum(len(o['vertices']) for o in data['objects'])>MAX_PROJECT_VERTICES:raise ValueError('Import exceeds the editable model vertex budget.')
            data['last_import']=dict(name=file.name,skipped=skipped,source_units=units,scale=1 if suffix=='.json' else body.get('scale',1),objects=len(values),bundle_id=destination)
            for o in values:
                if not any(x['name']==o['layer'] for x in data['layers']):data['layers'].append(dict(name=o['layer'],visible=True))
        else:data=execute(data,body,progress)
        progress('Modelling · saving geometry…')
        if current(s)['revision']!=captured or context(s)!=captured_context:raise ValueError('The workspace changed during modelling. Result was not applied.')
        commit(s,data)
    if path=='/api/cad-pick':s._cad_pick={}
    s.start_job(run,kind='modelling');h.send(dict(started=True));return True
