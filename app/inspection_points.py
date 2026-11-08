# Copyright (c) 2026 Damian Gulczynski.
"""Exact, disk-backed nearest retained reference point queries.

CloudReference copies every retained finite float64 coordinate into a private
spatial partition. It does not use the display preview, attributes or a sampled
reference. A bounded sample chooses partition planes only; every source point
is assigned to a child, and a poor split falls back to an exact radix median.
Coincident coordinates may share one identical query representative, without
changing reference_count or the nearest-point distance.

The spatial tree and coordinates live on disk. Owned read blocks, a small node
cache and an LRU of bounded cKDTrees limit working memory independently of the
reference size. The LRU has a 512 MiB component-estimate budget, plus at most one
new leaf being built; this is not a whole-process memory guarantee. Query blocks
contain at most 25,000 points. Queries use exact (eps=0) leaf searches and conservative bounding
box pruning, including neighbours in other partitions. This is directed,
unsigned distance to measured points, not a fitted/interpolated surface, signed
deviation, registration or an accuracy estimate.

The index is private to one evaluator: no persistent index can be reused with a
different mask/source. File identities, sizes and nanosecond modification/change
times are checked before/after building and each query batch. Source mutations
invalidate the evaluator. close(), cancellation and failures remove its files.
"""
from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import shutil
import sqlite3
import tempfile

import numpy as np
from scipy.spatial import cKDTree

import jobdata


READ_POINTS = 100_000
LEAF_POINTS = 32_768
QUERY_POINTS = 25_000
TREE_CACHE = 4_096
TREE_CACHE_BYTES = 512 * 1024 * 1024
NODE_CACHE = 4_096
SPLIT_SAMPLE = 8_192
_SIGN = np.uint64(1 << 63)
_ALL = np.uint64((1 << 64) - 1)


def _check(cancel):
    if cancel():
        raise jobdata.Cancelled('Cloud reference comparison cancelled. Original cloud data is unchanged.')


def _file_signature(path, expected):
    path = Path(path).resolve()
    stat = path.stat()
    if stat.st_size != expected:
        raise ValueError('The reference coordinate cache or deletion mask has an invalid length.')
    return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            stat.st_dev, stat.st_ino)


def _signature(meta):
    import analysis_sources, registration_pose
    raw = meta.get('count')
    if isinstance(raw, (bool, np.bool_)) or not isinstance(raw, (int, np.integer)) or raw < 1:
        raise ValueError('The reference cloud must contain at least one original point.')
    rows = []
    for row in analysis_sources.parts(meta):
        m = row['meta']; n = int(m['count']); R, t = registration_pose.get(m)
        rows.append((n, _file_signature(m['file'], n * 24),
                     _file_signature(m['mask_file'], n) if m.get('mask_file') else None,
                     tuple(R.ravel()), tuple(t)))
    if sum(row[0] for row in rows) != int(raw):
        raise ValueError('The reference scope count does not match its original scans.')
    return (int(raw), tuple(rows))


def _ordered_keys(values):
    """Order-preserving IEEE-754 keys for finite float64 coordinates."""
    bits = np.asarray(values, dtype='<f8').view('<u8')
    return np.where((bits & _SIGN) != 0, bits ^ _ALL, bits ^ _SIGN)


def _key_value(key):
    key = np.uint64(key)
    bits = key ^ _SIGN if key & _SIGN else key ^ _ALL
    return float(np.asarray([bits], dtype='<u8').view('<f8')[0])


class CloudReference:
    signed = False

    def __init__(self, meta, work, progress=lambda message: None, cancel=lambda: False):
        self._meta = meta
        self._progress = progress
        self._cancel = cancel
        self._closed = False
        self._directory = None
        self._connection = self._points = None
        self._trees = OrderedDict()
        self._cached_tree_bytes = 0
        self._nodes = OrderedDict()
        self._file_number = 0
        # Capture limits so changing a module constant cannot invalidate a live
        # evaluator. Tests can exercise the same paths with very small blocks.
        self._read_limit = max(1, min(int(READ_POINTS), 100_000))
        self._leaf_limit = max(1, min(int(LEAF_POINTS), 32_768))
        self._query_limit = max(1, min(int(QUERY_POINTS), 25_000))
        self._tree_limit = max(1, min(int(TREE_CACHE), 4_096))
        self._tree_byte_limit = max(1, min(int(TREE_CACHE_BYTES), 512 * 1024 * 1024))
        self._node_limit = max(1, min(int(NODE_CACHE), 4_096))
        self._sample_limit = max(1, min(int(SPLIT_SAMPLE), 8_192))
        self.reference_count = 0
        self.index_stats = dict(source_points=0, retained_finite_points=0,
                                excluded_points=0, nonfinite_retained_points=0,
                                leaves=0, nodes=0, largest_leaf=0,
                                duplicate_coordinates_collapsed=0,
                                maximum_owned_read_points=0,
                                peak_cached_trees=0, peak_cached_nodes=0,
                                peak_cached_component_bytes=0, tree_builds=0,
                                exact_median_fallbacks=0, leaf_queries=0,
                                robust_distance_fallback_queries=0)
        self.note = ('Unsigned exact nearest retained finite reference-point distance; '
                     'every retained reference point is indexed, without sampling or '
                     'surface interpolation. Point spacing affects the result. '
                     'Coincident coordinates share an identical query representative. '
                     'No registration or automatic alignment is performed.')
        try:
            _check(cancel)
            self._signature = _signature(meta)
            work = Path(work)
            work.mkdir(parents=True, exist_ok=True)
            # At most the original partition plus its children coexist. Completed
            # leaves replace their partition files, so storage stays below about
            # two retained XYZ copies plus the disk node index.
            jobdata.room(work, self._signature[0] * 48 + 16 * 1024 * 1024)
            self._directory = Path(tempfile.mkdtemp(prefix='inspection-cloud-', dir=work))
            self._connection = sqlite3.connect(self._directory / 'nodes.sqlite')
            self._connection.execute('PRAGMA cache_size=-8192')
            self._connection.execute('PRAGMA mmap_size=0')
            self._connection.execute('PRAGMA temp_store=FILE')
            self._connection.execute('PRAGMA journal_mode=OFF')
            self._connection.execute('PRAGMA synchronous=OFF')
            self._connection.execute('''CREATE TABLE nodes (
                id INTEGER PRIMARY KEY, count INTEGER NOT NULL,
                low BLOB NOT NULL, high BLOB NOT NULL,
                child0 INTEGER, child1 INTEGER, child2 INTEGER,
                offset INTEGER, stored INTEGER)''')
            path, low, high = self._copy_source()
            self._root = self._new_node(self.reference_count, low, high)
            self._build(path, low, high)
            self._connection.commit()
            self._assert_current()
            self._points = (self._directory / 'points.xyz64').open('rb')
            self._emit(f'Reference ready · {self.reference_count:,} retained finite points · '
                       f'{self.index_stats["leaves"]:,} spatial blocks')
        except BaseException:
            self._close_on_failure()
            raise

    def _emit(self, message):
        _check(self._cancel)
        self._progress(message)
        _check(self._cancel)

    def _assert_current(self):
        _check(self._cancel)
        try:
            current = _signature(self._meta)
        except (OSError, KeyError, ValueError) as exc:
            raise ValueError('The reference cloud changed or became unavailable. Restart the comparison.') from exc
        if current != self._signature:
            raise ValueError('The reference cloud or deletion mask changed. Restart the comparison.')

    def _path(self):
        self._file_number += 1
        return self._directory / f'partition-{self._file_number}.xyz64'

    def _read(self, source, count):
        _check(self._cancel)
        if count > self._read_limit:
            raise RuntimeError('Reference read exceeded its owned block limit.')
        values = np.fromfile(source, dtype='<f8', count=count * 3)
        if values.size != count * 3:
            raise ValueError('The reference coordinate cache is incomplete.')
        self.index_stats['maximum_owned_read_points'] = max(
            self.index_stats['maximum_owned_read_points'], count)
        _check(self._cancel)
        return values.reshape(count, 3)

    def _blocks(self, path, count):
        with path.open('rb') as source:
            for start in range(0, count, self._read_limit):
                yield self._read(source, min(self._read_limit, count - start))

    def _copy_source(self):
        import analysis_sources
        count = self._signature[0]
        path = self._path()
        low = np.full(3, np.inf)
        high = np.full(3, -np.inf)
        counters = dict(retained_points=0, deleted_points=0, invalid_points=0)
        with path.open('xb') as output:
            for ids, points in analysis_sources.blocks(self._meta, counters,
                                                       lambda m: self._emit('Indexing reference cloud · ' + m),
                                                       self._cancel, self._read_limit):
                self.index_stats['maximum_owned_read_points'] = max(
                    self.index_stats['maximum_owned_read_points'], min(self._read_limit, count))
                points.tofile(output)
                self.reference_count += len(points)
                low = np.minimum(low, points.min(axis=0))
                high = np.maximum(high, points.max(axis=0))
        self.index_stats['excluded_points'] = counters['deleted_points']
        self.index_stats['nonfinite_retained_points'] = counters['invalid_points']
        self.index_stats['source_points'] = count
        self.index_stats['retained_finite_points'] = self.reference_count
        if not self.reference_count:
            raise ValueError('The reference cloud has no retained finite points.')
        return path, low, high

    def _new_node(self, count, low, high):
        cursor = self._connection.execute(
            'INSERT INTO nodes(count,low,high) VALUES (?,?,?)',
            (int(count), np.asarray(low, dtype='<f8').tobytes(),
             np.asarray(high, dtype='<f8').tobytes()))
        self.index_stats['nodes'] += 1
        return int(cursor.lastrowid)

    def _pivot(self, path, count, axis):
        # Uniformly spaced short reads avoid assuming input order is spatial.
        # These points choose a split only: none replace source coordinates.
        windows = min(32, self._sample_limit)
        size = min(self._read_limit, max(1, self._sample_limit // windows))
        size = min(size, count)
        starts = np.linspace(0, count - size, windows, dtype=np.int64)
        values = []
        with path.open('rb') as source:
            for start in starts:
                source.seek(int(start) * 24)
                values.append(self._read(source, size)[:, axis].copy())
        sample = np.sort(np.concatenate(values))
        middle = len(sample) // 2
        if len(sample) > 3:
            # A median landing just inside one of two distant clusters can
            # leave a small child spanning both clusters. Prefer an obvious
            # central empty gap, so shuffled scanner/file blocks do not create
            # unnecessarily broad query bounds. The later count check still
            # rejects an unbalanced guess, and no reference points are omitted.
            begin, end = len(sample) // 4, 3 * len(sample) // 4
            gaps = sample[begin + 1:end + 1] / 2 - sample[begin:end] / 2
            gap = begin + int(np.argmax(gaps))
            span = sample[-1] / 2 - sample[0] / 2
            if gaps[gap - begin] > span * .2:
                return float(sample[gap] / 2 + sample[gap + 1] / 2)
        return float(sample[middle])

    def _exact_pivot(self, path, count, axis):
        """Select a true median with eight bounded byte-histogram passes."""
        self.index_stats['exact_median_fallbacks'] += 1
        rank = count // 2
        prefix = np.uint64(0)
        mask = np.uint64(0)
        for shift in range(56, -1, -8):
            histogram = np.zeros(256, dtype=np.int64)
            seen = 0
            for block_number, points in enumerate(self._blocks(path, count)):
                keys = _ordered_keys(points[:, axis])
                keys = keys[(keys & mask) == prefix]
                bins = ((keys >> np.uint64(shift)) & np.uint64(255)).astype(np.int64)
                histogram += np.bincount(bins, minlength=256)
                seen += len(points)
                if block_number % 16 == 0:
                    self._emit(f'Balancing reference block · pass {(56 - shift) // 8 + 1} / 8 · '
                               f'{seen:,} / {count:,} points')
            cumulative = np.cumsum(histogram)
            digit = int(np.searchsorted(cumulative, rank, side='right'))
            if digit > 255:
                raise RuntimeError('Reference median partition lost a source coordinate.')
            rank -= int(cumulative[digit - 1]) if digit else 0
            prefix |= np.uint64(digit) << np.uint64(shift)
            mask |= np.uint64(255) << np.uint64(shift)
            self._emit('Balancing a dense reference spatial block')
        return _key_value(prefix)

    def _partition(self, path, count, axis, pivot):
        paths = [self._path() for _ in range(3)]
        counts = [0, 0, 0]
        lows = [np.full(3, np.inf) for _ in range(3)]
        highs = [np.full(3, -np.inf) for _ in range(3)]
        with ExitStack() as files:
            outputs = [files.enter_context(p.open('xb')) for p in paths]
            seen = 0
            for block_number, points in enumerate(self._blocks(path, count)):
                coordinate = points[:, axis]
                masks = (coordinate < pivot, coordinate == pivot, coordinate > pivot)
                for side, use in enumerate(masks):
                    part = points[use]
                    if len(part):
                        part.tofile(outputs[side])
                        counts[side] += len(part)
                        lows[side] = np.minimum(lows[side], part.min(axis=0))
                        highs[side] = np.maximum(highs[side], part.max(axis=0))
                seen += len(points)
                if block_number % 16 == 0:
                    self._emit(f'Partitioning reference spatial block · {seen:,} / {count:,} points')
        if sum(counts) != count:
            raise RuntimeError('Reference partition did not preserve every finite coordinate.')
        return [(p, n, lo, hi) for p, n, lo, hi in zip(paths, counts, lows, highs)]

    def _build(self, path, low, high):
        stack = [(self._root, path, self.reference_count, low, high)]
        assigned = 0
        with (self._directory / 'points.xyz64').open('xb') as output:
            while stack:
                _check(self._cancel)
                node, path, count, low, high = stack.pop()
                identical = bool(np.array_equal(low, high))
                if count <= self._leaf_limit or identical:
                    offset = output.tell()
                    stored = 1 if identical else count
                    if identical:
                        with path.open('rb') as source:
                            self._read(source, 1).tofile(output)
                        self.index_stats['duplicate_coordinates_collapsed'] += count - 1
                    else:
                        for points in self._blocks(path, count):
                            points.tofile(output)
                    self._connection.execute('UPDATE nodes SET offset=?,stored=? WHERE id=?',
                                             (offset, stored, node))
                    path.unlink()
                    self.index_stats['leaves'] += 1
                    self.index_stats['largest_leaf'] = max(self.index_stats['largest_leaf'], stored)
                    assigned += count
                    self._emit(f'Partitioning reference · {assigned:,} / {self.reference_count:,} points ready')
                    continue
                varying = np.flatnonzero(high > low)
                # Half-extents avoid overflow for finite, opposite-sign bounds.
                extent = high[varying] / 2 - low[varying] / 2
                axis = int(varying[np.argmax(extent)])
                pivot = self._pivot(path, count, axis)
                parts = self._partition(path, count, axis, pivot)
                if max(parts[0][1], parts[2][1]) > count * .875:
                    for part in parts:
                        part[0].unlink()
                    pivot = self._exact_pivot(path, count, axis)
                    parts = self._partition(path, count, axis, pivot)
                children = []
                for child_path, n, lo, hi in parts:
                    if not n:
                        child_path.unlink()
                        continue
                    child = self._new_node(n, lo, hi)
                    children.append(child)
                    stack.append((child, child_path, n, lo, hi))
                if len(children) == 1:
                    # A nonconstant selected axis must produce distinct sides.
                    raise RuntimeError('Reference spatial partition failed to make progress.')
                children += [None] * (3 - len(children))
                self._connection.execute('UPDATE nodes SET child0=?,child1=?,child2=? WHERE id=?',
                                         (*children, node))
                path.unlink()
                if self.index_stats['nodes'] % 256 < 3:
                    self._connection.commit()
        if assigned != self.reference_count:
            raise RuntimeError('Reference index did not preserve every retained finite point.')

    def _node(self, ident):
        if ident in self._nodes:
            self._nodes.move_to_end(ident)
            return self._nodes[ident]
        row = self._connection.execute(
            'SELECT low,high,child0,child1,child2,offset,stored FROM nodes WHERE id=?',
            (int(ident),)).fetchone()
        if row is None:
            raise ValueError('The transient reference spatial index is incomplete.')
        node = (np.frombuffer(row[0], dtype='<f8'), np.frombuffer(row[1], dtype='<f8'),
                tuple(int(v) for v in row[2:5] if v is not None), row[5], row[6])
        self._nodes[ident] = node
        while len(self._nodes) > self._node_limit:
            self._nodes.popitem(last=False)
        self.index_stats['peak_cached_nodes'] = max(self.index_stats['peak_cached_nodes'], len(self._nodes))
        return node

    @staticmethod
    def _lower(points, node):
        # Outward-rounded boxes and a downward-rounded norm prevent pruning a
        # neighbouring block at a representable boundary through rounding alone.
        with np.errstate(over='ignore', invalid='ignore'):
            low = np.nextafter(node[0], -np.inf)
            high = np.nextafter(node[1], np.inf)
            delta = np.maximum(np.maximum(low - points, points - high), 0.)
            return np.maximum(0., np.nextafter(np.hypot.reduce(delta, axis=1), -np.inf))

    def _tree(self, ident, node):
        if ident in self._trees:
            self._trees.move_to_end(ident)
            return self._trees[ident][:2]
        _check(self._cancel)
        self._points.seek(int(node[3]))
        # Leaf reads can be larger than a deliberately tiny test read limit.
        parts = [self._read(self._points, min(self._read_limit, int(node[4]) - start))
                 for start in range(0, int(node[4]), self._read_limit)]
        points = parts[0] if len(parts) == 1 else np.concatenate(parts)
        # cKDTree uses float64 directly. Translating an entire block to one
        # origin can round distinct small coordinates together when the block
        # also contains a distant outlier, so the original values are retained.
        tree = cKDTree(points, compact_nodes=True, balanced_tree=True)
        result = (points, tree)
        self.index_stats['tree_builds'] += 1
        # The point array is shared with cKDTree; do not count it twice.
        # Budget its owned float64 coordinates and integer permutation, a
        # conservative 128 bytes per native node, and 4 KiB per cached object.
        # The node allowance exceeds the current bundled SciPy node size, but
        # this remains a component estimate, not a process-RSS assertion.
        component_bytes = points.nbytes + tree.indices.nbytes + tree.size * 128 + 4096
        if component_bytes <= self._tree_byte_limit:
            while self._trees and (len(self._trees) >= self._tree_limit or
                    self._cached_tree_bytes + component_bytes > self._tree_byte_limit):
                self._cached_tree_bytes -= self._trees.popitem(last=False)[1][2]
            self._trees[ident] = (*result, component_bytes)
            self._cached_tree_bytes += component_bytes
        self.index_stats['peak_cached_trees'] = max(self.index_stats['peak_cached_trees'], len(self._trees))
        self.index_stats['peak_cached_component_bytes'] = max(
            self.index_stats['peak_cached_component_bytes'], self._cached_tree_bytes)
        _check(self._cancel)
        return result

    @staticmethod
    def _robust_norm(a, b):
        with np.errstate(over='ignore', invalid='ignore', under='ignore'):
            delta = a - b
            direct = np.hypot.reduce(delta, axis=-1)
            overflow = ~np.isfinite(delta).all(axis=-1)
            if np.any(overflow):
                # Divide before subtracting only where subtraction overflowed;
                # ordinary survey-coordinate differences retain their precision.
                scaled = np.hypot.reduce(a / 2 - b / 2, axis=-1) * 2
                direct = np.where(overflow, scaled, direct)
            return direct

    def _nearest(self, ident, node, points):
        original, tree = self._tree(ident, node)
        self.index_stats['leaf_queries'] += 1
        result = np.full(len(points), np.inf)
        distance, index = tree.query(points, k=1, eps=0., workers=1)
        usable = (index < len(original)) & np.isfinite(distance)
        rows = np.flatnonzero(usable)
        result[rows] = self._robust_norm(points[rows], original[index[rows]])
        # Rare IEEE extremes can overflow/underflow the squared norm inside
        # cKDTree. Recheck only those queries against this bounded leaf using
        # robust hypot; never allocate a whole-cloud pairwise distance matrix.
        usable &= (distance >= 1e-150) | (result == 0.)
        for row in np.flatnonzero(~usable):
            _check(self._cancel)
            result[row] = np.min(self._robust_norm(original, points[row]))
            self.index_stats['robust_distance_fallback_queries'] += 1
        _check(self._cancel)
        return result

    def _evaluate_block(self, points):
        count = len(points)
        best = np.full(count, np.inf)
        seeded = np.zeros(count, dtype=np.int64)
        # Each query first descends the closest child box to obtain a finite
        # upper bound. Choosing one seed never excludes other eligible children.
        stack = [(self._root, np.arange(count, dtype=np.int64))]
        while stack:
            _check(self._cancel)
            ident, rows = stack.pop()
            node = self._node(ident)
            if not node[2]:
                best[rows] = self._nearest(ident, node, points[rows])
                seeded[rows] = ident
                continue
            lower = np.column_stack([self._lower(points[rows], self._node(c)) for c in node[2]])
            chosen = np.argmin(lower, axis=1)
            for side, child in enumerate(node[2]):
                use = rows[chosen == side]
                if len(use):
                    stack.append((child, use))
        # An exact zero cannot be improved by another leaf. This is especially
        # useful when inspecting retained points against an unedited scan.
        unresolved = np.flatnonzero(best > 0.)
        stack = [(self._root, unresolved)] if len(unresolved) else []
        while stack:
            _check(self._cancel)
            ident, rows = stack.pop()
            rows = rows[best[rows] > 0.]
            if not len(rows):
                continue
            node = self._node(ident)
            bound = self._lower(points[rows], node)
            with np.errstate(over='ignore', invalid='ignore'):
                upper = np.nextafter(best[rows] * (1 + 16 * np.finfo(float).eps), np.inf)
            rows = rows[bound <= upper]
            if not len(rows):
                continue
            if not node[2]:
                rows = rows[seeded[rows] != ident]
                if len(rows):
                    best[rows] = np.minimum(best[rows], self._nearest(ident, node, points[rows]))
            else:
                # Depth-first traversal keeps only one bounded query array per
                # pending sibling. Spatial median splits bound tree depth even
                # when many source coordinates share an axis value.
                ordered = [(float(np.min(self._lower(points[rows], self._node(child)))), child)
                           for child in node[2]]
                for _, child in sorted(ordered, reverse=True):
                    stack.append((child, rows))
        return best

    def evaluate(self, points):
        """Return (float64 distances, bool valid) in the supplied row order.

        Nonfinite query rows and unrepresentable float64 distances are NaN/False.
        Empty Nx3 input is valid. Queries do not alter points or source caches.
        The evaluator is retired after cancellation, source change or failure.
        """
        if self._closed:
            raise ValueError('The cloud reference evaluator is closed.')
        try:
            self._assert_current()
            query = np.asarray(points)
            if query.ndim != 2 or query.shape[1] != 3:
                raise ValueError('Reference queries must be an N by 3 coordinate array.')
            distance = np.full(len(query), np.nan, dtype=np.float64)
            valid = np.zeros(len(query), dtype=bool)
            for start in range(0, len(query), self._query_limit):
                self._assert_current()
                stop = min(len(query), start + self._query_limit)
                # Converting a caller's float32/noncontiguous array must not
                # allocate a second full-size float64 query cloud.
                block = np.asarray(query[start:stop], dtype=np.float64)
                finite = np.isfinite(block).all(axis=1)
                if finite.any():
                    rows = np.flatnonzero(finite) + start
                    values = self._evaluate_block(block[finite])
                    good = np.isfinite(values)
                    distance[rows[good]] = values[good]
                    valid[rows[good]] = True
                self._emit(f'Comparing with reference cloud · {stop:,} / {len(query):,} query points')
            self._assert_current()
            return distance, valid
        except BaseException:
            self._close_on_failure()
            raise

    def _close_on_failure(self):
        try:
            self.close()
        except Exception:
            # Preserve the useful calculation/cancellation exception. The
            # containing job may also remove its transient work directory.
            pass

    def close(self):
        self._closed = True
        self._trees.clear()
        self._cached_tree_bytes = 0
        self._nodes.clear()
        try:
            if self._points is not None:
                self._points.close()
                self._points = None
        finally:
            try:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
            finally:
                if self._directory is not None and self._directory.exists():
                    shutil.rmtree(self._directory)

    def __enter__(self):
        if self._closed:
            raise ValueError('The cloud reference evaluator is closed.')
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def __del__(self):
        if hasattr(self, '_closed'):
            self._close_on_failure()
