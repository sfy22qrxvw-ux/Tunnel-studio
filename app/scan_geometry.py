# Copyright (c) 2026 Damian Gulczynski.
"""Scan-only cutting planes and reviewable estimates of a circular tunnel centreline."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy, csv, io, uuid, json, hashlib
from collections import OrderedDict
import numpy as np
import pointio, jobdata, scan_edits, scene_objects, registration_pose, analysis_sources, inspection

from section_fitting import fit_circle_section, fit_ellipse_section, convergence_analysis

SECTION_PATHS = OrderedDict()


def section_revision(s,item,selection):
    meta=item['meta']
    axis=s.STATE.get('axis') if s is not None else None
    value=[item['id'],inspection.fingerprint(meta),item.get('limit_box'),item.get('limit_hide_outside',True),selection,axis.a.tolist() if axis is not None else None]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def section_item(s,ident):
    if ident==analysis_sources.ALL_ID or (isinstance(ident,str) and ident.startswith(analysis_sources.BUNDLE_PREFIX)):
        return analysis_sources.resolve(s,ident)
    import job_api
    item=job_api.item(s,ident);analysis_sources.require_current(item);return item


def chunks(meta, selection=None, clip=None):
    scope=None
    if selection or clip:
        limit=jobdata.selection_spec({'mode':'box',**clip}) if clip else None
        scope=dict(mode='selection' if selection else 'limit',selection=selection,
                   limits=[limit]*len(analysis_sources.parts(meta)))
    yield from analysis_sources.blocks(meta,block=pointio.CHUNK,scope=scope)


def sample(meta, selection=None, cap=180000, progress=lambda _: None):
    rng = np.random.default_rng(134); cloud = np.empty((0,3)); keys = np.empty(0); count = 0
    for ids, q in chunks(meta, selection):
        count += len(q)
        if len(q):
            cloud = np.concatenate((cloud,q)); keys = np.r_[keys,rng.random(len(q))]
            if len(keys)>cap:
                keep = np.argpartition(keys,-cap)[-cap:]; cloud = cloud[keep]; keys = keys[keep]
        progress(f'Reading retained scan for geometry: {count:,} points · up to {cap:,} used for the estimate')
    if len(cloud)<50: raise ValueError('Select at least 50 retained points.')
    return cloud,count


def sample_sources(items,selection=None,cap=180000,progress=lambda _:None):
    """One bounded deterministic reservoir across all selected registered sources."""
    rng=np.random.default_rng(134);cloud=np.empty((0,3));keys=np.empty(0);counts={}
    for item in items:
        count=0;clip=item.get('limit_box') if item.get('limit_hide_outside',True) else None
        for ids,q in chunks(item['meta'],selection,clip):
            count+=len(q)
            if len(q):
                cloud=np.concatenate((cloud,q));keys=np.r_[keys,rng.random(len(q))]
                if len(keys)>cap:
                    keep=np.argpartition(keys,-cap)[-cap:];cloud=cloud[keep];keys=keys[keep]
            progress(f"Reading {item['name']} · {count:,} retained points in scope · fitting sample capped at {cap:,}")
        counts[item['id']]=count
    if len(cloud)<50:raise ValueError('Select at least 50 retained points.')
    return cloud,sum(counts.values()),counts


def basis(normal):
    normal=np.asarray(normal,float)
    if normal.shape!=(3,) or not np.isfinite(normal).all() or np.linalg.norm(normal)<1e-8:raise ValueError('Enter a valid cutting-plane normal.')
    normal=normal/np.linalg.norm(normal); ref=np.eye(3)[np.argmin(np.abs(normal))]
    right=np.cross(ref,normal);right/=np.linalg.norm(right);up=np.cross(normal,right)
    if up[2]<0:right=-right;up=-up
    return normal,right,up


def direction(points):
    origin=np.median(points,0); delta=points-origin
    lengths=np.linalg.norm(delta,axis=1);use=lengths<=np.quantile(lengths,.99)
    values,vectors=np.linalg.eigh(np.cov(delta[use].T));normal=vectors[:,-1]
    if normal[np.argmax(np.abs(normal))]<0:normal=-normal
    return origin,normal,values


def circle(q):
    """Algebraic initial fit, then Huber-weighted geometric radial residuals."""
    offset=np.median(q,0);z=q-offset
    solution=np.linalg.lstsq(np.c_[2*z,np.ones(len(z))],np.sum(z*z,1),rcond=None)[0]
    centre=solution[:2];radius=float(np.sqrt(max(1e-12,solution[2]+centre@centre)))
    for _ in range(12):
        d=z-centre;radial=np.maximum(1e-9,np.linalg.norm(d,axis=1));error=radial-radius
        sigma=max(.003,1.4826*float(np.median(np.abs(error-np.median(error)))))
        weights=np.sqrt(np.minimum(1,1.5*sigma/np.maximum(np.abs(error),1e-9)))
        jac=np.c_[-d/radial[:,None],-np.ones(len(d))]
        step=np.linalg.lstsq(jac*weights[:,None],-error*weights,rcond=None)[0]
        centre+=step[:2];radius+=step[2]
        if np.linalg.norm(step)<1e-7:break
    angles=np.sort(np.mod(np.arctan2(z[:,1]-centre[1],z[:,0]-centre[0]),2*np.pi))
    coverage=360-np.max(np.diff(np.r_[angles,angles[0]+2*np.pi]))*180/np.pi
    residual=np.linalg.norm(z-centre,axis=1)-radius
    return centre+offset,float(radius),float(np.sqrt(np.mean(np.minimum(residual**2,np.quantile(residual**2,.9))))),float(coverage)


def estimate(meta, selection, interval=2., smoothing=1, start=0., progress=lambda _:None, prepared=None):
    interval=float(interval);smoothing=int(smoothing);start=float(start)
    if not np.isfinite([interval,start]).all() or not .1<=interval<=50 or not 0<=smoothing<=4:raise ValueError('Use an interval of 0.1–50 m and smoothing of 0–4.')
    points,total=prepared if prepared is not None else sample(meta,selection,progress=progress)
    origin,normal,eigen=direction(points)
    if not np.isfinite(eigen).all() or eigen[-1]<1e-10:raise ValueError('The selected points have no usable spatial extent. Select a larger area.')
    ambiguous=bool(eigen[-1]<2*eigen[-2]);reasons=[]
    if ambiguous:
        # A short tunnel can have more variance across its diameter than along
        # its axis. Compare circular support in all three PCA directions before
        # falling back to the dominant direction. This remains a tentative guess.
        from tunnel_lining import circle_seed
        _,vectors=np.linalg.eigh(np.cov((points-origin).T));rng=np.random.default_rng(137);best=None
        trial=points[::max(1,len(points)//12000)]-origin
        for candidate in vectors.T:
            candidate,r,u=basis(candidate)
            try:seed,score=circle_seed(np.c_[trial@r,trial@u],.15,rng)
            except ValueError:continue
            if best is None or score>best[0]:best=(score,candidate)
        if best is not None:normal=best[1]
        if normal[np.argmax(np.abs(normal))]<0:normal=-normal
        reasons.append('The scan has no clearly dominant direction; the displayed direction is a guess.')
    from tunnel_axis_fit import fit
    rows,quality,fit_warnings=fit(points,origin,normal,interval,smoothing,progress)
    reasons.extend(fit_warnings);tentative=bool(reasons)
    rows[:,0]=start+np.r_[0,np.cumsum(np.linalg.norm(np.diff(rows[:,1:4],axis=0),axis=1))]
    preview=points[::max(1,int(np.ceil(len(points)/6000)))]-origin
    return dict(id=uuid.uuid4().hex,rows=rows.tolist(),quality=quality,tentative=tentative,reviewed=False,preview=dict(origin=origin.tolist(),points=preview.tolist()),source_name=meta['name'],source_count=total,sample_count=len(points),interval=interval,smoothing=smoothing,selection=selection,method='Adaptive lining sections with robust curve regularisation',warnings=reasons+['Scan-derived centreline and radius; this is not the design alignment or a design tolerance check.','Review and drag the nodes in plan and elevation before use. Junctions and missing lining can bias the guess.'])


def section_overview(item,s=None,selection=None):
    """Bounded display sample for positioning a section; never an output cloud."""
    if item['kind']!='scan':raise ValueError('Select a scan first.')
    if selection:selection=jobdata.selection_spec(selection)
    meta=item['meta'];lo,hi=registration_pose.world_bounds(meta);origin=(lo+hi)/2
    # Match section scope: retained points and the optional limit box. Other
    # viewport-only hide/isolate controls do not define a section selection.
    source={k:item[k] for k in ('id','kind','meta','limit_box','limit_hide_outside') if k in item}
    if selection:
        scoped,_,_=sample_sources([item],selection,40000)
        packed=np.c_[scoped-origin,np.tile([.3,.8,.7],(len(scoped),1))]
    elif meta.get('cloud_parts'):
        sources=analysis_sources.parts(meta);cap=max(1,40000//len(sources))
        packed=np.concatenate([jobdata._item_sample(dict(id=x['id'],kind='scan',meta=x['meta']),cap,origin,[.3,.8,.7]) for x in sources])
    else:packed=jobdata._item_sample(source,40000,origin,[.3,.8,.7])
    packed=packed[np.isfinite(packed[:,:3]).all(1)]
    if len(packed)<3:raise ValueError('Too few overview points. Use the position controls or enlarge the limit box.')
    world=np.asarray(packed[:,:3],float)+origin
    centre,normal,_=direction(world)
    out=dict(source_id=item['id'],source_name=item['name'],origin=origin.tolist(),points=packed.tolist(),sample_count=len(packed),section_origin=centre.tolist(),normal=normal.tolist())
    if s is not None:
        import section_path
        token=section_revision(s,item,selection);cached=SECTION_PATHS.get(token)
        if cached is None:
            paths={};error=''
            try:paths['tunnel']=section_path.estimate(world)
            except (ValueError,np.linalg.LinAlgError) as e:error=str(e)
            axis=s.STATE.get('axis')
            if axis is not None:
                try:
                    path=section_path.model(axis.a,False,'Active alignment · '+axis.name)
                    chainages=axis.project(world)[:,0]
                    chainages=chainages[np.isfinite(chainages)]
                    if len(chainages) and (lo_ch:=float(chainages.min()))<(hi_ch:=float(chainages.max())):
                        path['view_range']=[lo_ch,hi_ch]
                    paths['alignment']=path
                except ValueError:pass
            ring_error='Select points around one narrow ring, then enable Use current 3D selection.'
            if selection:
                import section_shape
                try:paths['ring']=section_shape.ring_path(world);ring_error=''
                except (ValueError,np.linalg.LinAlgError) as e:ring_error=str(e)
            cached=dict(paths=paths,path_error=error,ring_error=ring_error,path_token=token)
            SECTION_PATHS[token]=cached
            while len(SECTION_PATHS)>4:SECTION_PATHS.popitem(last=False)
        out.update(copy.deepcopy(cached))
    return out


def section(s,item,body,progress):
    meta=item['meta'];selection=body.get('selection')
    if selection:selection=jobdata.selection_spec(selection)
    original_selection=copy.deepcopy(selection)
    if item.get('limit_box') and item.get('limit_hide_outside',True):
        if selection:selection['clip_box']=item['limit_box']
        else:selection={'mode':'box',**item['limit_box']}
    mode=body.get('plane','tunnel')
    path=None
    if mode in ('tunnel','alignment','ring'):
        import section_path
        token=body.get('path_token')
        if token:
            if token!=section_revision(s,item,original_selection) or token not in SECTION_PATHS:
                raise ValueError('The scan, selection or alignment changed. Reopen Cross-section to refresh its direction.')
            cached=SECTION_PATHS[token]
            if mode not in cached['paths']:raise ValueError(cached.get('ring_error') if mode=='ring' else cached['path_error'] or 'No active alignment is available.')
            path=cached['paths'][mode]
        elif mode=='alignment':
            active=s.STATE.get('axis')
            if active is None:raise ValueError('Load or activate an alignment, or choose the estimated tunnel direction.')
            path=section_path.model(active.a,False,'Active alignment · '+active.name)
        else:
            points,total=sample(meta,selection,cap=18000,progress=progress)
            if mode=='ring':
                if not original_selection:raise ValueError('Select one narrow ring first and enable Use current 3D selection.')
                import section_shape
                path=section_shape.ring_path(points)
            else:path=section_path.estimate(points,progress)
        position=float(body.get('position',0));centre,axis=section_path.at(path,position);origin=centre-position*axis
    elif mode in ('x','y','z','custom') and body.get('origin') is not None:
        # The locator supplies a fixed frame. Reuse it so the line and the
        # computed plane agree, and cut in one full-density pass.
        origin=np.asarray(body['origin'],float);axis=body.get('normal')
    else:
        points,total=sample(meta,selection,cap=60000,progress=progress)
        origin,axis,_=direction(points)
    if mode in ('x','y','z'):axis=np.eye(3)['xyz'.index(mode)]
    elif mode=='custom':axis=body.get('normal')
    elif mode not in ('tunnel','alignment','ring'):raise ValueError('Choose a cutting plane.')
    normal,right,up=basis(axis)
    if path is None and body.get('origin') is not None:
        origin=np.asarray(body['origin'],float)
        if origin.shape!=(3,) or not np.isfinite(origin).all():raise ValueError('Invalid section origin.')
    limits=[float('inf'),float('-inf')];retained=0
    position=float(body.get('position',0));width=float(body.get('width',.2))
    if not np.isfinite([position,width]).all() or not .001<=width<=10:raise ValueError('Use a section thickness between 0.001 and 10 m.')
    centre=origin+position*normal;dest=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.section.csv');preview=dest.with_suffix('.preview64');cloud=[];keys=np.empty(0);rng=np.random.default_rng(134);count=0
    try:
        with dest.open('w',encoding='utf8',newline='') as f:
            writer=csv.writer(f);writer.writerow(['e','n','h','section_u_m','section_v_m','distance_from_plane_m','source_scan_id','source_scan_name','original_scan_point_index_0based'])
            source_ranges=analysis_sources.source_ranges(meta);starts=np.array([r['start'] for r in source_ranges],dtype=np.uint64)
            for ids,q in chunks(meta,selection):
                retained+=len(q)
                if len(q):
                    stations=(q-origin)@normal;limits=[min(limits[0],float(stations.min())),max(limits[1],float(stations.max()))]
                distance=(q-centre)@normal;keep=np.abs(distance)<=width/2;q=q[keep];d=distance[keep]
                if len(q):
                    jobdata.room(s.WORK,len(q)*260);u=(q-centre)@right;v=(q-centre)@up
                    selected_ids=ids[keep];owners=np.searchsorted(starts,selected_ids,side='right')-1
                    writer.writerows([*row,source_ranges[owner].get('id') or item['id'],source_ranges[owner]['name'],int(index)-source_ranges[owner]['start']] for row,owner,index in zip(np.c_[q,u,v,d].tolist(),owners,selected_ids));count+=len(q)
                    values=np.c_[q,u,v];cloud=np.concatenate((cloud,values)) if len(cloud) else values;keys=np.r_[keys,rng.random(len(q))]
                    if len(keys)>60000:
                        pick=np.argpartition(keys,-60000)[-60000:];cloud=cloud[pick];keys=keys[pick]
                progress(f'Cutting section · {count:,} points inside {width:g} m thickness')
        if retained<50:raise ValueError('Select at least 50 retained points.')
        if not count:raise ValueError('No retained points intersect this plane. Move the slice or increase its thickness.')
        np.asarray(cloud,dtype='<f8').tofile(preview)
        uv=np.asarray(cloud)[:,3:5];lo=uv.min(0);hi=uv.max(0);outline=[(centre+x*right+y*up).tolist() for x,y in [(lo[0],lo[1]),(hi[0],lo[1]),(hi[0],hi[1]),(lo[0],hi[1]),(lo[0],lo[1])]]
        if path is not None:limits=[path['stations'][0],path['stations'][-1]]
        return dict(source_revision=section_revision(s,item,original_selection),requested_selection=original_selection,outline=outline,id=uuid.uuid4().hex,source_id=item['id'],source_name=item['name'],count=count,sample_count=len(cloud),origin=origin.tolist(),centre=centre.tolist(),normal=normal.tolist(),right=right.tolist(),up=up.tolist(),position=position,width=width,limits=limits,file=str(dest),preview_file=str(preview),plane=mode,path_token=body.get('path_token'),orientation=path['label'] if path else 'Fixed cutting plane',orientation_warning=path.get('warning','') if path else '')
    except Exception:dest.unlink(missing_ok=True);preview.unlink(missing_ok=True);raise


def csv_alignment(rows):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['chainage','e','n','h','radius']);writer.writerows(rows);return out.getvalue()


def get(s,h,path,args):
    if path=='/api/scan-section-fits':
        import section_epochs
        h.send(section_epochs.available(s));return True
    if path=='/api/scan-section-preview':
        if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
        import job_api
        job_api.sync_active(s)
        selection=json.loads(args['selection'][0]) if args.get('selection') else None
        if selection:selection=jobdata.selection_spec(selection)
        h.send(section_overview(section_item(s,args.get('id',[''])[0]),s,selection));return True
    if path=='/api/scan-geometry':
        result=copy.deepcopy(s.STATE.get('free_section'))
        if result:
            values=np.fromfile(result.pop('preview_file'),'<f8').reshape(-1,5);result.pop('file',None);result['points']=values.tolist()
        alignment=copy.deepcopy(s.STATE.get('estimated_alignment'))
        if alignment:alignment['source_changed']=scene_objects.alignment_outdated(s,alignment)
        h.send(dict(estimate=alignment,section=result));return True
    if path=='/api/scan-estimate-csv':
        result=s.STATE.get('estimated_alignment')
        if not result:raise ValueError('Estimate an alignment first.')
        h.send(csv_alignment(result['rows']),'text/csv',filename='scan_estimated_alignment.csv');return True
    if path=='/api/free-section-csv':
        result=s.STATE.get('free_section')
        if not result:raise ValueError('Create a scan cross-section first.')
        h.send_file(Path(result['file']),'text/csv; charset=utf-8','scan_cross_section.csv');return True
    return False


def post(s,h,path,body):
    if path!='/api/scan-geometry':return False
    if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
    import job_api
    job_api.sync_active(s);job_api.CANCEL.clear()
    def progress(message):job_api.progress(s,message)
    def perform():
        action=body.get('action');old=copy.deepcopy(s.STATE);created=[]
        try:
            if action in ('estimate','section'):
                item=section_item(s,body.get('id')) if action=='section' else job_api.item(s,body.get('id'))
                if item['kind']!='scan':raise ValueError('Select a scan first.')
                if action=='section':
                    result=section(s,item,body,progress);created=[Path(result[k]) for k in ('file','preview_file')];scene_objects.remember(s,'section',result);s.STATE['free_section']=result
                else:
                    ids=body.get('ids')
                    if ids is None:ids=[item['id']]
                    if not isinstance(ids,list) or not ids or any(not isinstance(ident,str) or not ident for ident in ids) or len(set(ids))!=len(ids):raise ValueError('Select one or more distinct linked scans.')
                    items=[job_api.item(s,ident) for ident in ids]
                    if any(x['kind']!='scan' for x in items):raise ValueError('Choose scans for alignment estimation.')
                    if len(items)>1:
                        import registration_workspace as rw
                        if not set(ids)<=rw.component(s,ids[0]):raise ValueError('The selected scans must be connected by registration links before estimating one alignment.')
                    revisions=body.get('source_revisions') or {item['id']:body.get('source_revision')}
                    for source in items:
                        analysis_sources.require_current(source)
                        if revisions.get(source['id']) and revisions[source['id']]!=scene_objects.source_revision(source):
                            raise ValueError('A source scan changed after the alignment window opened. Close it, select the required branch and reopen Scan-derived alignment.')
                    spec=jobdata.selection_spec(body['selection']) if body.get('selection') else None
                    progress(f'Estimating from {len(items)} scan(s) · deleted points excluded')
                    points,total,counts=sample_sources(items,spec,progress=progress)
                    source_name=items[0]['name'] if len(items)==1 else str(len(items))+' linked scans · '+', '.join(x['name'] for x in items)
                    result=estimate({'name':source_name},spec,body.get('interval',2),body.get('smoothing',1),body.get('start',0),progress,prepared=(points,total))
                    result.update(source_id=items[0]['id'],source_revision=scene_objects.source_revision(items[0]),source_ids=ids,source_revisions={x['id']:scene_objects.source_revision(x) for x in items},source_counts=counts,requested_selection=copy.deepcopy(body.get('selection')),source_scope='selected area' if body.get('selection') else 'limit box' if any(x.get('limit_box') and x.get('limit_hide_outside',True) for x in items) else 'all retained points')
                    name=str(body.get('name') or '').strip()[:150]
                    if name:result['name']=name
                    scene_objects.remember(s,'alignment',result);s.STATE['estimated_alignment']=result
            elif action=='section_fits':
                import section_epochs
                result=section_epochs.run(s,body,progress)
                progress('Saving section dimensions and comparison…')
                s.STATE['scan_section_fits']=result
            elif action=='section_shape':
                import section_shape
                result=s.STATE.get('free_section')
                if not result or result['id']!=body.get('revision'):raise ValueError('The section changed. Create it again before fitting.')
                item=section_item(s,result['source_id'])
                if result.get('source_revision')!=section_revision(s,item,result.get('requested_selection')):raise ValueError('The scan or alignment changed. Create a new section before fitting.')
                result=section_shape.analyse(result,body.get('threshold_mm',20),progress);scene_objects.remember(s,'section',result);s.STATE['free_section']=result
            elif action in ('save_estimate','use_estimate'):
                result=s.STATE.get('estimated_alignment')
                if not result or result['id']!=body.get('revision'):raise ValueError('The alignment estimate changed. Reopen its preview.')
                if action=='use_estimate' and scene_objects.alignment_outdated(s,result):
                    raise ValueError('The source points changed after this alignment was calculated. Create a fresh alignment from the retained branch before using it for chainage. The stored line remains available for editing.')
                rows=np.asarray(body.get('rows'),float)
                if rows.ndim!=2 or rows.shape[1]!=5 or len(rows)<2 or not np.isfinite(rows).all() or (np.diff(rows[:,0])<=0).any() or (rows[:,4]<=0).any():raise ValueError('Enter at least two finite nodes, increasing chainage and positive radii.')
                result['rows']=rows.tolist();result['reviewed']=True
                name=str(body.get('name') or '').strip()[:150]
                if name:result['name']=name
                scene_objects.remember(s,'alignment',result)
                if action=='use_estimate':
                    from core import Alignment
                    axis=Alignment([dict(zip(['chainage','e','n','h','radius'],r)) for r in rows.tolist()], 'ESTIMATED from scan · '+result['source_name'])
                    s.STATE.update(axis=axis,axis_provenance=dict(kind='scan_estimate',estimate_id=result['id'],tentative=bool(result.get('tentative'))),cloud=None,derived=None,valid_points=0,active_job_scan=None,rings=[],structures=[],demo=False,joints=None,edits=[],redo=[],audit=[],report_ready=False,clean_ready=False,export_ready=None)
            else:raise ValueError('Unknown scan geometry action.')
            s.save()
        except Exception:
            s.STATE.clear();s.STATE.update(old)
            for path in created:path.unlink(missing_ok=True)
            raise
    s.start_job(perform,'job',native_progress=True);h.send({'started':True});return True
