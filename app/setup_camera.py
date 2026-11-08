"""Main-viewport scanner pose and exact source-point recovery for cloud picks."""
import uuid
import numpy as np
import registration_workspace as rw
import jobdata,setup_photos,registration_pose

source_key=setup_photos.source_key

def has_pose(x):
    return x['meta'].get('source_format')=='hspca' or x['meta'].get('has_scanner_pose') is True

def opened(s,ident):
    x=rw.scan(s,ident)
    if not has_pose(x):
        raise ValueError('This scan has no known scanner position. Import individual Leica setups, or E57 setups with a saved pose and scan grid/associated photographs.')
    origin=np.asarray(x.get('scan_origin',[0,0,0]),float)
    rotation=np.asarray(x.get('scan_orientation',np.eye(3)),float)
    if origin.shape!=(3,) or rotation.shape!=(3,3) or not np.isfinite(origin).all() or not np.isfinite(rotation).all() or not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-5) or not np.isclose(np.linalg.det(rotation),1.,atol=1e-5):
        raise ValueError('The saved scanner position or orientation is invalid. Reimport this setup from the original project.')
    lo,hi=registration_pose.world_bounds(x['meta']);corners=np.asarray([lo,hi])-origin
    far=max(10.,float(np.linalg.norm(np.max(np.abs(corners),axis=0)))*1.1)
    neighbours=[];linked=rw.component(s,ident)
    for other in rw.scans(s):
        common_project=(x.get('leica_project_hash') and x.get('leica_project_hash')==other.get('leica_project_hash') and x['meta'].get('coordinate_frame')=='Leica project' and other['meta'].get('coordinate_frame')=='Leica project')
        if other['id']==ident or not has_pose(other) or not (other['id'] in linked or common_project):continue
        position=np.asarray(other.get('scan_origin'),float)
        if position.shape==(3,) and np.isfinite(position).all() and np.linalg.norm(position-origin)>.05:
            neighbours.append(dict(id=other['id'],name=other['name'],origin=position.tolist()))
    return dict(id=ident,name=x['name'],key=source_key(x),origin=origin.tolist(),orientation=rotation.tolist(),near=.03,far=far,fov=70,has_setup_photos=bool(x.get('setup_photo')),photo_notice=x.get('photo_notice'),neighbours=neighbours)

def preview_items(s,ident,key):
    opened(s,ident);x=rw.scan(s,ident)
    if key!=source_key(x):raise ValueError('The setup changed. Reopen its setup view.')
    # Visibility is scoped to this request; workspace eye toggles are not changed.
    return [{**x,'visible':True}]

def measured_pick(s,body,progress=lambda message:None):
    opened(s,body['id']);x=rw.scan(s,body['id']);m=x['meta']
    if body.get('key')!=source_key(x):raise ValueError('The setup changed. Reopen its setup view.')
    seed_world=np.asarray(body.get('point'),float)
    if seed_world.shape!=(3,) or not np.isfinite(seed_world).all():raise ValueError('Pick a visible cloud point.')
    seed=registration_pose.inverse_point(m,seed_world)
    # The display retains original samples as float32 offsets. Recover XYZ/index
    # from the float64 source within a bound for that display round trip.
    span=np.max(np.asarray(m['maximum'])-np.asarray(m['minimum']))
    tolerance=max(.0001,float(span)*np.finfo(np.float32).eps*2)
    bounds=setup_photos.blocks(s,x,progress)
    delta=np.maximum(np.maximum(bounds[:,0]-seed,seed-bounds[:,1]),0)
    chosen=np.flatnonzero(np.linalg.norm(delta,axis=1)<=tolerance)
    points=np.memmap(m['file'],'<f8','r',shape=(m['count'],3))
    mask=np.memmap(m['mask_file'],'u1','r',shape=(m['count'],)) if m.get('mask_file') else None
    best=None
    try:
        for block in chosen:
            start=int(block)*setup_photos.BLOCK;q=np.asarray(points[start:start+setup_photos.BLOCK]);valid=np.isfinite(q).all(1)
            if mask is not None:valid &= mask[start:start+len(q)]==0
            if x.get('limit_box') and x.get('limit_hide_outside',True):valid &= jobdata.selection_mask(q,{'mode':'box',**x['limit_box']})
            if x.get('isolate_box') is not None:valid &= jobdata.selection_mask(q,x['isolate_box'])
            for hidden in x.get('hidden_boxes',[]):valid &= ~jobdata.selection_mask(q,hidden)
            distance=np.linalg.norm(q-seed,axis=1);ids=np.flatnonzero(valid&(distance<=tolerance))
            if len(ids):
                i=ids[np.argmin(distance[ids])];rank=(float(distance[i]),int(start+i))
                if best is None or rank<best[0]:best=(rank,q[i].tolist())
            progress('Reading the original measured point for this setup…')
        if best is None:raise ValueError('This displayed point is no longer retained. Refresh the setup and pick again.')
        if body.get('key')!=source_key(x):raise ValueError('The setup changed during picking. Pick again.')
        return dict(scan_id=x['id'],key=body['key'],point=best[1],index=best[0][1],token=uuid.uuid4().hex)
    finally:
        points._mmap.close()
        if mask is not None:mask._mmap.close()

def saved_pick(s,body):
    result=getattr(s,'_setup_cloud_pick',None) or {};x=rw.scan(s,body['id'])
    if not result.get('point') or result.get('token')!=body.get('token') or result.get('scan_id')!=x['id'] or result.get('key')!=body.get('key') or result.get('key')!=source_key(x):
        raise ValueError('This cloud pick is no longer current. Pick the point again.')
    return result

def get(s,h,path,args):
    if path!='/api/setup-cloud-pick':return False
    h.send(getattr(s,'_setup_cloud_pick',None) or {});return True

def api(s,h,path,body):
    if path!='/api/setup-camera':return False
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    if body.get('action')=='open':h.send(opened(s,body['id']))
    elif body.get('action')=='pick':
        import job_api
        job_api.CANCEL.clear();s._setup_cloud_pick=None
        def run():s._setup_cloud_pick=measured_pick(s,body,lambda message:job_api.progress(s,message))
        s.start_job(run,kind='setup_pick');h.send({'started':True})
    else:raise ValueError('Unknown scanner-view operation.')
    return True
