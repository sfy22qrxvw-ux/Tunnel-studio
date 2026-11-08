"""Lightweight setup photographs; measured XYZ is queried from retained points."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from functools import lru_cache
import copy,hashlib,json,os,uuid
import numpy as np
import jobdata,registration_workspace as rw,setup_photo_codec as codec

BLOCK=32768

def copy_photo(setup,work):
    photo=setup.get('photograph') or {}
    if not photo.get('available'):return None
    if not photo.get('in_setup_frame'):raise ValueError('The project does not confirm photographs in the setup coordinate frame.')
    source=Path(photo['path'])
    if source.stat().st_size>128*1024*1024:raise ValueError('Setup photograph container exceeds the supported size.')
    data=source.read_bytes();meta=codec.decode(data)
    jobdata.room(work,len(data)+1024*1024)
    dest=asset_folder(work,'imported')/(uuid.uuid4().hex+'.setup-photo.dat')
    try:dest.write_bytes(data)
    except BaseException:dest.unlink(missing_ok=True);raise
    return dict(file=str(dest),sha256=meta['sha256'],source_name=source.name,format='leica_cube_jpeg_v1',in_setup_frame=True)

@lru_cache(maxsize=8)
def _description(path,size,modified):return codec.read(path)

def description(x):
    photo=x.get('setup_photo')
    if not photo:raise ValueError('No photographs are attached to this setup. Load photographs from the Leica project folder.')
    p=Path(photo['file']);stat=p.stat();d=_description(str(p),stat.st_size,stat.st_mtime_ns)
    if not photo.get('in_setup_frame') or d['sha256']!=photo.get('sha256'):raise ValueError('The setup photographs changed. Reload them from the original Leica project.')
    return d

def source_key(x):
    m=x['meta'];return hashlib.sha256(json.dumps([x['id'],m['file'],m.get('mask_file'),x.get('scan_origin',[0,0,0]),x.get('scan_orientation',np.eye(3).tolist()),(x.get('setup_photo') or {}).get('sha256'),x.get('hidden_boxes'),x.get('isolate_box'),x.get('limit_box'),x.get('limit_hide_outside',True)],sort_keys=True).encode()).hexdigest()

def opened(s,ident):
    x=rw.scan(s,ident)
    if not x.get('setup_photo'):return dict(available=False,id=ident,message=x.get('photo_notice') or 'Load photographs from your Leica project folder, or use Intensity view.')
    d=description(x);faces=[]
    for f in d['faces']:
        faces.append({k:f[k] for k in ('index','width','height','rotation','fx','fy','cx','cy')})
    return dict(available=True,id=ident,name=x['name'],key=source_key(x),photo_key=d['sha256'],faces=faces)

def attach(s,body):
    import leica_project
    info=leica_project.inspect(body['path']);old=copy.deepcopy(s.STATE);created=[];loaded=[];missing=[]
    by_guid={p['guid']:p for p in info['setups']}
    matches=[x for x in rw.scans(s) if x.get('leica_project_hash')==info['database_sha256'] and x.get('leica_setup_guid') in by_guid]
    if not matches:raise ValueError('This folder does not match any imported Leica setups. Choose the original project folder used for these scans.')
    try:
        for x in matches:
            setup=by_guid[x['leica_setup_guid']]
            try:photo=copy_photo(setup,s.WORK)
            except (ValueError,OSError) as e:missing.append(x['name']+': '+str(e));continue
            if photo:
                created.append(photo['file']);x['setup_photo']=photo;x.pop('photo_notice',None);loaded.append(x['name'])
            else:missing.append(x['name']+': photograph file not present in the project folder')
        if not loaded:raise ValueError('No supported photographs could be loaded. '+'; '.join(missing))
        s.save();return dict(loaded=loaded,missing=missing)
    except BaseException:
        s.STATE.clear();s.STATE.update(old)
        for p in created:Path(p).unlink(missing_ok=True)
        raise

def blocks(s,x,progress):
    m=x['meta'];p=Path(m['file']);stat=p.stat()
    digest=hashlib.sha256(json.dumps([str(p),stat.st_size,stat.st_mtime_ns,m['count'],BLOCK]).encode()).hexdigest()
    dest=Path(s.WORK)/(digest+'.photo-blocks.npz')
    if dest.is_file():
        try:
            with np.load(dest,allow_pickle=False) as d:
                b=d['bounds']
                if b.shape==((m['count']+BLOCK-1)//BLOCK,2,3):return b
        except (OSError,ValueError,KeyError):pass
    points=np.memmap(p,'<f8','r',shape=(m['count'],3));out=[]
    try:
        for start in range(0,len(points),BLOCK):
            q=np.asarray(points[start:start+BLOCK]);out.append([q.min(0),q.max(0)])
            if start%(BLOCK*16)==0:progress(f'Preparing point lookup once for this setup: {min(start+BLOCK,len(points)):,} / {len(points):,}')
    finally:points._mmap.close()
    b=np.asarray(out);temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('wb') as f:np.savez(f,bounds=b)
        os.replace(temp,dest)
    finally:temp.unlink(missing_ok=True)
    return b

def measured_pick(s,body,progress=lambda m:None):
    x=rw.scan(s,body['id']);d=description(x)
    if body.get('key')!=source_key(x):raise ValueError('This scan or its photographs changed. Reopen the setup view before picking.')
    index=body.get('face');u,v=float(body.get('u',-1)),float(body.get('v',-1))
    if type(index) is not int or not 0<=index<6 or not (np.isfinite([u,v]).all() and 0<=u<=1 and 0<=v<=1):raise ValueError('Pick inside a setup photograph.')
    face=d['faces'][index];fr=np.asarray(face['rotation']);px=u*(face['width']-1);py=v*(face['height']-1)
    ray=np.array([(px-face['cx'])/face['fx'],(py-face['cy'])/face['fy'],1.]);ray=ray@fr;ray/=np.linalg.norm(ray)
    origin=np.asarray(x.get('scan_origin',[0,0,0]),float);R=np.asarray(x.get('scan_orientation',np.eye(3)),float);world_ray=R@ray
    bounds=blocks(s,x,progress);centres=bounds.mean(1);radii=np.linalg.norm((bounds[:,1]-bounds[:,0])/2,axis=1);relative=centres-origin;distance=np.linalg.norm(relative,axis=1)
    # Conservative bounding spheres around spatially ordered source blocks.
    cone=np.arctan(3/min(face['fx'],face['fy']));allow=np.arcsin(np.clip(radii/np.maximum(distance,1e-12),0,1))+cone
    dot=relative@world_ray/np.maximum(distance,1e-12)
    chosen=np.flatnonzero((distance<=radii)|(dot>=np.cos(np.minimum(allow,np.pi))))
    m=x['meta'];points=np.memmap(m['file'],'<f8','r',shape=(m['count'],3));mask=np.memmap(m['mask_file'],'u1','r',shape=(m['count'],)) if m.get('mask_file') else None
    best=None;examined=0
    try:
        for j,block in enumerate(chosen):
            start=int(block)*BLOCK;q=np.asarray(points[start:start+BLOCK]);valid=np.isfinite(q).all(1)
            if mask is not None:valid&=mask[start:start+len(q)]==0
            if x.get('limit_box') and x.get('limit_hide_outside',True):valid&=jobdata.selection_mask(q,{'mode':'box',**x['limit_box']})
            if x.get('isolate_box') is not None:valid&=jobdata.selection_mask(q,x['isolate_box'])
            for hidden in x.get('hidden_boxes',[]):valid&=~jobdata.selection_mask(q,hidden)
            cam=((q-origin)@R)@fr.T;z=cam[:,2];valid&=z>1e-8
            use=np.flatnonzero(valid);cam=cam[use];examined+=len(q)
            dx=face['fx']*cam[:,0]/cam[:,2]+face['cx']-px;dy=face['fy']*cam[:,1]/cam[:,2]+face['cy']-py;error=np.hypot(dx,dy)
            keep=np.flatnonzero(error<=2.)
            if len(keep):
                # Prefer the nearest surface within the closest half-pixel band.
                bands=np.floor(error[keep]*2).astype(int);depth=np.linalg.norm(cam[keep],axis=1);order=np.lexsort((start+use[keep],error[keep],depth,bands));k=keep[order[0]]
                rank=(int(np.floor(error[k]*2)),float(np.linalg.norm(cam[k])),float(error[k]),int(start+use[k]))
                if best is None or rank<best[0]:best=(rank,q[use[k]].tolist())
            if j%8==0:progress(f'Finding the measured point: {j+1:,} / {len(chosen):,} nearby blocks')
        if best is None:raise ValueError('No retained measured point near this photograph pixel. Try a nearby surface or switch to Intensity view.')
        if body.get('key')!=source_key(x):raise ValueError('The scan changed during picking. Reopen its setup view.')
        return dict(point=best[1],index=best[0][3],scan_id=x['id'],key=body['key'],face=index,u=u,v=v,pixel_error=best[0][2],examined=examined,source_count=m['count'],token=uuid.uuid4().hex,photo=True)
    finally:
        points._mmap.close()
        if mask is not None:mask._mmap.close()

def saved_pick(s,body):
    result=getattr(s,'_setup_photo_pick',None) or {};x=rw.scan(s,body['id'])
    if not result.get('point') or result.get('token')!=body.get('token') or result.get('scan_id')!=x['id'] or result.get('key')!=source_key(x) or result.get('key')!=body.get('key'):raise ValueError('The photograph pick is no longer current. Pick the point again before saving.')
    return result

def get(s,h,path,args):
    if path=='/api/setup-photo-pick':h.send(getattr(s,'_setup_photo_pick',None) or {});return True
    if path!='/api/setup-photo-face':return False
    x=rw.scan(s,args.get('id',[''])[0]);d=description(x)
    if args.get('key',[''])[0]!=d['sha256']:raise ValueError('Setup photograph changed.')
    face=int(args.get('face',[-1])[0]);size=int(args.get('size',[1024])[0])
    if not 0<=face<6 or size not in (1024,2048):raise ValueError('Invalid photograph face request.')
    f=d['faces'][face];level=next((l for l in f['levels'] if l['width']<=size),f['levels'][-1])
    with Path(x['setup_photo']['file']).open('rb') as stream:stream.seek(level['offset']);blob=stream.read(level['length'])
    h.send(blob,'image/jpeg');return True

def api(s,h,path,body):
    if path!='/api/setup-photos':return False
    action=body.get('action')
    if action=='open':h.send(opened(s,body['id']));return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    if action=='attach':h.send(attach(s,body))
    elif action=='pick':
        import job_api
        s._setup_photo_pick=None;job_api.CANCEL.clear()
        def run():s._setup_photo_pick=measured_pick(s,body,lambda m:job_api.progress(s,m))
        s.start_job(run,kind='setup_pick');h.send({'started':True})
    else:raise ValueError('Unknown photograph operation.')
    return True
