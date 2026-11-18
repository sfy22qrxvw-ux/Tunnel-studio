"""Bounded exact accelerator for the original-point bottom-hash viewer.

This is not a lower-density final preview. If at least budget eligible rows
survive, every omitted source row has a larger hash than every cached row:
the selected IDs are therefore identical to a complete source pass. Otherwise
the caller must fall back to that pass. Analysis never uses this cache.
"""
import hashlib
import json
import os
from pathlib import Path
import uuid
import numpy as np
from checker_evidence import priority_keys

TARGET = 8_000_000
MAX_ROWS = 9_000_000
MAX_EXTRA_ROWS = 18_000_000
CHUNK = 262144
ROW = np.dtype([('id', '<i8'), ('xyz', '<f8', (3,)), ('attrs', '<f4', (4,))])


def signature(meta):
    result = [int(meta['count'])]
    for key in ('file', 'attributes_file'):
        path = meta.get(key)
        if not path:
            result.append(None)
        else:
            p = Path(path).resolve(); s = p.stat()
            result.append([str(p), s.st_size, s.st_mtime_ns])
    return result


def paths(meta):
    source = Path(meta['file']).resolve()
    name = hashlib.sha256(str(source).encode()).hexdigest()[:24]
    root = source.parent / 'display_cache'
    return root / (name + '.priority.bin'), root / (name + '.priority.json')


def info(meta):
    binary, manifest = paths(meta)
    try:
        saved = json.loads(manifest.read_text())
        extra = binary.with_suffix('.extra.bin')
        if (saved['version'] == 2 and saved['signature'] == signature(meta)
                and 0 < saved['rows'] <= MAX_ROWS
                and 0 <= saved['extra_rows'] <= MAX_EXTRA_ROWS
                and binary.stat().st_size == saved['rows'] * ROW.itemsize
                and extra.stat().st_size == saved['extra_rows'] * ROW.itemsize):
            return binary, saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def build(meta, progress=lambda s: None, cancel=None, target=TARGET):
    """One bounded reusable cache per source, independently of edits/pose."""
    from contextlib import ExitStack
    from detail_preview import _check_cancel
    existing = info(meta)
    if existing is not None:
        return existing[1]
    sig = signature(meta); n = int(meta['count'])
    target = min(MAX_ROWS, max(1, int(target)))
    threshold = min((1 << 64) - 1, (target * (1 << 64)) // max(1, n))
    extra_threshold = min((1 << 64) - 1, 3 * threshold)
    binary, manifest = paths(meta); binary.parent.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    temporary = binary.with_name(binary.name + '.' + token + '.tmp')
    temporary_json = manifest.with_name(manifest.name + '.' + token + '.tmp')
    extra = binary.with_suffix('.extra.bin')
    temporary_extra = extra.with_name(extra.name + '.' + token + '.tmp')
    total = extra_total = 0
    try:
        with ExitStack() as stack:
            xyz = stack.enter_context(Path(meta['file']).open('rb'))
            attrs = stack.enter_context(Path(meta['attributes_file']).open('rb')) if meta.get('attributes_file') else None
            output = stack.enter_context(temporary.open('wb'))
            extra_output = stack.enter_context(temporary_extra.open('wb'))
            for start in range(0, n, CHUNK):
                _check_cancel(cancel)
                count = min(CHUNK, n-start)
                ids = np.arange(start, start+count, dtype=np.int64)
                keys = priority_keys(ids)
                take = keys < np.uint64(extra_threshold)
                points = np.fromfile(xyz, '<f8', count=count*3)
                values = np.fromfile(attrs, '<f4', count=count*4) if attrs else None
                if len(points) != count*3 or (values is not None and len(values) != count*4):
                    raise ValueError('Source changed during view cache preparation.')
                rows = np.empty(int(take.sum()), ROW)
                rows['id'] = ids[take]; rows['xyz'] = points.reshape(-1, 3)[take]
                rows['attrs'] = values.reshape(-1, 4)[take] if values is not None else np.nan
                primary = keys[take] < np.uint64(threshold)
                total += int(primary.sum()); extra_total += int((~primary).sum())
                if total > MAX_ROWS or extra_total > MAX_EXTRA_ROWS:
                    raise ValueError('View cache size limit exceeded.')
                rows[primary].tofile(output); rows[~primary].tofile(extra_output)
                if start % (CHUNK * 32) == 0:
                    progress(f'Preparing exact zoom cache · {start/n:.0%}')
        _check_cancel(cancel)
        if signature(meta) != sig:
            raise ValueError('Source changed during view cache preparation.')
        saved = dict(version=2, signature=sig, rows=total, threshold=threshold, extra_rows=extra_total, extra_threshold=extra_threshold)
        temporary_json.write_text(json.dumps(saved))
        os.replace(temporary, binary); os.replace(temporary_extra, extra); os.replace(temporary_json, manifest)
        import preview_store
        for path in (binary, extra, manifest):
            preview_store._remember_owner(path, meta); preview_store._publish_owner(path)
        return saved
    finally:
        temporary.unlink(missing_ok=True); temporary_json.unlink(missing_ok=True); temporary_extra.unlink(missing_ok=True)


def sample(item, budget, origin, fallback, mode, z_range, view, cancel=None, decorate=None):
    import detail_preview as detail
    import jobdata, registration_pose
    meta = item['meta']; cached = info(meta)
    if cached is None:
        return None
    binary, saved = cached
    included, hidden = jobdata.view_selections(item)
    frame = detail.validate_view(view)
    chosen = detail._PrioritySample(budget)
    identity = registration_pose.is_identity(meta)
    mask = None; mask_signature = None
    def mask_state():
        p = Path(meta['mask_file']); s = p.stat()
        if s.st_size != int(meta['count']):
            raise ValueError('The point deletion mask is incomplete. Refresh the scan.')
        return str(p), s.st_size, s.st_mtime_ns
    if meta.get('mask_file'):
        mask_signature = mask_state()
        mask = np.memmap(meta['mask_file'], 'u1', 'r')
    try:
        used_files = []
        for path, length in ((binary, saved['rows']), (binary.with_suffix('.extra.bin'), saved['extra_rows'])):
            used_files.append((path, length))
            with path.open('rb') as source:
                for start in range(0, length, CHUNK):
                    detail._check_cancel(cancel)
                    rows = np.fromfile(source, ROW, count=min(CHUNK, length-start))
                    if len(rows) != min(CHUNK, length-start):
                        return None
                    q = rows['xyz'] if identity else registration_pose.points(meta, rows['xyz'])
                    use = detail._view_mask(q, view, frame)
                    jobdata.apply_view_selections(q, use, included, hidden)
                    if mask is not None:
                        ix = np.flatnonzero(use)
                        use[ix] &= mask[rows['id'][ix]] == 0
                    chosen.add(rows['id'][use], q[use])
            if chosen.used >= budget:
                break
        if chosen.used < budget:
            return None  # Never substitute an insufficient sample for wall detail.
        ids, q = chosen.finish_points()
        # IDs in the bounded file are ordered, so attributes need only one
        # sequential pass over this small file, not random reads across 15 GB.
        attributes = np.empty((len(ids), 4), '<f4')
        found = 0
        selected_keys = priority_keys(ids)
        for path, length in used_files:
            primary = selected_keys < np.uint64(saved['threshold'])
            destinations = np.flatnonzero(primary if path == binary else ~primary)
            wanted = ids[destinations]
            with path.open('rb') as source:
                for start in range(0, length, CHUNK):
                    detail._check_cancel(cancel)
                    rows = np.fromfile(source, ROW, count=min(CHUNK, length-start))
                    if len(rows) != min(CHUNK, length-start):
                        return None
                    a = np.searchsorted(wanted, rows['id'][0], side='left')
                    b = np.searchsorted(wanted, rows['id'][-1], side='right')
                    ix = np.searchsorted(rows['id'], wanted[a:b])
                    attributes[destinations[a:b]] = rows['attrs'][ix]; found += b-a
        if found != len(ids) or signature(meta) != saved['signature'] or (mask is not None and mask_state() != mask_signature):
            raise ValueError('Scan changed while loading detail. Refresh the view.')
        colours = jobdata._colour_from_attributes(meta, ids, fallback, mode, q, z_range, attributes=attributes, cancel=cancel)
        packed = np.empty((len(ids), 6), '<f4'); packed[:, :3] = q-origin; packed[:, 3:] = colours
        if decorate is not None:
            decorate(ids, packed)
        detail._check_cancel(cancel)
        return packed
    finally:
        if mask is not None:
            mask._mmap.close()
