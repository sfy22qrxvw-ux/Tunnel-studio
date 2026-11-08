"""Scanner-centred angular view, rasterized from retained measured points."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import base64,copy,hashlib,json,struct,uuid,zlib
import numpy as np
import registration_workspace as rw
import jobdata

WIDTH,HEIGHT=2048,1024

def key(x):return hashlib.sha256(json.dumps([x['id'],x['meta']['file'],x['meta'].get('mask_file'),x.get('scan_origin',[0,0,0]),x.get('scan_orientation',np.eye(3).tolist())],sort_keys=True).encode()).hexdigest()
def png(image):
    def chunk(name,value):return struct.pack('>I',len(value))+name+value+struct.pack('>I',zlib.crc32(name+value)&0xffffffff)
    h,w=image.shape;raw=b''.join(b'\0'+row.tobytes() for row in image)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,0,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def build(s,ident):
    import job_api
    progress=lambda message:job_api.progress(s,message)
    x=rw.scan(s,ident);m=x['meta']
    if m.get('source_format')!='hspca':raise ValueError('Setup view needs a native Leica setup with a known scanner position.')
    cache=x.get('setup_view_cache') or s.STATE.get('setup_view')
    if cache and cache['key']==key(x) and Path(cache['point_ids_file']).is_file():
        s.STATE['setup_view']=cache;s.save();return
    jobdata.room(s.WORK,WIDTH*HEIGHT*8+4*1024*1024)
    p=np.memmap(m['file'],'<f8','r',shape=(m['count'],3));a=np.memmap(m['attributes_file'],'<f4','r',shape=(m['count'],4));mask=np.memmap(m['mask_file'],'u1','r',shape=(m['count'],)) if m.get('mask_file') else None
    ids=np.full(WIDTH*HEIGHT,-1,dtype='<i8');depth=np.full(WIDTH*HEIGHT,np.inf);values=np.zeros(WIDTH*HEIGHT)
    origin=np.array(x.get('scan_origin',[0,0,0]));R=np.array(x.get('scan_orientation',np.eye(3)))
    try:
        for start in range(0,len(p),200000):
            pts=np.asarray(p[start:start+200000]);local=(pts-origin)@R;dist=np.linalg.norm(local,axis=1);valid=np.isfinite(local).all(1)&(dist>1e-7)
            if mask is not None:valid&=mask[start:start+len(pts)]==0
            take=np.flatnonzero(valid);v=local[take];d=dist[take]
            xx=np.floor((np.arctan2(v[:,1],v[:,0])+np.pi)/(2*np.pi)*WIDTH).astype(int)%WIDTH
            yy=np.clip(np.floor((.5-np.arcsin(np.clip(v[:,2]/d,-1,1))/np.pi)*HEIGHT).astype(int),0,HEIGHT-1);pix=yy*WIDTH+xx
            np.minimum.at(depth,pix,d);keep=d==depth[pix];ix=start+take[keep];dest=pix[keep]
            ids[dest]=ix;values[dest]=a[ix,0]
            progress(f'Preparing setup view: {min(start+len(pts),len(p)):,} / {len(p):,} source points')
        image=np.full(WIDTH*HEIGHT,32,np.uint8);valid=(ids>=0)&np.isfinite(values)
        if valid.any():
            lo,hi=np.percentile(values[valid],[2,98]);image[valid]=np.clip((values[valid]-lo)/max(hi-lo,1e-9)*255,0,255).astype(np.uint8)
        dest=asset_folder(s.WORK,'working')/(uuid.uuid4().hex+'.setupids64');ids.tofile(dest)
        result=dict(id=ident,name=x['name'],key=key(x),width=WIDTH,height=HEIGHT,point_ids_file=str(dest),data_url='data:image/png;base64,'+base64.b64encode(png(image.reshape(HEIGHT,WIDTH))).decode('ascii'))
        old=s.STATE.get('setup_view');previous=x.get('setup_view_cache');s.STATE['setup_view']=result;x['setup_view_cache']=result
        try:s.save()
        except Exception:s.STATE['setup_view']=old;x['setup_view_cache']=previous;dest.unlink(missing_ok=True);raise
    finally:
        p._mmap.close();a._mmap.close()
        if mask is not None:mask._mmap.close()

def public(s):return {k:v for k,v in (s.STATE.get('setup_view') or {}).items() if k!='point_ids_file'}
def pick(s,body):
    cache=s.STATE.get('setup_view') or {};x=rw.scan(s,body['id'])
    if cache.get('key')!=body.get('key') or cache.get('key')!=key(x):raise ValueError('Setup view changed; reopen it before picking.')
    xx,yy=int(body['x']),int(body['y'])
    if not 0<=xx<WIDTH or not 0<=yy<HEIGHT:raise ValueError('Pick inside the setup view.')
    ids=np.memmap(cache['point_ids_file'],'<i8','r',shape=(HEIGHT,WIDTH));candidates=[]
    for dy in range(-3,4):
        if not 0<=yy+dy<HEIGHT:continue
        for dx in range(-3,4):
            index=int(ids[yy+dy,(xx+dx)%WIDTH])
            if index>=0:candidates.append((dx*dx+dy*dy,index))
    ids._mmap.close()
    if not candidates:raise ValueError('No measured point at that pixel. Pick a visible intensity point nearby.')
    index=min(candidates)[1];m=x['meta'];p=np.memmap(m['file'],'<f8','r',shape=(m['count'],3));point=p[index].tolist();p._mmap.close()
    return dict(point=point,index=index,scan_id=x['id'])

def save_target(s,body):
    name=str(body.get('name') or '').strip()
    if not name or len(name)>60:raise ValueError('Enter a target name of 1–60 characters. Use the same name for this target in other scans.')
    photo=body.get('pick_source')=='photo'
    cloud=body.get('pick_source')=='cloud'
    measured=__import__('setup_camera').saved_pick(s,body) if cloud else __import__('setup_photos').saved_pick(s,body) if photo else pick(s,body);old=copy.deepcopy(s.STATE)
    try:
        r=rw.activate(s,body['id'])
        if any(row['id']==name for row in r.get('rows',[])) or any(c.get('name',c['id'])==name for c in r.get('candidates',[])):
            raise ValueError('This scan already has a target named '+name+'. Choose another name or edit the existing target.')
        ident=uuid.uuid4().hex
        c=dict(id=ident,name=name,source=measured['point'],status='accepted',method='Manually picked measured point from setup photograph' if photo else 'Manually picked measured point in scanner setup view',manual=True,channel='photograph' if photo else 'intensity',score=None,plane_rms_mm=None,point_index=measured['index'])
        if cloud:c.update(method='Manually picked measured point in 3D scanner setup view',channel='point cloud')
        thumbnail=body.get('thumbnail')
        if thumbnail:
            if not isinstance(thumbnail,str) or not thumbnail.startswith('data:image/png;base64,') or len(thumbnail)>250000:raise ValueError('The target preview is invalid. Pick the point again.')
            c['thumbnail']=dict(data_url=thumbnail,width=128,manual=True,note=('Setup photograph crop' if photo else 'Setup intensity view crop')+'; red cross = manually picked measured point. No automatic centre fit.')
        r.setdefault('candidates',[]).append(c)
        r.setdefault('rows',[]).append(dict(id=name,candidate_id=ident,role='fit',source=list(measured['point']),control=list(s.STATE.get('survey_controls',{}).get(name,[None,None,None])),method=c['method']))
        r.update(revision=uuid.uuid4().hex,solution=None,transformed=None);rw.persist(s);s.save()
        return dict(saved=True,id=ident,name=name,scan_id=body['id'],point=measured['point'])
    except Exception:
        s.STATE.clear();s.STATE.update(old);raise
def api(s,h,path,body=None):
    if path!='/api/setup-view':return False
    if body is None:h.send(public(s));return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation.')
    if body.get('action')=='build':
        import job_api
        job_api.CANCEL.clear();s.start_job(lambda:build(s,body['id']),kind='job',native_progress=True);h.send({'started':True})
    elif body.get('action')=='pick':h.send(pick(s,body))
    elif body.get('action')=='save_target':h.send(save_target(s,body))
    else:raise ValueError('Unknown setup view action.')
    return True
