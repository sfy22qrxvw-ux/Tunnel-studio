# Copyright (c) 2026 Damian Gulczynski.
"""Local, exact-ID feature selections with explicit review and reversible edits.

The spatial index only rejects blocks; every surviving original point is read
and tested. A hard local workload limit fails before any result is replaced.
Display samples never become deletion selections. Bolt heads are geometric
candidates measured relative to an explicitly supplied interior/view direction.
An explicit shape choice distinguishes raised fittings and recessed pockets. No missing lining is reconstructed.
"""
from project_data_layout import folder as asset_folder
from contextlib import ExitStack
from pathlib import Path
import copy, hashlib, json, math, time, uuid
import numpy as np
from scipy.spatial import cKDTree, ConvexHull, QhullError
import jobdata, preview_index, scan_edits, scan_export, registration_pose, registered_cloud

MAX_LOCAL_POINTS = 1_000_000
MAX_GRAPH_VISITS = 30_000_000
DISPLAY_POINTS = 60_000
DEFAULTS = dict(radius_m=.6, connection_mm=18., normal_angle_deg=25.,
                surface_distance_mm=12., normal_neighbours=16,
                min_height_mm=8., max_height_mm=150., max_diameter_mm=250.,
                min_points=8, max_points=500000, flip_side=False, feature_kind='raised')


def settings(raw=None):
    raw=raw or {}
    if not isinstance(raw,dict) or set(raw)-set(DEFAULTS):raise ValueError('Unknown local feature setting.')
    out={**DEFAULTS,**raw}
    limits={'radius_m':(.03,20),'connection_mm':(.1,500),
            'normal_angle_deg':(1,85),'surface_distance_mm':(.1,500),
            'normal_neighbours':(6,40),'min_height_mm':(.5,300),
            'max_height_mm':(1,1000),'max_diameter_mm':(5,2000),
            'min_points':(3,100000),'max_points':(1000,MAX_LOCAL_POINTS)}
    for key,(lo,hi) in limits.items():
        try:value=float(out[key])
        except (ValueError,TypeError):raise ValueError('Enter a number for '+key.replace('_',' ')+'.')
        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError(f'{key.replace("_"," ")} must be between {lo:g} and {hi:g}.')
        if key in ('normal_neighbours','min_points','max_points'):
            if value!=int(value):raise ValueError(key.replace('_',' ')+' must be a whole number.')
            value=int(value)
        out[key]=value
    if out['feature_kind'] not in ('raised','recess','departures'):raise ValueError('Choose a recessed bolt pocket or a raised fitting.')
    if not isinstance(out['flip_side'],bool):raise ValueError('Flip side must be true or false.')
    if out['feature_kind']!='departures' and out['max_height_mm']<=out['min_height_mm']:raise ValueError('Maximum feature depth/height must exceed the minimum.')
    return out


def vector(raw,label):
    try:q=np.asarray(raw,dtype=np.float64)
    except (TypeError,ValueError):raise ValueError(label+' needs three finite coordinates.')
    if q.shape!=(3,) or not np.isfinite(q).all():raise ValueError(label+' needs three finite coordinates.')
    return q


def source(s,ident):
    item=next((x for x in s.STATE.get('job_items',[]) if x.get('id')==ident and x.get('kind')=='scan'),None)
    if item is None:raise ValueError('Choose a scan and pick a retained point on it.')
    registered_cloud.require_current(item)
    if item.get('merge_archived'):raise ValueError('This merged cloud is archived. Choose the current registered scan before selecting features.')
    if item.get('visible') is False:raise ValueError('Show the source scan before selecting features.')
    return item


def fingerprint(item):
    value=[scan_export.signature(item),item.get('hidden_boxes'),item.get('isolate_box'),
           item.get('limit_box'),item.get('limit_hide_outside'),item.get('visible',True)]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def local_points(item,seed,cfg,progress,selection=None):
    """Bounded owned reads; never maps the complete source scan into memory."""
    meta=item['meta'];n=int(meta['count']);radius=cfg['radius_m'];cap=cfg['max_points']
    if n<1 or Path(meta['file']).stat().st_size!=n*24:raise ValueError('The point cache is incomplete.')
    if meta.get('mask_file') and Path(meta['mask_file']).stat().st_size!=n:raise ValueError('The exclusion mask is incomplete.')
    spec=jobdata.selection_spec(selection) if selection else None
    area={'mode':'box','minimum':(seed-radius).tolist(),'maximum':(seed+radius).tolist()}
    points=[];ids=[];total=checked=0
    with ExitStack() as files:
        xyz=files.enter_context(open(meta['file'],'rb'))
        mask=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        boxes=preview_index.world_boxes(meta,preview_index.bounds(meta,progress))
        for start,stop in preview_index.selection_blocks(meta,area,boxes=boxes):
            xyz.seek(start*24);q=np.fromfile(xyz,'<f8',count=(stop-start)*3)
            if q.size!=(stop-start)*3:raise ValueError('The point cache changed during the local read.')
            q=registration_pose.points(meta,q.reshape(-1,3));use=np.isfinite(q).all(1)&(np.sum((q-seed)**2,axis=1)<=radius**2)
            if mask is not None:
                mask.seek(start);excluded=np.fromfile(mask,'u1',count=stop-start)
                if len(excluded)!=len(q):raise ValueError('The exclusion mask changed during the local read.')
                use&=excluded==0
            if spec:use&=jobdata.selection_mask(q,spec)
            for hidden in item.get('hidden_boxes',[]):use&=~jobdata.selection_mask(q,hidden)
            if item.get('isolate_box'):use&=jobdata.selection_mask(q,item['isolate_box'])
            if item.get('limit_box') and item.get('limit_hide_outside',True):use&=jobdata.selection_mask(q,{'mode':'box',**item['limit_box']})
            found=int(use.sum());total+=found;checked+=len(q)
            if total>cap:raise ValueError(f'This local area contains more than {cap:,} retained points. Reduce Search radius or draw a smaller limit box. No points were sampled or deleted.')
            if found:points.append(q[use]);ids.append(np.flatnonzero(use).astype('<i8')+start)
            progress(f'Reading local area · {checked:,} source points checked · {total:,} retained points inside radius')
    if total<max(20,cfg['normal_neighbours']+1):raise ValueError('Too few retained points around the seed. Increase Search radius or choose a denser area.')
    return np.concatenate(points),np.concatenate(ids)


def point_normals(points,neighbours,progress):
    tree=cKDTree(points);normal=np.empty_like(points);curvature=np.empty(len(points))
    for start in range(0,len(points),4096):
        stop=min(len(points),start+4096);_,ix=tree.query(points[start:stop],k=min(len(points),neighbours+1),workers=__import__('compute_runtime').workers())
        q=points[ix[:,1:]];q-=q.mean(1,keepdims=True);cov=np.einsum('nki,nkj->nij',q,q)/q.shape[1]
        eig,axes=np.linalg.eigh(cov);normal[start:stop]=axes[:,:,0]
        curvature[start:stop]=eig[:,0]/np.maximum(eig.sum(1),1e-30)
        progress(f'Estimating local surface directions · {stop:,}/{len(points):,} locations')
    return tree,normal,curvature


def region_labels(points,seed,cfg,progress):
    origin=seed.copy();q,inverse=np.unique(points-origin,axis=0,return_inverse=True)
    if len(q)<cfg['normal_neighbours']+1:raise ValueError('Too few distinct locations to estimate a local surface direction.')
    tree,normal,curvature=point_normals(q,cfg['normal_neighbours'],progress)
    distance,first=tree.query(np.zeros(3));gap=cfg['connection_mm']/1000
    if distance>max(.002,2*gap):raise ValueError('The seed is no longer near a retained point. Pick the surface again.')
    visited=np.zeros(len(q),bool);visited[first]=True;queue=[int(first)];head=0;checks=0
    cosine=math.cos(math.radians(cfg['normal_angle_deg']));offset=cfg['surface_distance_mm']/1000
    # Each point is enqueued at most once. Neighbours are exact radius queries;
    # a fixed neighbour-count graph would silently omit dense connections.
    while head<len(queue):
        i=queue[head];head+=1
        near=np.asarray(tree.query_ball_point(q[i],gap),dtype=np.int64);checks+=len(near)
        if checks>MAX_GRAPH_VISITS:raise ValueError('The connection distance joins too many neighbours. Reduce Connection distance or Search radius, then preview again. The previous result is unchanged.')
        near=near[~visited[near]]
        if len(near):
            delta=q[near]-q[i]
            aligned=np.abs(normal[near]@normal[i])>=cosine
            plane=np.maximum(np.abs(delta@normal[i]),np.abs(np.einsum('ij,ij->i',delta,normal[near])))<=offset
            # High-curvature/ambiguous corners are not propagation bridges.
            smooth=(curvature[near]<=.15)&(curvature[i]<=.15)
            keep=near[aligned&plane&smooth];visited[keep]=True;queue.extend(keep.tolist())
        if head%1024==0:progress(f'Growing from the picked seed · {len(queue):,} connected locations · {checks:,} neighbour checks')
    labels=visited[inverse].astype('<i4');selected=points[labels==1]
    touches=bool(np.any(np.linalg.norm(selected-seed,axis=1)>cfg['radius_m']-gap))
    warnings=['Connectivity and local surface directions define this region; attached objects with similar geometry can still connect. Review the selection before removing it.']
    if touches:warnings.append('The region reaches the search-radius boundary. Increase Search radius and preview again if you want to grow farther.')
    groups=[dict(id=1,name='Connected region',points=int(labels.sum()),centre=selected.mean(0).tolist(),selected=True)]
    return labels,groups,dict(neighbour_checks=checks,unique_locations=len(q),reaches_radius=touches),warnings


def _design(xy):
    x,y=xy.T
    return np.column_stack([np.ones(len(x)),x,y,x*x,x*y,y*y])


def _footprint(xy,gap):
    """Measured planar diameter and principal-axis elongation.

    The diagonal of a bounding box is not a fitting's diameter: it rejects a
    circular fitting at diameter/sqrt(2), and its elongation changes with the
    scene orientation. A convex-hull caliper gives the actual farthest pair
    without a quadratic point-distance matrix; principal extents rotate with
    the measured component. Duplicate observations retain their original IDs
    but do not provide additional geometric support.
    """
    q=np.unique(np.asarray(xy,dtype=np.float64),axis=0)
    if len(q)<2:return 0.,0.,len(q)
    q=q-q.mean(0)
    _,axes=np.linalg.eigh(q.T@q/len(q))
    widths=np.ptp(q@axes,axis=0)
    elongation=float(widths.max()/max(float(widths.min()),gap))
    try:h=q[ConvexHull(q).vertices]
    except QhullError:
        # A collinear component has no 2D hull; the extremal points along its
        # principal axis still define its exact end-to-end extent.
        along=q@axes[:,1]
        return float(np.linalg.norm(q[np.argmax(along)]-q[np.argmin(along)])),elongation,len(q)
    count=len(h);opposite=1;diameter2=0.
    def area(edge,point):return float(edge[0]*point[1]-edge[1]*point[0])
    for first in range(count):
        following=(first+1)%count;edge=h[following]-h[first]
        while area(edge,h[(opposite+1)%count]-h[first])>area(edge,h[opposite]-h[first]):
            opposite=(opposite+1)%count
        for a in (first,following):
            delta=h[a]-h[opposite];diameter2=max(diameter2,float(delta@delta))
    return math.sqrt(diameter2),elongation,len(q)


def bolt_labels(points,seed,direction,cfg,progress):
    """Robust quadratic lining reference; only compact positive features pass."""
    q=points-seed;radius=cfg['radius_m'];maxdiam=cfg['max_diameter_mm']/1000
    if maxdiam>radius:raise ValueError('Search radius must be at least the maximum bolt/fitting diameter, with surrounding lining visible.')
    _,axes=np.linalg.eigh(q.T@q/max(1,len(q)));normal=axes[:,0]
    direction=vector(direction,'Direction towards the tunnel interior / camera');length=np.linalg.norm(direction)
    if length<1e-12:raise ValueError('Pick a seed with a valid view direction, or enter an interior direction.')
    direction/=length
    if abs(float(normal@direction))<.20:raise ValueError('The view is nearly edge-on to this surface. Look towards the lining, then pick the seed again.')
    if normal@direction<0:normal=-normal
    if cfg['flip_side']:normal=-normal
    u=axes[:,2];v=np.cross(normal,u);basis=np.column_stack([u,v,normal]);local=q@basis;xy=local[:,:2];radial=np.linalg.norm(xy,axis=1)
    fit=(radial>=min(maxdiam*.65,radius*.35))&(radial<=radius*.95)
    if fit.sum()<60:raise ValueError('Not enough surrounding lining to fit a reference. Increase Search radius or pick nearer the centre of a measured lining patch.')
    # Coordinates are normalised to avoid conditioning loss for small fittings.
    X=_design(xy/radius);z=local[:,2];use=fit.copy();coef=np.zeros(6)
    for iteration in range(9):
        if use.sum()<60:raise ValueError('The lining reference has too little consistent support. Choose a smaller, clearer patch.')
        A=X[use];b=z[use]
        coef,_,rank,_=np.linalg.lstsq(A,b,rcond=None)
        if rank<6 or np.linalg.cond(A)>500:raise ValueError('The surrounding lining does not span a usable surface patch. Pick a wider area with points on both sides.')
        residual=z-X@coef;centre=np.median(residual[use]);mad=1.4826*np.median(np.abs(residual[use]-centre))
        cutoff=max(.002,3.5*mad);new=fit&(abs(residual-centre)<=cutoff)
        progress(f'Fitting surrounding measured lining · pass {iteration+1}/9 · {int(new.sum()):,} supporting points')
        if np.array_equal(new,use):break
        use=new
    residual=z-X@coef;rms=float(np.sqrt(np.mean(residual[use]**2)))
    original_residual=residual
    if cfg.get('feature_kind','raised')=='recess':residual=-residual
    feature='recessed bolt pocket' if cfg.get('feature_kind','raised')=='recess' else 'raised fitting'
    minheight=cfg['min_height_mm']/1000;maxheight=cfg['max_height_mm']/1000
    if use.sum()<fit.sum()*.55 or rms>max(.003,minheight*.5):raise ValueError('The local lining fit is too uncertain for bolt selection. Choose a clearer patch or raise Minimum protrusion above the surface roughness.')
    angles=np.arctan2(xy[use,1],xy[use,0]);sectors=np.floor((angles+np.pi)*8/(2*np.pi)).astype(int)%8
    if len(np.unique(sectors))<6:raise ValueError('The reference needs measured lining around the seed, not just on one side. Move the seed or enlarge Search radius.')
    support=(abs(residual)<=max(.003,3*rms))&(radial<radius*.98)
    candidate=(residual>=max(minheight,4*rms))&(residual<=maxheight)&(radial<=radius*.80)
    idx=np.flatnonzero(candidate);labels=np.zeros(len(points),dtype='<i4');groups=[]
    if not len(idx):return labels,groups,dict(reference_rms_mm=rms*1000,reference_points=int(use.sum()),normal=normal.tolist(),negative_recess_points=int(np.count_nonzero(original_residual<-minheight))),['No compact '+feature+' candidates passed the depth/height and surrounding-wall support checks.']
    cq=local[idx];tree=cKDTree(cq);support_tree=cKDTree(xy[support]);visited=np.zeros(len(idx),bool);gap=cfg['connection_mm']/1000;checks=0;rejected=0
    reasons=dict(insufficient_points=0,oversize=0,elongated=0,unsupported=0)
    for first in range(len(idx)):
        if visited[first]:continue
        visited[first]=True;queue=[first];head=0
        while head<len(queue):
            at=queue[head];head+=1;near=np.asarray(tree.query_ball_point(cq[at],gap),dtype=np.int64);checks+=len(near)
            if checks>MAX_GRAPH_VISITS:raise ValueError('Too many neighbours in the protrusion search. Reduce Connection distance or Search radius.')
            new=near[~visited[near]];visited[new]=True;queue.extend(new.tolist())
            if head%2048==0:progress(f'Grouping compact protrusions · {checks:,} neighbour checks')
        take=idx[np.asarray(queue)];part=local[take]
        if len(take)<cfg['min_points']:
            rejected+=1;reasons['insufficient_points']+=1;continue
        extent,elongation,unique_count=_footprint(part[:,:2],gap)
        if unique_count<cfg['min_points']:
            rejected+=1;reasons['insufficient_points']+=1;continue
        if extent>maxdiam:
            rejected+=1;reasons['oversize']+=1;continue
        if elongation>4:
            rejected+=1;reasons['elongated']+=1;continue
        centre=part[:,:2].mean(0);r=max(float(np.max(np.linalg.norm(part[:,:2]-centre,axis=1)))+2*gap,.035)
        angle=np.arange(8)*np.pi/4;around=centre+r*np.c_[np.cos(angle),np.sin(angle)]
        distances,_=support_tree.query(around)
        # Neighbouring observed support on six sides prevents isolated/floating
        # objects and scan-edge extrapolation being accepted as bolt evidence.
        if np.count_nonzero(distances<=max(.025,2.5*gap))<6:
            rejected+=1;reasons['unsupported']+=1;continue
        ident=len(groups)+1;labels[take]=ident
        groups.append(dict(id=ident,name=f'{feature.capitalize()} candidate {ident}',points=len(take),unique_locations=unique_count,centre=points[take].mean(0).tolist(),height_mm=float(np.median(residual[take])*1000),peak_height_mm=float(residual[take].max()*1000),diameter_mm=extent*1000,elongation=elongation,selected=False))
        if ident>2000:raise ValueError('Too many candidates in one local area. Reduce Search radius and preview a smaller patch.')
        progress(f'Reviewing compact protrusions · {len(groups):,} supported candidates')
    stats=dict(reference_rms_mm=rms*1000,reference_points=int(use.sum()),normal=normal.tolist(),negative_recess_points=int(np.count_nonzero(original_residual<-minheight)),rejected_components=rejected,rejected_reasons=reasons,neighbour_checks=checks)
    warnings=['These are compact geometric '+feature+' candidates, not identified hardware. Review their measured points and the selected feature shape before removal.','Only measured points can be removed. No lining is added behind a bolt or fitting.']
    if not groups:warnings.insert(0,'No compact '+feature+' candidates passed all depth/height, size and surrounding-wall support checks.')
    if rejected:
        reason_text={'insufficient_points':'too few distinct measured locations','oversize':'larger than Maximum fitting diameter','elongated':'elongated rather than compact','unsupported':'insufficient measured lining around the feature'}
        warnings.append('Rejected protrusion groups: '+ '; '.join(f'{n:,} with {reason_text[k]}' for k,n in reasons.items() if n)+'. Review these checks before changing the search settings.')
    return labels,groups,stats,warnings


def _remove_cache(row):
    if not row:return
    for key in ('points_file','point_ids_file','labels_file','selected_file'):
        if row.get(key):
            try:Path(row[key]).unlink(missing_ok=True)
            except OSError:pass  # a locked obsolete cache must not invalidate a new result


def preview(s,body):
    item=source(s,body.get('source_id'));mode=body.get('mode','region')
    if mode not in ('region','bolts'):raise ValueError('Choose seeded region growth or protruding bolts.')
    cfg=settings(body.get('settings'));seed=vector(body.get('seed'),'Picked seed');before=fingerprint(item)
    if body.get('source_revision') and body['source_revision']!=registration_pose.revision(item['meta']):raise ValueError('The picked scan changed. Pick the seed again.')
    ident=uuid.uuid4().hex;paths={k:str(asset_folder(s.WORK,'analysis')/(ident+suffix)) for k,suffix in [('points_file','.feature_xyz64'),('point_ids_file','.feature_ids'),('labels_file','.feature_labels'),('selected_file','.feature_selected')]}
    started=time.monotonic()
    try:
        p,ids=local_points(item,seed,cfg,s.progress,body.get('selection'))
        if mode=='region':labels,groups,stats,warnings=region_labels(p,seed,cfg,s.progress)
        elif cfg.get('feature_kind')=='departures':labels,groups,stats,warnings=__import__('lining_departures').local_labels(p,seed,body.get('view_direction'),cfg,s.progress)
        else:labels,groups,stats,warnings=bolt_labels(p,seed,body.get('view_direction'),cfg,s.progress)
        chosen=np.isin(labels,[g['id'] for g in groups if g.get('selected')])
        if fingerprint(source(s,item['id']))!=before:raise ValueError('The source scan changed during analysis. Pick the seed again.')
        s.progress('Preparing exact original-point selection for review')
        p.astype('<f8').tofile(paths['points_file']);ids.astype('<i8').tofile(paths['point_ids_file']);labels.astype('<i4').tofile(paths['labels_file']);chosen.astype('u1').tofile(paths['selected_file'])
        row=dict(id=ident,revision=uuid.uuid4().hex,source_id=item['id'],source_name=item['name'],source_fingerprint=before,mode=mode,settings=cfg,seed=seed.tolist(),groups=groups,statistics={**stats,'local_points':len(p),'selected_points':int(chosen.sum()),'candidate_points':int(np.count_nonzero(labels)),'seconds':round(time.monotonic()-started,3)},warnings=warnings,count=len(p),**paths)
        old=getattr(s,'FEATURE_SELECTION',None);s.FEATURE_SELECTION=row;_remove_cache(old)
        return row
    except BaseException:
        _remove_cache(paths);raise


def current(s,body=None):
    row=getattr(s,'FEATURE_SELECTION',None)
    if not row:raise ValueError('Preview a local feature selection first.')
    body=body or {}
    if body.get('preview_id')!=row['id'] or body.get('revision')!=row['revision']:raise ValueError('This feature preview changed. Refresh the review before applying it.')
    if fingerprint(source(s,row['source_id']))!=row['source_fingerprint']:raise ValueError('The scan or its visible area changed. Preview the feature selection again.')
    return row


def _selection_commit(row,selected):
    dest=Path(row['selected_file']);tmp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        np.asarray(selected,'u1').tofile(tmp);__import__('os').replace(tmp,dest)
    finally:tmp.unlink(missing_ok=True)
    row['statistics']['selected_points']=int(np.count_nonzero(selected));row['revision']=uuid.uuid4().hex
    labels=np.fromfile(row['labels_file'],'<i4');counts=np.bincount(labels[np.asarray(selected,bool)],minlength=len(row['groups'])+1)
    for group in row['groups']:
        n=int(counts[group['id']]);group.update(selected=n>0,selected_points=n,partial=0<n<group['points'])


def select_groups(s,body):
    row=current(s,body);ids=body.get('group_ids')
    if not isinstance(ids,list) or any(type(i) is not int for i in ids) or len(ids)!=len(set(ids)) or not set(ids)<={g['id'] for g in row['groups']}:raise ValueError('Choose existing candidate groups.')
    labels=np.fromfile(row['labels_file'],'<i4');chosen=np.isin(labels,ids);_selection_commit(row,chosen)
    for g in row['groups']:g['selected']=g['id'] in ids
    return row


def refine(s,body):
    row=current(s,body);spec=jobdata.selection_spec(body.get('selection') or {})
    operation=body.get('operation')
    if operation not in ('keep','exclude'):raise ValueError('Choose Keep within drawn area or Exclude drawn area.')
    p=np.fromfile(row['points_file'],'<f8').reshape(-1,3);selected=np.fromfile(row['selected_file'],'u1')!=0
    inside=jobdata.selection_mask(p,spec);selected&=inside if operation=='keep' else ~inside
    s.progress('Refining the exact original-point selection');current(s,body);_selection_commit(row,selected)
    row['refined']=True
    return row


def delete(s,body):
    row=current(s,body)
    if body.get('confirmed') is not True:raise ValueError('Review the selected points before choosing Remove selected points.')
    selected=np.fromfile(row['selected_file'],'u1')!=0;ids=np.fromfile(row['point_ids_file'],'<i8')[selected]
    if not len(ids):raise ValueError('Choose candidate groups or grow a region before removing points.')
    path=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.feature_delete_ids');item=source(s,row['source_id'])
    try:
        ids.tofile(path);current(s,body)
        reason=('Lining departures' if row['settings'].get('feature_kind')=='departures' else 'Recessed bolt-pocket selection' if row['settings'].get('feature_kind')=='recess' else 'Raised fitting selection') if row['mode']=='bolts' else 'Seeded connected region'
        scan_edits.apply(s,item,'separate_selection' if body.get('action')=='separate' else 'delete_selection',selected_file={'file':str(path),'count':len(ids)},reason=reason)
        row['last_delete']=dict(count=len(ids),source_id=item['id']);return dict(deleted=len(ids),undo_available=True)
    finally:path.unlink(missing_ok=True)


def public(s):
    row=getattr(s,'FEATURE_SELECTION',None)
    if not row:return None
    out={k:copy.deepcopy(row[k]) for k in ('id','revision','source_id','source_name','mode','settings','seed','groups','statistics','warnings')}
    try:out['stale']=fingerprint(source(s,row['source_id']))!=row['source_fingerprint']
    except ValueError:out['stale']=True
    if row.get('last_delete'):out['last_delete']=copy.deepcopy(row['last_delete'])
    n=row['count'];step=max(1,math.ceil(n/DISPLAY_POINTS));indices=np.arange(0,n,step,dtype=np.int64)
    arrays=[]
    try:
        def mapped(key,dtype,shape):
            a=np.memmap(row[key],dtype,'r',shape=shape);arrays.append(a);return a
        p=mapped('points_file','<f8',(n,3));labels=mapped('labels_file','<i4',(n,));selected=mapped('selected_file','u1',(n,))
        # Reserve half the display for candidate points so a small bolt is not
        # lost in a wall-dominated strided preview. Exact IDs remain on disk.
        candidates=np.flatnonzero(labels);cstep=max(1,math.ceil(len(candidates)/(DISPLAY_POINTS//2)))
        indices=np.unique(np.r_[indices[::2],candidates[::cstep]])[:DISPLAY_POINTS]
        q=np.asarray(p[indices]);origin=np.asarray(row['seed']);colour=np.tile([.42,.53,.62],(len(indices),1))
        colour[np.asarray(labels[indices])>0]=[.1,.8,.85];colour[np.asarray(selected[indices])>0]=[1.,.55,.12]
        out['preview']=dict(origin=origin.tolist(),points=np.column_stack([q-origin,colour]).tolist(),count=n,source_id=row['source_id'],selected_count=row['statistics']['selected_points'],preview_count=len(indices),sampled=len(indices)<n)
    finally:
        for a in arrays:a._mmap.close()
    return out


def get(s,h,path,args):
    if path!='/api/feature-selection':return False
    h.send(dict(defaults=DEFAULTS,maximum_local_points=MAX_LOCAL_POINTS,result=public(s)));return True


def post(s,h,path,body):
    if path!='/api/feature-selection':return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    action=body.get('action')
    if action=='clear':
        _remove_cache(getattr(s,'FEATURE_SELECTION',None));s.FEATURE_SELECTION=None;h.send({'cleared':True});return True
    if action not in ('preview','select','refine','delete','separate'):raise ValueError('Unknown local feature action.')
    request=copy.deepcopy(body)
    if action!='preview':current(s,request)
    fn={'preview':preview,'select':select_groups,'refine':refine,'delete':delete,'separate':delete}[action]
    if action=='select':fn(s,request);h.send({'result':public(s)});return True
    s.start_job(lambda:fn(s,request),'local feature selection',native_progress=True);h.send({'started':True});return True
