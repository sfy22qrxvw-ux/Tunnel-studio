"""Experimental native setup reader; full XYZ/intensity caches, local coordinates."""
from pathlib import Path
import hashlib, json, stat, struct, time
import numpy as np
from hspca_codec import archive_entries, decode_node
from zstandard import ZstdError

NOTICE = ('Experimental Leica setup: scanner-local coordinates; Register 360 saved setup positions and external cleaning masks are not applied. '
          'Native intensity only; RGB decoding and independent survey accuracy are not yet validated.')

MAX_PREVIEW_SETUPS = 24
MAX_PREVIEW_POINTS = 12000


def sample_setups(paths, limit):
    """Read a bounded selection of original point nodes from individual scans."""
    if not 1<=len(paths)<=MAX_PREVIEW_SETUPS or not len(paths)<=limit<=MAX_PREVIEW_POINTS:
        raise ValueError('Invalid Leica preview budget.')
    deadline=time.monotonic()+7
    per_setup=limit//len(paths)
    node_count=max(12,48//len(paths))
    byte_budget=min(16*1024**2,96*1024**2//len(paths))
    results=[]
    for value in paths:
        if time.monotonic()>=deadline:
            results.append(dict(status='limited'))
            continue
        path=Path(value)
        try:
            before=path.stat()
            if not stat.S_ISREG(before.st_mode) or getattr(before,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_OFFLINE:
                raise OSError('Scan is not available locally.')
            entries=archive_entries(path,max_index_bytes=16*1024**2)
            indices=np.linspace(0,len(entries)-1,min(len(entries),node_count),dtype=np.int64)
            per_node=(per_setup+len(indices)-1)//len(indices)
            samples=[];remaining=byte_budget
            with path.open('rb') as stream:
                for index in indices:
                    if time.monotonic()>=deadline:break
                    offset,length=entries[index]
                    if length>remaining:continue
                    stream.seek(offset)
                    if stream.read(4)!=b'APRN':continue
                    remaining-=length
                    payload=b'APRN'+stream.read(length-4)
                    xyz,attrs,stats=decode_node(payload,sample_size=per_node)
                    if stats['bad_node_bounds'] or not np.isfinite(xyz).all():
                        raise ValueError('Invalid native point coordinates.')
                    samples.append(np.column_stack((xyz,attrs['INTENSITY'])))
            after=path.stat()
            if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                raise OSError('Scan changed while reading.')
            if samples:
                rows=np.concatenate(samples)
                rows=rows[np.linspace(0,len(rows)-1,min(len(rows),per_setup),dtype=np.int64)]
                results.append(dict(status='sampled',rows=rows.tolist()))
            else:
                results.append(dict(status='limited' if time.monotonic()>=deadline else 'unsupported'))
        except OSError:
            results.append(dict(status='unavailable'))
        except (ValueError,OverflowError,struct.error,ZstdError):
            results.append(dict(status='unsupported'))
    return results


def describe(path, progress=lambda s:None):
    entries=archive_entries(path);total=0;metadata=None
    with open(path,'rb') as f:
        for i,(offset,length) in enumerate(entries):
            f.seek(offset);h=f.read(min(length,172))
            if h[:4]==b'APRN':
                if len(h)<172:raise ValueError('Truncated HSPCA node.')
                n=struct.unpack_from('<Q',h,20)[0]
                if not 1<=n<=1000000:raise ValueError('Unsupported HSPCA node size.')
                # Validate every node schema before allocating any output cache.
                end=struct.unpack_from('<Q',h,112)[0]
                if not 172<end<min(length,4096):raise ValueError('Unsupported HSPCA node header.')
                f.seek(offset);b=f.read(end)
                from hspca_codec import NAMES
                count=struct.unpack_from('<I',b,168)[0];p=172;names=[]
                for _ in range(min(count,100)):
                    if p+8>len(b):break
                    size=struct.unpack_from('<H',b,p+6)[0];p+=8
                    if not 1<=size<=100 or p+size>len(b):break
                    names.append(b[p:p+size].decode('ascii',errors='replace'));p+=size
                if count!=len(NAMES) or names!=NAMES or p!=end:
                    raise ValueError('This HSPCA layout is not supported yet. Choose an individual scan from SetupsFolder; the combined PointCloud file uses a different layout.')
                if struct.unpack_from('<I',h,12)[0]!=22:raise ValueError('Unsupported HSPCA node version; only APRN 22 is supported.')
                total+=n
            elif h[:1]==b'{':
                if length>8*1024*1024:raise ValueError('Oversized HSPCA metadata.')
                f.seek(offset);metadata=json.loads(f.read(length)).get('tree-header-info')
            elif h[:4]!=b'APRT':raise ValueError('Unrecognised HSPCA entry; import stopped to avoid silently skipping data.')
            if i%100==0:progress(f'Checking Leica setup headers: {i+1:,} / {len(entries):,}')
    if not total:raise ValueError('No supported point nodes in this HSPCA.')
    if not metadata or metadata.get('point-count')!=total:
        raise ValueError('HSPCA metadata point count does not match its nodes.')
    grid=metadata.get('metadata',{})
    if not grid.get('numRows') or not grid.get('numCols'):
        raise ValueError('Only structured individual Leica setups are supported; use a SetupsFolder scan.')
    if grid.get('HasCleanedPoints'):
        raise ValueError('Native cleaning masks are not supported yet. Use an uncleaned setup or an E57 export.')
    return total,entries,metadata

def import_one(path,destination,progress=lambda s:None):
    from preview_index import StreamingBoundsBuilder
    spatial=StreamingBoundsBuilder()
    path=Path(path);dest=Path(destination);attr=dest.with_suffix('.attr32')
    count,entries,metadata=describe(path,progress)
    import shutil
    if shutil.disk_usage(dest.parent).free<count*40+520*1024*1024:
        raise ValueError(f'Not enough disk space. This scan needs about {count*40/1024**3:.2f} GiB for its full point cache, plus free working space.')
    written=0;lo=np.full(3,np.inf);hi=-lo.copy();imin=65535;imax=0
    try:
        with path.open('rb') as f,dest.open('wb') as out,attr.open('wb') as ao:
            for offset,length in entries:
                f.seek(offset);b=f.read(length)
                if b[:4]!=b'APRN':continue
                xyz,attrs,stats=decode_node(b)
                if stats['bad_node_bounds'] or not np.isfinite(xyz).all():raise ValueError('Native point validation failed; no scan was imported.')
                if np.any(attrs['WorkingMask']) or np.any(attrs['Label']):
                    raise ValueError('This setup has native point labels or masks whose meaning is not yet supported. No scan was imported.')
                intensity=attrs['INTENSITY'];values=np.full((len(xyz),4),np.nan,dtype='<f4');values[:,0]=intensity
                xyz.astype('<f8',copy=False).tofile(out);values.tofile(ao)
                spatial.add(written,xyz)
                written+=len(xyz);lo=np.minimum(lo,xyz.min(0));hi=np.maximum(hi,xyz.max(0))
                imin=min(imin,int(intensity.min()));imax=max(imax,int(intensity.max()))
                progress(f'Importing native Leica points: {written:,} / {count:,} ({100*written/count:.0f}%)')
        if written!=count:raise ValueError('HSPCA point count changed during import.')
        box=metadata.get('bounding-box',{})
        if not np.allclose(lo,box.get('min',[]),rtol=0,atol=1e-8) or not np.allclose(hi,box.get('max',[]),rtol=0,atol=1e-8):
            raise ValueError('Decoded scan bounds do not match native metadata.')
        sha=hashlib.sha256()
        with path.open('rb') as f:
            while block:=f.read(8*1024*1024):
                sha.update(block);progress('Verifying native source fingerprint…')
        meta=dict(name=path.name,file=str(dest),attributes_file=str(attr),count=written,
                    minimum=lo.tolist(),maximum=hi.tolist(),attribute_counts=[written,0,0,0],
                    attribute_min=[imin,None,None,None],attribute_max=[imax,None,None,None],
                    invalid_points_skipped=0,source_sha256=sha.hexdigest(),source_format='hspca',
                    coordinate_frame='scanner-local',position_status=NOTICE,native_metadata=metadata,
                    warnings=[NOTICE],units='Unconfirmed; native coordinate values preserved')
        spatial.finish(meta);return meta
    except BaseException:
        dest.unlink(missing_ok=True);attr.unlink(missing_ok=True);raise
