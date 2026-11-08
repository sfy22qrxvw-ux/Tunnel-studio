"""Inspect this workspace's storage and recognise explicitly managed point/mesh caches.

The inventory never follows directory links or reparse points and never opens point
data. File sizes are logical bytes, deduplicated by file identity. Neutral formats,
unknown files, original GIS sources, backups and program files are protected.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import heapq
import os
import re
import shutil
import stat

UUID = r'[0-9a-f]{32}'
INTERNAL = r'(?:xyz64|attr32|analysis64|mask|surface_ids|surface_labels|selection|patch_ids|preview64|vertices64|faces32|faceflags8|face_flags|faceflags|vcolour32|feature_ids|feature_labels|feature_selected|feature_xyz64|section\.preview64|inspection64|results64|design-results|inspection-results|xyz64\.viewindex\.npz)'
CACHE = re.compile(UUID + r'\.' + INTERNAL + r'\Z', re.I)
UNPACKED = re.compile(r'[0-9]{4,6}\.' + INTERNAL + r'\Z', re.I)
MESH_FOLDER = re.compile(r'mesh-' + UUID + r'\Z', re.I)
SHAPE_FOLDER = re.compile(r'shape-' + UUID + r'\Z', re.I)
INSPECT_FOLDER = re.compile(r'inspect-' + UUID + r'\Z', re.I)
SHAPE_CACHE = {'candidate_ids.bin','candidate_labels.bin','selected.bin','review.sqlite','preview.npz','evidence.sqlite','circle_samples.sqlite'}
ASSET_FOLDER = re.compile(r'project_assets_' + UUID + r'\Z', re.I)
OWNED_FOLDERS = {('Clouds','Imported'), ('Clouds','Registered'), ('Clouds','Derived'), ('Meshes',), ('Analysis',), ('Working',)}
MEDIA = {'.e57','.laz','.las','.xyz','.pts','.ptx','.obj','.ply','.stl','.dxf','.dwg','.shp','.shx','.dbf','.prj','.cpg','.sbn','.sbx','.qix','.fix','.zip','.csv','.tif','.tiff','.jpg','.jpeg','.png','.txt','.step','.stp','.iges','.ifc','.rtc360'}
CONTROL = {'project.json','ui-preferences.json','project_save_preferences.json','workspace-folders.json',
           '.tunnelstudio-workspace.json','tool-window-layout.json','job_cancel.request','job_status.json',
           'workspace.lock','project.lock'}
GROUPS = {'current':'Current workspace and Undo', 'named':'Saved projects', 'shared':'Shared project data',
          'library':'Saved shape library',
          'recovery':'Recovery records and assets','orphans':'Unreferenced generated caches',
          'exchange':'Originals, exports and backups','metadata':'Workspace settings and records',
          'quarantine':'Pending deletion','program':'Installed software','other':'Other protected files'}


def program_root():
    release = Path(__file__).resolve().parent.parent
    return release.parent.parent if release.parent.name.casefold()=='releases' else release


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def safe_path(path, root):
    """Check every lexical component, including junctions before calling resolve."""
    relative = path.relative_to(root)
    item = root
    if is_link(item.lstat()):return False
    for component in relative.parts:
        if component == '..':return False
        item /= component
        if is_link(item.lstat()):return False
    return True


def owned_name(root, path):
    if path.is_relative_to(program_root()): return False
    rel = path.relative_to(root)
    folders = rel.parts[:-1]
    for prefix in OWNED_FOLDERS:
        if folders[:len(prefix)]==prefix:
            folders=folders[len(prefix):]
            break
    if len(folders)==1 and SHAPE_FOLDER.fullmatch(folders[0]):
        return rel.name in SHAPE_CACHE or bool(re.fullmatch(UUID+r'\.selected',rel.name,re.I))
    if len(folders)==2 and SHAPE_FOLDER.fullmatch(folders[0]) and INSPECT_FOLDER.fullmatch(folders[1]):
        return rel.name in {'object.xyz64','object.ids','object.selection','object.intensity32','context.xyz64'}
    if not folders or (len(folders)==1 and MESH_FOLDER.fullmatch(folders[0])):
        return bool(CACHE.fullmatch(rel.name))
    if len(folders)==2 and ASSET_FOLDER.fullmatch(folders[0]) and folders[1]=='assets':
        return bool(UNPACKED.fullmatch(rel.name))
    return False


def eligible(root, path):
    try:
        info=path.lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_nlink==1 and owned_name(root,path)
                and safe_path(path,root))
    except (OSError,ValueError):
        return False


def normal_folder(path):
    if not path.exists() and not path.is_symlink(): return
    if not path.is_dir() or is_link(path.lstat()):
        raise ValueError(f'Storage review blocked by an unexpected linked or invalid folder at {path}.')


def regular_record(path, root):
    if not safe_path(path,root) or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Storage review blocked by a linked or invalid record: {path}')


def deletion_stamp(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def review_receipt(paths, selector=(), stamps=None):
    digest=hashlib.sha256()
    for title in sorted(selector):
        digest.update(os.fsencode(title)+b'\0')
    for path in sorted(paths,key=str):
        info=path.lstat()
        stamp=deletion_stamp(info)
        if stamps is not None:stamps[path]=stamp
        digest.update(os.fsencode(path)+b'\0'+str(stamp).encode()+b'\n')
    return digest.hexdigest()


def files_in(root,warnings):
    pending=[root]
    while pending:
        folder=pending.pop()
        try:
            if is_link(folder.lstat()):
                warnings.append(f'Linked folder skipped: {folder}');continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    path=Path(entry.path)
                    try:info=entry.stat(follow_symlinks=False)
                    except FileNotFoundError:continue
                    except OSError as err:
                        warnings.append(f'Could not stat {path}: {err}');continue
                    if is_link(info):
                        warnings.append(f'Link skipped: {path}');continue
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        yield path,info
        except FileNotFoundError:continue
        except OSError as err:warnings.append(f'Could not inspect {folder}: {err}')


def describe(root,path,info,refs,proven,active,recovery,uncertain):
    rel=path.relative_to(root)
    if path.is_relative_to(program_root()):return 'program','Program files are protected.'
    if 'ShapeLibrary' in rel.parts:return 'library','Independent confirmed shape examples. Manage teaching examples in Find similar shapes; project cleanup preserves them.'
    if info.st_nlink>1:return 'shared','Linked file. Cleanup preserves all links.'
    if rel.parts[0] in ('projects','startup_recovery','diagnostics') or path.name in CONTROL:
        return 'metadata','Saved record or working settings. Keep in place.'
    if rel.parts[0]=='.project_delete_queue':return 'quarantine','Already staged for deletion, or held for inspection.'
    if path.suffix.lower() in MEDIA|{'.tstudio','.bak'} or rel.parts[0] in ('Exports','GIS','CAD','backups'):
        return 'exchange','Original, export or backup. Automatic cleanup preserves this file.'
    if not owned_name(root,path):return 'other','Unrecognised or non-cache file. Kept by project and cache cleanup.'
    if uncertain:return 'other','Project references could not be fully verified. Kept pending review.'
    if refs and not proven:return 'other','Reference does not establish cache ownership. Kept.'
    if len(refs)>1:return 'shared','Referenced by more than one project or recovery.'
    if active:return 'current','Used by the open workspace or its Undo history.'
    if refs:return ('recovery','Referenced by recovery data.') if recovery else ('named','Referenced by a saved project.')
    return 'orphans','Unused internal cache. Review in Clean unreferenced data.'


def file_type(path, role=None):
    if 'ShapeLibrary' in path.parts:return 'Saved shape library'
    suffix=path.suffix.casefold()
    if suffix in ('.xyz64','.attr32'):
        kind={'imported':'Imported scan caches','registered':'Registered cloud caches','derived':'Derived cloud caches'}
        if role in kind:return kind[role]
        return 'Other scan coordinates and attributes'
    if suffix in ('.vertices64','.faces32','.face_flags','.faceflags','.faceflags8','.vcolour32') or path.name.endswith('.mesh.json'):return 'Mesh geometry'
    if suffix in ('.inspection-results','.analysis64','.design-results','.surface_labels','.surface_ids','.patch_ids'):return 'Analysis and cleaning results'
    if suffix in ('.mask','.selection') or suffix.startswith('.feature_'):return 'Point edits and selections'
    if suffix=='.json' and path.parent.name in ('projects','startup_recovery','retired_previous_sessions'):return 'Saved project and recovery records'
    if path.name=='project.json':return 'Saved project and recovery records'
    if path.name=='survey_report.html' or (path.parent.name=='Reports' and suffix in ('.pdf','.html','.csv','.txt')):return 'Reports'
    if suffix!='.tstudio' and ('Exports' in path.parts or role=='export'):return 'Exports'
    if path.parent.name in ('demo','target_demo') or re.fullmatch('surface_demo_'+UUID,path.parent.name):return 'Demonstration folder files'
    if suffix in ('.preview64','.npz','.npy'):return 'Previews and index files'
    if suffix in ('.tstudio','.bak'):return 'Portable projects and backups'
    if suffix in MEDIA:return 'Source, exchange and exported files'
    return 'Other files and project records'


def inventory(work,current_state,limit=100,*,index_sink=None):
    import projects
    root=Path(work).resolve()
    warnings=[]
    ref_errors=[]
    try:
        named=projects._read_named_asset_sets(root,strict=True,maintain=False)
        recoveries=projects._hidden_recovery_entries(root,strict=True,prune=False)
        live=projects._current_and_recovery_assets(root,current_state,strict=True,prune=False)
    except (OSError,ValueError,TypeError,RecursionError) as err:
        named={};recoveries=[];live=set();ref_errors.append(str(err))
    current_state=current_state or {}
    current_id=current_state.get('project_id')
    try:
        current_paths=projects._local_asset_paths(root,current_state)
    except ValueError as error:
        ref_errors.append(str(error));current_paths=set()
    ownership=defaultdict(set)
    metadata_owners={}
    proven=set()
    names={k:(v['data'].get('project_title') or 'Untitled project') for k,v in named.items()}
    current_key=current_id or '@current'
    names.setdefault(current_key,current_state.get('project_title') or 'Open workspace')
    def add(key,paths,owned):
        proven.update(owned)
        for path in paths:ownership[path].add(key)
    add(current_key,current_paths,projects._local_asset_paths(root,current_state,owned_only=True) if not ref_errors else set())
    for ident,row in named.items():
        add(ident,row['assets'],row['owned'])
        metadata_owners[row['path']]=[names[ident]]
    recovery_paths=set()
    for entry in recoveries:
        ident=entry['project_id'] or '@recovery'
        names.setdefault(ident,'Recovery snapshot')
        add(ident,entry['assets'],entry['owned'])
        metadata_owners[entry['path']]=[names[ident]]
        recovery_paths.update(entry['assets'])
    originals=set()
    if not ref_errors:
        originals.update(projects._originals(root,current_state))
        for entry in list(named.values())+recoveries:originals.update(entry['originals'])
    proven.difference_update(originals)
    # Include any extra live autosave paths required by the cleanup guard.
    for path in live-current_paths-recovery_paths:ownership[path].add('@autosave')
    names['@autosave']='Current autosave'
    from project_data_layout import category_for_state_asset_map
    roles={}
    for state in [entry['data'] for entry in named.values()]+[current_state]:
        for raw,role in category_for_state_asset_map(state).items():
            p=Path(raw)
            p=p if p.is_absolute() else root/p
            if roles.get(p) not in ('registered','derived') or role in ('registered','derived'):roles[p]=role
    by_type=defaultdict(lambda:[0,0],{k:[0,0] for k in ('Imported scan caches','Registered cloud caches','Derived cloud caches','Previews and index files','Analysis and cleaning results','Saved project and recovery records','Exports','Reports','Other files and project records')});tally=defaultdict(lambda:[0,0]);project_sizes=defaultdict(lambda:[0,0,0])
    largest=[];seen_links=set();used=0;count=0
    for path,info in files_in(root,warnings):
        refs=ownership.get(path,set())
        key,reason=describe(root,path,info,refs,path in proven,path in current_paths,path in recovery_paths,ref_errors)
        size=info.st_size
        kind=file_type(path.relative_to(root),roles.get(path))
        rank=(size,str(path))
        in_top=limit>0 and (len(largest)<limit or rank>largest[0][:2])
        if index_sink is not None or in_top:
            row=dict(name=path.name,path=str(path),bytes=size,type=kind,category=GROUPS[key],protection=reason,
                     owners=metadata_owners.get(path,sorted({names.get(i,i) for i in refs})))
            if index_sink is not None:index_sink(row)
        if info.st_nlink>1 and info.st_ino:
            inode=(info.st_dev,info.st_ino)
            if inode in seen_links:continue
            seen_links.add(inode)
        by_type[kind][0]+=size;by_type[kind][1]+=1
        used+=size;count+=1;tally[key][0]+=size;tally[key][1]+=1
        for ident in refs:
            sizes=project_sizes[ident];sizes[0]+=size
            reclaimable=len(refs)==1 and key in ('current','named','recovery')
            sizes[1 if reclaimable else 2]+=size
        if in_top:
            heapq.heappush(largest,(*rank,row))
            if len(largest)>limit:heapq.heappop(largest)
    rows=[]
    for ident,entry in named.items():
        total,exclusive,shared=project_sizes[ident]
        rows.append(dict(project_id=ident,project_title=names[ident],path=str(entry['path']),active=ident==current_id,
                         total_bytes=total,exclusive_bytes=exclusive,shared_bytes=shared))
    allwarnings=ref_errors+warnings
    return dict(data_root=str(root),program_root=str(program_root()),used_bytes=used,
                drive_free_bytes=shutil.disk_usage(root).free,file_count=count,omitted_file_count=count-len(largest),
                by_type=[dict(name=key,bytes=v[0],files=v[1]) for key,v in sorted(by_type.items(),key=lambda kv:-kv[1][0])],
                groups=[dict(key=key,name=GROUPS[key],bytes=values[0],files=values[1]) for key,values in sorted(tally.items(),key=lambda kv:-kv[1][0])],
                files=[r[-1] for r in sorted(largest,reverse=True)],projects=sorted(rows,key=lambda r:-r['total_bytes']),
                warnings=allwarnings[:25],warning_count=len(allwarnings),complete=not allwarnings)
