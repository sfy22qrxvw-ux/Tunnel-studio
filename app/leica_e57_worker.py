# Copyright (c) 2026 Damian Gulczynski.
"""Bounded E57 import for the Leica conversion bridge. No proprietary decoder.

File coordinates and declared poses are used exactly once. Full valid XYZ is
retained, regardless of a potentially incomplete cartesianBounds header.
"""
from pathlib import Path
import hashlib, json, math, shutil, sys, traceback
import numpy as np

CHUNK = 262144


def emit(kind, value):
    print(kind+'\t'+(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), flush=True)


def fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    with path.open('rb') as stream:
        head = stream.read(65536)
        if head[:8] != b'ASTM-E57':
            raise ValueError('Not an E57 file: '+path.name)
        stream.seek(max(0, stat.st_size-65536))
        tail = stream.read(65536)
    return hashlib.sha256(json.dumps([str(path), stat.st_size, stat.st_mtime_ns]).encode()+head+tail).hexdigest()


def value(node, key, default=None):
    return node[key].value() if node.isDefined(key) else default


def pose(node):
    from scipy.spatial.transform import Rotation
    if not node.isDefined('pose'):
        return np.eye(3), np.zeros(3), False
    p = node['pose']
    q = np.array([value(p, 'rotation/'+k, 1. if k=='w' else 0.) for k in 'xyzw'])
    t = np.array([value(p, 'translation/'+k, 0.) for k in 'xyz'])
    if not np.isfinite(q).all() or not np.isfinite(t).all() or abs(np.linalg.norm(q)-1) > 1e-5:
        raise ValueError('Invalid E57 setup or photograph pose.')
    return Rotation.from_quat(q).as_matrix(), t, True


def inspect_file(path):
    import pye57
    stamp = fingerprint(path)
    scans = []
    with pye57.E57(str(path)) as source:
        if not 1 <= source.scan_count <= 200:
            raise ValueError('Choose an E57 with 1–200 scans.')
        for index in range(source.scan_count):
            header = source.get_header(index)
            node = source.root['data3D'][index]
            R, t, known = pose(node)
            fields = header.point_fields
            if not (set(('cartesianX','cartesianY','cartesianZ')) <= set(fields) or
                    set(('sphericalRange','sphericalAzimuth','sphericalElevation')) <= set(fields)):
                raise ValueError('E57 scan has no supported Cartesian or spherical coordinates.')
            scans.append(dict(index=index, count=int(header.point_count),
                name=value(node, 'name', '') or (Path(path).stem+(f' · setup {index+1}' if source.scan_count>1 else '')),
                guid=header.guid, has_pose=known, rotation=R.tolist(), translation=t.tolist()))
    if stamp != fingerprint(path):
        raise ValueError('The E57 changed while reading its headers. Wait for export to finish.')
    return dict(path=str(Path(path).resolve()), stamp=stamp, scans=scans)


def import_one(path, dest, index, expected_stamp):
    import pye57
    from pye57 import libe57
    from preview_index import StreamingBoundsBuilder
    spatial=StreamingBoundsBuilder()
    path, dest = Path(path), Path(dest)
    if fingerprint(path) != expected_stamp:
        raise ValueError('The E57 changed. Inspect the exports again.')
    attr = dest.with_suffix('.attr32')
    preview = dest.with_suffix('.preview64')
    photograph = dest.with_suffix('.setup-photo.dat')
    assets = (dest, attr, preview, photograph)
    if any(p.exists() for p in assets):
        raise ValueError('Import cache destination already exists.')
    try:
        with pye57.E57(str(path)) as source:
            header = source.get_header(index)
            node = source.root['data3D'][index]
            R, t, known = pose(node)
            fields = set(header.point_fields)
            cartesian = {'cartesianX','cartesianY','cartesianZ'} <= fields
            coordinates = ['cartesian'+c for c in 'XYZ'] if cartesian else ['sphericalRange','sphericalAzimuth','sphericalElevation']
            if not set(coordinates) <= fields:
                raise ValueError('Unsupported E57 coordinates.')
            invalid = 'cartesianInvalidState' if cartesian else 'sphericalInvalidState'
            requested = coordinates+[k for k in (invalid,'intensity','isIntensityInvalid','colorRed','colorGreen','colorBlue','isColorInvalid') if k in fields]
            # Explicit supported C buffer types avoid platform-dependent int64.
            arrays = {k:np.empty(CHUNK, 'B' if 'Invalid' in k else 'd') for k in requested}
            buffers = libe57.VectorSourceDestBuffer()
            for key, array in arrays.items():
                buffers.append(libe57.SourceDestBuffer(source.image_file, key, array, CHUNK, True, True))
            total = int(header.point_count)
            if total <= 0:
                raise ValueError('The E57 setup is empty.')
            if shutil.disk_usage(dest.parent).free < total*40+512*1024**2:
                raise ValueError(f'This setup needs {total*40/1024**3:.2f} GiB for its full point cache, plus 512 MB free.')
            read = count = skipped = 0
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            amin, amax, counts = np.full(4,np.inf), np.full(4,-np.inf), np.zeros(4,np.int64)
            reader = header.points.reader(buffers)
            try:
                with dest.open('xb') as output, attr.open('xb') as attributes:
                    while size := reader.read():
                        emit('PROGRESS', f'Reading E57 setup {index+1}: {min(read+size,total):,} / {total:,} records')
                        if cartesian:
                            xyz = np.column_stack([arrays[k][:size] for k in coordinates])
                        else:
                            radius, az, el = [arrays[k][:size] for k in coordinates]
                            xyz = np.column_stack([radius*np.cos(el)*np.cos(az), radius*np.cos(el)*np.sin(az), radius*np.sin(el)])
                        good = np.isfinite(xyz).all(1)
                        if not cartesian:good &= radius>0
                        if invalid in arrays:good &= arrays[invalid][:size]==0
                        skipped += int((~good).sum()); read += size
                        xyz = xyz[good]@R.T+t
                        if not np.isfinite(xyz).all():raise ValueError('Non-finite coordinates after applying the E57 pose.')
                        values = np.full((len(xyz),4),np.nan,'<f4')
                        for j,key in enumerate(('intensity','colorRed','colorGreen','colorBlue')):
                            if key not in arrays:continue
                            raw = arrays[key][:size][good].copy()
                            flag = 'isIntensityInvalid' if j==0 else 'isColorInvalid'
                            if flag in arrays:raw[arrays[flag][:size][good]!=0]=np.nan
                            finite = np.isfinite(raw)
                            raw[~finite]=np.nan
                            if j and np.any(finite & ((raw<0)|(raw>255))):
                                raise ValueError('This E57 uses colour outside 0–255. Use the standard file importer for this colour format.')
                            if np.any(np.abs(raw[finite])>np.finfo(np.float32).max):raise ValueError('Unsupported attribute magnitude.')
                            values[:,j] = raw
                            if finite.any():
                                counts[j] += int(finite.sum()); amin[j] = min(amin[j],float(raw[finite].min())); amax[j] = max(amax[j],float(raw[finite].max()))
                        if len(xyz):
                            lo=np.minimum(lo,xyz.min(0));hi=np.maximum(hi,xyz.max(0))
                            xyz.astype('<f8',copy=False).tofile(output);values.tofile(attributes);spatial.add(count,xyz);count+=len(xyz)
                        if shutil.disk_usage(dest.parent).free < 128*1024**2:raise ValueError('Import stopped because the project drive is almost full.')
            finally:
                reader.close()
            if read!=total or not count:raise ValueError('E57 record count differs from its header, or no valid points were found.')
            warnings=[];photo=None;view_origin=t;view_rotation=R
            # Only complete, explicitly associated, co-located pinhole cubes.
            try:
                from e57_setup_photos import extract
                photo, view_origin = extract(source, header.guid, R, t, photograph)
            except (ValueError,KeyError,RuntimeError,OSError,ImportError) as exc:
                photograph.unlink(missing_ok=True);warnings.append('Setup photographs not attached: '+str(exc))
            structured={'rowIndex','columnIndex'} <= fields
            has_scanner_pose=bool(known and (structured or photo))
            stride=max(1,math.ceil(count/250000))
            mapping=np.memmap(dest,'<f8','r',shape=(count,3))
            try:
                sample=np.asarray(mapping[::stride]);sample.tofile(preview);preview_count=len(sample)
            finally:mapping._mmap.close()
            name=value(node,'name','') or path.stem
            meta=dict(name=name,file=str(dest),attributes_file=str(attr),preview_file=str(preview),
                preview_count=preview_count,preview_stride=stride,count=count,
                minimum=lo.tolist(),maximum=hi.tolist(),source_format='e57',e57_setup_index=index,
                source_file=str(path),source_stamp=expected_stamp,source_records=read,invalid_points_skipped=skipped,
                attribute_counts=counts.tolist(),attribute_min=[float(v) if n else None for v,n in zip(amin,counts)],
                attribute_max=[float(v) if n else None for v,n in zip(amax,counts)],
                coordinate_frame='E57 file coordinates',units='metres',has_scanner_pose=has_scanner_pose,
                position_status='E57 saved pose applied; registration has not been independently checked',warnings=warnings)
            result=dict(meta=meta,scan_origin=view_origin.tolist(),scan_orientation=view_rotation.tolist(),setup_photo=photo,
                        e57_pose=dict(rotation=R.tolist(),translation=t.tolist(),explicit=known))
        if fingerprint(path)!=expected_stamp:raise ValueError('The E57 changed during import. No setup was saved.')
        spatial.finish(meta)
        return result
    except BaseException:
        for asset in assets:asset.unlink(missing_ok=True)
        raise


if __name__=='__main__':
    try:
        mode=sys.argv[1]
        if mode=='describe':result=inspect_file(Path(sys.argv[2]))
        elif mode=='import':result=import_one(Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]),sys.argv[5])
        else:raise ValueError('Unknown E57 bridge worker mode.')
        emit('RESULT',result)
    except Exception as exc:
        emit('ERROR',f'{type(exc).__name__}: {exc}');traceback.print_exc(file=sys.stderr);sys.exit(1)
