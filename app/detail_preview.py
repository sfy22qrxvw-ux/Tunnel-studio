# Copyright (c) 2026 Damian Gulczynski.
"""Bounded original-point sampling for the current orthographic/setup camera."""
from contextlib import ExitStack
from pathlib import Path
import numpy as np
from checker_evidence import priority_keys

# Each scan read owns at most 3 MiB of XYZ. A cancelled viewport request stops
# between these blocks rather than continuing a multi-gigabyte scan in silence.
READ_POINTS = 131072


def validate_view(view):
    if not isinstance(view,dict):raise ValueError('Invalid detail view.')
    target=np.asarray(view.get('target'),float)
    axes=np.asarray([view.get(k) for k in ('right','up','forward')],float)
    scale=float(view.get('scale',0));aspect=float(view.get('aspect',0))
    if (target.shape!=(3,) or axes.shape!=(3,3) or not np.isfinite(target).all()
        or not np.isfinite(axes).all() or not .02<=scale<=1e6 or not .05<=aspect<=20
        or not np.allclose(axes@axes.T,np.eye(3),atol=1e-4)):
        raise ValueError('Invalid detail-view frame.')
    if view.get('projection')=='perspective':
        near=float(view.get('near',0));far=float(view.get('far',0))
        if not np.isfinite([near,far]).all() or not .001<=near<far or far>1e8 or not .02<=scale<=2:
            raise ValueError('Invalid perspective view range.')
    return target,axes,scale,aspect


def _check_cancel(cancel):
    if cancel is not None and cancel():
        raise InterruptedError('The previous viewport request was cancelled.')


def request_cancelled(handler):
    """A browser abort closes its HTTP connection; no global job is cancelled."""
    import select, socket
    connection = getattr(handler, 'connection', None)
    if connection is None:
        return False
    try:
        if connection.fileno() < 0:
            return True
        readable, _, _ = select.select([connection], [], [], 0)
        return bool(readable and connection.recv(1, socket.MSG_PEEK) == b'')
    except (OSError, ValueError):
        return True


def _view_mask(points, view, frame):
    target, axes, scale, aspect = frame
    projected = (points - target) @ axes.T
    finite = np.isfinite(points).all(1)
    if view.get('projection') == 'perspective':
        z = projected[:, 2]
        return (finite & (z >= view['near']) & (z <= view['far'])
                & (np.abs(projected[:, 0]) <= z * scale * aspect * 1.15)
                & (np.abs(projected[:, 1]) <= z * scale * 1.15))
    return finite & (np.abs(projected) <= [scale * 1.15, scale / aspect * 1.15, np.inf]).all(1)


class _PrioritySample:
    """Exact global smallest-hash sample with bounded, amortised compaction.

    The old path copied and partitioned up to 800k retained rows for every
    16k input block. Candidates now accumulate in an owned bounded buffer;
    after its first prune, hashes above the retained threshold are discarded
    before copying. This returns exactly the same deterministic point IDs.
    """
    def __init__(self, budget):
        self.budget = int(budget)
        self.capacity = max(READ_POINTS, 2 * self.budget)
        self.ids = np.empty(self.capacity, dtype=np.int64)
        self.keys = np.empty(self.capacity, dtype=np.uint64)
        self.used = 0
        self.cutoff = None
        self.partitions = 0

    def _prune(self):
        if self.used > self.budget:
            take = np.argpartition(self.keys[:self.used], self.budget - 1)[:self.budget]
            self.ids[:self.budget] = self.ids[take]
            self.keys[:self.budget] = self.keys[take]
            self.used = self.budget
            self.partitions += 1
        if self.used >= self.budget:
            self.cutoff = self.keys[:self.used].max()

    def add(self, ids):
        if not len(ids):
            return
        keys = priority_keys(ids)
        if self.cutoff is not None:
            use = keys < self.cutoff
            ids, keys = ids[use], keys[use]
        offset = 0
        while offset < len(ids):
            count = min(self.capacity - self.used, len(ids) - offset)
            self.ids[self.used:self.used+count] = ids[offset:offset+count]
            self.keys[self.used:self.used+count] = keys[offset:offset+count]
            self.used += count; offset += count
            if self.used == self.capacity:
                self._prune()
        if self.cutoff is None and self.used >= self.budget:
            self._prune()

    def finish(self):
        self._prune()
        return np.sort(self.ids[:self.used])


def _coalesced_ranges(ranges):
    """Join adjacent spatial blocks without exceeding the bounded read size."""
    first = last = None
    for start, stop in ranges:
        if first is not None and start == last and stop - first <= READ_POINTS:
            last = stop
        else:
            if first is not None:
                yield first, last
            first, last = start, stop
    if first is not None:
        yield first, last


def _quick_sample(item, budget, origin, fallback, mode, z_range, view, cancel=None):
    import jobdata, preview_store
    # This is an explicitly preliminary measured overview, not a new ceiling
    # on detail. The ordinary request below reads every eligible original row.
    overview = jobdata._item_sample(item, min(int(item['meta']['count']), preview_store.OVERVIEW_POINTS),
                                    origin, fallback, mode, z_range, quick=True,cancel=cancel)
    frame = validate_view(view)
    points = overview[:, :3].astype(np.float64) + np.asarray(origin)
    shown = overview[_view_mask(points, view, frame)]
    if len(shown) > budget:
        shown = shown[np.linspace(0, len(shown)-1, budget, dtype=np.int64)]
    return shown


def sample(item, budget, origin, fallback, mode, z_range, view, quick=False, cancel=None):
    fallback,mode=__import__('scan_appearance').resolve(item,fallback,mode)
    import jobdata, preview_index, preview_store, registration_pose
    frame = validate_view(view)
    budget = max(0, min(1000000, int(budget)))
    if not budget:
        return np.empty((0, 6), dtype='<f4')
    _check_cancel(cancel)
    cache = None
    try:
        cache = preview_store.cache_path(item, budget, origin, fallback, mode, z_range, quick, view=view)
        saved = preview_store.read(cache)
        if saved is not None:
            _check_cancel(cancel)
            return saved
    except InterruptedError:
        raise
    except (OSError, TypeError, ValueError):
        cache = None
    if quick:
        packed = _quick_sample(item, budget, origin, fallback, mode, z_range, view,cancel=cancel)
    else:
        m = item['meta']; n = int(m['count'])
        source_key = preview_index._key(m)
        local_view = registration_pose.local_view(m, view)
        boxes = preview_index.cached_bounds(m, source_key)
        builder = preview_index.BoundsBuilder(m, source_key) if boxes is None else None
        if boxes is None:
            ranges = ((start, min(n, start+READ_POINTS)) for start in range(0, n, READ_POINTS))
        else:
            ranges = _coalesced_ranges(preview_index.candidate_blocks(
                m, local_view, validate_view(local_view), boxes=boxes))
        chosen = _PrioritySample(budget)
        with ExitStack() as files:
            source = files.enter_context(Path(m['file']).open('rb'))
            excluded = files.enter_context(Path(m['mask_file']).open('rb')) if m.get('mask_file') else None
            if excluded is not None and Path(m['mask_file']).stat().st_size != n:
                raise ValueError('The point deletion mask is incomplete. Refresh the scan.')
            for start, stop in ranges:
                _check_cancel(cancel)
                count = stop-start; source.seek(start*24)
                local = np.fromfile(source, '<f8', count=count*3)
                if local.size != count*3:
                    raise ValueError('The point cache changed while loading detail. Refresh the scan.')
                local = local.reshape(count, 3)
                if builder is not None:
                    builder.add(start, local)
                q = registration_pose.points(m, local)
                use = _view_mask(q, view, frame)
                if excluded is not None:
                    excluded.seek(start); flags = np.fromfile(excluded, 'u1', count=count)
                    if len(flags) != count:
                        raise ValueError('The point deletion mask changed while loading detail.')
                    use &= flags == 0
                if item.get('limit_box') and item.get('limit_hide_outside', True):
                    use &= jobdata.selection_mask(q, {'mode':'box', **item['limit_box']})
                if item.get('isolate_box') is not None:
                    use &= jobdata.selection_mask(q, item['isolate_box'])
                for hidden in item.get('hidden_boxes', []):
                    use &= ~jobdata.selection_mask(q, hidden)
                chosen.add(np.flatnonzero(use) + start)
        _check_cancel(cancel)
        if builder is not None:
            builder.finish()
        elif preview_index._key(m) != source_key:
            raise ValueError('The scan changed while loading detail. Refresh the view.')
        selected = chosen.finish()
        q = registration_pose.points(m, jobdata._display_rows(m['file'], n, selected,cancel=cancel))
        _check_cancel(cancel)
        colours = jobdata._colour_from_attributes(m, selected, fallback, mode, q, z_range,cancel=cancel)
        packed = np.empty((len(q), 6), dtype='<f4'); packed[:, :3] = q-origin; packed[:, 3:] = colours
    _check_cancel(cancel)
    if cache is not None:
        # Never publish a reusable result under a revision read before an edit.
        if cache != preview_store.cache_path(item, budget, origin, fallback, mode, z_range, quick, view=view):
            raise ValueError('The scan changed while loading detail. Refresh the view.')
        preview_store.write(cache, packed)
    return packed
