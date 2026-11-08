# Copyright (c) 2026 Damian Gulczynski.
"""Full-density, disk-backed TMatch-style tunnel lining classification.

Independent implementation informed by Wang et al. (2025),
doi:10.3390/app15062926, sections 2.2.1--2.2.3. The paper does not specify
the initial clustering algorithm completely. Here R is a Euclidean connection
distance and N is the minimum *final component* point count. Every original
lining point participates. Small components remain unclassified and retained.

An occupied grid cell has diameter <= R, so its points form a connected clique.
Two cells are joined only after an actual point pair at distance <= R is found
(or their actual bounding boxes prove that every pair is within R). Therefore
the grid is an exact acceleration structure, not voxel downsampling (edge
comparisons include a 1 nm floating-point guard). Cells,
point records and union-find parents live in a temporary SQLite database;
neighbour comparisons use bounded trees, including across all tile boundaries.

Optional ring grouping fits a weighted GMM to *physical longitudinal cluster
centres*, not arbitrary cluster IDs. It requires observed separated bands with
substantial angular support. A supplied ring width is a consistency check, not
permission to invent boundaries. Block types/sector templates are not invented.
"""
from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import math
import os
import sqlite3
import tempfile
import time

import numpy as np
from scipy.spatial import cKDTree
from scipy.special import logsumexp


RECORD = np.dtype([('index', '<i8'), ('xyz', '<f8', (3,))])
BLOCK_POINTS = 50000
CACHE_CELLS = 50000
# This bounds result/GUI metadata, not input or classification point counts.
# Excess components are explicitly reported and retained with label zero.
MAX_NAMED_COMPONENTS = 6000
# Eight guaranteed preview representatives per named group keep the duplicated
# review/automatic metadata comfortably below the portable manifest limit.
# Rendering still uses the full preview pipeline; labels cover every point.
PREVIEW_POINTS_PER_CLUSTER = 8
LABEL_WINDOW_BYTES = 8 * 1024 * 1024
LABEL_WINDOWS = 8
CLASS_NAMES = {
    1: 'GuSAC interior obstruction candidates · review',
    2: 'GuSAC recesses / outside lining · retained for review',
    3: 'GuSAC uncertain lining · retained',
    4: 'GuSAC normal disagreement · retained for review',
}


def _emit(progress, cancel, message):
    if cancel():
        raise ValueError('Tunnel classification cancelled. The source and previous result are unchanged.')
    progress(message)


def _check(cancel):
    if cancel():
        raise ValueError('Tunnel classification cancelled. The source and previous result are unchanged.')


def _settings(settings):
    radius = float(settings.get('tmatch_radius_mm', 15.0)) / 1000.0
    minimum = int(settings.get('tmatch_min_points', 30))
    width = float(settings.get('tmatch_ring_width_m', 0.0))
    if not np.isfinite(radius) or not .002 <= radius <= .2:
        raise ValueError('TMatch connection distance must be between 2 and 200 mm.')
    if not 3 <= minimum <= 1000000:
        raise ValueError('TMatch minimum component size must be between 3 and 1,000,000 points.')
    if not np.isfinite(width) or not 0 <= width <= 10:
        raise ValueError('Enter a ring width between 0 and 10 m; 0 uses observed boundaries only.')
    return radius, minimum, width


class _LabelStore:
    """Bounded mapped windows for output labels, including random point writes.

    A single map of a billion-point label file can retain gigabytes of resident
    pages despite chunked callers. This store unmaps evicted windows explicitly.
    The default maximum concurrently mapped label storage is 64 MiB.
    """

    def __init__(self, path, count, cancel=lambda: False):
        self.path = Path(path)
        self.count = int(count)
        self.cancel = cancel
        self.window_points = max(1, int(LABEL_WINDOW_BYTES) // 4)
        self.window_limit = max(1, int(LABEL_WINDOWS))
        self.windows = OrderedDict()
        self.peak_mapping_bytes = 0
        self.peak_open_windows = 0

    def _window(self, number):
        _check(self.cancel)
        number = int(number)
        if number in self.windows:
            self.windows.move_to_end(number)
            return self.windows[number]
        while len(self.windows) >= self.window_limit:
            _, previous = self.windows.popitem(last=False)
            try:
                previous.flush()
            finally:
                previous._mmap.close()
        start = number * self.window_points
        size = min(self.window_points, self.count - start)
        if start < 0 or size <= 0:
            raise IndexError('Classification label index is outside the selected point count.')
        mapped = np.memmap(self.path, dtype='<i4', mode='r+', offset=start * 4, shape=(size,))
        self.windows[number] = mapped
        self.peak_mapping_bytes = max(self.peak_mapping_bytes, sum(a.nbytes for a in self.windows.values()))
        self.peak_open_windows = max(self.peak_open_windows, len(self.windows))
        return mapped

    def _slice_parts(self, key):
        start, stop, step = key.indices(self.count)
        if step != 1:
            raise ValueError('Label storage requires contiguous slices.')
        offset = 0
        while start < stop:
            window, local = divmod(start, self.window_points)
            take = min(stop - start, self.window_points - local)
            yield window, local, offset, take
            start += take
            offset += take

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self.count)
            if step != 1:
                raise ValueError('Label storage requires contiguous slices.')
            result = np.empty(max(0, stop - start), dtype=np.int32)
            for window, local, offset, take in self._slice_parts(key):
                result[offset:offset + take] = self._window(window)[local:local + take]
            return result
        indices = np.asarray(key, dtype=np.int64)
        if indices.ndim != 1 or np.any((indices < 0) | (indices >= self.count)):
            raise IndexError('Invalid classification label indices.')
        result = np.empty(len(indices), dtype=np.int32)
        windows = indices // self.window_points
        order = np.argsort(windows, kind='stable')
        boundaries = np.r_[0, np.flatnonzero(np.diff(windows[order])) + 1, len(order)]
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            if right == left:
                continue
            use = order[left:right]
            window = int(windows[use[0]])
            result[use] = self._window(window)[indices[use] - window * self.window_points]
        return result

    def __setitem__(self, key, value):
        values = np.asarray(value, dtype=np.int32)
        if isinstance(key, slice):
            start, stop, step = key.indices(self.count)
            if step != 1 or (values.ndim != 0 and values.shape != (max(0, stop - start),)):
                raise ValueError('Classification label slice dimensions do not match.')
            for window, local, offset, take in self._slice_parts(key):
                self._window(window)[local:local + take] = values if values.ndim == 0 else values[offset:offset + take]
            return
        indices = np.asarray(key, dtype=np.int64)
        if indices.ndim != 1 or np.any((indices < 0) | (indices >= self.count)):
            raise IndexError('Invalid classification label indices.')
        if values.ndim != 0 and values.shape != indices.shape:
            raise ValueError('Classification label index/value dimensions do not match.')
        windows = indices // self.window_points
        order = np.argsort(windows, kind='stable')
        boundaries = np.r_[0, np.flatnonzero(np.diff(windows[order])) + 1, len(order)]
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            if right == left:
                continue
            use = order[left:right]
            window = int(windows[use[0]])
            self._window(window)[indices[use] - window * self.window_points] = values if values.ndim == 0 else values[use]

    def flush(self):
        for mapped in self.windows.values():
            mapped.flush()

    def close(self):
        first_error = None
        while self.windows:
            _, mapped = self.windows.popitem(last=False)
            try:
                mapped.flush()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            finally:
                try:
                    mapped._mmap.close()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error


class _Grid:
    """A bounded-memory exact radius graph. No per-point Python objects persist."""

    def __init__(self, path, radius, cancel):
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=OFF')
        self.db.execute('PRAGMA synchronous=OFF')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('PRAGMA cache_size=-65536')
        self.db.executescript('''
            CREATE TABLE cells (
                id INTEGER PRIMARY KEY, x INTEGER, y INTEGER, z INTEGER,
                parent INTEGER, n INTEGER, sx REAL, sy REAL, sz REAL,
                lo0 REAL, lo1 REAL, lo2 REAL, hi0 REAL, hi1 REAL, hi2 REAL,
                ss REAL, smin REAL, smax REAL, angles INTEGER, label INTEGER DEFAULT 0,
                UNIQUE(x,y,z));
            CREATE TABLE chunks (cell INTEGER, data BLOB);
            CREATE INDEX chunk_cell ON chunks(cell);
            CREATE TABLE components (
                root INTEGER PRIMARY KEY, n INTEGER, sx REAL, sy REAL, sz REAL,
                lo0 REAL, lo1 REAL, lo2 REAL, hi0 REAL, hi1 REAL, hi2 REAL,
                ss REAL, smin REAL, smax REAL, angles INTEGER,
                label INTEGER DEFAULT 0);
        ''')
        self.radius = radius
        # Slight inward rounding guarantees a cell diameter no larger than R.
        self.cell = radius / math.sqrt(3.0) * (1.0 - 1e-12)
        self.cancel = cancel
        self.cell_cache = OrderedDict()
        self.parent_cache = OrderedDict()
        self.cells = 0
        self.edge_checks = 0
        self.joins = 0

    @staticmethod
    def _cache_put(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > CACHE_CELLS:
            cache.popitem(last=False)

    def close(self):
        self.cell_cache.clear()
        self.parent_cache.clear()
        self.db.close()

    def add(self, indices, points, station, angle):
        q = np.asarray(points, dtype=np.float64)
        keys = np.floor(q / self.cell).astype(np.int64)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        order = np.argsort(inverse, kind='stable')
        offsets = np.r_[0, np.cumsum(np.bincount(inverse))]
        for j, key_arr in enumerate(unique):
            if j % 256 == 0:
                _check(self.cancel)
            use = order[offsets[j]:offsets[j + 1]]
            p = q[use]
            s = station[use]
            bins = np.unique(np.minimum(35, np.floor(np.mod(angle[use], 2 * np.pi) * 36 / (2 * np.pi)).astype(int)))
            bits = sum(1 << int(k) for k in bins)
            sums = p.sum(axis=0)
            low, high = p.min(axis=0), p.max(axis=0)
            key = tuple(map(int, key_arr))
            cell_id = self.cell_cache.get(key)
            if cell_id is None:
                row = self.db.execute('SELECT id FROM cells WHERE x=? AND y=? AND z=?', key).fetchone()
                cell_id = row[0] if row else None
            if cell_id is None:
                cursor = self.db.execute(
                    'INSERT INTO cells(x,y,z,parent,n,sx,sy,sz,lo0,lo1,lo2,hi0,hi1,hi2,ss,smin,smax,angles) '
                    'VALUES(?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (*key, len(p), *map(float, sums), *map(float, low), *map(float, high),
                     float(s.sum()), float(s.min()), float(s.max()), bits))
                cell_id = cursor.lastrowid
                self.db.execute('UPDATE cells SET parent=id WHERE id=?', (cell_id,))
                self.cells += 1
            else:
                self.db.execute(
                    'UPDATE cells SET n=n+?,sx=sx+?,sy=sy+?,sz=sz+?,'
                    'lo0=min(lo0,?),lo1=min(lo1,?),lo2=min(lo2,?),'
                    'hi0=max(hi0,?),hi1=max(hi1,?),hi2=max(hi2,?),'
                    'ss=ss+?,smin=min(smin,?),smax=max(smax,?),angles=angles|? WHERE id=?',
                    (len(p), *map(float, sums), *map(float, low), *map(float, high),
                     float(s.sum()), float(s.min()), float(s.max()), bits, cell_id))
            self._cache_put(self.cell_cache, key, cell_id)
            # Input is sliced into BLOCK_POINTS, so each BLOB/tree is bounded.
            records = np.empty(len(use), dtype=RECORD)
            records['index'] = indices[use]
            records['xyz'] = p
            self.db.execute('INSERT INTO chunks(cell,data) VALUES(?,?)', (cell_id, records.tobytes()))

    def _parent(self, node):
        parent = self.parent_cache.get(node)
        if parent is None:
            parent = int(self.db.execute('SELECT parent FROM cells WHERE id=?', (node,)).fetchone()[0])
            self._cache_put(self.parent_cache, node, parent)
        return parent

    def root(self, node):
        """Constant-memory path halving, even for adversarial insertion orders."""
        node = int(node)
        current = node
        hops = 0
        while True:
            parent = self._parent(current)
            if parent == current:
                break
            grandparent = self._parent(parent)
            if grandparent != parent:
                self.db.execute('UPDATE cells SET parent=? WHERE id=?', (grandparent, current))
                self._cache_put(self.parent_cache, current, grandparent)
            current = grandparent
            hops += 1
            if hops % 64 == 0:
                _check(self.cancel)
        # Aggregation uses each queried cell's stored parent as its final root.
        if node != current and self._parent(node) != current:
            self.db.execute('UPDATE cells SET parent=? WHERE id=?', (current, node))
            self._cache_put(self.parent_cache, node, current)
        return current

    def union(self, a, b):
        a, b = self.root(a), self.root(b)
        if a != b:
            small, large = min(a, b), max(a, b)
            self.db.execute('UPDATE cells SET parent=? WHERE id=?', (small, large))
            self._cache_put(self.parent_cache, large, small)
            self.joins += 1

    def records(self, cell):
        for (blob,) in self.db.execute('SELECT data FROM chunks WHERE cell=?', (int(cell),)):
            yield np.frombuffer(blob, dtype=RECORD)

    def _touches(self, a, b):
        """a/b = id, x/y/z, lower[3], upper[3]. Exact edge existence."""
        self.edge_checks += 1
        lo_a, hi_a = np.asarray(a[4:7]), np.asarray(a[7:10])
        lo_b, hi_b = np.asarray(b[4:7]), np.asarray(b[7:10])
        tolerance = max(1e-9, self.radius * 1e-10)
        limit = self.radius + tolerance
        nearest = np.maximum(0.0, np.maximum(lo_a - hi_b, lo_b - hi_a))
        if nearest @ nearest > limit * limit:
            return False
        farthest = np.maximum(abs(lo_a - hi_b), abs(lo_b - hi_a))
        if farthest @ farthest <= limit * limit:
            return True
        for records_b in self.records(b[0]):
            _check(self.cancel)
            # Use a local origin to avoid subtracting large survey coordinates
            # repeatedly in the tree's distance calculations.
            origin = records_b['xyz'][0]
            tree = cKDTree(records_b['xyz'] - origin)
            for records_a in self.records(a[0]):
                _check(self.cancel)
                distances, _ = tree.query(records_a['xyz'] - origin, k=1, distance_upper_bound=limit, workers=1)
                if np.isfinite(distances).any():
                    return True
        return False

    def connect(self, progress):
        """Spatially ordered scan; merge globally, including the angular seam."""
        self.cell_cache.clear()
        columns = 'id,x,y,z,lo0,lo1,lo2,hi0,hi1,hi2'
        # Visit each unordered cell pair once in spatial order. The three
        # disjoint forward halves cover all possible neighbours at offsets
        # -2..2. A BETWEEN on the final indexed coordinate scans its short
        # range instead of probing all five z values independently; this uses
        # thirteen small index ranges rather than 125 exact-key probes per
        # occupied cell. No neighbour or original point is sampled or dropped.
        neighbour_sql = (
            'SELECT ' + columns + ' FROM cells WHERE '
            'x IN (?,?) AND y IN (?,?,?,?,?) AND z BETWEEN ? AND ? '
            'UNION ALL SELECT ' + columns + ' FROM cells WHERE '
            'x=? AND y IN (?,?) AND z BETWEEN ? AND ? '
            'UNION ALL SELECT ' + columns + ' FROM cells WHERE '
            'x=? AND y=? AND z BETWEEN ? AND ?')
        cursor = self.db.execute('SELECT ' + columns + ' FROM cells ORDER BY x,y,z')
        for ordinal, a in enumerate(cursor, 1):
            if ordinal % 128 == 1:
                _emit(progress, self.cancel, f'Connecting lining cells · {ordinal:,}/{self.cells:,}')
            x, y, z = a[1:4]
            # h=R/sqrt(3): no cell farther than two indices can contain an edge.
            neighbours = self.db.execute(
                neighbour_sql,
                (x + 1, x + 2, y - 2, y - 1, y, y + 1, y + 2, z - 2, z + 2,
                 x, y + 1, y + 2, z - 2, z + 2,
                 x, y, z + 1, z + 2))
            a_root = self.root(a[0])
            for b in neighbours:
                b_root = self.root(b[0])
                if a_root == b_root:
                    continue
                if self._touches(a, b):
                    small, large = min(a_root, b_root), max(a_root, b_root)
                    self.db.execute('UPDATE cells SET parent=? WHERE id=?', (small, large))
                    self._cache_put(self.parent_cache, large, small)
                    self.joins += 1
                    a_root = small
            if ordinal % 2048 == 0:
                self.db.commit()
        self.db.commit()

    def aggregate(self, progress, minimum):
        fields = 'id,n,sx,sy,sz,lo0,lo1,lo2,hi0,hi1,hi2,ss,smin,smax,angles'
        for ordinal, row in enumerate(self.db.execute('SELECT ' + fields + ' FROM cells ORDER BY id'), 1):
            if ordinal % 256 == 1:
                _emit(progress, self.cancel, f'Collecting connected lining segments · {ordinal:,}/{self.cells:,}')
            root = self.root(row[0])
            values = (root, *row[1:])
            self.db.execute(
                'INSERT INTO components(root,n,sx,sy,sz,lo0,lo1,lo2,hi0,hi1,hi2,ss,smin,smax,angles) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(root) DO UPDATE SET '
                'n=n+excluded.n,sx=sx+excluded.sx,sy=sy+excluded.sy,sz=sz+excluded.sz,'
                'lo0=min(lo0,excluded.lo0),lo1=min(lo1,excluded.lo1),lo2=min(lo2,excluded.lo2),'
                'hi0=max(hi0,excluded.hi0),hi1=max(hi1,excluded.hi1),hi2=max(hi2,excluded.hi2),'
                'ss=ss+excluded.ss,smin=min(smin,excluded.smin),smax=max(smax,excluded.smax),'
                'angles=angles|excluded.angles', values)
        # Keep the largest supported components when GUI metadata would exceed
        # the explicit budget. Sorting their labels spatially remains stable
        # across input order/IDs. No source points are omitted from processing.
        budget = max(0, int(MAX_NAMED_COMPONENTS))
        self.db.execute(
            'UPDATE components SET label=-1 WHERE root IN ('
            'SELECT root FROM components WHERE n>=? '
            'ORDER BY n DESC,lo0,lo1,lo2,hi0,hi1,hi2 LIMIT ?)', (minimum, budget))
        deferred_count, deferred_points = self.db.execute(
            'SELECT count(*),coalesce(sum(n),0) FROM components WHERE n>=? AND label=0',
            (minimum,)).fetchone()
        rows = []
        unknown_count = 0
        unknown_sum = np.zeros(3, dtype=np.float64)
        all_components = 0
        for data in self.db.execute('SELECT * FROM components ORDER BY lo0,lo1,lo2,hi0,hi1,hi2'):
            _check(self.cancel)
            all_components += 1
            root, n = data[:2]
            sums = np.asarray(data[2:5], dtype=np.float64)
            if data[15] != -1:
                unknown_count += n
                unknown_sum += sums
                continue
            label = len(rows) + 1
            if label >= np.iinfo(np.int32).max - 8:
                raise ValueError('There are too many distinct lining groups for 32-bit labels. Increase the component minimum or review the connection distance.')
            self.db.execute('UPDATE components SET label=? WHERE root=?', (label, root))
            rows.append(dict(id=label, name=f'Lining cluster {label}', points=int(n),
                centre=(sums / n).tolist(), supervoxels=0, linearity=0.0,
                planar_fraction=0.0, overlap=0.0, automatic_keep=True,
                cleaning_class=0, ring_id=0, preview_indices=[],
                station_m=float(data[11] / n), station_min_m=float(data[12]),
                station_max_m=float(data[13]), angular_support_bits=int(data[14])))
        self.db.execute('UPDATE cells SET label=(SELECT label FROM components WHERE root=cells.parent)')
        # root() compressed every cell in the aggregation pass; root cells point
        # to themselves, so this join includes every original lining point.
        self.db.commit()
        return rows, unknown_count, unknown_sum, all_components, int(deferred_count), int(deferred_points)

    def write_labels(self, labels, rows, unknown_preview, progress, rings=None, axis=None):
        by_id = {row['id']: row for row in rows}
        moments = {}
        for ring in rings or []:
            members = [by_id[label] for label in ring['clusters']]
            origin = sum((np.asarray(row['centre']) * row['points'] for row in members), np.zeros(3)) / ring['points']
            moments[ring['id']] = dict(n=0, origin=origin, first=np.zeros(3), second=np.zeros((3, 3)))
        cursor = self.db.execute('SELECT cells.label,chunks.data FROM chunks JOIN cells ON cells.id=chunks.cell')
        for ordinal, (label, blob) in enumerate(cursor, 1):
            if ordinal % 256 == 1:
                _emit(progress, self.cancel, f'Writing full-density cluster labels · {ordinal:,} stored point batches')
            if label is None:
                raise ValueError('An internal classification component has no final label.')
            label = int(label)
            representatives = by_id[label]['preview_indices'] if label else unknown_preview
            preview_limit = PREVIEW_POINTS_PER_CLUSTER if label else 32
            records = np.frombuffer(blob, dtype=RECORD)
            indices = records['index']
            labels[indices] = label
            if len(representatives) < preview_limit:
                representatives.extend(map(int, indices[:preview_limit - len(representatives)]))
            ring_id = by_id[label]['ring_id'] if label else 0
            if ring_id:
                moment = moments[ring_id]
                local = records['xyz'] - moment['origin']
                moment['n'] += len(local)
                moment['first'] += local.sum(axis=0)
                moment['second'] += local.T @ local
        for ring in rings or []:
            moment = moments[ring['id']]
            mean = moment['first'] / moment['n']
            covariance = moment['second'] / moment['n'] - np.outer(mean, mean)
            covariance = (covariance + covariance.T) / 2
            eigenvalues, vectors = np.linalg.eigh(covariance)
            normal = vectors[:, 0]
            reference = np.asarray(axis, dtype=np.float64) if axis is not None else None
            if reference is not None and normal @ reference < 0:
                normal = -normal
            if reference is None and normal[np.argmax(abs(normal))] < 0:
                normal = -normal
            ratio = float(max(0., eigenvalues[0]) / max(eigenvalues[1], 1e-15))
            aligned = reference is None or abs(float(normal @ reference)) >= math.cos(math.radians(35))
            reliable = bool(moment['n'] >= 20 and eigenvalues[1] > 1e-8 and ratio <= .25 and aligned)
            ring.update(plane_centre=(moment['origin'] + mean).tolist(),
                        plane_normal=normal.tolist() if reliable else None,
                        plane_normal_supported=reliable,
                        plane_eigenvalue_ratio=ratio,
                        axial_spread_m=float(math.sqrt(max(0., eigenvalues[0]))) if reliable else None,
                        minimum_variance_spread_m=float(math.sqrt(max(0., eigenvalues[0]))),
                        plane_points=int(moment['n']),
                        plane_status=('Full-density centroid/covariance plane; axial spread describes ring thickness, not instrument accuracy.'
                                      if reliable else 'Plane normal withheld: ring coverage or eigenvalue separation is insufficient.'))


def _weighted_gmm(values, weights, initial_means, initial_variance, variance_floor, cancel=lambda: False):
    """Stable 1-D EM; observations are physical component centres, never IDs."""
    x = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    means = np.asarray(initial_means, dtype=np.float64).copy()
    variance = np.full(len(means), max(initial_variance, variance_floor), dtype=np.float64)
    mixture = np.full(len(means), 1.0 / len(means))
    previous = -np.inf
    # Never materialize an observations-by-rings matrix for the whole tunnel.
    block_size = max(1, min(BLOCK_POINTS, 1000000 // max(1, len(means))))
    for _ in range(100):
        _check(cancel)
        mass = np.zeros(len(means))
        first = np.zeros(len(means))
        second = np.zeros(len(means))
        objective = 0.0
        # Centre sufficient statistics near each component mean to avoid loss
        # of variance precision on long tunnels.
        old_means = means.copy()
        for start in range(0, len(x), block_size):
            _check(cancel)
            bx = x[start:start + block_size]
            bw = weights[start:start + block_size]
            delta = bx[:, None] - old_means[None, :]
            logp = np.log(np.maximum(mixture, 1e-15))[None, :] - .5 * (
                np.log(2 * np.pi * variance)[None, :] + delta ** 2 / variance[None, :])
            norm = logsumexp(logp, axis=1)
            responsibility = np.exp(logp - norm[:, None]) * bw[:, None]
            mass += responsibility.sum(axis=0)
            first += (responsibility * delta).sum(axis=0)
            second += (responsibility * delta ** 2).sum(axis=0)
            objective += float(bw @ norm)
        if np.any(mass < 1e-9):
            raise ValueError('The geometric ring mixture contains an unsupported component.')
        shift = first / mass
        means = old_means + shift
        variance = np.maximum(second / mass - shift ** 2, variance_floor)
        mixture = mass / mass.sum()
        objective /= weights.sum()
        if abs(objective - previous) < 1e-10:
            break
        previous = objective
    # Recompute membership using the final parameter values.
    assigned = np.empty(len(x), dtype=np.int64)
    confidence = np.empty(len(x), dtype=np.float64)
    for start in range(0, len(x), block_size):
        _check(cancel)
        bx = x[start:start + block_size]
        logp = np.log(mixture)[None, :] - .5 * (
            np.log(2 * np.pi * variance)[None, :] + (bx[:, None] - means) ** 2 / variance[None, :])
        responsibility = np.exp(logp - logsumexp(logp, axis=1)[:, None])
        choice = np.argmax(responsibility, axis=1)
        assigned[start:start + len(bx)] = choice
        confidence[start:start + len(bx)] = responsibility[np.arange(len(bx)), choice]
    return means, assigned, confidence


def group_rings(rows, radius, supplied_width=0.0, cancel=lambda: False):
    """Validate observed bands locally; a seamless shell keeps ring_id=0.

    An incomplete ring at either end of a selection, or a locally unsupported
    patch, must not erase the independently supported rings elsewhere. No
    band is manufactured to fill those gaps. Ring IDs follow the observed-band
    order, so a withheld band does not silently renumber its neighbours.
    """
    eligible = [r for r in rows if r.get('cleaning_class') == 0 and r['id'] > 0]
    for row in eligible:
        _check(cancel)
        row['ring_id'] = 0
        row.pop('ring_confidence', None)
        row.pop('ring_withheld_reason', None)
    if len(eligible) < 2:
        return [], 'No inter-ring boundaries supported: fewer than two separate lining components.'
    eligible.sort(key=lambda r: (r['station_m'], r['station_min_m'], r['id']))
    widths = np.array([r['station_max_m'] - r['station_min_m'] for r in eligible])
    usable = widths[widths > 4 * radius]
    if not len(usable):
        return [], 'Ring labels withheld: components are too short relative to the connection distance.'
    typical = float(np.median(usable))
    gap = max(4 * radius, .45 * typical)
    groups = []
    for row in eligible:
        if not groups or row['station_m'] - groups[-1][-1]['station_m'] > gap:
            groups.append([row])
        else:
            groups[-1].append(row)
    if len(groups) < 2:
        return [], 'No separated longitudinal ring bands were observed; a seamless shell is not divided into invented rings.'
    candidates = []
    withheld = {}

    def withhold(index, reason):
        withheld[index] = reason
        for row in groups[index]:
            row['ring_withheld_reason'] = reason

    for index, group in enumerate(groups):
        _check(cancel)
        low = min(r['station_min_m'] for r in group)
        high = max(r['station_max_m'] for r in group)
        bits = 0
        for row in group:
            bits |= row.get('angular_support_bits', 0)
        shared = min(r['station_max_m'] for r in group) - max(r['station_min_m'] for r in group)
        if bits.bit_count() < 18 or shared < max(2 * radius, .35 * (high - low)):
            withhold(index, 'Insufficient shared longitudinal extent or circumferential support.')
            continue
        if supplied_width and abs((high - low) - supplied_width) > max(4 * radius, .25 * supplied_width):
            withhold(index, 'Observed band width does not agree with the supplied ring width.')
            continue
        candidates.append((index, low, high))

    # Test all overlapping intervals, including a broad interval spanning more
    # than its immediate successor. Only the conflicting bands are withheld.
    farthest = None
    for candidate in sorted(candidates, key=lambda c: (c[1], c[2], c[0])):
        _check(cancel)
        if farthest is not None and candidate[1] - farthest[2] < .5 * radius:
            reason = 'Observed bands overlap or lack a resolved longitudinal joint.'
            withhold(candidate[0], reason)
            withhold(farthest[0], reason)
        if farthest is None or candidate[2] > farthest[2]:
            farthest = candidate
    accepted = [candidate for candidate in candidates if candidate[0] not in withheld]
    if len(accepted) < 2:
        return [], 'Ring labels withheld: fewer than two independently supported, separated longitudinal bands remain. Review the unresolved groups.'

    accepted_rows = [row for index, _, _ in accepted for row in groups[index]]
    values = np.asarray([r['station_m'] for r in accepted_rows])
    weights = np.asarray([r['points'] for r in accepted_rows], dtype=np.float64)
    initial = np.asarray([sum(r['station_m'] * r['points'] for r in groups[index]) /
                          sum(r['points'] for r in groups[index]) for index, _, _ in accepted])
    try:
        means, assigned, confidence = _weighted_gmm(values, weights, initial,
                                         max(radius ** 2, (.05 * typical) ** 2), max((radius / 2) ** 2, 1e-10), cancel)
    except ValueError as exc:
        _check(cancel)
        return [], str(exc)
    permutation = np.argsort(means, kind='stable')
    ordered = np.empty(len(permutation), dtype=int)
    ordered[permutation] = np.arange(1, len(permutation) + 1)
    # EM may refine centres but must not override the observed band partition.
    expected = {row['id']: position + 1 for position, (index, _, _) in enumerate(accepted)
                for row in groups[index]}
    for row, component, certainty in zip(accepted_rows, assigned, confidence):
        if certainty < .9 or int(ordered[component]) != expected[row['id']]:
            index = accepted[expected[row['id']] - 1][0]
            withhold(index, 'Geometric GMM membership is ambiguous or disagrees with the observed joint band.')
    if sum(index not in withheld for index, _, _ in accepted) < 2:
        return [], 'Ring labels withheld: fewer than two independently supported bands have unambiguous geometric membership.'
    rings = []
    for position, (index, low, high) in enumerate(accepted):
        if index in withheld:
            continue
        group = groups[index]
        rings.append(dict(id=index + 1, station_m=float(means[permutation[position]]),
                          station_min_m=float(low), station_max_m=float(high),
                          points=sum(r['points'] for r in group), clusters=[r['id'] for r in group]))
    for row, certainty in zip(accepted_rows, confidence):
        index = accepted[expected[row['id']] - 1][0]
        if index in withheld:
            continue
        row['ring_id'] = index + 1
        row['ring_confidence'] = float(certainty)
    note = 'Ring labels use observed separated lining bands and a weighted GMM of physical longitudinal centres; no design sector pattern is assumed.'
    if withheld:
        note += (f' {len(withheld):,} unsupported or ambiguous observed bands remain unresolved; '
                 f'{len(rings):,} independently supported bands retain their ring labels.')
    return rings, note


def _cluster_into_files(batches, work, ids_path, labels_path, settings, progress, cancel,
                        selected_total, model, classifier, coordinates):
    """Compute into owned staging files. Caller publishes only after success."""
    started = time.monotonic()
    radius, minimum, width = _settings(settings)
    if selected_total <= 0:
        raise ValueError('There are no selected points to classify.')
    counts = np.zeros(5, dtype=np.int64)
    sums = np.zeros((5, 3), dtype=np.float64)
    representatives = {k: [] for k in range(5)}
    written = 0
    Path(work).mkdir(parents=True, exist_ok=True)
    _emit(progress, cancel, 'Indexing every original point for tunnel classification')
    try:
        with open(labels_path, 'wb') as output:
            output.truncate(int(selected_total) * 4)
        labels = _LabelStore(labels_path, selected_total, cancel)
        try:
            with tempfile.TemporaryDirectory(prefix='tmatch_', dir=work) as folder:
                grid = _Grid(Path(folder) / 'connectivity.sqlite', radius, cancel)
                try:
                    with open(ids_path, 'wb') as idout:
                        for ids, points, _ in batches():
                            ids = np.asarray(ids)
                            if (ids.ndim != 1 or not np.issubdtype(ids.dtype, np.integer)
                                    or np.any(ids < 0) or np.any(ids > np.iinfo(np.int64).max)):
                                raise ValueError('Tunnel classification received invalid original point identifiers.')
                            ids = np.asarray(ids, dtype='<i8')
                            points = np.asarray(points, dtype=np.float64)
                            if points.shape != (len(ids), 3) or not np.isfinite(points).all():
                                raise ValueError('Tunnel classification requires finite XYZ coordinates.')
                            if written + len(ids) > selected_total:
                                raise ValueError('The source selection changed during classification. Run again.')
                            ids.tofile(idout)
                            for start in range(0, len(ids), BLOCK_POINTS):
                                _emit(progress, cancel, f'Indexing original lining points · {written + start:,}/{selected_total:,}')
                                p = points[start:start + BLOCK_POINTS]
                                decision = np.asarray(classifier(p, model), dtype=np.int32)
                                if decision.shape != (len(p),) or np.any((decision < 0) | (decision > 4)):
                                    raise ValueError('GuSAC returned invalid cleaning states for tunnel classification.')
                                indices = written + start + np.arange(len(p), dtype=np.int64)
                                # Negative values are private temporary cleaning states.
                                labels[indices] = np.where(decision == 0, 0, -decision)
                                for k in range(5):
                                    use = decision == k
                                    counts[k] += int(use.sum())
                                    sums[k] += p[use].sum(axis=0)
                                    if len(representatives[k]) < 32:
                                        representatives[k].extend(map(int, indices[use][:32 - len(representatives[k])]))
                                on = decision == 0
                                if on.any():
                                    local = coordinates(p[on], model)
                                    station = np.asarray(local['s'], dtype=np.float64)
                                    angle = np.asarray(local['angle'], dtype=np.float64)
                                    if not np.isfinite(station).all() or not np.isfinite(angle).all():
                                        raise ValueError('The fitted tunnel frame produced invalid coordinates.')
                                    grid.add(indices[on], p[on], station, angle)
                            written += len(ids)
                            grid.db.commit()
                    if written != selected_total:
                        raise ValueError('The source selection changed during classification. Run again.')
                    grid.connect(progress)
                    rows, unknown_count, unknown_sum, raw_components, deferred_count, deferred_points = grid.aggregate(progress, minimum)
                    unknown_preview = []
                    rings, ring_note = group_rings(rows, radius, width, cancel)
                    grid.write_labels(labels, rows, unknown_preview, progress, rings,
                                      model.get('seed', {}).get('axis'))
                    if unknown_count:
                        rows.insert(0, dict(id=0, name='Unclassified lining points · retained',
                            points=int(unknown_count), centre=(unknown_sum / unknown_count).tolist(),
                            supervoxels=0, linearity=0.0, planar_fraction=0.0, overlap=0.0,
                            automatic_keep=True, cleaning_class=0, ring_id=0,
                            preview_indices=unknown_preview))
                    next_id = max((r['id'] for r in rows), default=0) + 1
                    cleaning_map = {}
                    for k in range(1, 5):
                        if not counts[k]:
                            continue
                        cleaning_map[-k] = next_id
                        rows.append(dict(id=next_id, name=CLASS_NAMES[k], points=int(counts[k]),
                            centre=(sums[k] / counts[k]).tolist(), supervoxels=0,
                            linearity=0.0, planar_fraction=0.0, overlap=0.0,
                            automatic_keep=k != 1, cleaning_class=k, ring_id=0,
                            preview_indices=representatives[k]))
                        next_id += 1
                    for start in range(0, selected_total, BLOCK_POINTS):
                        _check(cancel)
                        block = np.array(labels[start:start + BLOCK_POINTS], copy=True)
                        for old, new in cleaning_map.items():
                            block[block == old] = new
                        if np.any(block < 0):
                            raise ValueError('An original point has no final tunnel classification state.')
                        labels[start:start + len(block)] = block
                    labels.flush()
                    kept = [r['id'] for r in rows if r['automatic_keep']]
                    warnings = [
                        'TMatch is an independent interpretation: connection distance R joins actual lining points; minimum N applies to the final component. The paper supplies no universal R/N values.',
                        f'Joints narrower than the {radius * 1000:g} mm connection distance can join neighbouring blocks. Review the coloured clusters; increase radius only when point spacing requires it.',
                        'Every selected original point is labelled. Small/unresolved lining groups, recesses and uncertain GuSAC decisions remain retained; only interior obstruction candidates start excluded.',
                        ring_note,
                        'Colours identify geometric instances, not semantic cable/pipe types. Segment types and theoretical sector boundaries require the correct project design.',
                    ]
                    underresolved_points = unknown_count - deferred_points
                    if underresolved_points > max(0, counts[0]) * .25:
                        warnings.insert(0, f'{underresolved_points:,} lining points remain unclassified: point spacing, incomplete joints or the component minimum do not support reliable clustering at this connection distance. These points are retained.')
                    if deferred_count:
                        warnings.insert(0, f'The {MAX_NAMED_COMPONENTS:,}-group review metadata budget was reached. '
                            f'{deferred_count:,} additional qualifying components ({deferred_points:,} original points) '
                            'are retained as unclassified label 0. Every original point was processed; '
                            'use a smaller review area or increase the component minimum to name those fragments separately.')
                    if len([r for r in rows if r['id'] and r['cleaning_class'] == 0]) == 1:
                        warnings.insert(0, 'Only one connected lining component was observed. Visible seams may be too narrow or bridged; the software has not invented separate panels.')
                    lining_clusters = sum(r['id'] > 0 and r['cleaning_class'] == 0 for r in rows)
                    if not lining_clusters:
                        status = 'no_supported_components'
                        summary = ('No supported lining components could be separated. '
                                   'The original points remain available in the review groups; '
                                   'this result does not identify tunnel segments.')
                        warnings.insert(0, summary)
                    elif lining_clusters == 1:
                        status = 'single_connected_component'
                        summary = ('One connected lining component was found. '
                                   'Connectivity has not separated the lining into physical segments; '
                                   'use Segments from seams to inspect measured joint boundaries.')
                    else:
                        status = 'multiple_connected_components'
                        summary = (f'{lining_clusters:,} connected lining components were found; '
                                   f'{len(rings):,} observed ring bands have sufficient support. '
                                   'Components require review before treating them as physical segments.')
                    stats = dict(input_points=int(selected_total), unique_points=None,
                        duplicate_locations=None, supervoxels=0, expected_supervoxels=0,
                        merge_passes=1, initial_edges=grid.edge_checks, removed_edges=0,
                        retained_edges=grid.joins, clusters=len(rows),
                        kept_points=int(selected_total - counts[1]), noise_points=0,
                        noise_candidates=int(counts[1]), spacing_m=None,
                        unclassified_points=int(unknown_count), lining_points=int(counts[0]),
                        underresolved_points=int(underresolved_points),
                        raw_components=int(raw_components), lining_clusters=lining_clusters,
                        metadata_group_limit=int(MAX_NAMED_COMPONENTS),
                        metadata_preview_points_per_group=int(PREVIEW_POINTS_PER_CLUSTER),
                        metadata_deferred_components=deferred_count,
                        metadata_deferred_points=deferred_points,
                        label_mapping_budget_bytes=int(LABEL_WINDOW_BYTES * LABEL_WINDOWS),
                        peak_mapped_label_bytes=int(labels.peak_mapping_bytes),
                        peak_open_label_windows=int(labels.peak_open_windows),
                        rings=len(rings), ring_planes_supported=sum(r['plane_normal_supported'] for r in rings), connectivity_cells=grid.cells,
                        support_radius_m=radius, minimum_component_points=minimum,
                        tmatch_radius_mm=radius * 1000, tmatch_ring_width_m=width,
                        uncertain_retained_points=int(counts[2:].sum()),
                        strategy='full-density exact radius components with disk-backed global connectivity; GuSAC cleaning decisions retained separately',
                        seconds=round(time.monotonic() - started, 3))
                    return dict(clusters=rows, kept=kept, statistics=stats,
                        warnings=warnings, rings=rings,
                        algorithm_notes=[
                            'The paper does not fully specify the R/N clustering operation. This implementation uses exact Euclidean radius connectivity and a final component-size minimum.',
                            'Ring GMM input is the physical longitudinal centre of each observed lining component, weighted by its point count. Arbitrary cluster label numbers are never fitted.',
                            'Accepted ring planes use centroid and covariance from every original assigned point, with eigenvalue and orientation checks. Axial spread is ring thickness, not a precision claim.',
                            'The paper describes a further adjacent-group plane reassignment heuristic without complete tolerances. This version keeps unsupported assignments unresolved instead of forcing that heuristic.',
                            'Within-ring block types and angular templates are not inferred from a universal six-block design; the correct project segment layout is required.',
                            'The GuSAC reference is a supported measured single-branch model. It does not reproduce the paper\'s crown-maximum MST and cubic-axis segmentation exactly.',
                            f'Named component metadata is bounded to {MAX_NAMED_COMPONENTS:,} groups. Additional qualifying fragments remain retained under label 0 and are counted explicitly; there is no total input-point cutoff.',
                            'Label output uses at most eight concurrent 8 MiB mappings; evicted windows are flushed and unmapped. This bounds label-file working mappings without loading the complete result.',
                        ],
                        classification=dict(kind='lining_instances', unclassified_label=0,
                            status=status, summary=summary,
                            supported_component_points=int(counts[0] - unknown_count),
                            unresolved_lining_points=int(unknown_count + counts[3]),
                            ring_method='supported physical-station weighted GMM', ring_status=ring_note,
                            coordinate_precision='float64 originals; metadata labels only',
                            published_accuracy_reproduced=False),
                        method='TMatch-style full-density lining components after GuSAC; independent implementation',
                        doi='10.3390/app15062926', method_code='tmatch')
                finally:
                    grid.close()
        finally:
            labels.close()
    except Exception:
        Path(ids_path).unlink(missing_ok=True)
        Path(labels_path).unlink(missing_ok=True)
        raise


def _replace_outputs(pairs):
    """Publish both files, rolling back pre-existing outputs if a rename fails.

    Staging/backup paths share each target's filesystem, so replacements are
    atomic individual renames. Cancellation is checked before this short commit
    boundary, never between its two file replacements.
    """
    backups = []
    installed = []
    try:
        for staging, target in pairs:
            backup = staging.parent / ('previous_' + staging.name)
            if target.exists():
                os.replace(target, backup)
                backups.append((backup, target))
        for staging, target in pairs:
            os.replace(staging, target)
            installed.append(target)
    except Exception:
        for target in installed:
            target.unlink(missing_ok=True)
        for backup, target in reversed(backups):
            os.replace(backup, target)
        raise
    for backup, _ in backups:
        backup.unlink(missing_ok=True)


def _cluster_batches(batches, work, ids_path, labels_path, settings, progress, cancel,
                     selected_total, model, classifier, coordinates):
    """Stage complete outputs; failure/cancellation preserves previous bytes."""
    ids_path, labels_path = Path(ids_path), Path(labels_path)
    if ids_path.resolve() == labels_path.resolve():
        raise ValueError('Point-ID and classification-label output paths must differ.')
    _check(cancel)
    with ExitStack() as stack:
        ids_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='tmatch_ids_', dir=ids_path.parent)))
        if ids_path.parent.resolve() == labels_path.parent.resolve():
            labels_dir = ids_dir
        else:
            labels_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='tmatch_labels_', dir=labels_path.parent)))
        staged_ids, staged_labels = ids_dir / 'result.ids', labels_dir / 'result.labels'
        result = _cluster_into_files(batches, work, staged_ids, staged_labels, settings, progress,
                                     cancel, selected_total, model, classifier, coordinates)
        _emit(progress, cancel, 'Publishing completed tunnel classification')
        _check(cancel)
        _replace_outputs(((staged_ids, ids_path), (staged_labels, labels_path)))
        return result


def run_batches(batches, work, ids_path, labels_path, settings, progress, cancel, selected_total):
    import gusac
    started = time.monotonic()
    _settings(settings)
    model = gusac.fit_batches(batches, settings,
                              lambda message: progress('GuSAC preparation · ' + message), cancel)
    result = _cluster_batches(batches, work, ids_path, labels_path, settings, progress, cancel,
                              selected_total, model, gusac.classify, gusac.coordinates)
    result['statistics']['seconds'] = round(time.monotonic() - started, 3)
    result['statistics']['calibration_points'] = int(model.get('sample_points', 0))
    result['statistics']['reference_radius_m'] = float(model.get('radius', 0.0))
    return result
