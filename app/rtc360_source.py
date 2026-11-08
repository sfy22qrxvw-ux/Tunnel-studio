# Copyright (c) 2026 Damian Gulczynski.
"""Bounded header inspection and source identity for direct RTC360 import."""
from pathlib import Path
import hashlib, json, struct
from rtc360_folder import INDEX, contained, discover
from rtc360_scan_probe import read_setup, require
from rtc360_optics import parameters, MODEL, NOTICE


def describe(folder):
    folder=Path(folder).resolve();setup=read_setup(folder/INDEX);parameters(setup)
    rows,cols=(setup['measured_raster'][k] for k in ('rows','columns'))
    require(0<rows<=100000 and 0<cols<=40000 and rows*cols<=2_000_000_000,'Unsupported scanner raster size.')
    refs=setup['references']['ScanSlice']
    require(0<len(refs)<=2000 and all(r.get('format')=='21' for r in refs),'Unsupported scan slice format.')
    identity=[str(folder),setup['sha256']];total_rows=0;total_bytes=0
    for kind in ('ScanSlice','LwpoSlice'):
        for ref in setup['references'][kind]:
            # Only point slices and the HDR preview are consumed by this reader.
            if kind=='LwpoSlice' and ref.get('format')!='17':continue
            path=contained(folder,ref['filename'])
            if not path.is_file():
                require(kind!='ScanSlice','Missing scan slice: '+path.name)
                identity.append([path.name,'missing']);continue
            st=path.stat();total_bytes+=st.st_size
            with path.open('rb') as stream:
                header=stream.read(65536);stream.seek(max(0,st.st_size-65536));tail=stream.read(65536)
            if kind=='ScanSlice':
                require(len(header)>=28,'Truncated scan slice: '+path.name)
                magic,word,h,jxr,w,h2,variant=struct.unpack_from('<4s6I',header)
                require(magic==b'SCV6' and word==6 and variant==2162433 and w==cols and h==h2
                        and h>0 and w*h<=16*1024*1024 and 0<jxr<st.st_size<=256*1024*1024,
                        'Unsupported or incomplete SCV6 slice: '+path.name)
                total_rows+=h
            identity.append([path.name,st.st_size,st.st_mtime_ns,hashlib.sha256(header+tail).hexdigest()])
    require(total_rows==rows,'Scan slices do not cover the declared raster size.')
    stamp=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    from rtc360_names import display_name
    return dict(id=setup['id'],name=display_name(setup['name'],folder,setup['id']),source_setup_name=setup['name'],folder=str(folder),stamp=stamp,
                raster=dict(rows=rows,columns=cols),raster_slots=rows*cols,source_bytes=total_bytes,
                cache_bytes_estimate=rows*cols*40+16*1024**2,model=MODEL,
                api_version=setup['attributes'].get('ApiVersion'),
                creator_device=setup['attributes'].get('CreatorDevice'))


def inspect(path):
    info=discover(path)
    for item in info['setups']:
        if item.get('error'):continue
        try:
            original_bytes=item['source_bytes']
            item.update(describe(item['folder']),native_available=True,
                        status='Ready for experimental direct import')
            item['decoded_source_bytes']=item['source_bytes'];item['source_bytes']=original_bytes
        except (ValueError,KeyError,OSError) as exc:
            item.update(native_available=False,native_error=str(exc))
    info.update(format='rtc360_raw',point_import_supported=any(s.get('native_available') for s in info['setups']),
                native_experimental=True,message=NOTICE,converters=[])
    return info
