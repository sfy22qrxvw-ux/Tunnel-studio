# Copyright (c) 2026 Damian Gulczynski.
"""Named snapshots use immutable caches; portable .tstudio contains its own assets."""
from pathlib import Path, PurePosixPath, PureWindowsPath
import copy, hashlib, json, os, shutil, stat, time, uuid, zipfile, threading, re
from datetime import datetime,timezone
import storage_inventory as storage
PATH_KEYS={'results_file','face_flags_file','file','attributes_file','vertex_colours_file','mask_file','derived','before_mask','after_mask','mesh_file','point_ids_file','labels_file','truth_file','preview_file','vertices_file','faces_file','ids_file'}
# This is a metadata resource guard, not a scan-size or point-count limit.
# Cleaning clusters, review history and CAD surfaces can legitimately exceed
# the old 20 MB limit. Keep every project route on the same supported limit.
MAX_MANIFEST_BYTES=256_000_000
MAX_ARCHIVE_ENTRIES=5000
_IMPORT_FOLDERS=set()
_COPY_BLOCK_BYTES=4*1024*1024
_JSON_CHUNK_CHARS=64*1024
_PURGE_ROOT='.project_delete_queue'
_PURGE_META='purge.json'
_PURGE_LOCK=threading.Lock()
_PURGE_THREADS={}
_RETAINED_CACHES=(('retained_properties','mask_file'),('before_properties','before_mask'),
                  ('after_properties','after_mask'),('retained_summary','mask_file'),
                  ('before_summary','before_mask'),('after_summary','after_mask'))
_RETAINED_CACHE_KEYS=frozenset(key for key,_ in _RETAINED_CACHES)



_PREVIOUS_SESSION_RE=re.compile(r"(?: — previous session \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2})+$")

def is_previous_session_snapshot(data):
    """True only for the automatically generated historical startup labels.

    These were redundant named-project copies created by releases before 0.17.12.
    The real crash-recovery metadata lives under startup_recovery and remains hidden.
    """
    if not isinstance(data,dict):return False
    if data.get('snapshot_kind')=='startup_recovery':return True
    title=str(data.get('project_title') or '')
    return bool(_PREVIOUS_SESSION_RE.search(title))

def retire_legacy_previous_session_projects(work):
    """Remove obsolete *named-project* recovery manifests without deleting survey data.

    Old releases wrote a normal named project in addition to the byte-for-byte hidden
    startup recovery manifest.  Keeping those normal manifests made Open Project noisy
    and, more importantly, made stale caches look shared forever.  Only the tiny JSON
    manifest is retired; no referenced point/cache file is touched here.
    """
    root=Path(work).resolve();folder=root/'projects';retired=[]
    storage.normal_folder(folder)
    if not folder.is_dir():return retired
    audit=root/'diagnostics'/'retired_previous_sessions';storage.normal_folder(audit.parent);storage.normal_folder(audit)
    for path in folder.glob('*.json'):
        try:
            storage.regular_record(path,root)
            data=read_json(path)
            if data.get('format') or isinstance(data.get('state'),dict):data,_=normalize_manifest(data)
            if not is_previous_session_snapshot(data):continue
            target=audit/(path.stem+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
            # Same-volume rename is metadata-only.  The audit copy is deliberately outside
            # projects/startup_recovery so it cannot protect gigabytes of stale caches.
            audit.mkdir(parents=True,exist_ok=True)
            os.replace(path,target);retired.append({'project_id':path.stem,'title':data.get('project_title')})
        except (OSError,ValueError,TypeError,AttributeError):continue
    return retired

class MetadataTooLarge(ValueError):
    """The metadata guard was reached before replacing any saved file."""


def metadata_limit_error():
    size=(f'{MAX_MANIFEST_BYTES/1_000_000:g} MB' if MAX_MANIFEST_BYTES>=1_000_000
          else f'{MAX_MANIFEST_BYTES:,} bytes')
    return MetadataTooLarge('Project metadata exceeds the supported '+size+
                           ' metadata limit. The previous saved snapshot is unchanged. '
                           'This limit applies to project metadata, not scan point counts.')


def _json_chunks(data):
    """Encode incrementally without making a complete JSON string and byte copy.

    The Python object graph remains in memory. This bounds serialization buffers;
    it is not a whole-application memory bound or an incremental JSON parser.
    """
    encoder=json.JSONEncoder(allow_nan=False,ensure_ascii=False,separators=(',',':'))
    pieces=[];size=0
    for piece in encoder.iterencode(data):
        if len(piece)>_JSON_CHUNK_CHARS:
            if pieces:yield ''.join(pieces).encode('utf8');pieces=[];size=0
            for start in range(0,len(piece),_JSON_CHUNK_CHARS):
                yield piece[start:start+_JSON_CHUNK_CHARS].encode('utf8')
        else:
            pieces.append(piece);size+=len(piece)
            if size>=_JSON_CHUNK_CHARS:
                yield ''.join(pieces).encode('utf8');pieces=[];size=0
    if pieces:yield ''.join(pieces).encode('utf8')


def write_json(stream,data,progress=None):
    """Write checked UTF-8 chunks, stopping before the metadata guard is crossed."""
    total=0;reported=0
    for block in _json_chunks(data):
        total+=len(block)
        if total>MAX_MANIFEST_BYTES:raise metadata_limit_error()
        if stream is not None:stream.write(block)
        if progress is not None and total-reported>=_COPY_BLOCK_BYTES:
            progress(f'Packaging project metadata · {total/1_000_000:.1f} MB');reported=total
    return total


def validate_metadata(data):
    return write_json(None,data)


def _copy_stream(source,target,total,progress,label,digest=None):
    """Bounded copies with cancellation opportunities inside a single huge asset."""
    count=0;updated=time.monotonic()
    while True:
        block=source.read(_COPY_BLOCK_BYTES)
        if not block:break
        target.write(block)
        if digest is not None:digest.update(block)
        count+=len(block);now=time.monotonic()
        if count>=total or now-updated>=.5:
            progress(f'{label} · {count:,}/{total:,} bytes');updated=now
    if count!=total:raise ValueError('A project asset changed size while copying: '+label)


def visit(value,fn):
    # Recovery records deliberately preserve unavailable paths as information;
    # they must not become active dependencies of the recovered project.
    if isinstance(value,dict):
        out={k:copy.deepcopy(v) if k=='project_recovery' else fn(v) if k in PATH_KEYS and isinstance(v,str) and v else visit(v,fn) for k,v in value.items()}
        # Cached retained counts/bounds are tied to one immutable mask. Their
        # revision is a path only in this exact context, not a generic UUID.
        for props_key,mask_key in _RETAINED_CACHES:
            props=value.get(props_key)
            if isinstance(props,dict) and props.get('revision') and props['revision']==value.get(mask_key):out[props_key]['revision']=out[mask_key]
        return out
    if isinstance(value,list): return [visit(v,fn) for v in value]
    return value


def _retained_caches(value,transform,meta=None):
    """Transform caches with their source scan and the appropriate Undo mask."""
    if isinstance(value,dict):
        if value.get('kind')=='scan' and isinstance(value.get('meta'),dict):meta=value['meta']
        elif isinstance(value.get('file'),str) and type(value.get('count')) is int:meta=value
        for key,mask_key in _RETAINED_CACHES:
            if key not in value:continue
            replacement=transform(value[key],meta,value.get(mask_key) or '')
            if replacement is None:value.pop(key,None)
            else:value[key]=replacement
        for key,child in list(value.items()):
            if key!='project_recovery' and key not in _RETAINED_CACHE_KEYS:
                # The active survey duplicates its item's history at the
                # state root. Those entries belong to that state's cloud,
                # not to the last item encountered elsewhere in the tree.
                child_meta=value['cloud'] if key in ('edits','redo') and isinstance(value.get('cloud'),dict) else meta
                _retained_caches(child,transform,child_meta)
    elif isinstance(value,list):
        for child in value:_retained_caches(child,transform,meta)


def _cache_sources(cached,meta,revision):
    if not isinstance(cached,dict) or not isinstance(meta,dict) or cached.get('revision')!=revision:return None
    count=meta.get('count')
    if type(count) is not int or count<0:return None
    retained=cached.get('count')
    if type(retained) is not int or not 0<=retained<=count:return None
    sources=[]
    for key,width in (('file',24),('attributes_file',16),('mask_file',1)):
        path=revision if key=='mask_file' else meta.get(key)
        if not path:
            if key=='file':return None
            continue
        if not isinstance(path,str):return None
        sources.append((key,path,count*width))
    return count,sources


def _file_revision(info):
    return [info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def _portable_signature(count,rows):
    # A tagged signature is intentionally invalid to older releases, which
    # safely rebuild properties instead of trusting relocated local timestamps.
    return {'format':'TunnelStudioRetainedAssets','version':1,'count':count,'assets':rows}


def _pack_retained_caches(state,assets):
    """Bind only already-valid local caches to the bytes actually packaged."""
    def pack(cached,meta,revision):
        source=_cache_sources(cached,meta,revision)
        if source is None:return None
        count,sources=source;local=[];portable=[]
        for key,path,size in sources:
            asset=assets.get(path)
            if asset is None or asset['revision'][0]!=size:return None
            local.append([key,asset['source'],*asset['revision']])
            portable.append([key,path,size,asset['digest']])
        if cached.get('source_signature')!=[count,local]:return None
        cached['source_signature']=_portable_signature(count,portable)
        # These optional counts historically had no derived-file revision.
        # Preserve verified source metadata without promoting unverified
        # analysis statistics to a fresh local revision.
        cached.pop('derived',None);cached.pop('valid_points',None);cached.pop('derived_signature',None)
        return cached
    _retained_caches(state,pack)


def _unpack_retained_caches(state,assets):
    """Re-anchor portable caches only after all their assets pass integrity checks.

    Legacy/local signatures are not evidence of the copied bytes. Leave those
    caches absent so consumers calculate what they need for the new local data.
    """
    def unpack(cached,meta,revision):
        source=_cache_sources(cached,meta,revision)
        if source is None:return None
        count,sources=source;portable=[];local=[]
        for key,path,size in sources:
            asset=assets.get(path)
            if asset is None or asset['revision'][0]!=size:return None
            try:
                if _file_revision(Path(path).stat())!=asset['revision']:return None
            except OSError:return None
            portable.append([key,asset['member'],size,asset['digest']])
            local.append([key,str(Path(path).resolve()),*asset['revision']])
        if cached.get('source_signature')!=_portable_signature(count,portable):return None
        cached['source_signature']=[count,local]
        cached.pop('derived',None);cached.pop('valid_points',None);cached.pop('derived_signature',None)
        return cached
    _retained_caches(state,unpack)


def atomic_json(path,data):
    path=Path(path);tmp=path.with_name(path.name+'.tmp_'+uuid.uuid4().hex)
    try:
        with tmp.open('xb') as stream:
            write_json(stream,data);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def read_json(path):
    path=Path(path)
    if path.stat().st_size>MAX_MANIFEST_BYTES:raise metadata_limit_error()
    def invalid(value):raise ValueError('Project metadata contains a non-finite number: '+value)
    # Recheck actual bytes as well as stat, in case a source grows while opening.
    with path.open('rb') as stream:raw=stream.read(MAX_MANIFEST_BYTES+1)
    if len(raw)>MAX_MANIFEST_BYTES:raise metadata_limit_error()
    return json.loads(raw.decode('utf-8-sig'),parse_constant=invalid)


def normalize_manifest(value):
    """Read current v1 wrappers and known older raw-state/v0 layouts.

    Unknown future formats are not guessed. Application release numbers are
    independent of this portable-container version.
    """
    if not isinstance(value,dict):raise ValueError('Project metadata must contain an object.')
    warnings=[]
    if value.get('format') is not None:
        if value.get('format')!='TunnelStudioProject':raise ValueError('Not a Tunnel Studio project container.')
        version=value.get('version',0)
        if version not in (0,1,'0','1'):raise ValueError('This project uses an unsupported container version. Open it with a compatible Tunnel Studio version.')
        state=value.get('state')
        if version in (0,'0'):warnings.append('Opened a legacy version-0 project container and upgraded its metadata in memory.')
    elif isinstance(value.get('state'),dict) and not any(k in value for k in ('axis','cloud','job_items','cad')):
        state=value['state'];warnings.append('Opened a legacy project state wrapper.')
    else:
        state=value;warnings.append('Opened a legacy raw project snapshot.')
    if not isinstance(state,dict) or not any(k in state for k in ('axis','cloud','job_items','cad','project_title','registration','structures','tunnel_design')):
        raise ValueError('The file does not contain recognizable Tunnel Studio project data.')
    return copy.deepcopy(state),warnings


def save_named(work,data,name=None,as_new=False):
    data=copy.deepcopy(data); title=str(name or data.get('project_title') or 'Untitled project').strip()[:120]
    if not title: raise ValueError('Enter a project name.')
    ident=uuid.uuid4().hex if as_new or not data.get('project_id') else data['project_id']
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('Invalid project ID.')
    data.update(project_id=ident,project_title=title,saved_at=datetime.now(timezone.utc).isoformat())
    __import__('project_browser').attach(data,work)
    folder=Path(work)/'projects'; folder.mkdir(exist_ok=True)
    try:atomic_json(folder/(ident+'.json'),data)
    except MetadataTooLarge:
        if 'project_preview' not in data:raise
        # Optional display metadata cannot stop an otherwise valid named save.
        # atomic_json has preserved the old record and removed its temporary.
        data.pop('project_preview',None)
        atomic_json(folder/(ident+'.json'),data)
    return data


def listing(work):
    # Pre-0.17.12 releases created redundant named "previous session" projects.
    # Retire those tiny manifests before listing so recovery stays internal.
    retire_legacy_previous_session_projects(work)
    result=[]
    for path in (Path(work)/'projects').glob('*.json'):
        try:
            d=read_json(path)
            if d.get('format') or isinstance(d.get('state'),dict):d,_=normalize_manifest(d)
            if is_previous_session_snapshot(d):continue
            if not d.get('project_id'):d['project_id']=path.stem
            result.append({k:d.get(k) for k in ('project_id','project_title','saved_at')})
        except (OSError,ValueError,TypeError,AttributeError): continue
    return sorted(result,key=lambda d:d.get('saved_at') or '',reverse=True)


def read_named(work,ident):
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('Invalid project ID.')
    value=read_json(Path(work)/'projects'/(ident+'.json'))
    # Named snapshots are normally raw state; preserve them without treating
    # their lack of a portable wrapper as a migration warning.
    if isinstance(value,dict) and ('format' in value or ('state' in value and 'axis' not in value)):
        value,warnings=normalize_manifest(value);value['_project_import']={'warnings':warnings}
    if not isinstance(value,dict):raise ValueError('The saved project snapshot is damaged.')
    return value


def portable(work,data,destination,progress=lambda s:None,*,publish=None):
    root=Path(work).resolve(); files={}
    def map_path(value):
        p=Path(value).resolve()
        if not p.is_relative_to(root) or not p.is_file(): raise ValueError('A project asset is unavailable: '+str(value)+'. Open a recovered project or restore that cache before exporting a portable copy.')
        key=str(p)
        if key not in files: files[key]=f'assets/{len(files):04d}{p.suffix}'
        return files[key]
    clean=copy.deepcopy(data); clean.pop('export_ready',None); clean.pop('last_backup',None);clean.pop('_project_import',None); clean.update(report_ready=False,clean_ready=False)
    __import__('project_browser').attach(clean,work,progress)
    clean=visit(clean,map_path); manifest={'format':'TunnelStudioProject','version':1,'state':clean}
    try:validate_metadata(manifest)
    except MetadataTooLarge:
        if 'project_preview' not in clean:raise
        clean.pop('project_preview',None)
        validate_metadata(manifest)
    if len(files)+1>MAX_ARCHIVE_ENTRIES:raise ValueError('This project contains too many linked cache files for one portable archive.')
    if publish is not None:
        # ZIP_STORED includes each distinct referenced asset once. Space on
        # the selected drive is required while an older copy is still intact.
        asset_bytes=sum(Path(path).stat().st_size for path in files)
        free_bytes=shutil.disk_usage(Path(destination).parent).free
        if free_bytes < asset_bytes:
            raise ValueError(f'The project assets need at least {asset_bytes/1e9:.2f} GB '
                             f'on the destination drive. It has {free_bytes/1e9:.2f} GB free.')
    temp=Path(str(destination)+'.tmp_'+uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
            verified={}
            for i,(path,name) in enumerate(files.items()):
                label=f'Packaging project asset {i+1}/{len(files)}';progress(label)
                digest=hashlib.sha256()
                with open(path,'rb') as src,z.open(name,'w',force_zip64=True) as out:
                    before=os.fstat(src.fileno())
                    _copy_stream(src,out,before.st_size,progress,label,digest)
                    if (_file_revision(os.fstat(src.fileno()))!=_file_revision(before) or
                            _file_revision(Path(path).stat())!=_file_revision(before)):
                        raise ValueError('A project asset changed while copying: '+label)
                verified[name]={'source':path,'revision':_file_revision(before),'digest':digest.hexdigest()}
            _pack_retained_caches(clean,verified)
            progress('Packaging project metadata')
            with z.open('project.json','w',force_zip64=True) as out:write_json(out,manifest,progress)
        if publish is None:
            os.replace(temp,destination)
        else:
            with temp.open('rb+') as completed:
                os.fsync(completed.fileno())
            progress('Finishing portable project copy…')
            publish(temp, destination)
    finally: temp.unlink(missing_ok=True)


def _safe_archive_name(name):
    q=PurePosixPath(name)
    if q.is_absolute() or not q.parts or '..' in q.parts or '\\' in name or ':' in name or '\x00' in name or q.as_posix()!=name.rstrip('/'):
        raise ValueError('Unsafe project archive path: '+name)
    return q


# Full-density assets from one unpack can occupy several category folders.
# Only folders registered during this live operation may be rolled back.
_IMPORT_CATEGORY_DIRS={}


def _import_targets(work,dest,state):
    from project_data_layout import category_for_state_asset_map, folder, gis_member_names
    if not (work/'.tunnelstudio-workspace.json').is_file():
        return lambda source,rel: dest/rel
    categories=category_for_state_asset_map(state)
    gis=gis_member_names(state)
    owned=_IMPORT_CATEGORY_DIRS.setdefault(str(dest),set())
    used=set()
    def target(source,rel):
        kind=categories.get(source,'working')
        base=folder(work,kind)/dest.name
        if base not in owned:
            base.mkdir()
            owned.add(base)
        relative=gis.get(source,rel)
        path=base/relative
        key=str(path).casefold()
        if key in used:raise ValueError('Two project members would overwrite the same file.')
        used.add(key)
        path.parent.mkdir(parents=True,exist_ok=True)
        return path
    return target


def _remove_import_dirs(dest):
    for folder in _IMPORT_CATEGORY_DIRS.pop(str(dest),set()):
        shutil.rmtree(folder,ignore_errors=True)
    shutil.rmtree(dest,ignore_errors=True)
    _IMPORT_FOLDERS.discard(str(dest))


def cleanup_import(work,data):
    marker=data.get('_project_import',{}) if isinstance(data,dict) else {}
    root=marker.get('assets_root')
    if root:
        p=Path(root).resolve();base=Path(work).resolve()
        if str(p) in _IMPORT_FOLDERS and p.parent==base and p.name.startswith('project_assets_'):_remove_import_dirs(p)
        _IMPORT_FOLDERS.discard(str(p))


def release_import(data):
    """A successfully installed folder is no longer owned by a pending open."""
    token=str((data.get('_project_import') or {}).get('assets_root',''))
    _IMPORT_FOLDERS.discard(token)
    if _IMPORT_CATEGORY_DIRS.pop(token,None) is not None:
        try:Path(token).rmdir()
        except OSError:pass


def unpack(work,path,progress=lambda s:None):
    work=Path(work).resolve();path=Path(path)
    dest=work/('project_assets_'+uuid.uuid4().hex); dest.mkdir();_IMPORT_FOLDERS.add(str(dest))
    try:
        if not zipfile.is_zipfile(path):
            state,warnings=normalize_manifest(read_json(path));mapped={}
            new_path=_import_targets(work,dest,state)
            source_root=path.resolve().parent
            if source_root.name=='projects' and len(path.stem)==32 and all(c in '0123456789abcdef' for c in path.stem):
                source_root=source_root.parent
            roots=(work,source_root)
            def link(value):
                if value in mapped:return mapped[value]
                raw=Path(value);name=PureWindowsPath(value).name if '\\' in value else raw.name
                choices=[raw] if raw.is_absolute() else [path.resolve().parent/raw,source_root/raw,work/raw]
                choices.extend([r/name for r in roots])
                candidates=[]
                for candidate in choices:
                    p=candidate.resolve()
                    if any(p.is_relative_to(root) for root in roots) and p.is_file() and p not in candidates:candidates.append(p)
                target=new_path(value,f'{len(mapped):04d}_'+name)
                if len(candidates)==1:
                    label='Copying legacy project asset '+name;progress(label)
                    with candidates[0].open('rb') as src,target.open('wb') as out:_copy_stream(src,out,candidates[0].stat().st_size,progress,label)
                else:warnings.append(('Missing' if not candidates else 'Ambiguous')+' legacy linked asset: '+value)
                mapped[value]=str(target);return str(target)
            state=visit(state,link)
            _unpack_retained_caches(state,{})
            state.update(project_id=None,project_title=str(state.get('project_title') or path.stem or 'Imported project')[:120],report_ready=False,clean_ready=False,export_ready=None)
            state['_project_import']={'assets_root':str(dest),'warnings':warnings,'source_name':path.name}
            return state
        with zipfile.ZipFile(path) as z:
            entries=z.infolist(); names=[i.filename for i in entries]
            if len(entries)>MAX_ARCHIVE_ENTRIES or len(names)!=len({n.casefold() for n in names}): raise ValueError('Invalid or duplicate project archive entries.')
            for entry in entries:
                _safe_archive_name(entry.filename)
                if stat.S_IFMT(entry.external_attr>>16)==stat.S_IFLNK:raise ValueError('Project archives cannot contain symbolic links.')
            candidates=[n for n in names if PurePosixPath(n).name=='project.json' and not n.endswith('/')]
            if len(candidates)!=1:raise ValueError('Not a supported Tunnel Studio project manifest.')
            if z.getinfo(candidates[0]).file_size>MAX_MANIFEST_BYTES:raise metadata_limit_error()
            manifest_name=candidates[0]
            def invalid(value):raise ValueError('Project metadata contains a non-finite number: '+value)
            with z.open(manifest_name) as source:raw=source.read(MAX_MANIFEST_BYTES+1)
            if len(raw)>MAX_MANIFEST_BYTES:raise metadata_limit_error()
            manifest=json.loads(raw.decode('utf-8-sig'),parse_constant=invalid)
            del raw
            state,warnings=normalize_manifest(manifest);referenced=set();mapped={};member_paths={}
            new_path=_import_targets(work,dest,state)
            files={n for n in names if not n.endswith('/') and n!=manifest_name}
            basenames={}
            for name in files:basenames.setdefault(PurePosixPath(name).name.casefold(),[]).append(name)
            def check(v):
                if v in mapped:return mapped[v]
                # Portable v1 references are relative; older snapshots sometimes
                # retained an absolute Windows cache path. Relink only to a
                # uniquely matching member already contained in this archive.
                q=PurePosixPath(v)
                if '..' in q.parts or '..' in PureWindowsPath(v).parts or '\x00' in v:raise ValueError('Invalid project asset reference.')
                name=v if v in files else None
                if name is None:
                    base=PureWindowsPath(v).name if '\\' in v else q.name
                    matches=basenames.get(base.casefold(),[])
                    if len(matches)==1:
                        name=matches[0];warnings.append('Relinked legacy cache reference: '+base)
                    elif len(matches)>1:warnings.append('Ambiguous linked cache reference: '+v)
                if name is None:
                    target=dest/'unavailable'/f'{len(mapped):04d}.missing';warnings.append('Missing linked project asset: '+v)
                else:
                    _safe_archive_name(name)
                    if name not in member_paths:
                        member_paths[name]=new_path(v,PurePosixPath(name))
                    target=member_paths[name];referenced.add(name)
                mapped[v]=str(target);return str(target)
            state=visit(state,check)
            total=sum(z.getinfo(n).file_size for n in referenced)
            if total>shutil.disk_usage(work).free-50_000_000: raise ValueError('Not enough free disk space to open this project.')
            verified={}
            for i,name in enumerate(sorted(referenced)):
                label=f'Opening project asset {i+1}/{len(referenced)}';progress(label)
                target=member_paths[name];target.parent.mkdir(parents=True,exist_ok=True)
                try:
                    digest=hashlib.sha256()
                    with z.open(name) as src,target.open('wb') as out:
                        _copy_stream(src,out,z.getinfo(name).file_size,progress,label,digest)
                    verified[str(target)]={'member':name,'revision':_file_revision(target.stat()),'digest':digest.hexdigest()}
                except (zipfile.BadZipFile,RuntimeError,EOFError) as exc:
                    target.unlink(missing_ok=True);warnings.append('Damaged linked asset '+name+': '+str(exc))
            _unpack_retained_caches(state,verified)
            state.update(project_id=None,project_title=str(state.get('project_title','Imported project'))[:120],report_ready=False,clean_ready=False,export_ready=None)
            state['_project_import']={'assets_root':str(dest),'warnings':list(dict.fromkeys(warnings)),'source_name':path.name}
            return state
    except BaseException:
        _remove_import_dirs(dest);raise


# --- Project storage management (0.17.9) -------------------------------------
def _project_id(ident):
    ident=str(ident or '')
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid project ID.')
    return ident

class RecoveryOwnershipError(ValueError):
    pass


def _originals(root,data):
    """Paths explicitly identified as original inputs are never disposable."""
    return _local_asset_paths(root,data,originals_only=True)


def _local_asset_paths(work,data,owned_only=False,originals_only=False):
    """Any reference protects a file; only verified generated outputs prove ownership."""
    root=Path(work).resolve(); found=set();seen=set()
    def walk(value,key=None):
        if isinstance(value,dict):
            for k,v in value.items():walk(v,k)
        elif isinstance(value,(tuple,list)):
            for v in value:walk(v,key)
        elif isinstance(value,str) and key=='original_data_json':
            if owned_only:return  # recovery JSON protects references, but does not prove ownership
            try:decoded=json.loads(value)
            except (ValueError,TypeError,RecursionError) as error:
                raise RecoveryOwnershipError('A project recovery ownership record contains invalid JSON. Restore or repair the project metadata before deleting working files.') from error
            if isinstance(decoded,(dict,list)):walk(decoded)
        elif isinstance(value,str) and value:
            if owned_only and key not in PATH_KEYS:return
            if originals_only and key not in ('source_path','original_path','original_file','input_path','external_path'):return
            if value in seen:return
            seen.add(value)
            try:
                raw=Path(value)
                if not raw.is_absolute():raw=root/raw
                q=raw.resolve()
                if q.is_relative_to(root) and q.is_file():
                    if owned_only and (not storage.safe_path(raw,root) or not storage.eligible(root,q)):return
                    found.add(q)
                    if q.suffix.lower()=='.xyz64':
                        sidecar=Path(str(q)+'.viewindex.npz')
                        if sidecar.is_file() and (not owned_only or storage.eligible(root,sidecar)):found.add(sidecar)
            except (OSError,ValueError,TypeError,RuntimeError):pass
    walk(data);return found


def _storage_reference_error(path,error):
    if isinstance(error,RecoveryOwnershipError):return error
    return ValueError("Storage cleanup is blocked because project metadata '"+Path(path).name+
                      "' could not be read: "+str(error)+
                      '. Recover or repair that project before deleting shared data. No survey/cache files were deleted.')


def _read_named_asset_sets(work,strict=False,maintain=True):
    """Read real named project metadata once and return referenced local assets.

    Historical automatic previous-session manifests are not projects and must not keep
    stale multi-gigabyte caches alive.  Hidden startup recovery is protected separately.
    """
    root=Path(work).resolve();out={}
    folder=root/'projects'
    storage.normal_folder(folder)
    if maintain:retire_legacy_previous_session_projects(root)
    if not folder.is_dir():return out
    for path in folder.glob('*.json'):
        try:
            storage.regular_record(path,root)
            data=read_json(path)
            if data.get('format') or isinstance(data.get('state'),dict):data,_=normalize_manifest(data)
            if is_previous_session_snapshot(data):continue
            ident=str(data.get('project_id') or path.stem)
            if ident!=path.stem or not re.fullmatch('[a-f0-9]{32}',ident) or ident in out:
                if strict:raise ValueError('Invalid saved project identity.')
                continue
            out[ident]={'path':path,'data':data,'assets':_local_asset_paths(root,data),'owned':_local_asset_paths(root,data,owned_only=True),'originals':_originals(root,data)}
        except (OSError,ValueError,TypeError,AttributeError) as error:
            if strict:raise _storage_reference_error(path,error) from error
    return out


def prune_hidden_recoveries(work,keep=1):
    """Keep only the newest hidden startup recovery metadata files."""
    root=Path(work).resolve()/'startup_recovery'
    if not root.is_dir():return []
    rows=[]
    for p in root.glob('*.json'):
        try:rows.append((p.stat().st_mtime_ns,p.name,p))
        except OSError:pass
    rows.sort(reverse=True);removed=[]
    for _,_,path in rows[max(0,int(keep)):]:
        try:path.unlink();removed.append(str(path))
        except OSError:pass
    return removed


def _hidden_recovery_entries(work,strict=False,prune=True):
    """Read the latest recovery once, retaining its project ownership.

    An explicit named-project deletion also owns that project's recovery.  A
    recovery for another project, or an unnamed workspace, remains protected.
    """
    root=Path(work).resolve()
    recovery=root/'startup_recovery'
    storage.normal_folder(recovery)
    if not recovery.is_dir():return []
    out=[];candidates=[]
    for path in recovery.glob('*.json'):
        try:
            storage.regular_record(path,root)
            candidates.append((path.stat().st_mtime_ns,path.name,path))
        except OSError as error:
            if strict:raise _storage_reference_error(path,error) from error
    # Validate the authoritative latest recovery before retiring older metadata.
    # A malformed newest snapshot must never turn its unknown references into
    # apparently unreferenced data eligible for destruction.
    ordered=sorted(candidates,reverse=True)
    for _,_,path in (ordered[:1] if prune else ordered):
        try:
            data=read_json(path)
            if data.get('format') or isinstance(data.get('state'),dict):data,_=normalize_manifest(data)
            out.append({'path':path,'project_id':data.get('project_id'),'assets':_local_asset_paths(root,data),'owned':_local_asset_paths(root,data,owned_only=True),'originals':_originals(root,data)})
        except (OSError,ValueError,TypeError,AttributeError) as error:
            if strict:raise _storage_reference_error(path,error) from error
            return []
    if prune:prune_hidden_recoveries(root,1)
    return out


def _current_and_recovery_assets(work,current_state=None,exclude_ids=(),strict=False,prune=True):
    root=Path(work).resolve();exclude=set(exclude_ids or ());protected=set()
    try:
        active=root/'project.json'
        records=[current_state] if isinstance(current_state,dict) else []
        if active.exists() or active.is_symlink():
            storage.regular_record(active,root)
            records.append(read_json(active))
        for current in records:
            if strict and current is not None and not isinstance(current,dict):raise ValueError('Invalid current workspace metadata.')
            if isinstance(current,dict) and current.get('project_id') not in exclude:
                protected.update(_local_asset_paths(root,current))
    except (OSError,ValueError,TypeError,AttributeError) as error:
        if strict:raise _storage_reference_error(root/'project.json',error) from error
    for entry in _hidden_recovery_entries(root,strict=strict,prune=prune):
        if entry['project_id'] not in exclude:protected.update(entry['assets'])
    return protected


def _asset_bytes(items):
    out=0;linked=set()
    for q in items:
        try:
            info=q.stat()
            if info.st_nlink>1 and info.st_ino:
                key=(info.st_dev,info.st_ino)
                if key in linked:continue
                linked.add(key)
            out+=info.st_size
        except OSError:pass
    return out


def _protected_asset_paths(work,exclude_project=None,current_state=None):
    exclude={exclude_project} if exclude_project else set()
    named=_read_named_asset_sets(work,strict=True);protected=set()
    for ident,entry in named.items():
        if ident not in exclude:protected.update(entry['assets'])
    protected.update(_current_and_recovery_assets(work,current_state,exclude,strict=True,prune=False))
    return protected


def projects_storage_overview(work,current_state=None):
    """Return project rows with total, individually reclaimable and shared bytes.

    One metadata/reference pass is used for the complete Open Project list so displaying
    sizes does not run a separate project-storage review for every row.
    """
    root=Path(work).resolve();named=_read_named_asset_sets(root,maintain=False)
    recoveries=_hidden_recovery_entries(root,prune=False)
    current_assets=set()
    if isinstance(current_state,dict):
        try:current_assets=_local_asset_paths(root,current_state)
        except (OSError,ValueError,TypeError,AttributeError):current_assets=set()
    refs={}
    for ident,entry in named.items():
        for q in entry['assets']:refs.setdefault(q,set()).add(ident)
    rows=[]
    current_id=current_state.get('project_id') if isinstance(current_state,dict) else None
    for ident,entry in named.items():
        data=entry['data'];assets=set(entry['assets']);owned=set(entry['owned']);originals=set(entry['originals'])
        if ident==current_id:
            assets.update(current_assets);owned.update(_local_asset_paths(root,current_state,owned_only=True));originals.update(_originals(root,current_state))
        recovery_only=set()
        for recovery in recoveries:
            if recovery['project_id']==ident:assets.update(recovery['assets']);owned.update(recovery['owned']);originals.update(recovery['originals'])
            else:recovery_only.update(recovery['assets'])
        exclusive=set();shared=set()
        for q in assets:
            other_named=refs.get(q,set())-{ident}
            # Current autosave protects the asset only when it belongs to another
            # project; an autosave of this same named project must not self-protect.
            current_protect=(q in recovery_only) or (current_id!=ident and q in current_assets)
            if other_named or current_protect or q not in owned or q in originals:shared.add(q)
            else:exclusive.add(q)
        meta_bytes=_asset_bytes([entry['path']]+[r['path'] for r in recoveries if r['project_id']==ident])
        total=_asset_bytes(assets)
        rows.append({
            'project_id':ident,**__import__('project_browser').summary(data,entry['path']),
            'saved_at':data.get('saved_at'),'active':ident==current_id,
            'asset_files':len(assets),'total_bytes':total,'exclusive_bytes':_asset_bytes(exclusive),
            'shared_bytes':_asset_bytes(shared),'metadata_bytes':meta_bytes,
        })
    return sorted(rows,key=lambda d:d.get('saved_at') or '',reverse=True)


def projects_storage_review(work,idents,current_state=None,*,_stamps=None):
    """Review one or more projects as a single deletion transaction.

    Data shared only among the selected projects becomes reclaimable. Data referenced
    by any unselected project, current workspace, or recovery snapshot remains protected.
    """
    root=Path(work).resolve();ids=[]
    for raw in idents if isinstance(idents,(list,tuple,set)) else [idents]:
        ident=_project_id(raw)
        if ident not in ids:ids.append(ident)
    if not ids:raise ValueError('Select at least one saved project.')
    selected=set(ids);named=_read_named_asset_sets(root,strict=True,maintain=False)
    missing=[i for i in ids if i not in named]
    if missing:raise ValueError('One or more selected projects no longer exist.')
    selected_assets=set();owned=set();originals=set()
    for ident in ids:
        selected_assets.update(named[ident]['assets']);owned.update(named[ident]['owned']);originals.update(named[ident]['originals'])
    selected_recoveries=[r for r in _hidden_recovery_entries(root,strict=True,prune=False) if r['project_id'] in selected]
    for recovery in selected_recoveries:
        selected_assets.update(recovery['assets']);owned.update(recovery['owned']);originals.update(recovery['originals'])
    current_id=current_state.get('project_id') if isinstance(current_state,dict) else None
    if current_id in selected:
        try:
            selected_assets.update(_local_asset_paths(root,current_state));owned.update(_local_asset_paths(root,current_state,owned_only=True));originals.update(_originals(root,current_state))
        except (OSError,ValueError,TypeError,AttributeError):pass
    protected=set()
    for ident,entry in named.items():
        if ident not in selected:protected.update(entry['assets'])
    protected.update(_current_and_recovery_assets(root,current_state,selected,strict=True,prune=False))
    protected.update(selected_assets-owned);protected.update(originals)
    exclusive=sorted(selected_assets-protected,key=lambda q:str(q).lower())
    shared=sorted(selected_assets&protected,key=lambda q:str(q).lower())
    titles=[named[i]['data'].get('project_title') or 'Untitled project' for i in ids]
    metadata_bytes=_asset_bytes([named[i]['path'] for i in ids]+[r['path'] for r in selected_recoveries])
    return {
        'review_token':storage.review_receipt(exclusive+[named[i]['path'] for i in ids]+[r['path'] for r in selected_recoveries],ids,_stamps),
        'sample_files':_file_sample(exclusive),
        'project_ids':ids,'project_count':len(ids),'project_titles':titles,
        'project_title':titles[0] if len(titles)==1 else f'{len(titles)} projects',
        'active':bool(current_id in selected),
        'total_files':len(selected_assets),'total_bytes':_asset_bytes(selected_assets),
        'exclusive_files':len(exclusive),'exclusive_bytes':_asset_bytes(exclusive),
        'shared_files':len(shared),'shared_bytes':_asset_bytes(shared),
        'metadata_bytes':metadata_bytes,'exclusive_paths':[str(q) for q in exclusive],
    }


def _file_sample(paths):
    import heapq
    return [dict(path=name,bytes=size) for size,name in heapq.nlargest(100,((p.stat().st_size,str(p)) for p in paths))]


def project_storage_review(work,ident,current_state=None):
    review=projects_storage_review(work,[ident],current_state)
    review['project_id']=review['project_ids'][0]
    return review


def _purge_meta(stage):return stage/_PURGE_META


def _write_purge_meta(stage,meta):
    atomic_json(_purge_meta(stage),meta)


def _purge_stage(stage):
    stage=Path(stage)
    try:
        if not _committed_purge(stage):return
        # Keep the tiny metadata file until the end so Open Project can report that
        # physical disk reclamation is still running.
        try:children=list(stage.iterdir())
        except FileNotFoundError:return  # Already purged/removed before the worker started.
        for child in children:
            if child.name==_PURGE_META:continue
            # rmtree does not follow directory symlinks or Windows junctions.
            # Refuse unexpected link entries at the queue root as well.
            try:
                if storage.is_link(child.lstat()):return
                if child.is_dir():shutil.rmtree(child)
                else:child.unlink(missing_ok=True)
            except OSError:return  # keep the commit marker for a later retry
        _purge_meta(stage).unlink(missing_ok=True)
        try:stage.rmdir()
        except OSError:pass
    finally:
        with _PURGE_LOCK:_PURGE_THREADS.pop(str(stage),None)


def _schedule_purge(stage):
    stage=Path(stage)
    key=str(stage)
    with _PURGE_LOCK:
        t=_PURGE_THREADS.get(key)
        if t and t.is_alive():return
        t=threading.Thread(target=_purge_stage,args=(stage,),daemon=True,name='TunnelStudio-storage-purge')
        _PURGE_THREADS[key]=t;t.start()


def _committed_purge(stage):
    """An old prefix or an interrupted, partly moved stage is not a commit."""
    if not re.fullmatch(r'(?:delete|orphans|select)_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}|items_[0-9a-f]{32}|legacy_(?:delete_project|clean_orphans)_[A-Za-z0-9_-]+_[0-9a-f]{8}',stage.name):
        return False
    try:
        if not storage.safe_path(_purge_meta(stage),stage.parent.parent):return False
        meta=read_json(_purge_meta(stage))
        return isinstance(meta,dict) and type(meta.get('bytes')) is int and meta['bytes']>=0 and bool(meta.get('label'))
    except (OSError,ValueError,TypeError):return False


def resume_storage_purges(work):
    root=Path(work).resolve()/_PURGE_ROOT
    storage.normal_folder(root)
    if not root.is_dir():return
    for stage in root.iterdir():
        if _committed_purge(stage):_schedule_purge(stage)


def storage_purge_status(work):
    root=Path(work).resolve()/_PURGE_ROOT;items=[];total=0
    if root.is_dir():
        for stage in root.iterdir():
            if not _committed_purge(stage):continue
            meta={}
            try:meta=read_json(_purge_meta(stage))
            except Exception:pass
            b=int(meta.get('bytes') or 0);total+=b
            items.append({'id':stage.name,'bytes':b,'label':meta.get('label') or 'Deleted project data','created_at':meta.get('created_at')})
    return {'running':bool(items),'jobs':len(items),'bytes':total,'items':items}


def delete_projects_permanently(work,idents,current_state=None,expected_bytes=None,expected_token=None):
    """Remove selected projects immediately from the workspace and purge in background.

    Exclusive files are renamed into a same-volume quarantine first. Renaming is a
    metadata operation, so the UI can return quickly even for hundreds of gigabytes.
    The physical disk release then runs on a daemon thread and resumes on next use if
    Tunnel Studio is closed before it completes.
    """
    root=Path(work).resolve();stamps={}
    if root.is_relative_to(storage.program_root()):raise ValueError('The program installation cannot be cleaned as a workspace.')
    review=projects_storage_review(root,idents,current_state,_stamps=stamps)
    if (expected_token is not None and expected_token!=review['review_token']) or (expected_bytes is not None and int(expected_bytes)!=int(review['exclusive_bytes'])):
        raise ValueError('Project storage changed since the delete review. Review the selected projects again before deleting them.')
    purge_root=root/_PURGE_ROOT;storage.normal_folder(purge_root);purge_root.mkdir(exist_ok=True)
    stage=purge_root/('delete_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex)
    stage.mkdir();moved=[];manifests=[]
    try:
        for raw in review['exclusive_paths']:
            src=Path(raw)
            if not storage.eligible(root,src) or storage.deletion_stamp(src.lstat())!=stamps[src]:raise ValueError('A project asset changed; review storage again.')
            rel=src.relative_to(root);dst=stage/'assets'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dst);moved.append((src,dst))
        for ident in review['project_ids']:
            src=root/'projects'/(ident+'.json')
            storage.regular_record(src,root)
            if storage.deletion_stamp(src.lstat())!=stamps[src]:raise ValueError('A named project changed; review storage again.')
            dst=stage/'manifests'/(ident+'.json');dst.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dst);manifests.append((src,dst))
        # Retire only recovery metadata owned by an explicitly selected project.
        # Leaving it live would pin the deleted project's caches indefinitely.
        for recovery in _hidden_recovery_entries(root,strict=True,prune=False):
            if recovery['project_id'] not in review['project_ids']:continue
            src=recovery['path']
            if storage.deletion_stamp(src.lstat())!=stamps.get(src):raise ValueError('A recovery record changed; review storage again.')
            dst=stage/'recovery'/src.name;dst.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dst);manifests.append((src,dst))
        _write_purge_meta(stage,{
            'bytes':review['exclusive_bytes']+review['metadata_bytes'],
            'label':review['project_title'],'project_ids':review['project_ids'],
            'created_at':datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        for src,dst in reversed(manifests+moved):
            try:
                src.parent.mkdir(parents=True,exist_ok=True)
                if dst.exists() and not src.exists():os.replace(dst,src)
            except OSError:pass
        if not any(dst.exists() for _,dst in manifests+moved):shutil.rmtree(stage,ignore_errors=True)
        raise
    _schedule_purge(stage)
    review.pop('exclusive_paths',None)
    review.update(purge_pending=True,purge_bytes=review['exclusive_bytes']+review['metadata_bytes'])
    return review


def delete_project_permanently(work,ident,current_state=None,expected_bytes=None):
    out=delete_projects_permanently(work,[ident],current_state,expected_bytes)
    out['project_id']=out['project_ids'][0]
    return out

def orphan_storage_review(work,current_state=None,*,_stamps=None):
    root=Path(work).resolve()
    protected=_protected_asset_paths(root,None,current_state)
    files=[];warnings=[]
    for path,info in storage.files_in(root,warnings):
        if path not in protected and info.st_nlink==1 and storage.owned_name(root,path):
            files.append(path)
    # Missing/reparse *files* cannot be proven disposable. The walk skips their
    # entire subtree; it never stages a directory or follows the link target.
    size=_asset_bytes(files)
    return {'files':len(files),'folders':0,'bytes':size,'paths':[str(q) for q in files],
            'review_token':storage.review_receipt(files,('orphans',),_stamps), 'sample_files':_file_sample(files)}


def clean_orphan_storage(work,current_state=None,expected_bytes=None,expected_token=None):
    root=Path(work).resolve();stamps={}
    if root.is_relative_to(storage.program_root()):raise ValueError('The program installation cannot be cleaned as a workspace.')
    review=orphan_storage_review(root,current_state,_stamps=stamps)
    if (expected_token is not None and expected_token!=review['review_token']) or (expected_bytes is not None and int(expected_bytes)!=int(review['bytes'])):
        raise ValueError('Project storage changed since the cleanup review. Review storage again before cleaning.')
    purge_root=root/_PURGE_ROOT;storage.normal_folder(purge_root);purge_root.mkdir(exist_ok=True)
    stage=purge_root/('orphans_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex)
    stage.mkdir();moved=[]
    try:
        for raw in review['paths']:
            src=Path(raw)
            if not storage.eligible(root,src) or storage.deletion_stamp(src.lstat())!=stamps[src]:raise ValueError('An unreferenced cache changed; review storage again.')
            rel=src.relative_to(root);dst=stage/'assets'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dst);moved.append((src,dst))
        _write_purge_meta(stage,{'bytes':review['bytes'],'label':'Unreferenced Tunnel Studio data','created_at':datetime.now(timezone.utc).isoformat()})
    except Exception:
        for src,dst in reversed(moved):
            try:
                src.parent.mkdir(parents=True,exist_ok=True)
                if dst.exists() and not src.exists():os.replace(dst,src)
            except OSError:pass
        if not any(dst.exists() for _,dst in moved):shutil.rmtree(stage,ignore_errors=True)
        raise
    _schedule_purge(stage)
    review.pop('paths',None);review.update(purge_pending=True,purge_bytes=review['bytes']);return review

# --- Universal imported-asset storage overview (0.17.12) ---------------------
def _source_format(meta,name=''):
    value=str((meta or {}).get('source_format') or '').strip().lower()
    if value:return value
    suffix=Path(str((meta or {}).get('name') or name or '')).suffix.lower().lstrip('.')
    return suffix or ('mesh' if (meta or {}).get('vertices_file') else 'point_cloud')


def workspace_storage_overview(work,current_state=None):
    """Describe storage used by imported workspace items regardless of source format.

    All supported scan formats are converted to the same immutable internal cache model,
    and meshes use the same reference-aware ownership rules.  This view is informational:
    project deletion/cleanup remains the operation that can physically reclaim shared data.
    """
    root=Path(work).resolve();state=current_state if isinstance(current_state,dict) else {}
    named=_read_named_asset_sets(root)
    protected=set()
    for entry in named.values():protected.update(entry['assets'])
    protected.update(_current_and_recovery_assets(root,{},()))
    rows=[];seen_sets=[];source_sizes={}
    def add_source_size(meta,fallback,key):
        size=int(meta.get('source_bytes') or fallback or 0)
        # E57 setups and virtual copies share their source file. Count it once
        # in the workspace total while retaining per-item provenance below.
        source_key=('path',str(meta['source_path'])) if meta.get('source_path') else ('item',key)
        source_sizes[source_key]=max(size,source_sizes.get(source_key,0))
        return size
    for entry in state.get('job_items') or []:
        if not isinstance(entry,dict):continue
        meta=entry.get('meta') if isinstance(entry.get('meta'),dict) else {}
        assets=_local_asset_paths(root,entry);seen_sets.append(assets)
        total=_asset_bytes(assets);shared=_asset_bytes(assets & protected)
        rows.append({
            'id':entry.get('id'),'name':entry.get('name') or meta.get('name') or 'Imported item',
            'kind':entry.get('kind') or ('mesh' if meta.get('vertices_file') else 'scan'),
            'source_format':_source_format(meta,entry.get('name')),
            'source_name':meta.get('source_name') or meta.get('name') or entry.get('name'),
            'source_bytes':add_source_size(meta,entry.get('source_bytes'),len(rows)),
            'cache_bytes':total,'protected_bytes':shared,
            'asset_files':len(assets),
        })
    # Legacy active survey/registration clouds may pre-date job_items. Include them once.
    covered=set().union(*seen_sets) if seen_sets else set()
    for label,meta in (('Active survey scan',state.get('cloud')),('Registration source', (state.get('registration') or {}).get('cloud'))):
        if not isinstance(meta,dict):continue
        assets=_local_asset_paths(root,meta)-covered
        if not assets:continue
        covered.update(assets);total=_asset_bytes(assets);shared=_asset_bytes(assets & protected)
        rows.append({'id':None,'name':meta.get('name') or label,'kind':'scan','source_format':_source_format(meta),
                     'source_name':meta.get('source_name') or meta.get('name') or label,
                     'source_bytes':add_source_size(meta,0,len(rows)),'cache_bytes':total,
                     'protected_bytes':shared,'asset_files':len(assets),'legacy':True})
    return {
        'items':rows,
        'cache_bytes':_asset_bytes(covered),
        'source_bytes':sum(source_sizes.values()),
        'formats':['rtc360_raw','hspca','e57','las','laz','ply','pts','xyz','txt','csv','obj','zip','dxf','json','tstudio'],
        'note':'All imported formats use the same project-reference protection. Original source files outside the Tunnel Studio data folder are never deleted by project cleanup.',
    }
