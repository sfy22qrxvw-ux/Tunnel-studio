# Copyright (c) 2026 Damian Gulczynski.
"""Disposable, revision-keyed point previews. Full scan data stays authoritative."""
from pathlib import Path
import hashlib
import json
import os
import uuid
import zipfile
import numpy as np

OVERVIEW_POINTS = 250000
DISK_BUDGET = 256 * 1024**2
_CACHE_OWNERS = {}


def _remember_owner(path,meta):
    # Small provenance lets whole-item deletion reclaim old colour/pose variants
    # without touching display caches belonging to other saved scans.
    source=meta.get('file') or meta.get('vertices_file')
    if source:
        if len(_CACHE_OWNERS)>2048:_CACHE_OWNERS.clear()
        _CACHE_OWNERS[str(path)]=str(Path(source).resolve())


def _publish_owner(path):
    source=_CACHE_OWNERS.get(str(path));record=Path(str(path)+'.owner.json')
    if not source or record.exists():return
    temporary=record.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps({'source_file':source}),encoding='utf8')
        os.replace(temporary,record)
    except OSError:pass
    finally:temporary.unlink(missing_ok=True)


def overview_path(meta, maximum=OVERVIEW_POINTS):
    """A measured, source-frame overview survives pose and exclusion edits."""
    sources = []
    for key in ('file', 'attributes_file'):
        if meta.get(key):
            path = Path(meta[key]); stat = path.stat()
            sources.append((key, str(path.resolve()), stat.st_size, stat.st_mtime_ns))
    maximum=max(1,min(1000000,int(maximum)))
    signature = [1, sources, int(meta['count']), maximum]
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return Path(meta['file']).parent / 'display_cache' / ('overview-' + digest + '.npz')


def read_overview(meta, maximum=OVERVIEW_POINTS):
    try:
        maximum=max(1,min(1000000,int(maximum)))
        path = overview_path(meta,maximum)
        if path.stat().st_size > maximum * 48 + 65536:
            return None
        with np.load(path, allow_pickle=False) as saved:
            ids, points, attrs = saved['ids'], saved['points'], saved['attributes']
        if (ids.dtype != np.dtype('<i8') or ids.ndim != 1 or not 0 < len(ids) <= maximum
                or points.dtype != np.dtype('<f8') or points.shape != (len(ids), 3)
                or attrs.dtype != np.dtype('<f4') or attrs.shape not in ((0, 4), (len(ids), 4))
                or ids[0] < 0 or ids[-1] >= int(meta['count'])
                or np.any(ids[1:] <= ids[:-1])):
            return None
        os.utime(path, None)
        return ids, points, attrs if len(attrs) else None
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return None


def _trim(directory):
    files = sorted((p for p in directory.iterdir() if p.suffix in ('.npy', '.npz')),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for entry in files:
        total += entry.stat().st_size
        if total > DISK_BUDGET:
            entry.unlink(missing_ok=True)
            Path(str(entry)+'.owner.json').unlink(missing_ok=True)


def write_overview(meta, ids, points, attrs, expected_path=None, maximum=OVERVIEW_POINTS):
    """Disposable display data only; original coordinates and row IDs are kept."""
    path = overview_path(meta,maximum)
    if expected_path is not None and path != expected_path:
        return
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(exist_ok=True)
        with temporary.open('xb') as stream:
            np.savez(stream, ids=np.asarray(ids, dtype='<i8'),
                     points=np.asarray(points, dtype='<f8'),
                     attributes=np.asarray(attrs, dtype='<f4') if attrs is not None else np.empty((0, 4), '<f4'))
        os.replace(temporary, path)
        _remember_owner(path,meta);_publish_owner(path)
        _trim(path.parent)
    except OSError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


def cache_path(item, per, origin, colour, mode, z_range, quick, view=None):
    meta = item['meta']
    sources = []
    for key in ('file', 'vertices_file', 'attributes_file', 'mask_file', 'preview_file'):
        if meta.get(key):
            path = Path(meta[key]); stat = path.stat()
            sources.append((key, str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    if quick and meta.get('file'):
        overview=overview_path(meta)
        if overview.is_file():sources.append(('measured_overview',str(overview)))
    revision = [5, sources, per, list(origin), colour, mode, z_range, quick, view,
                meta.get('count'), meta.get('preview_stride'), meta.get('registration_rotation'), meta.get('registration_translation'),
                item.get('limit_box') if item.get('limit_hide_outside', True) else None,
                item.get('isolate_box'), item.get('hidden_boxes')]
    digest = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
    path=Path(meta.get('file') or meta['vertices_file']).parent / 'display_cache' / (digest + '.npy')
    _remember_owner(path,meta)
    return path


def read(path):
    try:
        if path.stat().st_size>1000000*24+65536:return None
        values = np.load(path, allow_pickle=False)
        if values.dtype == np.dtype('<f4') and values.ndim == 2 and values.shape[1] == 6:
            os.utime(path, None)
            _publish_owner(path)
            return values
    except (OSError, ValueError):
        pass


def write(path, values):
    """Caching is optional; a full or read-only disk must not break viewing."""
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(exist_ok=True)
        with temporary.open('wb') as stream:
            np.save(stream, values, allow_pickle=False)
        os.replace(temporary, path)
        _publish_owner(path)
        _trim(path.parent)
    except OSError:
        pass
    finally:
        temporary.unlink(missing_ok=True)
