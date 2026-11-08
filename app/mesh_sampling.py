# Copyright (c) 2026 Damian Gulczynski.
"""Bounded, read-only access to retained measurements for surface construction.

Tunnel support is counted from every retained measurement. Generic reconstruction
uses spatial representatives, each an unchanged measured XYZ row. Neither path
uses the display cache or writes an index beside the source scan.
"""
from contextlib import ExitStack, closing
from copy import deepcopy
from pathlib import Path
import math
import numpy as np
import jobdata,registration_pose
import compute_runtime


def coordinate_roundoff(minimum, maximum):
    """Conservative float64 coordinate/projection roundoff in source metres.

    This is numerical uncertainty, not a measurement tolerance or a gap-fill
    distance. It is used only to break ties at existing bin boundaries.
    """
    lo, hi = np.asarray(minimum, float), np.asarray(maximum, float)
    magnitude = np.maximum(np.maximum(np.abs(lo), np.abs(hi)), 1.)
    return float(8*np.max(np.spacing(magnitude)) +
                 16*np.finfo(np.float64).eps*max(float(np.linalg.norm(hi-lo)), 1.))


def stable_floor(values, tolerance=0):
    """Floor bins consistently at roundoff-scale exact boundary ties."""
    q = np.asarray(values, dtype=np.float64)
    nearest = np.rint(q)
    tolerance = np.maximum(tolerance, 16*np.finfo(np.float64).eps*np.maximum(np.abs(q), 1.))
    return np.floor(np.where(np.abs(q-nearest) <= tolerance, nearest, q)).astype(np.int64)


def priority_keys(values):
    """Deterministic SplitMix64 priorities, independent of chunk boundaries."""
    x = np.asarray(values, dtype=np.uint64).copy()
    with np.errstate(over='ignore'):
        x += np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


class Source:
    """Repeatable chunks with exact masks/selections and source-change checks."""
    def __init__(self, meta, selection=None, block=100000):
        self.meta = deepcopy(meta)
        self.selection = jobdata.selection_spec(deepcopy(selection)) if selection else None
        self.count = int(meta['count'])
        self.block = max(1000, min(250000, int(block)))
        if self.count < 3:
            raise ValueError('At least three source points are required for meshing.')
        self.paths = [Path(meta['file'])]
        if meta.get('mask_file'):
            self.paths.append(Path(meta['mask_file']))
        self.signature = self._signature()
        if self.signature[0][1] != self.count * 24:
            raise ValueError('The source coordinate cache does not match its point count.')
        if len(self.paths) > 1 and self.signature[1][1] != self.count:
            raise ValueError('The source exclusion mask does not match its point count.')
        self.passes = 0
        self.statistics = None

    def _signature(self):
        result = []
        for path in self.paths:
            st = path.stat()
            result.append((str(path.resolve()), st.st_size, st.st_mtime_ns,
                           st.st_ctime_ns, st.st_ino))
        return tuple(result)

    def assert_unchanged(self):
        if self._signature() != self.signature:
            raise ValueError('The source scan or exclusion mask changed while meshing. Run the operation again on its current retained points.')

    def chunks(self, progress=lambda s: None, label='Reading retained measurements'):
        self.assert_unchanged()
        self.passes += 1
        spans = [(0, self.count)]
        if self.selection:
            # Reuse an existing conservative index; never create one as a side
            # effect of meshing, including in a read-only reference project.
            import preview_index
            boxes = preview_index.cached_bounds(self.meta)
            if boxes is not None:
                boxes = preview_index.world_boxes(self.meta, boxes)
                spans = preview_index.selection_blocks(self.meta, self.selection, boxes=boxes)
        with ExitStack() as stack:
            source = stack.enter_context(self.paths[0].open('rb'))
            mask = stack.enter_context(self.paths[1].open('rb')) if len(self.paths) > 1 else None
            for start, stop in spans:
                for offset in range(start, stop, self.block):
                    compute_runtime.check_cancel()
                    count = min(self.block, stop-offset)
                    source.seek(offset*24)
                    q = np.fromfile(source, '<f8', count=count*3)
                    if q.size != count*3:
                        raise ValueError('The source coordinate cache is incomplete.')
                    q = registration_pose.points(self.meta, q.reshape(count, 3))
                    use = np.isfinite(q).all(1)
                    if self.selection:
                        use &= jobdata._selection_mask_validated(q, self.selection)
                    if mask is not None:
                        mask.seek(offset)
                        kept = np.fromfile(mask, 'u1', count=count)
                        if len(kept) != count:
                            raise ValueError('The source exclusion mask is incomplete.')
                        use &= kept == 0
                    progress(f'{label}: {offset+count:,} / {self.count:,}')
                    if use.any():
                        ids = np.flatnonzero(use).astype(np.int64)+offset
                        yield q[use], ids
        self.assert_unchanged()

    def stats(self, progress=lambda s: None):
        """Stable double-precision covariance; memory independent of scan size."""
        if self.statistics is not None:
            self.assert_unchanged()
            return self.statistics
        total = 0
        anchor = None
        mean = np.zeros(3)
        scatter = np.zeros((3, 3))
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for q, _ in self.chunks(progress, 'Measuring full retained extent'):
            if anchor is None:
                anchor = q[0].copy()
            local = q-anchor
            batch_mean = local.mean(0)
            delta = batch_mean-mean
            centred = local-batch_mean
            count = len(q)
            scatter += centred.T@centred + np.outer(delta, delta)*(total*count/(total+count))
            mean += delta*(count/(total+count))
            total += count
            lo = np.minimum(lo, q.min(0))
            hi = np.maximum(hi, q.max(0))
        if total < 3:
            raise ValueError('The selected area contains too few finite retained points for meshing.')
        self.statistics = dict(count=total, centre=anchor+mean,
                               covariance=scatter/max(total-1, 1), minimum=lo, maximum=hi)
        return self.statistics


class CellReservoir:
    """Bottom-k original-ID priorities per cell; exact counts, bounded samples.

    SplitMix64 gives reproducible uniform measurement priorities. The retained
    sample is independent of how source rows are split into I/O chunks. There
    is no Python loop over measurements or occupied cells in add().
    """
    def __init__(self, cells, capacity):
        self.capacity = int(capacity)
        self.keys = np.full((int(cells), self.capacity), np.iinfo(np.uint64).max, dtype=np.uint64)
        self.values = np.full((int(cells), self.capacity), np.nan)
        self.counts = np.zeros(int(cells), np.int64)
        self.points = np.full((int(cells), 3), np.nan)

    def add(self, cells, values, ids, points):
        cells = np.asarray(cells, np.int64)
        priorities = priority_keys(ids)
        order = np.lexsort((priorities, cells))
        ordered = cells[order]
        touched, starts, counts = np.unique(ordered, return_index=True, return_counts=True)
        self.counts[touched] += counts
        ranks = np.arange(len(order))-np.repeat(starts, counts)
        selected = ranks < self.capacity
        chosen, ranks = order[selected], ranks[selected]
        rows = np.repeat(np.arange(len(touched)), np.minimum(counts, self.capacity))
        new_keys = np.full((len(touched), self.capacity), np.iinfo(np.uint64).max, np.uint64)
        new_values = np.full((len(touched), self.capacity), np.nan)
        new_keys[rows, ranks] = priorities[chosen]
        new_values[rows, ranks] = values[chosen]
        first = order[starts]
        replace = priorities[first] < self.keys[touched].min(1)
        self.points[touched[replace]] = points[first[replace]]
        combined_keys = np.concatenate((self.keys[touched], new_keys), axis=1)
        combined_values = np.concatenate((self.values[touched], new_values), axis=1)
        take = np.argpartition(combined_keys, self.capacity-1, axis=1)[:, :self.capacity]
        self.keys[touched] = np.take_along_axis(combined_keys, take, axis=1)
        self.values[touched] = np.take_along_axis(combined_values, take, axis=1)

    def medians(self):
        out = np.full(len(self.counts), np.nan)
        observed = self.counts > 0
        out[observed] = np.nanmedian(self.values[observed], axis=1)
        return out


_VOXEL_DTYPE = np.dtype([('x', '<i8'), ('y', '<i8'), ('z', '<i8')])


def _voxel_records(keys):
    return np.ascontiguousarray(keys, dtype='<i8').view(_VOXEL_DTYPE).reshape(-1)


def spatial_sample(source, budget=500000, distance=0, progress=lambda s: None):
    """One original point nearest each voxel centre, bounded across the extent.

    If occupied voxels exceed the budget, stable voxel priorities retain a
    spatially unbiased set of voxels. More observations in a dense scanner setup
    cannot displace an observed voxel merely through their repetition.
    """
    stats = source.stats(progress)
    budget = max(3, min(2000000, int(budget)))
    distance = float(distance or 0)
    if not np.isfinite(distance) or distance < 0:
        raise ValueError('Point sampling distance must be a finite non-negative number.')
    extent = stats['maximum']-stats['minimum']
    surface_box = 2*sum(extent[i]*extent[j] for i, j in ((0, 1), (1, 2), (2, 0)))
    automatic = math.sqrt(max(surface_box, 1e-16)/(1.3*budget))
    cell = max(distance, automatic, np.max(extent)/1e12, 1e-9)
    origin = stats['minimum']
    kept_keys = np.empty((0, 3), np.int64)
    kept_points = np.empty((0, 3))
    kept_scores = np.empty(0)
    kept_ids = np.empty(0, np.int64)
    kept_priorities = np.empty(0, np.uint64)
    threshold = np.uint64(np.iinfo(np.uint64).max)
    limited = False
    def prepare(chunk):
        points,ids=chunk
        local = (points-origin)/cell
        keys = np.floor(local).astype(np.int64)
        scores = np.sum((local-keys-.5)**2, axis=1)
        priorities = (priority_keys(keys[:, 0].view(np.uint64)) ^
                      priority_keys(keys[:, 1].view(np.uint64)+np.uint64(0xD1B54A32D192ED03)) ^
                      priority_keys(keys[:, 2].view(np.uint64)+np.uint64(0x94D049BB133111EB)))
        order = np.lexsort((ids, scores, keys[:, 2], keys[:, 1], keys[:, 0]))
        sorted_keys = keys[order]
        first = np.r_[True, np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)]
        order = order[first]
        keys, points, scores, ids, priorities = (a[order] for a in (keys, points, scores, ids, priorities))
        return keys,points,scores,ids,priorities

    with closing(compute_runtime.process_chunks(prepare,source.chunks(),progress=progress,
                 label='Meshing · sampling measured surface cells')) as results:
      for keys,points,scores,ids,priorities in results:
        # The shared reservoir and its threshold are updated only here, in
        # source order. Workers independently compute one chunk's candidates.
        eligible = priorities <= threshold
        keys, points, scores, ids, priorities = (a[eligible] for a in (keys, points, scores, ids, priorities))
        if not len(keys):continue
        records = _voxel_records(keys)
        existing = _voxel_records(kept_keys)
        where = np.searchsorted(existing, records)
        match = where < len(existing)
        if len(existing):
            match &= existing[np.minimum(where, len(existing)-1)] == records
            better = match & ((scores < kept_scores[np.minimum(where, len(existing)-1)]) |
                ((scores == kept_scores[np.minimum(where, len(existing)-1)]) & (ids < kept_ids[np.minimum(where, len(existing)-1)])))
            at = where[better]
            kept_points[at], kept_scores[at], kept_ids[at] = points[better], scores[better], ids[better]
        fresh = ~match
        if not fresh.any():
            continue
        kept_keys = np.concatenate((kept_keys, keys[fresh]))
        kept_points = np.concatenate((kept_points, points[fresh]))
        kept_scores = np.concatenate((kept_scores, scores[fresh]))
        kept_ids = np.concatenate((kept_ids, ids[fresh]))
        kept_priorities = np.concatenate((kept_priorities, priorities[fresh]))
        if len(kept_keys) > budget:
            limited = True
            take = np.argpartition(kept_priorities, budget-1)[:budget]
            kept_keys, kept_points, kept_scores, kept_ids, kept_priorities = (
                a[take] for a in (kept_keys, kept_points, kept_scores, kept_ids, kept_priorities))
            threshold = kept_priorities.max()
        order = np.lexsort((kept_keys[:, 2], kept_keys[:, 1], kept_keys[:, 0]))
        kept_keys, kept_points, kept_scores, kept_ids, kept_priorities = (
            a[order] for a in (kept_keys, kept_points, kept_scores, kept_ids, kept_priorities))
    if len(kept_points) < 3:
        raise ValueError('Spatial sampling retained too few distinct cells. Reduce the point sampling distance.')
    return kept_points, dict(support_mode='spatial_representatives',
        finite_source_points=stats['count'], source_passes=source.passes,
        sampling_cell_m=float(cell), working_voxels=int(len(kept_points)),
        sampling_budget_limited=bool(limited),
        sampling_note='One unchanged measured XYZ row nearest each retained voxel centre. '
        'Voxel priorities bound working memory without weighting the sample towards dense scanner setups. '
        'The reported cell size is a sampling resolution, not an accuracy guarantee.')
