# Copyright (c) 2026 Damian Gulczynski.
"""Disk-backed job assets. Preview sampling never replaces full-density data."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from itertools import islice
import json, math, os, shutil, subprocess, struct, sys, time, uuid, zipfile
import numpy as np
import pointio

RESERVE=512*1024*1024
POINT_EXTS={'.hspca','.e57','.las','.laz','.ply','.pts','.xyz','.txt','.csv'}

class Cancelled(ValueError): pass

def room(work,needed=0):
    if shutil.disk_usage(work).free < needed+RESERVE:
        raise ValueError('Not enough free space on the project drive. Keep at least 512 MB free in addition to the import/cache size.')

def remove_assets(meta):
    if meta.get('file'):Path(str(meta['file'])+'.viewindex.npz').unlink(missing_ok=True)
    for k in ('file','attributes_file','preview_file','vertices_file','faces_file','vertex_colours_file'):
        if meta.get(k):Path(meta[k]).unlink(missing_ok=True)

def preview_cache(meta,work,progress=lambda s:None):
    """Keep a bounded on-disk preview cache for recovery/compatibility.

    The Job Workspace can now stream a larger strided sample directly from the full
    cache, so this preview is not the display-density ceiling.
    """
    n=meta.get('count',meta.get('vertices'));key='file' if 'count' in meta else 'vertices_file'
    stride=max(1,math.ceil(n/250000));p=np.memmap(meta[key],dtype='<f8',mode='r',shape=(n,3))
    dest=Path(work)/(uuid.uuid4().hex+'.preview64');sample=np.asarray(p[::stride])
    try:sample.tofile(dest)
    except Exception:dest.unlink(missing_ok=True);raise
    finally:p._mmap.close()
    meta.update(preview_file=str(dest),preview_count=len(sample),preview_stride=stride)
    if 'count' in meta:
        progress('Preparing reusable colour and intensity overview…')
        _source_overview(meta,build=True,progress=progress)
    return meta

def _worker_python():
    """Use the console interpreter beside the desktop runtime for isolated workers."""
    exe=Path(sys.executable)
    candidate=exe.with_name('python.exe')
    return candidate if candidate.is_file() else exe

def _run_e57_worker(args,progress=lambda s:None,worker_name="e57_worker.py"):
    label="Leica HSPCA" if worker_name=="hspca_worker.py" else "E57"
    worker=Path(__file__).resolve().with_name(worker_name)
    cmd=[str(_worker_python()),str(worker),*map(str,args)]
    creationflags=0x08000000 if os.name=='nt' else 0
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',bufsize=1,creationflags=creationflags)
    result=None;tail=[]
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line=raw.rstrip('\r\n')
            if line.startswith('PROGRESS\t'):
                progress(line.split('\t',1)[1])
            elif line.startswith('RESULT\t'):
                result=json.loads(line.split('\t',1)[1])
            elif line.startswith('ERROR\t'):
                tail.append(line.split('\t',1)[1])
            elif line:
                tail.append(line)
                if len(tail)>25:tail=tail[-25:]
        code=proc.wait()
    except Exception:
        try:proc.terminate()
        except Exception:pass
        try:proc.wait(timeout=2)
        except Exception:
            try:proc.kill();proc.wait(timeout=2)
            except Exception:pass
        raise
    finally:
        if proc.stdout is not None:proc.stdout.close()
    if code!=0 or result is None:
        detail='\n'.join(tail[-8:]).strip()
        raise ValueError(label+' import stopped.'+(('\n'+detail) if detail else ''))
    return result

def scan_parts(path,progress=lambda s:None):
    if Path(path).suffix.lower()=='.hspca':
        rows=_run_e57_worker(['describe',Path(path)],progress,'hspca_worker.py')
        return [(None,int(n)) for _,n in rows]
    if Path(path).suffix.lower()=='.e57':
        rows=_run_e57_worker(['describe',Path(path)],progress)
        return [(int(i),int(n)) for i,n in rows]
    if Path(path).suffix.lower() in ('.las','.laz'):
        import laspy
        with laspy.open(path) as source:return [(None,source.header.point_count)]
    return [(None,None)]

def import_scan(path,work,index=None,expected=None,progress=lambda s:None):
    work=asset_folder(work,'imported')
    room(work,expected*40 if expected else 0)
    meta=None;dest=Path(work)/(uuid.uuid4().hex+'.xyz64');last=[0.]
    def check(message):
        progress(message)
        if time.monotonic()-last[0]>.5:room(work);last[0]=time.monotonic()
    try:
        if Path(path).suffix.lower()=='.hspca':
            check('Starting native Leica setup import…')
            meta=_run_e57_worker(['import',Path(path),dest],check,'hspca_worker.py')
            check('Building 3D preview…');preview_cache(meta,work,check)
        elif Path(path).suffix.lower()=='.e57':
            check('Starting isolated E57 import worker…')
            meta=_run_e57_worker(['import',Path(path),dest,Path(work),'none' if index is None else index],check)
        else:
            meta=pointio.import_cloud(path,dest,check,e57_scan=index)
            check('Building bounded 3D preview…');preview_cache(meta,work,check)
        source=Path(path);st=source.stat()
        meta.setdefault('source_name',source.name);meta.setdefault('source_path',str(source.resolve()))
        meta.setdefault('source_format',source.suffix.lower().lstrip('.') or 'point_cloud')
        meta.setdefault('source_bytes',int(st.st_size));meta.setdefault('source_mtime_ns',int(getattr(st,'st_mtime_ns',int(st.st_mtime*1e9))))
        if index is not None:meta.setdefault('source_setup_index',int(index))
        return meta
    except Exception:
        if meta:remove_assets(meta)
        dest.unlink(missing_ok=True);dest.with_suffix('.attr32').unlink(missing_ok=True);Path(str(dest)+'.viewindex.npz').unlink(missing_ok=True)
        raise

def import_obj(source,work,progress=lambda s:None):
    """Triangle OBJ; float64 coordinates, uint32 topology; no Python mega-lists."""
    source=Path(source);work=asset_folder(work,'mesh');ident=uuid.uuid4().hex
    vp=work/(ident+'.vertices64');fp=work/(ident+'.faces32');cp=work/(ident+'.vcolour32');temp=None;meta=None
    name=source.name;obj=source;v=f=None
    try:
        if source.suffix.lower()=='.zip':
            with zipfile.ZipFile(source) as z:
                entries=[i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.obj')]
                if len(entries)!=1:raise ValueError('Choose an OBJ, or a ZIP containing exactly one OBJ. Native .3dr projects must be exported to OBJ in Cyclone 3DR.')
                member=entries[0];room(work,member.file_size*2);name=Path(member.filename).name
                temp=work/(ident+'.incoming.obj');obj=temp
                with z.open(member) as inp,temp.open('wb') as out:
                    while block:=inp.read(4*1024*1024):
                        progress('Extracting OBJ from ZIP…');out.write(block)
        elif source.suffix.lower()!='.obj':raise ValueError('Choose a triangular OBJ, or a ZIP containing one OBJ. Native .3dr files are not supported.')
        room(work,obj.stat().st_size)
        n=faces=coloured=0;line_no=0;highest=0;lowest=None;lo=np.full(3,np.inf);hi=-lo
        with obj.open('rb',buffering=4*1024*1024) as inp,vp.open('wb') as vertices,fp.open('wb') as topology,cp.open('wb') as colours:
            while lines:=list(islice(inp,50000)):
                progress(f'Importing model: {n:,} vertices / {faces:,} triangles');room(work)
                vr=[];fr=[];cr=[]
                for line in lines:
                    line_no+=1;tokens=line.split()
                    if not tokens:continue
                    if tokens[0]==b'v':
                        if len(tokens) not in (4,7):raise ValueError(f'OBJ line {line_no}: use XYZ vertices, optionally followed by normalized RGB (0–1).')
                        vr.append(tokens[1:4]);n+=1
                        if len(tokens)==7:
                            rgb=np.asarray(tokens[4:7],float)
                            if not np.isfinite(rgb).all() or np.any((rgb<0)|(rgb>1)):raise ValueError(f'OBJ line {line_no}: vertex RGB must be normalized to 0–1.')
                            cr.append(rgb*255);coloured+=1
                        else:cr.append((np.nan,np.nan,np.nan))
                    elif tokens[0]==b'f':
                        if len(tokens)!=4:raise ValueError(f'OBJ line {line_no}: export a triangulated mesh first.')
                        row=[int(t.split(b'/')[0]) for t in tokens[1:]]
                        row=[i-1 if i>0 else n+i if i<0 else -1 for i in row]
                        if min(row)<0 or max(row)>0xffffffff:raise ValueError(f'OBJ line {line_no}: invalid vertex index.')
                        fr.append(row);faces+=1
                if vr:
                    a=np.asarray(vr,dtype='<f8')
                    if not np.isfinite(a).all():raise ValueError('OBJ contains non-finite vertex coordinates.')
                    lo=np.minimum(lo,a.min(axis=0));hi=np.maximum(hi,a.max(axis=0));a.tofile(vertices)
                    np.asarray(cr,dtype='<f4').tofile(colours)
                if fr:
                    a=np.asarray(fr,dtype='<u4');highest=max(highest,int(a.max()));lowest=int(a.min()) if lowest is None else min(lowest,int(a.min()));a.tofile(topology)
        if not n or not faces or highest>=n:raise ValueError('OBJ has no valid mesh, or refers to missing vertices.')
        v=np.memmap(vp,dtype='<f8',mode='r',shape=(n,3));f=np.memmap(fp,dtype='<u4',mode='r',shape=(faces,3));degenerate=0
        for start in range(0,faces,100000):
            progress(f'Checking triangle geometry: {min(start+100000,faces):,} / {faces:,}')
            t=v[f[start:start+100000]];area=np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1)/2
            degenerate+=int((area<=1e-16).sum())
        v._mmap.close();f._mmap.close();v=f=None
        st=source.stat()
        meta={'name':name,'source_name':source.name,'source_path':str(source.resolve()),
              'source_format':source.suffix.lower().lstrip('.') or 'obj','source_bytes':int(st.st_size),
              'source_mtime_ns':int(getattr(st,'st_mtime_ns',int(st.st_mtime*1e9))),
              'vertices_file':str(vp),'faces_file':str(fp),'vertices':n,'triangles':faces,'minimum':lo.tolist(),'maximum':hi.tolist(),'degenerate_triangles':degenerate,'units':'Unconfirmed; original coordinate values preserved','quality':'Topology indices checked; no manifold, self-intersection or design-accuracy certification.'}
        if coloured:meta.update(vertex_colours_file=str(cp),coloured_vertices=coloured)
        else:cp.unlink(missing_ok=True)
        preview_cache(meta,work,progress);progress('Model imported. Full geometry retained; bounded display sample ready.')
        return meta
    except Exception:
        for mapping in (v,f):
            if mapping is not None:mapping._mmap.close()
        if meta:remove_assets(meta)
        vp.unlink(missing_ok=True);fp.unlink(missing_ok=True);cp.unlink(missing_ok=True)
        raise
    finally:
        if temp:temp.unlink(missing_ok=True)

def validate(meta,mesh=False):
    spec=[('vertices_file',meta.get('vertices',0),24),('faces_file',meta.get('triangles',0),12)] if mesh else [('file',meta.get('count',0),24)]
    if mesh and meta.get('vertex_colours_file'):spec.append(('vertex_colours_file',meta.get('vertices',0),12))
    if meta.get('attributes_file'):spec.append(('attributes_file',meta.get('count',0),16))
    if meta.get('mask_file'):spec.append(('mask_file',meta.get('count',0),1))
    # Older projects can have a 100k preview cache; it remains valid.
    if meta.get('preview_file'):spec.append(('preview_file',meta.get('preview_count',0),24))
    for key,n,width in spec:
        if type(n) is not int or n<1 or not meta.get(key) or Path(meta[key]).stat().st_size!=n*width:raise ValueError('Invalid or missing job cache: '+key)
    if meta.get('preview_file') and (type(meta.get('preview_stride')) is not int or meta['preview_stride']<1):raise ValueError('Invalid preview stride.')


def _box(value):
    if not value:return None
    try:
        lo=np.asarray(value['minimum'],dtype=float);hi=np.asarray(value['maximum'],dtype=float)
    except Exception:raise ValueError('Invalid 3D selection box.')
    if lo.shape!=(3,) or hi.shape!=(3,) or not np.isfinite([lo,hi]).all() or np.any(lo>=hi):raise ValueError('Invalid 3D selection box.')
    return lo,hi


def selection_spec(value):
    """Validate a persistent full-density selection specification.

    screen_polygon stores the camera used when the user drew the polygon so the
    selection stays fixed even if the 3D view is orbited afterwards.
    """
    if not isinstance(value,dict):raise ValueError('Select an area in the 3D view first.')
    mode=value.get('mode','box')
    clip={}
    if value.get('clip_box'):
        box=value['clip_box']
        if not isinstance(box,dict) or box.get('mode','box') not in ('box','oriented_box') or box.get('clip_box'):
            raise ValueError('Invalid limit box.')
        clip={'clip_box':selection_spec({**box,'invert':False})}
    if mode=='box':
        lo,hi=_box(value)
        return {'mode':'box','minimum':lo.tolist(),'maximum':hi.tolist(),'invert':bool(value.get('invert',False)),**clip}
    if mode=='oriented_box':
        try:
            centre=np.asarray(value['centre'],float);axes=np.asarray(value['axes'],float);half=np.asarray(value['half_size'],float)
        except Exception:raise ValueError('Invalid rotated limit box.')
        if centre.shape!=(3,) or axes.shape!=(3,3) or half.shape!=(3,) or not np.isfinite(centre).all() or not np.isfinite(axes).all() or not np.isfinite(half).all() or np.any(half<=0):
            raise ValueError('Invalid rotated limit box.')
        if not np.allclose(axes@axes.T,np.eye(3),rtol=0,atol=1e-6) or np.linalg.det(axes)<.999999:
            raise ValueError('Limit-box axes must be perpendicular unit vectors.')
        return {'mode':mode,'centre':centre.tolist(),'axes':axes.tolist(),'half_size':half.tolist(),'invert':bool(value.get('invert',False)),**clip}
    if mode not in ('screen_polygon','screen_brush'):raise ValueError('Unknown 3D selection type.')
    try:
        target=np.asarray(value['target'],float);right=np.asarray(value['right'],float);up=np.asarray(value['up'],float)
        forward=np.asarray(value.get('forward') or [0,0,0],float);scale=float(value['scale']);aspect=float(value['aspect']);polygon=np.asarray(value.get('polygon',[]),float)
        depth_min=float(value['depth_min']) if value.get('depth_min') is not None else -np.inf
        depth_max=float(value['depth_max']) if value.get('depth_max') is not None else np.inf
    except Exception:raise ValueError('Invalid screen selection.')
    if mode=='screen_brush':
        try:path=np.asarray(value['path'],float);radius=float(value['radius'])
        except Exception:raise ValueError('Invalid brush selection.')
        if target.shape!=(3,) or right.shape!=(3,) or up.shape!=(3,) or forward.shape!=(3,) or path.ndim!=2 or path.shape[1]!=2 or not 1<=len(path)<=512:raise ValueError('Invalid brush selection.')
        if not np.isfinite([*target,*right,*up,*forward,scale,aspect,radius,*path.ravel()]).all() or scale<=0 or aspect<=0 or not 0<radius<=2 or depth_min>=depth_max:raise ValueError('Invalid brush selection.')
        axes=np.asarray([right,up])
        if not np.allclose(axes@axes.T,np.eye(2),rtol=0,atol=1e-6):raise ValueError('Brush camera axes must be perpendicular unit vectors.')
        if np.isnan(depth_min) or np.isnan(depth_max):raise ValueError('Invalid brush depth range.')
        has_depth=bool(np.linalg.norm(forward)>0 and np.isfinite(depth_min) and np.isfinite(depth_max))
        if has_depth and not np.allclose(np.asarray([right,up,forward])@np.asarray([right,up,forward]).T,np.eye(3),rtol=0,atol=1e-6):raise ValueError('Brush depth axis must be perpendicular to the camera plane.')
        return {'mode':mode,'target':target.tolist(),'right':right.tolist(),'up':up.tolist(),'forward':forward.tolist() if has_depth else None,'scale':scale,'aspect':aspect,'path':path.tolist(),'radius':radius,'depth_min':depth_min if has_depth else None,'depth_max':depth_max if has_depth else None,'invert':bool(value.get('invert',False)),**clip}
    if target.shape!=(3,) or right.shape!=(3,) or up.shape!=(3,) or forward.shape!=(3,) or polygon.ndim!=2 or polygon.shape[1]!=2 or len(polygon)<3:raise ValueError('Invalid screen selection.')
    finite=[*target,*right,*up,scale,aspect,*polygon.ravel()]
    if not np.isfinite(finite).all() or scale<=0 or aspect<=0 or depth_min>=depth_max:raise ValueError('Invalid screen selection.')
    if len(polygon)>512:raise ValueError('Selection polygon has too many vertices. Draw a simpler lasso.')
    has_depth=bool(np.linalg.norm(forward)>0 and np.isfinite(depth_min) and np.isfinite(depth_max))
    return {'mode':'screen_polygon','target':target.tolist(),'right':right.tolist(),'up':up.tolist(),'forward':forward.tolist() if has_depth else None,'scale':scale,'aspect':aspect,'polygon':polygon.tolist(),'depth_min':depth_min if has_depth else None,'depth_max':depth_max if has_depth else None,'invert':bool(value.get('invert',False)),**clip}


def _poly_mask(x,y,polygon):
    """Vectorised 2D point-in-polygon test (ray casting)."""
    poly=np.asarray(polygon,dtype=float);out=np.zeros(len(x),dtype=bool)
    lo=poly.min(0);hi=poly.max(0);ids=np.flatnonzero((x>=lo[0])&(x<=hi[0])&(y>=lo[1])&(y<=hi[1]))
    if not len(ids):return out
    x=x[ids];y=y[ids];inside=np.zeros(len(ids),dtype=bool);j=len(poly)-1
    for i in range(len(poly)):
        xi,yi=poly[i];xj,yj=poly[j]
        cross=((yi>y)!=(yj>y)) & (x < (xj-xi)*(y-yi)/(yj-yi + (1e-30 if yj==yi else 0.0)) + xi)
        inside ^= cross;j=i
    out[ids]=inside;return out


def _brush_mask(x,y,path,radius):
    """Exact union of closed disks/capsules around a bounded screen stroke."""
    path=np.asarray(path,float);out=np.zeros(len(x),bool);r2=radius*radius
    lo=path.min(0)-radius;hi=path.max(0)+radius
    candidates=np.flatnonzero((x>=lo[0])&(x<=hi[0])&(y>=lo[1])&(y<=hi[1]))
    if not len(candidates):return out
    xx=x[candidates];yy=y[candidates];hit=np.zeros(len(candidates),bool)
    for a,b in zip(path,np.r_[path[1:],path[-1:]]):
        use=np.flatnonzero(~hit&(xx>=min(a[0],b[0])-radius)&(xx<=max(a[0],b[0])+radius)&(yy>=min(a[1],b[1])-radius)&(yy<=max(a[1],b[1])+radius))
        if not len(use):continue
        dx,dy=b-a;length=dx*dx+dy*dy
        t=np.clip(((xx[use]-a[0])*dx+(yy[use]-a[1])*dy)/length,0,1) if length else np.zeros(len(use))
        hit[use]=((xx[use]-a[0]-t*dx)**2+(yy[use]-a[1]-t*dy)**2)<=r2+1e-14
    out[candidates]=hit;return out


def selection_mask(points,spec):
    return _selection_mask_validated(np.asarray(points),selection_spec(spec))


def _selection_mask_validated(q,spec):
    """Exact predicate for an already checked immutable selection description."""
    if spec['mode']=='box':
        lo=np.asarray(spec['minimum']);hi=np.asarray(spec['maximum']);use=((q>=lo)&(q<=hi)).all(axis=1)
    elif spec['mode']=='oriented_box':
        local=(q-np.asarray(spec['centre']))@np.asarray(spec['axes']).T
        use=(np.abs(local)<=np.asarray(spec['half_size'])+1e-8).all(axis=1)
    else:
        t=np.asarray(spec['target']);r=np.asarray(spec['right']);u=np.asarray(spec['up']);d=q-t
        x=(d@r)/spec['scale'];y=(d@u)/spec['scale']
        use=_brush_mask(x,y,spec['path'],spec['radius']) if spec['mode']=='screen_brush' else _poly_mask(x,y*spec['aspect'],spec['polygon'])
        if spec.get('forward') is not None:
            z=d@np.asarray(spec['forward']);use&=(z>=spec['depth_min'])&(z<=spec['depth_max'])
    if spec.get('invert'):use=~use
    if spec.get('clip_box'):
        use&=_selection_mask_validated(q,spec['clip_box'])
    # Invalid coordinates are never measured or visible points, including for
    # an inverted selection. This also agrees with conservative index boxes.
    use&=np.isfinite(q).all(1)
    return use


def selection_ids(meta,spec,progress=lambda _:None,*,visibility=None):
    """Yield retained full-density point ids inside the exact 3D/screen selection."""
    from contextlib import ExitStack
    import preview_index,registration_pose
    spec=selection_spec(spec);total=int(meta['count']);block=max(1,min(pointio.CHUNK,100000))
    visible=visibility or {}
    limited=selection_spec({'mode':'box',**visible['limit_box']}) if visible.get('limit_box') and visible.get('limit_hide_outside',True) else None
    isolated=selection_spec(visible['isolate_box']) if visible.get('isolate_box') else None
    hidden=[selection_spec(area) for area in visible.get('hidden_boxes',[])]
    if not preview_index.selection_possible(meta,spec):return
    R,t=registration_pose.get(meta);positioned=not registration_pose.is_identity(meta)
    # Use cached conservative block boxes when available. A first selection
    # builds those boxes during its exact full-density scan, avoiding the old
    # full indexing read followed by a second coordinate read.
    source_key=preview_index._key(meta)
    boxes=preview_index.cached_bounds(meta,source_key)
    if boxes is not None:boxes=preview_index.world_boxes(meta,boxes)
    builder=preview_index.BoundsBuilder(meta,source_key) if boxes is None else None
    def spans():
        if boxes is None:
            yield 0,total
            return
        first=last=None
        for start,stop in preview_index.selection_blocks(meta,spec,boxes=boxes):
            if last==start:last=stop
            else:
                if first is not None:yield first,last
                first,last=start,stop
        if first is not None:yield first,last
    with ExitStack() as stack:
        source=stack.enter_context(Path(meta['file']).open('rb'))
        mask=stack.enter_context(Path(meta['mask_file']).open('rb')) if meta.get('mask_file') else None
        progress('Selecting retained points · '+('building reusable index in the same pass' if builder else 'using cached spatial index'))
        for start,stop in spans():
            for offset in range(start,stop,block):
                count=min(block,stop-offset);source.seek(offset*24);q=np.fromfile(source,'<f8',count=count*3)
                if q.size!=count*3:raise ValueError('The source coordinate cache is incomplete.')
                q=q.reshape(count,3)
                if builder is not None:
                    builder.add(offset,q)
                    # Newly collected boxes include every point in this chunk
                    # (and possibly an earlier part of its first block). They
                    # may conservatively skip an entirely distant chunk before
                    # expensive lasso/brush predicates or a mask read.
                    first=offset//preview_index.BLOCK;last=(offset+count+preview_index.BLOCK-1)//preview_index.BLOCK
                    if not preview_index.selection_box_mask(preview_index.world_boxes(meta,builder.boxes[first:last]),spec).any():
                        progress(f'Selecting retained points: {offset+count:,} / {total:,}')
                        continue
                world=q@R.T+t if positioned else q
                use=_selection_mask_validated(world,spec)
                if limited is not None:use&=_selection_mask_validated(world,limited)
                if isolated is not None:use&=_selection_mask_validated(world,isolated)
                for area in hidden:use&=~_selection_mask_validated(world,area)
                if mask is not None:
                    mask.seek(offset);kept=np.fromfile(mask,'u1',count=count)
                    if len(kept)!=count:raise ValueError('The source exclusion mask is incomplete.')
                    use&=kept==0
                ids=np.flatnonzero(use).astype('<i8')+offset
                progress(f'Selecting retained points: {offset+count:,} / {total:,}')
                if len(ids):yield ids
    if builder is not None:
        builder.finish()
    elif preview_index._key(meta)!=source_key:
        raise ValueError('The scan changed while selecting points. Select the area again.')


def box_ids(meta,box,include_hidden=True):
    # Backward-compatible name used by older saved projects/tests.
    yield from selection_ids(meta,{'mode':'box',**box})


def selection_count(meta,spec):
    return sum(len(ids) for ids in selection_ids(meta,spec))


def selection_file(meta,spec,destination,progress=lambda s:None):
    spec=selection_spec(spec);total=0
    with Path(destination).open('wb') as out:
        for ids in selection_ids(meta,spec,progress):
            ids.tofile(out);total+=len(ids);progress(f'Selecting full-density points: {total:,}')
    if not total:raise ValueError('The selected area contains no retained full-density points.')
    return {'file':str(destination),'count':total,'spec':{'job_selection':spec}}


def extract_selection(meta,spec,work,name,progress=lambda s:None):
    """Create an independent disk-backed scan from an exact selection."""
    spec=selection_spec(spec);count=selection_count(meta,spec)
    return extract_ids(meta,selection_ids(meta,spec),count,work,name,progress)


def extract_ids(meta,batches,count,work,name,progress=lambda s:None):
    count=int(count)
    if count<=0:raise ValueError('The selected area contains no retained full-density points.')
    room(work,count*40)
    work=asset_folder(work,'derived')
    dest=work/(uuid.uuid4().hex+'.xyz64');attr=dest.with_suffix('.attr32')
    has_attributes=bool(meta.get('attributes_file'));preview=None;iterator=iter(batches)
    lo=np.full(3,np.inf);hi=-lo;written=0;counts=np.zeros(4,dtype='i8');amin=np.full(4,np.inf);amax=-amin
    try:
        with dest.open('wb') as out, attr.open('wb') as ao:
            for source_ids in iterator:
                # Sorting only the bounded gather improves source-window reuse.
                # Restore original order afterwards, including duplicate IDs.
                for start in range(0,len(source_ids),100000):
                    ids=np.asarray(source_ids[start:start+100000])
                    if ids.ndim!=1 or not np.issubdtype(ids.dtype,np.integer):raise ValueError('Selected point IDs must be a one-dimensional integer sequence.')
                    if (ids<0).any() or (ids>=meta['count']).any():raise ValueError('Selected point IDs are outside the source scan.')
                    ids=np.asarray(ids,dtype=np.int64)
                    if written+len(ids)>count:raise ValueError('The selected point count changed during extraction.')
                    order=np.argsort(ids,kind='stable');ordered=ids[order]
                    gathered=_display_rows(meta['file'],meta['count'],ordered,3,'<f8')
                    q=np.empty_like(gathered);q[order]=gathered
                    if not np.isfinite(q).all():raise ValueError('The selected source coordinates contain nonfinite values.')
                    q.tofile(out);lo=np.minimum(lo,q.min(0));hi=np.maximum(hi,q.max(0))
                    if has_attributes:
                        gathered_attrs=_display_rows(meta['attributes_file'],meta['count'],ordered,4,'<f4')
                        b=np.empty_like(gathered_attrs);b[order]=gathered_attrs;b.tofile(ao)
                        for j in range(4):
                            v=b[np.isfinite(b[:,j]),j];counts[j]+=len(v)
                            if len(v):amin[j]=min(amin[j],float(v.min()));amax[j]=max(amax[j],float(v.max()))
                    written+=len(q);progress(f'Separating selected points: {written:,} / {count:,}')
        if written!=count:raise ValueError('The selected point count changed during extraction.')
        result={'name':name,'file':str(dest),'count':written,'minimum':lo.tolist(),'maximum':hi.tolist(),'invalid_points_skipped':0}
        # These are unchanged source-frame measurements. Keep their virtual
        # pose so separated/restored/cleaned copies stay at the same registered
        # position without applying a second transform to the stored cache.
        if any(k in meta for k in ('registration_rotation','registration_translation')):
            import registration_pose
            registration_pose.set_pose(result,*registration_pose.get(meta))
        if meta.get('experimental_reconstruction'):
            from rtc360_optics import retain_notice
            retain_notice(result,meta)
        if has_attributes and counts.any():
            result.update(attributes_file=str(attr),attribute_counts=counts.tolist(),attribute_min=[float(v) if np.isfinite(v) else None for v in amin],attribute_max=[float(v) if np.isfinite(v) else None for v in amax])
        else:attr.unlink(missing_ok=True)
        # Preserve the existing strided preview semantics without mapping the
        # entire newly created output back into the process.
        stride=max(1,math.ceil(written/250000));preview_ids=np.arange(0,written,stride,dtype=np.int64)
        sample=_display_rows(dest,written,preview_ids,3,'<f8')
        preview=Path(work)/(uuid.uuid4().hex+'.preview64');sample.tofile(preview)
        result.update(preview_file=str(preview),preview_count=len(sample),preview_stride=stride)
        return result
    except Exception:
        dest.unlink(missing_ok=True);attr.unlink(missing_ok=True)
        if preview is not None:preview.unlink(missing_ok=True)
        raise
    finally:
        close=getattr(iterator,'close',None)
        if close is not None:close()


def extract_box(meta,box,work,name,progress=lambda s:None):
    return extract_selection(meta,{'mode':'box',**box},work,name,progress)


def _colour_ramp(values):
    values=np.clip(np.asarray(values,dtype=np.float32),0,1)
    anchors=np.array([0,.25,.5,.75,1],dtype=np.float32)
    palette=np.array([[.08,.16,.65],[0,.72,1],[.08,.88,.38],[1,.84,.05],[.92,.12,.04]],dtype=np.float32)
    return np.column_stack([np.interp(values,anchors,palette[:,j]) for j in range(3)]).astype(np.float32)


from functools import lru_cache

def _gather_display_rows(path,count,ids,width=3,dtype='<f8',progress=None,cancel=None):
    """Gather sorted preview indices through <=64 MiB file mappings.

    A sparse preview of a billion-point cache must not leave gigabytes of
    randomly touched mmap pages resident in the process.
    """
    ids=np.asarray(ids,dtype=np.int64);out=np.empty((len(ids),width),dtype=dtype)
    if not len(ids):return out
    row_bytes=np.dtype(dtype).itemsize*width;window=max(1,(64*1024*1024)//row_bytes)
    buckets=ids//window;edges=np.r_[0,np.flatnonzero(np.diff(buckets))+1,len(ids)]
    for a,b in zip(edges[:-1],edges[1:]):
        if cancel is not None and cancel():raise InterruptedError('The previous viewport request was cancelled.')
        if progress:progress(f'Preparing reusable overview · {int(a):,} / {len(ids):,} display samples')
        start=int(buckets[a])*window;length=min(int(count)-start,window)
        mapped=np.memmap(path,dtype,'r',offset=start*row_bytes,shape=(length,width))
        try:out[a:b]=mapped[ids[a:b]-start]
        finally:mapped._mmap.close()
    return out


def _display_rows(path,count,ids,width=3,dtype='<f8',progress=None,cancel=None):
    """Read measured XYZ/attribute rows through bounded windows."""
    return _gather_display_rows(path,count,ids,width,dtype,progress,cancel)

@lru_cache(maxsize=32)
def _intensity_window_cached(path,count,size,modified):
    ids=np.linspace(0,count-1,min(count,100000),dtype=np.int64)
    values=_display_rows(path,count,ids,4,'<f4')[:,0];values=values[np.isfinite(values)]
    return tuple(np.percentile(values,[2,98])) if len(values) else (0.,1.)

def intensity_window(meta):
    p=Path(meta['attributes_file']);stat=p.stat()
    return _intensity_window_cached(str(p),int(meta['count']),stat.st_size,stat.st_mtime_ns)


def _colour_from_attributes(meta,ids,fallback,mode='rgb',points=None,z_range=None,attributes=None,intensity_range=None,cancel=None):
    colours=np.tile(np.asarray(fallback,dtype=np.float32),(len(ids),1))
    if mode=='rgb' and meta.get('source_format')=='hspca':mode='intensity-gray'
    if mode=='bundle' or not len(ids):return colours
    if mode=='elevation' and points is not None:
        lo,hi=z_range if z_range is not None else (float(np.min(points[:,2])),float(np.max(points[:,2])))
        t=(points[:,2]-lo)/max(hi-lo,1e-9);return _colour_ramp(t)
    if not meta.get('attributes_file'):return colours
    try:
        attrs=attributes if attributes is not None else _display_rows(meta['attributes_file'],meta['count'],ids,4,'<f4',cancel=cancel)
        missing_rgb=None
        if mode=='rgb':
            rgb=attrs[:,1:4];valid=np.isfinite(rgb).all(axis=1)
            if valid.any():
                mx=max(float(np.nanmax(rgb[valid])),1.0);scale=1.0 if mx<=1.01 else 255.0 if mx<=255.5 else 65535.0
                colours[valid]=np.clip(rgb[valid]/scale,0,1)
            if valid.all():return colours
            # Raw RTC360 and intensity-only E57 have no per-point RGB. Use the
            # measured intensity for missing colours, preserving valid RGB.
            missing_rgb=~valid;mode='intensity-gray'
        values=attrs[:,0];valid=np.isfinite(values)
        if missing_rgb is not None:valid &= missing_rgb
        if not valid.any():return colours
        lo,hi=intensity_range if intensity_range is not None else intensity_window(meta);t=np.zeros(len(values),dtype=np.float32);t[valid]=np.clip((values[valid]-lo)/max(float(hi-lo),1e-9),0,1)
        if mode=='intensity-gray':colours[valid]=(.08+.92*t[valid])[:,None]
        elif mode=='intensity-hue':colours[valid]=_colour_ramp(t[valid])
    except InterruptedError:raise
    except Exception:pass
    return colours


def _display_sample_ids(count,budget):
    """One deterministic original point per interval, without raster aliasing."""
    from checker_evidence import priority_keys
    count=int(count);budget=min(count,max(0,int(budget)))
    if not budget:return np.empty(0,dtype=np.int64)
    index=np.arange(budget,dtype=np.int64)
    start=index*count//budget;stop=(index+1)*count//budget
    jitter=(priority_keys(index)%((stop-start).astype(np.uint64))).astype(np.int64)
    return start+jitter


def _source_overview(meta,build=False,progress=None,cancel=None,maximum=250000):
    """Read a bounded measured source sample, independent of display edits.

    Opening an already cached project, changing its pose or deleting interior
    points does not need another scattered read across the multi-GB XYZ and
    attributes. The stored sample never supplies analytical input.
    """
    import preview_store
    maximum=max(1,min(1000000,int(maximum)))
    saved=preview_store.read_overview(meta,maximum)
    if saved is not None or not build:return saved
    path=preview_store.overview_path(meta,maximum)
    ids=_display_sample_ids(meta['count'],maximum)
    points=_display_rows(meta['file'],meta['count'],ids,progress=progress,cancel=cancel)
    attrs=_display_rows(meta['attributes_file'],meta['count'],ids,4,'<f4',progress=progress,cancel=cancel) if meta.get('attributes_file') else None
    preview_store.write_overview(meta,ids,points,attrs,expected_path=path,maximum=maximum)
    if maximum>preview_store.OVERVIEW_POINTS and preview_store.read_overview(meta) is None:
        # An older project gains its fast, correctly coloured first view from
        # these already-read original rows, without another source-file pass.
        take=np.linspace(0,len(ids)-1,min(len(ids),preview_store.OVERVIEW_POINTS),dtype=np.int64)
        preview_store.write_overview(meta,ids[take],points[take],attrs[take] if attrs is not None else None)
    return ids,points,attrs


def _item_sample(item,per,origin,colour,mode='rgb',z_range=None,quick=False,cancel=None):
    colour,mode=__import__('scan_appearance').resolve(item,colour,mode)
    import preview_store
    cache=None
    try:
        cache=preview_store.cache_path(item,per,origin,colour,mode,z_range,quick)
        saved=preview_store.read(cache)
        if saved is not None:return saved
    except OSError:pass
    if cancel is not None and cancel():raise InterruptedError('The previous viewport request was cancelled.')
    packed=_item_sample_uncached(item,per,origin,colour,mode,z_range,quick,cancel=cancel)
    if cancel is not None and cancel():raise InterruptedError('The previous viewport request was cancelled.')
    if cache is not None and cache==preview_store.cache_path(item,per,origin,colour,mode,z_range,quick):preview_store.write(cache,packed)
    return packed


def _item_sample_uncached(item,per,origin,colour,mode='rgb',z_range=None,quick=False,cancel=None):
    colour,mode=__import__('scan_appearance').resolve(item,colour,mode)
    m=item['meta'];is_scan=item['kind']=='scan';n=m['count'] if is_scan else m['vertices'];key='file' if is_scan else 'vertices_file'
    attrs=None;intensity_range=None
    retained=__import__('scan_edits').retained_count(m,cancel=cancel) if is_scan and not quick else n
    overview_budget=250000 if quick else max(250000,per)
    overview=_source_overview(m,build=not quick and retained>=n*.25,cancel=cancel,maximum=overview_budget) if is_scan else None
    # A heavily isolated/deleted scan still gets a sample of its retained rows;
    # ordinary edits can filter the bounded overview without rescanning a mask.
    cached=bool(overview is not None and (quick or per<=len(overview[0]) and retained>=n*.25))
    coarse=bool(not cached and quick and m.get('preview_file') and m.get('preview_count') and m.get('preview_stride') and m.get('source_format')!='rtc360_raw_experimental')
    # Legacy projects may have no reusable measured-colour overview. Keep the
    # first response small rather than touching a million scattered disk pages.
    # Ordinary refinement still honours the full requested display budget.
    first_budget=min(per,20000) if quick and not cached else per
    if cached:
        ids,q,attrs=overview
        if attrs is not None:
            values=attrs[:,0];values=values[np.isfinite(values)]
            if len(values):intensity_range=tuple(np.percentile(values,[2,98]))
    elif coarse:
        take=_display_sample_ids(m['preview_count'],first_budget)
        ids=take*int(m['preview_stride'])
        q=_display_rows(m['preview_file'],m['preview_count'],take,cancel=cancel)
    elif is_scan and m.get('mask_file') and not quick:
        wanted=_display_sample_ids(retained,per);offset=0;parts=[]
        with Path(m['mask_file']).open('rb') as excluded:
            for start in range(0,n,pointio.CHUNK):
                if cancel is not None and cancel():raise InterruptedError('The previous viewport request was cancelled.')
                flags=np.fromfile(excluded,'u1',count=min(pointio.CHUNK,n-start))
                if len(flags)!=min(pointio.CHUNK,n-start):raise ValueError('Incomplete point deletion mask.')
                available=np.flatnonzero(flags==0)+start
                lo,hi=np.searchsorted(wanted,[offset,offset+len(available)])
                parts.append(available[wanted[lo:hi]-offset]);offset+=len(available)
        ids=np.concatenate(parts) if parts else np.empty(0,dtype=np.int64)
    else:
        ids=_display_sample_ids(n,first_budget)
    if not coarse and not cached:q=_display_rows(m[key],n,ids,cancel=cancel)
    if is_scan:
        # Full cache remains in its stored frame; render/edit coordinates are
        # mapped to the registered world frame on demand.
        import registration_pose
        q=registration_pose.points(m,q)
        use=np.ones(len(ids),bool)
        if m.get('mask_file'):
            use&=_gather_display_rows(m['mask_file'],n,ids,1,'u1',cancel=cancel).ravel()==0
        if item.get('limit_box') and item.get('limit_hide_outside',True):use&=selection_mask(q,{'mode':'box',**item['limit_box']})
        isolated=item.get('isolate_box')
        if isolated is not None:use&=selection_mask(q,isolated)
        for hidden in item.get('hidden_boxes',[]):
            use&=~selection_mask(q,hidden)
        ids=ids[use];q=q[use]
        if attrs is not None:attrs=attrs[use]
        elif quick and m.get('attributes_file'):
            attrs=_display_rows(m['attributes_file'],n,ids,4,'<f4',cancel=cancel)
            values=attrs[:,0];values=values[np.isfinite(values)]
            if len(values):intensity_range=tuple(np.percentile(values,[2,98]))
        c=_colour_from_attributes(m,ids,colour,mode,q,z_range,attrs,intensity_range,cancel=cancel)
    else:c=np.tile(np.asarray(colour,dtype=np.float32),(len(q),1))
    if len(q)>per:
        take=np.linspace(0,len(q)-1,per,dtype=np.int64);q=q[take];c=c[take]
    packed=np.empty((len(q),6),dtype='<f4');packed[:,:3]=(q-origin).astype(np.float32);packed[:,3:]=c
    return packed


def preview_binary(items,detail=25,mode='rgb',view=None,owners=False,quick=False,cancel=None):
    """Binary WebGL preview. 1–100 is a user-facing display-detail percentage.

    100% means the viewer's safe maximum detail, not 100% of a multi-gigabyte scan.
    Full-density data remains on disk for processing.
    """
    detail=max(1,min(100,int(float(detail))));mode=mode if mode in ('rgb','intensity-gray','intensity-hue','bundle','elevation') else 'rgb';visible=[x for x in items if x.get('visible',True)]
    if not visible:
        header=struct.pack('<4sI3d3d',b'TSPV',1,0.,0.,0.,0.,0.,0.);return header,{'sample_count':0,'source_points':0,'source_vertices':0,'detail':detail,'rgb':False}
    import registration_pose
    bounds=[]
    for x in visible:
        if x['kind']=='scan':bounds.append(registration_pose.world_bounds(x['meta']))
        else:bounds.append((np.asarray(x['meta']['minimum'],float),np.asarray(x['meta']['maximum'],float)))
    low=np.min([b[0] for b in bounds],axis=0);high=np.max([b[1] for b in bounds],axis=0);origin=(low+high)/2
    # Non-linear slider: useful control at the low end, up to 1,000,000 displayed points.
    display_properties={x['id']:__import__('scan_edits').display_properties(x['meta']) for x in visible if x['kind']=='scan'}
    budget=int(20000+(detail/100.0)**1.65*980000);source_points=sum(p['count'] for p in display_properties.values());source_vertices=sum(x['meta'].get('vertices',0) for x in visible if x['kind']=='mesh')
    budget=min(budget,max(1,source_points+source_vertices));per=max(1,budget//len(visible));colours=[[.22,.73,.81],[.49,.76,.39],[.66,.48,.86],[.89,.52,.43],[.38,.55,.90],[.84,.68,.28]]
    chunks=[];rgb=False;ranges=[];offset=0
    for i,item in enumerate(visible):
        if cancel is not None and cancel():raise InterruptedError('The previous viewport request was cancelled.')
        first=len(chunks)
        if item['kind']=='scan':
            fallback=colours[i%len(colours)];rgb=rgb or bool(item['meta'].get('attributes_file') and (item['meta'].get('attribute_counts') or [0,0,0,0])[1:4] and min((item['meta'].get('attribute_counts') or [0,0,0,0])[1:4])>0)
        else:fallback=[.92,.70,.29]
        if item['kind']=='scan' and display_properties[item['id']]['count_exact'] and not display_properties[item['id']]['count']:
            chunks.append(np.empty((0,6),dtype='<f4'))
        elif view is not None and item['kind']=='scan':
            from detail_preview import sample
            base=max(1,per//5)
            if base:chunks.append(_item_sample(item,base,origin,fallback,mode,(float(low[2]),float(high[2])),quick=quick,cancel=cancel))
            chunks.append(sample(item,per-base,origin,fallback,mode,(float(low[2]),float(high[2])),view,quick=quick,cancel=cancel))
        elif item['kind']=='mesh':
            # Vertices support fitting/picking only; triangle-preview statuses
            # report a broken model independently without losing other models.
            try:
                if Path(item['meta']['vertices_file']).stat().st_size!=int(item['meta']['vertices'])*24:
                    raise ValueError('Incomplete mesh vertex cache.')
                sample=_item_sample(item,per,origin,fallback,mode,(float(low[2]),float(high[2])),quick=quick,cancel=cancel)
                if not np.isfinite(sample[:,:3]).all():raise ValueError('Invalid mesh vertex coordinates.')
                chunks.append(sample)
            except InterruptedError:raise
            except (ValueError,OSError,MemoryError):chunks.append(np.empty((0,6),dtype='<f4'))
        else:chunks.append(_item_sample(item,per,origin,fallback,mode,(float(low[2]),float(high[2])),quick=quick,cancel=cancel))
        length=sum(len(c) for c in chunks[first:]);ranges.append([offset,offset+length,item['id']]);offset+=length
    packed=np.concatenate(chunks,axis=0) if chunks else np.empty((0,6),dtype='<f4')
    # Refinement may have completed the shared mask-only count while sampling.
    # Report the latest completeness without recalculating analytical bounds.
    display_properties={x['id']:__import__('scan_edits').display_properties(x['meta']) for x in visible if x['kind']=='scan'}
    source_points=sum(p['count'] for p in display_properties.values())
    header=struct.pack('<4sI3d3d',b'TSPV',2 if owners else 1,*origin.tolist(),float(len(packed)),float(source_points),float(source_vertices))
    tail=__import__('json').dumps(ranges,separators=(',',':')).encode() if owners else b''
    return header+packed.tobytes(order='C')+tail,{'sample_count':len(packed),'source_points':source_points,'source_vertices':source_vertices,'detail':detail,'rgb':rgb,'budget':budget,'count_exact':all(p['count_exact'] for p in display_properties.values())}


def preview(items,budget=100000):
    """Compatibility JSON preview for tests/older clients.

    Unlike the percentage-driven WebGL endpoint, callers of this legacy helper
    expect ``budget`` to be a hard upper bound. Keep that behaviour so older
    code never receives more points than it requested.
    """
    budget=max(1,int(budget));visible=[x for x in items if x.get('visible',True)]
    if not visible:return {'origin':[0.,0.,0.],'points':[],'sample_count':0,'source_points':0,'source_vertices':0,'detail':0,'rgb':False,'budget':budget}
    import registration_pose
    bounds=[]
    for x in visible:
        if x['kind']=='scan':bounds.append(registration_pose.world_bounds(x['meta']))
        else:bounds.append((np.asarray(x['meta']['minimum'],float),np.asarray(x['meta']['maximum'],float)))
    low=np.min([b[0] for b in bounds],axis=0);high=np.max([b[1] for b in bounds],axis=0);origin=(low+high)/2
    source_points=sum(__import__('scan_edits').retained_count(x['meta']) for x in visible if x['kind']=='scan');source_vertices=sum(x['meta'].get('vertices',0) for x in visible if x['kind']=='mesh')
    per=max(1,budget//len(visible));colours=[[.22,.73,.81],[.49,.76,.39],[.66,.48,.86],[.89,.52,.43],[.38,.55,.90],[.84,.68,.28]]
    chunks=[];rgb=False;ranges=[];offset=0
    for i,item in enumerate(visible):
        first=len(chunks)
        if item['kind']=='scan':
            fallback=colours[i%len(colours)];counts=item['meta'].get('attribute_counts') or [0,0,0,0];rgb=rgb or bool(item['meta'].get('attributes_file') and len(counts)>=4 and min(counts[1:4])>0)
        else:fallback=[.92,.70,.29]
        chunks.append(_item_sample(item,per,origin,fallback))
    packed=np.concatenate(chunks,axis=0) if chunks else np.empty((0,6),dtype='<f4')
    if len(packed)>budget:packed=packed[:budget]
    return {'origin':origin.tolist(),'points':packed.tolist(),'sample_count':len(packed),'source_points':source_points,'source_vertices':source_vertices,'detail':None,'rgb':rgb,'budget':budget}



def mesh_preview_binary(items, budget=200000):
    """Full faces or connected simplification, with independent per-model results.

    Version 2 retains the 40-byte TSMF header and expanded triangle buffer, then
    appends UTF-8 JSON counts/statuses. A failed model never hides another model
    or substitutes a point cloud for its faces. Original files stay untouched.
    """
    import mesh_preview
    import json
    visible=[x for x in items if x.get('visible',True)]
    meshes=[x for x in visible if x['kind']=='mesh']
    origin=(np.min([x['meta']['minimum'] for x in visible],axis=0)+np.max([x['meta']['maximum'] for x in visible],axis=0))/2 if visible else np.zeros(3)
    budget=max(1,min(300000,int(budget)));total=sum(int(x['meta']['triangles']) for x in meshes)
    chunks=[];results=[];allocation_error=None
    try:quotas=mesh_preview.allocate([x['meta']['triangles'] for x in meshes],budget)
    except ValueError as error:
        allocation_error=str(error);quotas=np.zeros(len(meshes),dtype=np.int64)
    for item,per in zip(meshes,quotas):
        m=item['meta'];n=int(m['triangles'])
        status={'id':item['id'],'name':str(item.get('name') or m.get('name') or 'Mesh')[:2048],
                'state':'error','displayed_triangles':0,'total_triangles':n,'simplified':False,'error':''}
        try:
            if not n:raise ValueError('This model has no triangle faces. Create a surface mesh from the scan or import a model containing faces.')
            if allocation_error:raise ValueError(allocation_error)
            if not per:raise ValueError('No solid preview capacity remains for this model. Hide other models, then retry.')
            v,faces=mesh_preview.geometry(m,per)
            if not len(faces):raise ValueError('This model has no usable triangle faces. Create a new surface mesh from the scan.')
            q=np.asarray(v[faces])-origin
            normals=np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]);normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-15)
            light=np.array([.3,.5,.8]);light/=np.linalg.norm(light)
            shade=.35+.65*np.abs(normals@light)
            rgb=shade[:,None]*np.array([.83,.70,.43])
            if m.get('face_flags_file') and len(faces)==n:
                flags=np.fromfile(m['face_flags_file'],'u1').astype(bool)
                if len(flags)==len(rgb):rgb[flags]=shade[flags,None]*np.array([1.,.38,.12])
            packed=np.empty((len(q),3,6),dtype='<f4');packed[:,:,:3]=q;packed[:,:,3:]=rgb[:,None,:]
            if not np.isfinite(packed).all():raise ValueError('The model lies outside the supported display coordinate range. Check its coordinate system.')
            chunks.append(packed.reshape(-1,6))
            status.update(state='ready',displayed_triangles=len(faces),simplified=len(faces)<n)
        except (ValueError,RuntimeError,OSError,MemoryError) as error:
            status['error']=str(error)[:4096] or 'The connected surface preview could not be prepared. Hide other models, then retry.'
        results.append(status)
    values=np.concatenate(chunks) if chunks else np.empty((0,6),dtype='<f4')
    metadata={'displayed_triangles':len(values)//3,'total_triangles':total,'items':results}
    tail=json.dumps(metadata,separators=(',',':')).encode('utf8')
    if len(tail)>4*1024*1024:raise ValueError('Too many visible models for a solid preview. Hide some models, then retry. Full geometry is retained.')
    return struct.pack('<4sI3dII',b'TSMF',2,*origin.tolist(),len(values)//3,min(total,0xffffffff))+values.tobytes()+tail


def export_mesh_obj(meta, path):
    """Export every original-coordinate triangle, retaining inferred-face groups.

    Owned 100k-row reads avoid whole-mesh mappings. Seventeen significant digits
    preserve the saved float64 coordinates when an OBJ reader parses them back.
    """
    from contextlib import ExitStack
    nv,nf=int(meta['vertices']),int(meta['triangles'])
    if nv<0 or nf<0:raise ValueError('Invalid mesh record counts.')
    for key,count,width in (('vertices_file',nv,24),('faces_file',nf,12)):
        if Path(meta[key]).stat().st_size!=count*width:raise ValueError('The mesh cache is incomplete or has an unexpected length.')
    flags_path=meta.get('face_flags_file')
    if flags_path and Path(flags_path).stat().st_size!=nf:raise ValueError('The inferred-face flags do not match this mesh.')
    destination=Path(path);temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    inferred=0
    try:
        with ExitStack() as files:
            vertices=files.enter_context(open(meta['vertices_file'],'rb'));faces=files.enter_context(open(meta['faces_file'],'rb'))
            flags=files.enter_context(open(flags_path,'rb')) if flags_path else None
            stream=files.enter_context(temporary.open('w',encoding='utf8',newline='\n'))
            units=' '.join(str(meta.get('units','Unconfirmed source coordinate units')).split())
            stream.write('# Tunnel Studio triangle mesh; original coordinate values preserved\n# Source units: '+units+'\n# Face groups distinguish original_surface from inferred_repair geometry.\n')
            for start in range(0,nv,100000):
                count=min(100000,nv-start);block=np.fromfile(vertices,'<f8',count=count*3)
                if len(block)!=count*3 or not np.isfinite(block).all():raise ValueError('The vertex cache changed or contains non-finite coordinates.')
                for vertex in block.reshape(-1,3):stream.write('v %.17g %.17g %.17g\n'%tuple(vertex))
            group=None
            for start in range(0,nf,100000):
                count=min(100000,nf-start);block=np.fromfile(faces,'<u4',count=count*3)
                if len(block)!=count*3 or (len(block) and block.max()>=nv):raise ValueError('The face cache changed or contains an invalid vertex index.')
                kinds=np.fromfile(flags,'u1',count=count) if flags is not None else np.zeros(count,np.uint8)
                if len(kinds)!=count:raise ValueError('The inferred-face flags were truncated during export.')
                inferred+=int(np.count_nonzero(kinds))
                for face,kind in zip(block.reshape(-1,3),kinds):
                    current='inferred_repair' if kind else 'original_surface'
                    if current!=group:stream.write('g '+current+'\n');group=current
                    stream.write('f %d %d %d\n'%tuple(face.astype(np.int64)+1))
        temporary.replace(destination)
    finally:temporary.unlink(missing_ok=True)
    return {'count':nf,'inferred_faces':inferred,'warnings':['Full triangle mesh exported in original project coordinates. OBJ groups distinguish original surface and inferred repairs.']}
