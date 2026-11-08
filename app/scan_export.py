# Copyright (c) 2026 Damian Gulczynski.
"""Bounded-memory export of retained points and explicit destination handling."""
from pathlib import Path
from contextlib import contextmanager
import copy, hashlib, json, os, tempfile, uuid
import numpy as np
import registration_pose, jobdata

CHUNK = 200000
FORMATS = {'e57','las','laz','ply','pts','xyz','csv','tstudio','obj','json','dxf'}


@contextmanager
def retained(meta, work, progress=lambda message: None):
    """Give format writers exact world-coordinate retained points.

    Virtual registration poses are applied only while explicitly exporting; the
    source cache itself remains untouched. A temporary compact cache is created
    only when a deletion mask or non-identity pose makes it necessary.
    """
    n = int(meta['count'])
    for key, size in [('file',24),('attributes_file',16),('mask_file',1)]:
        if meta.get(key) and Path(meta[key]).stat().st_size != n*size:
            raise ValueError('Invalid scan cache: '+key)
    if n < 1: raise ValueError('This scan has no remaining points to export.')
    virtual = not registration_pose.is_identity(meta)
    if not meta.get('mask_file') and not virtual:
        yield copy.deepcopy(meta); return
    # This is an explicit materialisation operation, unlike registration Apply.
    # Reserve enough room for the compact temporary XYZ/attribute stream.
    jobdata.room(work, n*24 + (n*16 if meta.get('attributes_file') else 0) + 8*1024*1024)
    arrays = []
    with tempfile.TemporaryDirectory(prefix='retained-export-',dir=work) as folder:
        try:
            def mapped(key,dtype,shape):
                a=np.memmap(meta[key],dtype,'r',shape=shape);arrays.append(a);return a
            points=mapped('file','<f8',(n,3));mask=mapped('mask_file','u1',(n,)) if meta.get('mask_file') else None
            attrs=mapped('attributes_file','<f4',(n,4)) if meta.get('attributes_file') else None
            xyz=Path(folder)/'retained.xyz64';attr=Path(folder)/'retained.attr32'
            count=0;lo=np.full(3,np.inf);hi=-lo;counts=np.zeros(4,dtype=np.int64);amin=np.full(4,np.inf);amax=-amin
            with xyz.open('wb') as out, attr.open('wb') as ao:
                for start in range(0,n,CHUNK):
                    stop=min(n,start+CHUNK);use=(mask[start:stop]==0) if mask is not None else slice(None)
                    q=np.asarray(points[start:stop][use]);q=registration_pose.points(meta,q)
                    q.astype('<f8',copy=False).tofile(out);count+=len(q)
                    if len(q):lo=np.minimum(lo,q.min(0));hi=np.maximum(hi,q.max(0))
                    if attrs is not None:
                        a=attrs[start:stop][use];a.tofile(ao)
                        for j in range(4):
                            v=a[np.isfinite(a[:,j]),j];counts[j]+=len(v)
                            if len(v):amin[j]=min(amin[j],float(v.min()));amax[j]=max(amax[j],float(v.max()))
                    progress(f'Preparing registered export: {stop:,} / {n:,} checked · {count:,} retained')
            if not count:raise ValueError('All points in this scan are deleted. Restore some points before exporting.')
            result=copy.deepcopy(meta)
            for k in ('mask_file','preview_file','truth_file','retained_properties','retained_summary','registration_rotation','registration_translation','virtual_registration'):result.pop(k,None)
            result.update(file=str(xyz),count=count,minimum=lo.tolist(),maximum=hi.tolist())
            if attrs is not None:
                result.update(attributes_file=str(attr),attribute_counts=counts.tolist(),attribute_min=[float(v) if np.isfinite(v) else None for v in amin],attribute_max=[float(v) if np.isfinite(v) else None for v in amax])
            yield result
        finally:
            for a in arrays:a._mmap.close()


def export_cloud(meta, dest, fmt, work, progress, writer):
    try:
        with retained(meta,work,progress) as clean:
            result=writer(clean,dest,fmt,progress) or {}
            result['count']=clean['count'];result['deleted_points']=meta['count']-clean['count']
            if meta.get('experimental_reconstruction'):
                from rtc360_optics import NOTICE
                result['warnings']=list(result.get('warnings') or [])+[NOTICE]
            return result
    except BaseException:
        Path(dest).unlink(missing_ok=True);raise


def signature(item):
    m=item['meta'];files=[]
    for k in ('file','mask_file','attributes_file','vertices_file','faces_file'):
        path=m.get(k);stamp=None
        if path:
            try:
                st=Path(path).stat();stamp=[st.st_size,st.st_mtime_ns]
            except OSError:stamp='missing'
        files.append([k,path,stamp])
    return hashlib.sha256(json.dumps([files,m.get('count'),m.get('vertices'),m.get('triangles'),item.get('geometry_revision'),m.get('registration_rotation'),m.get('registration_translation')],sort_keys=True).encode()).hexdigest()


def current(s, ident):
    item=s.STATE.get('export_ready')
    if not item or not ident or item.get('id')!=ident:raise ValueError('This export is out of date. Export the current scan again.')
    if s.JOB['busy']:raise ValueError('Wait until the current operation finishes.')
    for ident,revision in item.get('mesh_revisions',{}).items():
        source=next((x for x in s.STATE.get('job_items',[]) if x['id']==ident),None)
        if source is None or signature(source)!=revision:raise ValueError('The mesh changed after preparation. Export the current mesh again.')
    if item.get('mesh_id'):
        source=next((x for x in s.STATE.get('job_items',[]) if x['id']==item['mesh_id']),None)
        if source is None or signature(source)!=item.get('mesh_revision'):raise ValueError('The mesh changed after preparation. Export the current mesh again.')
    if item.get('highlight'):
        import workspace_tools
        if workspace_tools.revision(s,item['highlight'])!=item.get('highlight_revision'):raise ValueError('The highlighted sources changed. Export the current selection again.')
    if item.get('object_ids'):
        import scene_objects
        objects={o['id']:o for o in scene_objects.public(s)}
        values=[objects.get(i) for i in item['object_ids']]
        if hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()!=item.get('object_revision'):raise ValueError('The highlighted objects changed. Export them again.')
    if item.get('source')=='scene' and item.get('format') not in ('obj','tstudio'):
        source=next((x for x in s.STATE.get('job_items',[]) if x['id']==item.get('source_id')),None)
        if source is None or signature(source)!=item.get('source_revision'):raise ValueError('The scan changed after this export. Export the current remaining points again.')
    return item


def destination(s, value, fmt):
    if fmt not in FORMATS:raise ValueError('Unsupported export format.')
    p=Path(str(value).strip().strip('"')).expanduser()
    if not p.is_absolute():raise ValueError('Enter the full folder and filename for the exported file.')
    if p.is_dir():raise ValueError('Choose a filename, not a folder.')
    if p.suffix.lower()!='.'+fmt:p=Path(str(p)+'.'+fmt)
    p=p.resolve()
    if not p.parent.is_dir():raise ValueError('The destination folder does not exist. Choose an existing folder.')
    if p.is_dir():raise ValueError('Choose a filename, not a folder.')
    if p.is_relative_to(Path(s.WORK).resolve()):raise ValueError('Save outside the internal Tunnel Studio data folder.')
    # Also protect original external inputs recorded anywhere in project state.
    protected=set()
    def visit(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if isinstance(v,str) and ('file' in k or 'path' in k):
                    try:
                        q=Path(v)
                        if q.is_absolute():protected.add(q.resolve())
                    except (OSError,ValueError):pass
                else:visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    visit({k:v for k,v in s.STATE.items() if k!='export_ready'})
    if p in protected or any(q.exists() and p.exists() and os.path.samefile(p,q) for q in protected):
        raise ValueError('Choose a new filename; original scan and project files cannot be overwritten.')
    return p


def fingerprint(p):
    if not p.exists():return None
    st=p.stat()
    # This opaque confirmation token crosses a browser JSON round trip.
    # Nanosecond timestamps and file identifiers exceed JavaScript's exact
    # integer range; sending numeric fields would reject a valid confirmation.
    value=f'{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}'
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def publish_file(temp, dest, expected):
    """Publish a completed sibling file, guarding the reviewed destination."""
    if fingerprint(dest) != expected:
        raise ValueError('The destination changed while saving. Choose the destination again.')
    if expected is not None:
        os.replace(temp, dest)
    elif os.name == 'nt':
        # Windows rename is atomic and fails when a competing file appeared.
        os.rename(temp, dest)
    else:
        os.link(temp, dest)


def save(s, body):
    item=current(s,body.get('id'));dest=destination(s,body.get('destination',''),item['format'])
    before=fingerprint(dest)
    if before is not None and body.get('replace_version')!=before:
        return dict(needs_overwrite=True,destination=str(dest),replace_version=before)
    temp=dest.with_name('.'+dest.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with Path(item['file']).open('rb') as src,temp.open('xb') as out:
            while True:
                chunk=src.read(4*1024*1024)
                if not chunk:break
                out.write(chunk);s.progress(f'Saving {dest.name}: {out.tell():,} bytes')
            out.flush();os.fsync(out.fileno())
        publish_file(temp, dest, before)
        return dict(saved=True,destination=str(dest),count=item.get('count'))
    finally:temp.unlink(missing_ok=True)
