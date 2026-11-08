# Copyright (c) 2026 Damian Gulczynski.
"""Keep a local ESRI shapefile family together as a project resource."""
import os
from pathlib import Path, PureWindowsPath
import shutil
import tempfile
import uuid

import jobdata
from project_data_layout import folder, is_link

# Same-stem resource members only. Optional vendor and projection metadata travel
# alongside the mandatory geometry/index/attribute triplet.
EXTENSIONS = {'.shp','.shx','.dbf','.prj','.cpg','.sbn','.sbx','.fbn','.fbx',
              '.ain','.aih','.ixs','.mxs','.atx','.qix','.qpj','.xml',
              '.shp.xml','.dbf.xml','.shp.aux.xml'}
REQUIRED = {'.shp','.shx','.dbf'}


def family_members(path):
    if not isinstance(path,(Path,str)) or not str(path):
        raise ValueError('Choose the main .shp file.')
    path=Path(path)
    if not path.is_absolute() or path.suffix.casefold()!='.shp' or not path.is_file() or is_link(path):
        raise ValueError('Choose a regular local .shp file using its full path.')
    stem=path.stem
    invalid='<>:"/\\|?*'
    if any(c in invalid or ord(c)<32 for c in stem) or stem.endswith((' ','.')) or PureWindowsPath(stem).is_reserved():
        raise ValueError('The shapefile name cannot be used as a Windows project folder.')
    members={}
    for candidate in path.parent.iterdir():
        if not candidate.name.casefold().startswith(stem.casefold()+'.'):
            continue
        ext=candidate.name[len(stem):].casefold()
        if ext not in EXTENSIONS and not (ext.endswith('.atx') and ext.count('.')==2):
            continue
        if is_link(candidate) or not candidate.is_file():
            raise ValueError('Shapefile companions must be regular files: '+candidate.name)
        if ext in members:
            raise ValueError('Ambiguous case variants for shapefile companion '+ext)
        members[ext]=candidate
    missing=sorted(REQUIRED-members.keys())
    if missing:
        raise ValueError('Shapefile family is missing '+', '.join(missing)+'. Keep the files together before copying.')
    return dict(sorted(members.items()))


def _revision(st):
    return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)


def archive(s, path):
    from workspace_folders import managed_root
    if not managed_root(s.WORK):
        raise ValueError('Choose a dedicated project folder to store GIS files.')
    base=folder(s.WORK,'gis')
    source=Path(path)
    members=family_members(source)
    snapshots={ext:_revision(p.stat()) for ext,p in members.items()}
    total=sum(info[2] for info in snapshots.values())
    jobdata.room(base,total)
    ident=uuid.uuid4().hex
    # Always publish a unique family, even if another dataset has the same stem.
    dest=base/(source.stem[:100]+'-'+ident[:12])
    temp=Path(tempfile.mkdtemp(prefix='.copy-'+ident+'-',dir=base))
    published=False
    try:
        records=[]
        copied=0
        for ext, original in members.items():
            s.progress('Copying shapefile family · '+original.name)
            with original.open('rb') as inp,(temp/original.name).open('xb') as out:
                if _revision(os.fstat(inp.fileno()))!=snapshots[ext]:
                    raise ValueError('A shapefile member changed before copying.')
                while block:=inp.read(4*1024*1024):
                    out.write(block)
                    copied+=len(block)
                    s.progress(f'Copying shapefile family · {copied:,} / {total:,} bytes')
            records.append({'name':original.name,'file':str(dest/original.name),'bytes':snapshots[ext][2]})
        after=family_members(source)
        if set(after)!=set(members) or any(_revision(after[e].stat())!=snapshots[e] for e in members):
            raise ValueError('The source family changed while copying. Try again after editing has finished.')
        s.progress('Saving shapefile resource '+source.stem)
        if dest.exists(): raise ValueError('Destination already exists. Retry the copy.')
        temp.rename(dest);published=True
        existed='gis_resources' in s.STATE
        before=s.STATE.get('gis_resources',[])
        result={'id':ident,'name':source.stem,'kind':'shapefile','members':records,'bytes':total}
        s.STATE['gis_resources']=[*before,result]
        try:s.save()
        except BaseException:
            if existed:s.STATE['gis_resources']=before
            else:s.STATE.pop('gis_resources',None)
            raise
        published=False
        return result
    finally:
        if published:shutil.rmtree(dest)
        shutil.rmtree(temp,ignore_errors=True)


def listing(s):
    rows=[]
    for resource in s.STATE.get('gis_resources',[]):
        members=resource.get('members') or []
        if not members:continue
        rows.append({'name':resource.get('name') or 'Shapefile',
                     'folder':str(Path(members[0]['file']).parent),
                     'members':len(members),'bytes':int(resource.get('bytes',0))})
    return {'resources':rows}


def post(s,h,body):
    source=body.get('path')
    if not isinstance(source,str) or not source:
        raise ValueError('Choose the main .shp file.')
    if s.JOB.get('busy'):
        raise ValueError('Wait for the current operation to finish.')
    s.start_job(lambda:archive(s,source),'project_shapefile')
    h.send({'started':True})
    return True
