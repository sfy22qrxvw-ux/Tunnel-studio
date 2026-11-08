# Copyright (c) 2026 Damian Gulczynski.
"""Isolated, bounded direct RTC360 SCV6 -> editable Tunnel Studio caches.

All positive finite range samples are reconstructed, including unknown-status
and overlapping samples. This is explicitly NOT Leica's valid-point selection.
"""
from pathlib import Path
import os, sys

# The embedded application's NumPy/codec environment is never upgraded. Only
# this child process loads the independently installed raw-reader dependencies.
extra=Path(__file__).resolve().parents[1]/'runtime/rtc360_reader_v1'
if extra.is_dir():sys.path.insert(0,str(extra))
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
import hashlib, io, json, math, shutil, struct, tempfile, time, traceback
import numpy as np
from rtc360_scan_probe import read_setup, split_scan, require
from rtc360_folder import INDEX, source_bytes, FACE_ROTATIONS
from rtc360_source import describe
from rtc360_optics import parameters, reconstruct, MODEL, NOTICE


def emit(kind,value):
    print(kind+'\t'+(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)),flush=True)


def photos(folder,setup,dest):
    import OpenEXR
    from PIL import Image
    from rtc360_image_probe import unwrap_cubemap,inspect_exr
    refs=[r for r in setup['references']['LwpoSlice'] if r.get('format')=='17']
    require(len(refs)==1,'No unique supported HDR preview.')
    faces={}
    with tempfile.TemporaryDirectory(prefix=dest.stem+'_photos_',dir=dest.parent) as temp:
        for name,(blob,_) in unwrap_cubemap(source_bytes(folder,refs[0])).items():
            meta=inspect_exr(blob);require(meta['width']==meta['height']==1024,'Unsupported HDR preview dimensions.')
            path=Path(temp)/(name+'.exr');path.write_bytes(blob)
            with OpenEXR.File(str(path)) as f:
                require('RGB' in f.channels(),'HDR preview has no RGB channels.')
                pixels=f.channels()['RGB'].pixels.astype(np.float32)
            require(pixels.shape==(1024,1024,3) and np.isfinite(pixels).all(),'Invalid HDR preview pixels.')
            faces[name]=pixels
    sample=np.concatenate([a[::8,::8].reshape(-1,3) for a in faces.values()])
    light=sample@np.array([.2126,.7152,.0722]);positive=light[light>0]
    require(len(positive)>0,'HDR preview has no usable luminance.')
    exposure=.18/float(np.median(positive));manifest=[];chunks=[];offset=0
    for i,(name,rotation) in enumerate(FACE_ROTATIONS.items()):
        a=np.maximum(faces[name]*exposure,0);a=np.clip((a/(1+a))**(1/2.2),0,1)
        im=Image.fromarray(np.rint(a*255).astype(np.uint8));levels=[]
        for size in (1024,512):
            buffer=io.BytesIO();im.resize((size,size),Image.Resampling.LANCZOS).save(buffer,'JPEG',quality=93)
            data=buffer.getvalue();levels.append(dict(width=size,offset=offset,length=len(data)));chunks.append(data);offset+=len(data)
        manifest.append(dict(index=i,width=1024,height=1024,fx=511.5,fy=511.5,cx=511.5,cy=511.5,rotation=rotation,levels=levels))
    header=json.dumps(dict(version=1,faces=manifest),separators=(',',':')).encode()
    data=b'TSPHOTO1'+struct.pack('<I',len(header))+header+b''.join(chunks)
    from e57_setup_photos import decode
    checked=decode(data)
    with dest.open('xb') as stream:stream.write(data)
    return dict(file=str(dest),sha256=checked['sha256'],format='rtc360_hdr_preview_v1',in_setup_frame=True,
                source_name=refs[0]['filename'],experimental=True,
                notice='1024-pixel HDR previews; colour and image-to-point calibration remain experimental.')


def import_one(folder,dest,stamp,progress=None):
    import imagecodecs
    from PIL import Image
    from preview_index import StreamingBoundsBuilder
    spatial=StreamingBoundsBuilder()
    progress=progress or (lambda message:emit('PROGRESS',message))
    folder,dest=Path(folder).resolve(),Path(dest).resolve()
    require(not dest.is_relative_to(folder),'The working cache must be outside the scanner setup folder.')
    info=describe(folder);require(info['stamp']==stamp,'The scanner files changed. Inspect the folder again.')
    setup=read_setup(folder/INDEX);p,tilt=parameters(setup)
    rows,cols=(info['raster'][k] for k in ('rows','columns'))
    attr=dest.with_suffix('.attr32');preview=dest.with_suffix('.preview64');photo_path=dest.with_suffix('.setup-photo.dat')
    assets=(dest,attr,preview,photo_path)
    require(not any(path.exists() for path in assets),'Import cache destination already exists.')
    require(shutil.disk_usage(dest.parent).free>=info['cache_bytes_estimate']+512*1024**2,
            f'This full-resolution setup needs approximately {info["cache_bytes_estimate"]/1024**3:.2f} GiB of cache space, plus 512 MB free.')
    started=time.monotonic();count=read=skipped=preview_count=0
    stride=max(1,math.ceil(rows*cols/250000));covered=np.zeros(rows,bool)
    lo,hi=np.full(3,np.inf),np.full(3,-np.inf);imin,imax=255,0
    flags_total=np.zeros(16,np.int64);flags_kept=np.zeros(16,np.int64);slices=[]
    try:
        with dest.open('xb') as output,attr.open('xb') as attributes,preview.open('xb') as previews:
            for ordinal,ref in enumerate(setup['references']['ScanSlice'],1):
                progress(f'{info["name"]}: decoding slice {ordinal}/{len(setup["references"]["ScanSlice"])}')
                blob=source_bytes(folder,ref);report,jxr,jpeg=split_scan(blob)
                indices=np.array([row['index'] for row in report['rows']])
                require(report['width']==cols and indices.min()>=0 and indices.max()<rows and len(np.unique(indices))==len(indices) and not covered[indices].any(),
                        'Missing, overlapping or out-of-range scanner rows.')
                ranges=imagecodecs.jpegxr_decode(jxr)
                require(ranges.dtype==np.float32 and ranges.shape==(len(indices),cols),'Unsupported decoded range grid.')
                with Image.open(io.BytesIO(jpeg)) as im:
                    require(im.mode=='L' and im.size==(cols,len(indices)),'Unsupported intensity grid.')
                    intensity=np.array(im)
                coeff=np.array([r['coefficients_unapplied'] for r in report['rows']])
                flags=np.empty(ranges.shape,np.uint8)
                for i,row in enumerate(report['rows']):
                    block=row['blocks'][5];packed=np.frombuffer(blob,np.uint8,count=block['bytes'],offset=block['offset'])
                    # Verified against all 20,168 rows in the two supplied setups.
                    flags[i,::2]=packed>>4;flags[i,1::2]=(packed&15)[:cols//2]
                flags_total+=np.bincount(flags.ravel(),minlength=16)
                before=count
                # ~267k beams at the observed resolution; never allocate the full
                # setup XYZ or ask the UI thread to decode a native image.
                block_rows=max(1,262144//cols)
                for start in range(0,len(indices),block_rows):
                    end=min(start+block_rows,len(indices));rg=ranges[start:end].ravel()
                    valid=np.isfinite(rg)&(rg>0);cs=coeff[start:end];columns=np.arange(cols)
                    az=(cs[:,0,None]+cs[:,2,None]*columns).ravel()[valid]
                    el=(cs[:,1,None]+cs[:,3,None]*columns).ravel()[valid]
                    xyz=reconstruct(az,el,rg[valid],p)@tilt.T
                    values=np.full((len(xyz),4),np.nan,'<f4');values[:,0]=intensity[start:end].ravel()[valid]
                    flags_kept+=np.bincount(flags[start:end].ravel()[valid],minlength=16)
                    if len(xyz):
                        lo=np.minimum(lo,xyz.min(0));hi=np.maximum(hi,xyz.max(0))
                        imin=min(imin,int(values[:,0].min()));imax=max(imax,int(values[:,0].max()))
                        xyz.astype('<f8',copy=False).tofile(output);values.tofile(attributes)
                        spatial.add(count,xyz)
                        # Preview indices follow retained-cache order, as required
                        # by existing point colours and exact point picking.
                        sample=xyz[(-count)%stride::stride];sample.astype('<f8',copy=False).tofile(previews);preview_count+=len(sample)
                    count+=len(xyz);read+=len(rg);skipped+=int((~valid).sum())
                covered[indices]=True
                slices.append(dict(filename=ref['filename'],sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob),
                                   first_row=int(indices[0]),last_row=int(indices[-1]),retained=count-before,all_bytes_accounted_for=True))
                progress(f'{info["name"]}: {read:,} / {rows*cols:,} readings · {count:,} points retained')
        require(covered.all() and read==rows*cols and count>0,'The setup is incomplete or contains no positive finite readings.')
        warnings=[NOTICE,'Setups are imported separately at their own scanner origins with recorded tilt. No VIS/world position or registration is inferred.',
                  'Intensity comes from the stored 8-bit scan JPEG; point RGB is not reconstructed.']
        progress(info['name']+': preparing setup photographs…');photo=None
        try:
            photo=photos(folder,setup,photo_path)
            warnings.append(photo['notice'])
        except (ValueError,KeyError,RuntimeError,OSError,ImportError) as exc:
            photo_path.unlink(missing_ok=True);warnings.append('Setup photographs unavailable: '+str(exc))
        require(describe(folder)['stamp']==stamp,'The scanner files changed during import. No setup was saved.')
        status='Experimental RTC360 · unregistered · calibration unverified · unfiltered readings'
        meta=dict(name=info['name'],file=str(dest),attributes_file=str(attr),preview_file=str(preview),preview_count=preview_count,
                  preview_stride=stride,count=count,minimum=lo.tolist(),maximum=hi.tolist(),source_format='rtc360_raw_experimental',
                  source_file=str(folder/INDEX),source_stamp=stamp,source_records=read,
                  nonpositive_or_nonfinite_ranges_skipped=skipped,attribute_counts=[count,0,0,0],
                  attribute_min=[imin,None,None,None],attribute_max=[imax,None,None,None],
                  coordinate_frame='Unregistered scanner-local, recorded tilt applied',units='metres',has_scanner_pose=True,
                  position_status=status,warnings=warnings,experimental_reconstruction=True,
                  raw_import=dict(model=MODEL,full_factory_calibration_verified=False,validity_filter_applied=False,
                                  thermal_correction_applied=False,point_sampling_applied=False,setup_id=setup['id'],source_setup_name=setup['name'],raster=info['raster'],
                                  flags_total=flags_total.tolist(),flags_retained=flags_kept.tolist(),slices=slices,
                                  acquisition_metadata=setup['attributes'],scan_parameters=setup['scan_parameters'],
                                  kinematic_metadata=setup['kinematic_parameters_unapplied'],recorded_tilt=setup['tilt_unapplied'],
                                  seconds=time.monotonic()-started))
        spatial.finish(meta)
        return dict(meta=meta,scan_origin=[0.,0.,0.],scan_orientation=tilt.tolist(),setup_photo=photo)
    except BaseException:
        for path in assets:path.unlink(missing_ok=True)
        raise


if __name__=='__main__':
    try:
        require(len(sys.argv)==5 and sys.argv[1]=='import','Expected import, source folder, cache path and source stamp.')
        emit('RESULT',import_one(sys.argv[2],sys.argv[3],sys.argv[4]))
    except Exception as exc:
        emit('ERROR',f'{type(exc).__name__}: {exc}');traceback.print_exc(file=sys.stderr);sys.exit(1)
