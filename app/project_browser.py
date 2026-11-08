# Copyright (c) 2026 Damian Gulczynski.
"""Named project cards and bounded, saved display overviews for project browsing.

Previews store a small, local-origin display sample inside the project metadata.
They never supply analysis, registration or export points. Opening a browser
does not open a project, extract its point assets, or change the live workspace.
Older local projects can acquire a bounded overview on demand; portable files
without a saved sample show explicitly labelled geometry bounds instead.
"""
from collections import OrderedDict
from pathlib import Path
import json
import math
import re
import threading
import uuid
import zipfile

import numpy as np

MAX_POINTS = 12000
MAX_LINE_VERTICES = 6000
MAX_PREVIEW_BYTES = 1_500_000
MAX_BROWSE_FILES = 200
_BROWSES = OrderedDict()
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_OPAQUE = re.compile(r'[a-f0-9-]{24,}', re.I)


def title(data, path=None):
    value = data.get('project_title') if isinstance(data, dict) else None
    if isinstance(value, str) and value.strip() and not _OPAQUE.fullmatch(value.strip()):
        return value.strip()[:120]
    if path is not None and not _OPAQUE.fullmatch(Path(path).stem) and Path(path).stem != 'project':
        return Path(path).stem[:120]
    for item in data.get('job_items', []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get('name'):
            return str(item['name'])[:100] + ' · project'
    return 'Untitled project'


def _check(cancel):
    if cancel is not None and cancel():
        raise InterruptedError('Project preview cancelled.')


def _vectors(value):
    a = np.asarray(value, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != 3 or not np.isfinite(a).all():
        raise ValueError('Invalid project preview coordinates.')
    return a


def checked(value):
    """Validate persisted/untrusted display data before it reaches the viewer."""
    if not isinstance(value, dict) or value.get('format') != 'TunnelStudioDisplayPreview' or value.get('version') != 1:
        raise ValueError('No supported saved 3D overview.')
    origin = np.asarray(value.get('origin'), dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Invalid project preview origin.')
    result = dict(value)
    for key, limit in (('points', MAX_POINTS), ('lines', MAX_LINE_VERTICES)):
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError('The saved project overview exceeds its display budget.')
        if rows:
            a = np.asarray(rows, dtype=float)
            if a.shape != (len(rows), 6) or not np.isfinite(a).all() or np.any((a[:, 3:] < 0) | (a[:, 3:] > 1)):
                raise ValueError('Invalid saved project overview rows.')
        result[key] = rows
    if len(result['lines']) % 2:
        raise ValueError('Invalid project overview line pairs.')
    if len(json.dumps(result, allow_nan=False).encode('utf8')) > MAX_PREVIEW_BYTES:
        raise ValueError('The saved project overview is too large.')
    return result


def _local_meta(meta, root):
    """Browsing a manifest cannot read arbitrary paths outside its workspace."""
    import storage_inventory
    m = dict(meta)
    for key in ('file', 'vertices_file', 'attributes_file', 'mask_file', 'preview_file'):
        if not m.get(key):
            continue
        path = Path(m[key])
        if not path.is_absolute():
            path = root / path
        storage_inventory.regular_record(path, root)
        m[key] = str(path)
    return m


def build(data, work=None, progress=lambda _: None, cancel=None):
    """Build a display sample only, without loading a full cloud or changing it."""
    import jobdata
    import registration_pose
    if not isinstance(data, dict):
        raise ValueError('Invalid project metadata for a 3D overview.')
    root = Path(work).resolve() if work is not None else None
    items = [x for x in data.get('job_items', []) if isinstance(x, dict)
             and x.get('kind') in ('scan', 'mesh') and x.get('visible', True)
             and not x.get('merge_archived') and not x.get('merge_stale')
             and isinstance(x.get('meta'), dict)]
    # Legacy survey-only projects did not have a job_items list.
    if not items and isinstance(data.get('cloud'), dict):
        items = [dict(kind='scan', name=data['cloud'].get('name', 'Survey'),
                      meta=data['cloud'], visible=True)]
    bounds = []
    usable = []
    warnings = []
    for x in items:
        try:
            m = x['meta']
            b = registration_pose.world_bounds(m) if x['kind'] == 'scan' else (m['minimum'], m['maximum'])
            b = _vectors(b)
            if b.shape != (2, 3) or np.any(b[0] > b[1]):
                raise ValueError('Invalid bounds.')
            bounds.extend(b)
            usable.append((x, b))
        except (ValueError, TypeError, KeyError, OverflowError):
            warnings.append('A scan or mesh has no usable overview bounds.')
    # Alignment and editable model geometry stay useful in projects without scans.
    line_world = []
    axis = data.get('axis')
    if isinstance(axis, dict):
        rows = axis.get('rows', [])
        try:
            a = np.asarray(rows, dtype=float)
            if a.ndim == 2 and a.shape[1] >= 4 and np.isfinite(a).all():
                a = a[:, 1:4]
                stride = max(1, math.ceil(len(a) / 1000))
                a = a[::stride]
                for left, right in zip(a[:-1], a[1:]):
                    line_world.extend(([*left, .2, .85, .75], [*right, .2, .85, .75]))
                bounds.extend(a)
        except (ValueError, TypeError, OverflowError):
            warnings.append('The alignment overview is unavailable.')
    cad=data.get('cad')
    for obj in cad.get('objects', []) if isinstance(cad,dict) else []:
        if not isinstance(obj, dict) or not obj.get('visible', True):
            continue
        try:
            raw=obj.get('vertices', [])
            if not isinstance(raw,list):
                continue
            v = _vectors(raw[::max(1,math.ceil(len(raw)/2000))])
            if not len(v):
                continue
            bounds.extend((v.min(0), v.max(0)))
            if len(line_world) + 2 * len(v) > MAX_LINE_VERTICES:
                continue
            for left, right in zip(v[:-1], v[1:]):
                line_world.extend(([*left, .4, .72, 1.], [*right, .4, .72, 1.]))
            if obj.get('closed') and len(v) > 2:
                line_world.extend(([*v[-1], .4, .72, 1.], [*v[0], .4, .72, 1.]))
        except (ValueError, TypeError, OverflowError):
            warnings.append('An editable model has no usable overview geometry.')
    if bounds:
        b = np.asarray(bounds, dtype=float)
        origin = (b.min(0) + b.max(0)) / 2
    else:
        origin = np.zeros(3)
    parts = []
    outlines = []
    total = 0
    saved_mode = (data.get('ui') or {}).get('job-colour', 'intensity-gray')
    for ordinal, (x, box) in enumerate(usable):
        _check(cancel)
        count = x['meta'].get('count' if x['kind'] == 'scan' else 'vertices', 0)
        if type(count) is int and count >= 0:
            total += count
        budget = MAX_POINTS // max(1, len(usable)) + (ordinal < MAX_POINTS % max(1, len(usable)))
        packed = None
        if root is not None and budget:
            try:
                progress('Saving 3D overview · ' + str(x.get('name', 'Scan')))
                item = {**x, 'meta': _local_meta(x['meta'], root)}
                packed = jobdata._item_sample_uncached(item, budget, origin, [.5, .83, .8],
                                                       mode=saved_mode, quick=True, cancel=cancel)
                if len(packed) and not np.isfinite(packed).all():
                    raise ValueError('Non-finite preview rows.')
                if (len(packed) and x.get('display_mode','global')=='global'
                        and float(packed[:,3:].max())<.08):
                    # Constant zero intensity/RGB otherwise makes a whole
                    # overview invisible on dark cards. This changes only
                    # display colours; measured attributes remain untouched.
                    packed[:,3:]=[.42,.78,.76]
                if len(packed):
                    parts.append(packed)
            except InterruptedError:
                raise
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
                if isinstance(error,jobdata.Cancelled):
                    raise
                warnings.append(str(x.get('name', 'Scan')) + ': display sample unavailable; bounds shown.')
        if packed is None or not len(packed):
            lo, hi = box
            corners = np.array([[hi[j] if bit & (1 << j) else lo[j] for j in range(3)] for bit in range(8)])
            for bit in range(8):
                for dim in range(3):
                    if not bit & (1 << dim):
                        outlines.extend(([*corners[bit], .4, .6, .7], [*corners[bit | (1 << dim)], .4, .6, .7]))
    _check(cancel)
    points = np.concatenate(parts) if parts else np.empty((0, 6))
    lines = np.asarray((line_world + outlines)[:MAX_LINE_VERTICES], dtype=float).reshape(-1, 6)
    if len(lines):
        lines[:, :3] -= origin
    def rows(a):
        result = a.copy()
        if len(result):
            result[:, :3] = np.round(result[:, :3], 5)
            result[:, 3:] = np.round(np.clip(result[:, 3:], 0, 1), 3)
        return result.tolist()
    return checked(dict(format='TunnelStudioDisplayPreview', version=1, origin=origin.tolist(),
                        points=rows(points), lines=rows(lines), source_points=total,
                        item_count=len(items), quality='sampled' if len(points) else 'bounds_only' if len(lines) else 'empty',
                        note='Saved display overview only. Not analysis or measurement data.',
                        warnings=list(dict.fromkeys(warnings))[:12]))


def attach(data, work, progress=lambda _: None):
    """A missing/failed preview must never prevent a valid project from saving."""
    try:
        data['project_preview'] = build(data, work, progress)
    except (InterruptedError, KeyboardInterrupt):
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        import jobdata
        if isinstance(error,jobdata.Cancelled):
            raise
        data.pop('project_preview', None)
    return data


def summary(data, path=None):
    return dict(project_title=title(data, path), saved_at=data.get('saved_at'),
                project_file=str(path) if path is not None else None,
                has_preview=isinstance(data.get('project_preview'), dict),
                item_count=len(data.get('job_items', [])),
                source_points=sum(int(x.get('meta', {}).get('count', 0)) for x in data.get('job_items', [])
                                  if isinstance(x, dict) and x.get('kind') == 'scan'
                                  and not x.get('merge_archived') and x.get('visible', True)))


def _stamp(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path):
    import projects
    import storage_inventory
    storage_inventory.regular_record(path, path.parent)
    if path.suffix.lower() == '.tstudio' or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos = [i for i in archive.infolist() if i.filename == 'project.json']
            if len(infos) != 1 or infos[0].file_size > projects.MAX_MANIFEST_BYTES:
                raise ValueError('This portable project has no supported metadata record.')
            with archive.open(infos[0]) as stream:
                raw = stream.read(projects.MAX_MANIFEST_BYTES + 1)
            if len(raw) > projects.MAX_MANIFEST_BYTES:
                raise ValueError('The project metadata exceeds the supported size.')
            value = json.loads(raw)
    else:
        value = projects.read_json(path)
    value, _ = projects.normalize_manifest(value)
    return value


def _preview(data, path, root=None):
    signature = (str(path), _stamp(path))
    with _LOCK:
        if signature in _CACHE:
            _CACHE.move_to_end(signature)
            return _CACHE[signature]
    try:
        result = checked(data.get('project_preview'))
    except (ValueError, TypeError, OverflowError):
        result = build(data, root)
        result['note'] = ('Display overview prepared from this older local project.' if root is not None
                          else 'This older portable project has no saved point overview. Geometry bounds are shown.')
    with _LOCK:
        _CACHE[signature] = result
        while len(_CACHE) > 24:
            _CACHE.popitem(last=False)
    return result


def request(s, body):
    """Read-only preview/browse API. Opening still uses guarded project workflows."""
    import projects
    if not isinstance(body, dict):
        raise ValueError('Invalid project browser request.')
    action = body.get('action')
    if action == 'browse':
        raw = body.get('folder')
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Choose a folder containing Tunnel Studio or REGISTER 360 projects.')
        root = Path(raw).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('The project folder is unavailable.')
        candidates = []
        # A selected parent can hold several dedicated workspaces with opaque
        # directory names. Inspect only their conventional metadata locations,
        # never descend through point-cache/category directories recursively.
        import project_data_layout
        parents=[root,root/'projects']
        children=0
        for child in root.iterdir():
            if child.name=='projects' or not child.is_dir() or project_data_layout.is_link(child):
                continue
            parents.extend((child,child/'projects'))
            children+=1
            if children>=128:break
        for parent in parents:
            if not parent.is_dir() or parent.is_symlink():
                continue
            for path in sorted(parent.iterdir()):
                if path.is_file() and not path.is_symlink() and (path.suffix.lower() == '.tstudio'
                      or path.name == 'project.json' or parent.name == 'projects' and path.suffix.lower() == '.json'):
                    candidates.append(path)
                    if len(candidates) >= MAX_BROWSE_FILES:
                        break
            if len(candidates) >= MAX_BROWSE_FILES:
                break
        rows, records = [], {}
        warnings = []
        for path in candidates:
            try:
                before = _stamp(path)
                data = _read(path)
                if projects.is_previous_session_snapshot(data):
                    continue
                if before != _stamp(path):
                    raise ValueError('The project changed during browsing.')
                token = uuid.uuid4().hex
                records[token] = dict(path=path, stamp=before)
                data_id=data.get('project_id')
                rows.append(dict(key=token, path=str(path), saved_project_id=data_id if isinstance(data_id,str) else None,kind='portable' if path.suffix.lower() == '.tstudio' else 'snapshot',
                                 bytes=before[2], **summary(data, path)))
            except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, RecursionError):
                warnings.append(path.name + ': metadata could not be read.')
        # A named snapshot and its workspace autosave represent one catalogue
        # project. Prefer the named record; portable backup files stay separate.
        unique={}
        for row in rows:
            path=Path(row['path']);workspace=path.parent.parent if path.parent.name=='projects' else path.parent
            key=(str(workspace),row['saved_project_id']) if row['kind']=='snapshot' and row['saved_project_id'] else row['path']
            if key not in unique or path.parent.name=='projects':unique[key]=row
        rows=list(unique.values())
        import leica_catalog
        remaining=MAX_BROWSE_FILES-len(rows)
        leica_projects,leica_limited=leica_catalog.discover(root,max_projects=remaining) if remaining>0 else ([],True)
        for info in leica_projects:
            token=uuid.uuid4().hex
            records[token]=dict(path=Path(info['path']),leica=info)
            rows.append(dict(key=token,path=info['path'],project_title=info['project_title'],
                             saved_at=info['saved_at'],kind='leica_register360',
                             title_source=info['title_source'],name_note=info['name_note'],
                             project_guid=info['project_guid'],setups=info['setups'],
                             databases=info['databases'],import_available=info['import_available'],
                             has_preview=True))
            warnings.extend(info['warnings'][:3])
        with _LOCK:
            browse_id = uuid.uuid4().hex
            _BROWSES[browse_id] = records
            while len(_BROWSES) > 8:
                _BROWSES.popitem(last=False)
        return dict(browse_id=browse_id, folder=str(root), projects=rows, warnings=warnings[:10],
                    limited=len(candidates) >= MAX_BROWSE_FILES or children>=128 or leica_limited)
    if action == 'preview':
        ident = body.get('id')
        if ident is not None:
            if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident):
                raise ValueError('Choose an existing saved project.')
            data = projects.read_named(s.WORK, ident)
            path = Path(s.WORK) / 'projects' / (ident + '.json')
            root = Path(s.WORK).resolve()
        else:
            with _LOCK:
                record = (_BROWSES.get(str(body.get('browse_id'))) or {}).get(str(body.get('key')))
            if record is None:
                raise ValueError('The project search expired. Browse the folder again.')
            if record.get('leica'):
                import leica_catalog
                info=record['leica']
                if info['revision']!=leica_catalog.stamp(info['source_files']):
                    raise ValueError('This Leica project metadata changed. Refresh the folder before previewing it.')
                result=checked(leica_catalog.preview(info,body.get('database'),sample_scans=not body.get('thumbnail')))
                if info['revision']!=leica_catalog.stamp(info['source_files']):
                    raise ValueError('This Leica project metadata changed. Refresh the folder before previewing it.')
                return dict(project_title=info['project_title'],preview=result)
            path = record['path']
            if record['stamp'] != _stamp(path):
                raise ValueError('This project changed. Refresh the folder before previewing it.')
            data = _read(path)
            root = None if path.suffix.lower() == '.tstudio' else path.parent.parent if path.parent.name == 'projects' else path.parent
        result = _preview(data,path,root)
        if body.get('thumbnail'):
            result = dict(result)
            for key in ('points','lines'):
                rows=result[key]
                if key=='lines':
                    result[key]=[v for i in range(0,len(rows),2*max(1,math.ceil(len(rows)/1200))) for v in rows[i:i+2]]
                else:result[key]=rows[::max(1,math.ceil(len(rows)/1200))]
        return dict(project_title=title(data,path),preview=result)
    raise ValueError('Choose browse or preview for the project browser.')
