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
    relative = points - target
    if view.get('projection') == 'perspective':
        projected = relative @ axes.T
        finite = np.isfinite(points).all(1)
        z = projected[:, 2]
        return (finite & (z >= view['near']) & (z <= view['far'])
                & (np.abs(projected[:, 0]) <= z * scale * aspect * 1.15)
                & (np.abs(projected[:, 1]) <= z * scale * 1.15))
    # Orthographic depth is unbounded. Check horizontal overlap first
    # and only project rows that could survive the viewport filter.
    with np.errstate(invalid='ignore'):
        horizontal = np.einsum('ij,j->i', relative, axes[0])
        candidates = np.flatnonzero(np.abs(horizontal) <= scale * 1.15)
        vertical = np.einsum('ij,j->i', relative[candidates], axes[1])
    keep = np.zeros(len(points), dtype=bool)
    keep[candidates] = np.abs(vertical) <= scale / aspect * 1.15
    return keep


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
        self.points = None
        self.used = 0
        self.cutoff = None
        self.partitions = 0

    def _prune(self):
        if self.used > self.budget:
            take = np.argpartition(self.keys[:self.used], self.budget - 1)[:self.budget]
            self.ids[:self.budget] = self.ids[take]
            self.keys[:self.budget] = self.keys[take]
            if self.points is not None:
                self.points[:self.budget] = self.points[take]
            self.used = self.budget
            self.partitions += 1
        if self.used >= self.budget:
            self.cutoff = self.keys[:self.used].max()

    def add(self, ids, points=None):
        if not len(ids):
            return
        if points is not None and self.points is None:
            self.points = np.empty((self.capacity, 3), dtype=np.float64)
        keys = priority_keys(ids)
        if self.cutoff is not None:
            use = keys < self.cutoff
            ids, keys = ids[use], keys[use]
            if points is not None:
                points = points[use]
        offset = 0
        while offset < len(ids):
            count = min(self.capacity - self.used, len(ids) - offset)
            self.ids[self.used:self.used+count] = ids[offset:offset+count]
            self.keys[self.used:self.used+count] = keys[offset:offset+count]
            if points is not None:
                self.points[self.used:self.used+count] = points[offset:offset+count]
            self.used += count; offset += count
            if self.used == self.capacity:
                self._prune()
        if self.cutoff is None and self.used >= self.budget:
            self._prune()

    def finish(self):
        self._prune()
        return np.sort(self.ids[:self.used])


    def finish_points(self):
        self._prune()
        order = np.argsort(self.ids[:self.used])
        points = self.points[order] if self.points is not None else np.empty((0, 3))
        return self.ids[order], points


def _coalesced_ranges(ranges, gap=0):
    """Join adjacent spatial blocks without exceeding the bounded read size."""
    first = last = None
    for start, stop in ranges:
        if first is not None and 0 <= start-last <= gap and stop - first <= READ_POINTS:
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


INTERACTIVE_BLOCKS = 24
NAVIGATION_BLOCKS = 2048
NAVIGATION_ROWS_PER_BLOCK = 2048


def sample(item, budget, origin, fallback, mode, z_range, view, quick=False, cancel=None, *, decorate=None, interactive=False):
    """Sample a viewport. A review can colour its rows using the original IDs."""
    fallback,mode=__import__('scan_appearance').resolve(item,fallback,mode)
    import jobdata, preview_index, preview_store, registration_pose
    frame = validate_view(view)
    budget = max(0, min(1000000, int(budget)))
    if not budget:
        return np.empty((0, 6), dtype='<f4')
    _check_cancel(cancel)
    navigation = view.get("navigation_sample") == "stratified-v1" and not decorate and not interactive
    cache = None
    if decorate is not None and quick:
        raise ValueError('Review detail requires original point IDs.')
    if decorate is None and not interactive:
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
        if not interactive and not navigation:
            import priority_view_cache
            accelerated = priority_view_cache.sample(item, budget, origin, fallback, mode, z_range, view, cancel, decorate)
            if accelerated is not None:
                if cache is not None:
                    if cache != preview_store.cache_path(item, budget, origin, fallback, mode, z_range, quick, view=view):
                        raise ValueError('The scan changed while loading detail. Refresh the view.')
                    preview_store.write(cache, accelerated)
                return accelerated
        m = item['meta']; n = int(m['count'])
        included, hidden = jobdata.view_selections(item)
        source_key = preview_index._key(m)
        local_view = registration_pose.local_view(m, view)
        boxes = preview_index.cached_bounds(m, source_key)
        if interactive and boxes is None:
            return _quick_sample(item, budget, origin, fallback, mode, z_range, view, cancel=cancel)
        builder = preview_index.BoundsBuilder(m, source_key) if boxes is None else None
        if boxes is None:
            ranges = ((start, min(n, start+READ_POINTS)) for start in range(0, n, READ_POINTS))
        else:
            blocks = preview_index.candidate_blocks(m, local_view, validate_view(local_view), boxes=boxes, included=included, hidden=hidden)
            if interactive:
                candidates = np.array(list(blocks), dtype=np.int64)
                if len(candidates) > INTERACTIVE_BLOCKS:
                    bounds = boxes[candidates[:, 0] // preview_index.BLOCK]
                    target, axes, scale, aspect = validate_view(local_view)
                    delta = bounds.mean(axis=1) - target
                    projected = (delta @ axes[:2].T) / [scale, scale / aspect]
                    cells = np.clip(np.floor((projected + 1) * 2), 0, 3).astype(int)
                    cell_ids = cells[:, 0] * 4 + cells[:, 1]
                    distance = np.sum(delta**2, axis=1)
                    # Cover different parts of the screen first. Taking the
                    # nearest rows alone can show one strip of a long tunnel.
                    by_distance = np.argsort(distance, kind='stable')
                    _, first = np.unique(cell_ids[by_distance], return_index=True)
                    representative = by_distance[first]
                    remainder = by_distance[~np.isin(by_distance, representative)]
                    take = np.r_[representative, remainder][:INTERACTIVE_BLOCKS]
                    candidates = candidates[np.sort(take)]
                blocks = iter(candidates)
            elif navigation:
                candidates = list(blocks)
                if len(candidates) > NAVIGATION_BLOCKS:
                    take = np.linspace(0,len(candidates)-1,NAVIGATION_BLOCKS,dtype=np.int64)
                    candidates = [candidates[i] for i in take]
                def slices():
                    for start, stop in candidates:
                        width = min(NAVIGATION_ROWS_PER_BLOCK,stop-start)
                        block = start // preview_index.BLOCK
                        # Vary the slice phase across scan lines to avoid
                        # repeating bands in the navigation display.
                        offset = (block * 2654435761 % 4294967296) % (stop-start-width+1)
                        yield start+offset,start+offset+width
                blocks = slices()
            fine = None
            if not interactive and not navigation and local_view.get('projection') != 'perspective':
                import fine_view_index
                coarse = list(blocks)
                fine = fine_view_index.candidates(source_key, local_view, validate_view(local_view), coarse)
                blocks = coarse if fine is None else fine
            ranges = _coalesced_ranges(blocks, gap=1024 if fine is not None else 0)
        chosen = _PrioritySample(budget)
        # The pose is constant for this viewport request. Avoid validating the
        # identity transform again for every spatial block (tens of thousands
        # of blocks on a full tunnel), retaining the original nonidentity path.
        identity_pose = registration_pose.is_identity(m)
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
                q = local if identity_pose else registration_pose.points(m, local)
                use = _view_mask(q, view, frame)
                jobdata.apply_view_selections(q, use, included, hidden)
                if not use.any():
                    continue
                if excluded is not None:
                    excluded.seek(start); flags = np.fromfile(excluded, 'u1', count=count)
                    if len(flags) != count:
                        raise ValueError('The point deletion mask changed while loading detail.')
                    use &= flags == 0
                indices = np.flatnonzero(use)
                chosen.add(indices + start, q[indices])
        _check_cancel(cancel)
        if builder is not None:
            builder.finish()
        elif preview_index._key(m) != source_key:
            raise ValueError('The scan changed while loading detail. Refresh the view.')
        selected, q = chosen.finish_points()
        colours = jobdata._colour_from_attributes(m, selected, fallback, mode, q, z_range, cancel=cancel)
        packed = np.empty((len(q), 6), dtype='<f4'); packed[:, :3] = q-origin; packed[:, 3:] = colours
        if decorate is not None:
            decorate(selected, packed)
    _check_cancel(cancel)
    if cache is not None:
        # Never publish a reusable result under a revision read before an edit.
        if cache != preview_store.cache_path(item, budget, origin, fallback, mode, z_range, quick, view=view):
            raise ValueError('The scan changed while loading detail. Refresh the view.')
        preview_store.write(cache, packed)
    return packed
