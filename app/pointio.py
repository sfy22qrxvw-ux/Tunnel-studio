# Copyright (c) 2026 Damian Gulczynski.
"""Chunked coordinates and optional, row-aligned intensity/RGB (I,R,G,B float32).
Missing attributes are NaN, never silently invented as measurements.
"""
from pathlib import Path
import re,time
import numpy as np
CHUNK=100_000


def e57_buffers(source, fields, capacity):
    """Read declared fields including validity flags omitted by pye57's wrapper.

    Use explicit libE57 buffer types, as in the setup import worker. The
    wrapper's field allow-list does not include isIntensityInvalid or
    isColorInvalid; dropping these fields would turn invalid attributes into
    measured seam evidence. Arrays must live for the lifetime of the reader.
    """
    from pye57 import libe57
    data={key:np.empty(capacity,'B' if 'Invalid' in key else 'd') for key in fields}
    buffers=libe57.VectorSourceDestBuffer()
    for key,array in data.items():
        buffers.append(libe57.SourceDestBuffer(source.image_file,key,array,capacity,True,True))
    return data,buffers


def records(path, progress=lambda s:None, e57_scan=None):
    path=Path(path); ext=path.suffix.lower()
    if ext in ('.las','.laz'):
        import laspy
        with laspy.open(path) as f:
            dims=set(f.header.point_format.dimension_names); done=0
            for c in f.chunk_iterator(CHUNK):
                p=np.column_stack((c.x,c.y,c.z)); a=np.full((len(p),4),np.nan,dtype='f4')
                a[:,0]=c.intensity
                if {'red','green','blue'}<=dims: a[:,1:]=np.column_stack((c.red,c.green,c.blue))
                done+=len(p); progress(f'Reading {path.name}: {done:,} / {f.header.point_count:,}')
                yield p,a
    elif ext=='.e57':
        import pye57
        with pye57.E57(str(path)) as f:
            for scan in (range(f.scan_count) if e57_scan is None else [e57_scan]):
                h=f.get_header(scan); fields=set(h.point_fields)
                if {'cartesianX','cartesianY','cartesianZ'}<=fields:
                    names=['cartesianX','cartesianY','cartesianZ']; spherical=False; invalid='cartesianInvalidState'
                elif {'sphericalRange','sphericalAzimuth','sphericalElevation'}<=fields:
                    names=['sphericalRange','sphericalAzimuth','sphericalElevation']; spherical=True; invalid='sphericalInvalidState'
                else: raise ValueError(f'E57 scan {scan+1} has no supported coordinates.')
                attrs=['intensity','colorRed','colorGreen','colorBlue']
                selected=names+[k for k in [invalid,*attrs,'isIntensityInvalid','isColorInvalid'] if k in fields]
                data,buffers=e57_buffers(f,selected,CHUNK); reader=h.points.reader(buffers); done=0
                try:
                    while True:
                        n=reader.read()
                        if not n: break
                        p=np.column_stack([data[k][:n] for k in names]); a=np.full((n,4),np.nan,dtype='f4')
                        for j,k in enumerate(attrs):
                            if k in data: a[:,j]=data[k][:n]
                        if 'isIntensityInvalid' in data: a[data['isIntensityInvalid'][:n]!=0,0]=np.nan
                        if 'isColorInvalid' in data: a[data['isColorInvalid'][:n]!=0,1:]=np.nan
                        if invalid in data:
                            keep=data[invalid][:n]==0; p=p[keep]; a=a[keep]
                        if spherical:
                            r,az,el=p.T; p=np.column_stack((r*np.cos(el)*np.cos(az),r*np.cos(el)*np.sin(az),r*np.sin(el)))
                        if h.has_pose(): p=p@h.rotation_matrix.T+h.translation
                        done+=n; progress(f'E57 scan {scan+1}/{f.scan_count}: {done:,} / {h.point_count:,}')
                        yield p,a
                finally: reader.close()
    elif ext=='.ply':
        import ply_ascii
        ascii_format,_=ply_ascii.inspect(path)
        if ascii_format:
            yield from ply_ascii.records(path,CHUNK,progress)
            return
        from plyfile import PlyData
        v=PlyData.read(str(path),mmap='r')['vertex']; names=set(v.data.dtype.names)
        if not {'x','y','z'}<=names: raise ValueError('PLY requires x, y and z vertex properties.')
        for i in range(0,len(v),CHUNK):
            p=np.column_stack([v[k][i:i+CHUNK] for k in ('x','y','z')]).astype(float); a=np.full((len(p),4),np.nan,dtype='f4')
            for j,k in enumerate(('intensity','red','green','blue')):
                if k in names: a[:,j]=v[k][i:i+CHUNK]
            progress(f'Reading PLY: {min(i+CHUNK,len(v)):,} / {len(v):,}'); yield p,a
    elif ext in ('.xyz','.pts','.txt','.csv'):
        with path.open(encoding='utf-8-sig') as f:
            columns=None; acols=[None]*4; pts=[]; attrs=[]; expected=None; count=0; began=False; started=time.monotonic()
            def text_progress():
                if expected is None:
                    progress(f'Reading {path.name}: {count:,} points')
                    return
                message=f'Reading {path.name}: {count:,} / {expected:,} points'
                elapsed=time.monotonic()-started
                if count and elapsed>=1 and count<expected:
                    rate=count/elapsed;remaining=max(0,(expected-count)/rate)
                    if remaining>=3600:estimate=f'{int(remaining//3600)} h {int(remaining%3600//60)} min'
                    elif remaining>=60:estimate=f'{int(remaining//60)} min {int(remaining%60)} s'
                    else:estimate=f'{int(remaining)} s'
                    message+=f' · {rate:,.0f} points/s · estimated {estimate} remaining'
                progress(message)
            for line_no,line in enumerate(f,1):
                text=line.strip()
                if not text or text.startswith('#'): continue
                parts=[s.strip().strip('"') for s in re.split(r'[,;\t ]+',text)]
                if not began:
                    if ext=='.pts' and len(parts)==1 and expected is None:
                        try:expected=int(parts[0])
                        except ValueError:raise ValueError('PTS point-count header must be a non-negative integer.')
                        if expected<0:raise ValueError('PTS point-count header must be a non-negative integer.')
                        text_progress();continue
                    try: [float(v) for v in parts[:3]]
                    except ValueError:
                        hdr=[v.lower() for v in parts]
                        def find(keys,required=False):
                            for k in keys:
                                if k in hdr: return hdr.index(k)
                            if required: raise ValueError('Headers must contain E,N,H or X,Y,Z.')
                        columns=[find(('e','easting','x'),True),find(('n','northing','y'),True),find(('h','height','z','elevation'),True)]
                        acols=[find(('intensity','i','reflectance')),find(('r','red')),find(('g','green')),find(('b','blue'))]
                        began=True; continue
                    if ext=='.pts':
                        if len(parts) not in (3,4,7): raise ValueError('PTS records must be XYZ, XYZI or XYZI RGB.')
                        acols=([3,None,None,None] if len(parts)==4 else [3,4,5,6] if len(parts)==7 else [None]*4)
                began=True
                try:
                    p=[float(parts[k]) for k in (columns or [0,1,2])]
                    a=[float(parts[k]) if k is not None else np.nan for k in acols]
                except (ValueError,IndexError): raise ValueError(f'Invalid record at line {line_no}; import stopped.')
                pts.append(p); attrs.append(a); count+=1
                if len(pts)>=CHUNK:
                    text_progress(); yield np.asarray(pts),np.asarray(attrs,dtype='f4'); pts=[]; attrs=[]
            if pts:
                text_progress();yield np.asarray(pts),np.asarray(attrs,dtype='f4')
            if expected is not None and count!=expected: raise ValueError(f'PTS header declares {expected:,} points but {count:,} records were read.')
    else: raise ValueError('Use E57, LAS, LAZ, PLY, PTS, XYZ, TXT or CSV. Export proprietary scanner projects first.')


def import_cloud(path,destination,progress=lambda s:None,e57_scan=None):
    from preview_index import StreamingBoundsBuilder
    spatial=StreamingBoundsBuilder()
    dest=Path(destination); attr=dest.with_suffix('.attr32'); total=skipped=0
    lo=np.full(3,np.inf); hi=-lo; counts=np.zeros(4,dtype='i8'); amin=np.full(4,np.inf); amax=-amin
    preallocate=None
    if Path(path).suffix.lower()=='.ply':
        import ply_ascii
        ascii_format,declared=ply_ascii.inspect(path)
        if ascii_format:preallocate=declared
    source=Path(path)
    if any(source.resolve()==target.resolve() or (target.exists() and source.samefile(target)) for target in (dest,attr)):
        raise ValueError('Import destination must not overwrite the source.')
    try:
        with dest.open('wb') as out, attr.open('wb') as ao:
            if preallocate is not None:
                # Reserve disk length, never a full-size Python/NumPy array.
                out.truncate(preallocate*24);ao.truncate(preallocate*16)
            for p,a in records(path,progress,e57_scan):
                keep=np.isfinite(p).all(axis=1); skipped+=int((~keep).sum()); p=np.asarray(p[keep],dtype='<f8'); a=np.asarray(a[keep],dtype='<f4')
                if not len(p): continue
                p.tofile(out); a.tofile(ao);spatial.add(total,p);total+=len(p);lo=np.minimum(lo,p.min(axis=0));hi=np.maximum(hi,p.max(axis=0))
                for j in range(4):
                    v=a[np.isfinite(a[:,j]),j]; counts[j]+=len(v)
                    if len(v): amin[j]=min(amin[j],float(v.min())); amax[j]=max(amax[j],float(v.max()))
            if preallocate is not None:
                out.truncate(total*24);ao.truncate(total*16)
        if not total: raise ValueError('No finite coordinate points were found.')
    except BaseException:
        dest.unlink(missing_ok=True); attr.unlink(missing_ok=True); raise
    st=source.stat()
    meta={'name':source.name,'source_name':source.name,'source_path':str(source.resolve()),
          'source_format':source.suffix.lower().lstrip('.') or 'point_cloud','source_bytes':int(st.st_size),
          'source_mtime_ns':int(getattr(st,'st_mtime_ns',int(st.st_mtime*1e9))),
          'file':str(dest),'count':total,'invalid_points_skipped':skipped,'minimum':lo.tolist(),'maximum':hi.tolist()}
    if preallocate is not None:
        # Cache remains authoritative world float64. Origin is display metadata;
        # exports must read the cache, never reconstruct from float32 previews.
        meta.update(coordinate_storage='world-float64',display_origin=((lo+hi)/2).astype('<f8').tolist(),display_origin_dtype='float64')
    if counts.any():
        meta.update(attributes_file=str(attr),attribute_counts=counts.tolist(),attribute_min=[float(v) if np.isfinite(v) else None for v in amin],attribute_max=[float(v) if np.isfinite(v) else None for v in amax])
    else: attr.unlink(missing_ok=True)
    spatial.finish(meta)
    return meta


def arrays(meta):
    p=np.memmap(meta['file'],dtype='<f8',mode='r',shape=(meta['count'],3))
    a=np.memmap(meta['attributes_file'],dtype='<f4',mode='r',shape=(meta['count'],4)) if meta.get('attributes_file') else None
    return p,a


def chunks(meta):
    """Export retained originals using owned reads of at most 100k records.

    Keeping a whole-source mapping open while exporting can accumulate resident
    mapped pages. This iterator owns only the current XYZ/attribute/mask blocks.
    """
    from contextlib import ExitStack
    total=int(meta['count']);block=max(1,min(int(CHUNK),100000))
    with ExitStack() as files:
        xyz_file=files.enter_context(open(meta['file'],'rb'))
        attr_file=files.enter_context(open(meta['attributes_file'],'rb')) if meta.get('attributes_file') else None
        mask_file=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        for start in range(0,total,block):
            count=min(block,total-start);raw=np.fromfile(xyz_file,dtype='<f8',count=count*3)
            if len(raw)!=count*3:raise ValueError('The source point cache is incomplete during export.')
            q=raw.reshape(count,3);b=None
            if attr_file is not None:
                values=np.fromfile(attr_file,dtype='<f4',count=count*4)
                if len(values)!=count*4:raise ValueError('The source attribute cache is incomplete during export.')
                b=values.reshape(count,4)
            if mask_file is not None:
                flags=np.fromfile(mask_file,dtype='u1',count=count)
                if len(flags)!=count:raise ValueError('The source exclusion mask is incomplete during export.')
                keep=flags==0;q=q[keep];b=b[keep] if b is not None else None
            if len(q):yield q,b
