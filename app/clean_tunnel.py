# Copyright (c) 2026 Damian Gulczynski.
"""Full-density tunnel noise screening with bounded spatial working sets.

Original coordinates are spooled in 100,000-record blocks. Each processing
window obtains its complete 2R support from neighbouring tile files. Two
bounded KD-tree query passes preserve the local neighbour-distance/MAD rule.
No sampling or coordinate changes are used to reduce the input point count.

The admission ceiling is two billion selected points, not two billion points
in RAM. An unusually dense support region or excessive tile index is refused
before its estimated working set exceeds the cleaning budget. This is noise
candidate classification, not a survey measurement or a guarantee of accuracy.
"""
from itertools import product
from pathlib import Path
import operator
import shutil
import tempfile
import time

import compute_runtime
import numpy as np
from scipy.spatial import cKDTree

MAX_POINTS = 2_000_000_000
CHUNK = 100_000
QUERY_CHUNK = 4096
MIB = 1024 ** 2
RECORD = np.dtype([('id', '<i8'), ('xyz', '<f8', (3,))])
NAMES = {0: 'Retained points', 1: 'Sparse noise candidates',
         2: 'Distance outlier candidates'}
# Conservative allocation estimates, not an operating-system RAM guarantee.
BYTES_PER_SUPPORT_RECORD = 320
QUERY_RESERVE = 32 * MIB


def _cancel(cancel):
    if cancel is not None and cancel():
        raise ValueError('Clean Tunnel cancelled. Previous results and scan points are unchanged.')


def _integer(value, name):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f'{name} must be an integer.')
    try:
        return operator.index(value)
    except TypeError as exc:
        raise ValueError(f'{name} must be an integer.') from exc


def _check_total(total):
    if not 0 <= total <= MAX_POINTS:
        raise ValueError(f'Clean Tunnel supports at most {MAX_POINTS:,} selected points per operation.')


def _space(requirements):
    """Aggregate additional disk requirements by filesystem, without allocation."""
    volumes = {}
    for folder, required in requirements:
        folder = Path(folder)
        device = folder.stat().st_dev
        representative, size = volumes.get(device, (folder, 0))
        volumes[device] = (representative, size + int(required))
    for folder, size in volumes.values():
        required = size + max(64 * MIB, size // 20)
        free = shutil.disk_usage(folder).free
        if required > free:
            raise ValueError(
                f'Clean Tunnel needs approximately {required / 1e9:.2f} GB '
                f'additional free space on the volume containing {folder}; '
                f'{free / 1e9:.2f} GB is available. Source points are unchanged.')


def classify(points, radius=.10, neighbours=12, minimum=3, multiplier=3.0,
             worker_count=None, cancel=None):
    """Classify unique points from one bounded support region.

    The caller supplies the complete 2R halo. Neighbour arrays are limited to
    QUERY_CHUNK rows; the tree and per-point statistics belong to this region.
    """
    q = np.asarray(points, dtype=np.float64)
    if q.ndim != 2 or q.shape[1] != 3 or not np.isfinite(q).all():
        raise ValueError('Noise classification requires finite XYZ coordinates.')
    neighbours = _integer(neighbours, 'Neighbour count')
    minimum = _integer(minimum, 'Minimum support')
    radius, multiplier = float(radius), float(multiplier)
    if not (np.isfinite(radius) and radius > 0 and 1 <= minimum <= neighbours <= 64
            and np.isfinite(multiplier) and multiplier > 0):
        raise ValueError('Invalid noise radius, neighbour count, support or strength.')
    n = len(q)
    if not n:
        return np.empty(0, np.int32)
    workers = compute_runtime.workers() if worker_count is None else _integer(worker_count, 'Worker count')
    if workers == 0 or workers < -1:
        raise ValueError('Worker count must be positive or -1.')
    _cancel(cancel)
    tree = cKDTree(q)
    k = min(neighbours, max(1, n - 1))
    support = np.zeros(n, np.int32)
    means = np.full(n, np.nan, np.float64)
    labels = np.zeros(n, np.int32)
    bound = radius * (1 + 1e-12)

    # Pass one: every unique location gets its own supported mean distance.
    for start in range(0, n, QUERY_CHUNK):
        _cancel(cancel)
        end = min(n, start + QUERY_CHUNK)
        distance, indices = tree.query(q[start:end], k=k + 1,
                                      distance_upper_bound=bound, workers=workers)
        distance = distance[:, 1:]
        valid = np.isfinite(distance)
        number = valid.sum(axis=1)
        support[start:end] = number
        means[start:end] = np.divide(
            np.where(valid, distance, 0).sum(axis=1), number,
            out=np.full(end - start, np.nan), where=number > 0)
        del distance, indices, valid, number
    labels[support < minimum] = 1

    # Pass two: the same neighbour rule, without retaining an N-by-k matrix.
    for start in range(0, n, QUERY_CHUNK):
        _cancel(cancel)
        end = min(n, start + QUERY_CHUNK)
        distance, indices = tree.query(q[start:end], k=k + 1,
                                      distance_upper_bound=bound, workers=workers)
        distance, indices = distance[:, 1:], indices[:, 1:]
        valid = np.isfinite(distance)
        safe = np.minimum(indices, n - 1)
        reference = means[safe]
        usable = valid & (support[safe] >= minimum) & np.isfinite(reference)
        reference[~usable] = np.nan
        eligible = (support[start:end] >= minimum) & (usable.sum(axis=1) >= minimum)
        if eligible.any():
            local = reference[eligible]
            median = np.nanmedian(local, axis=1)
            mad = np.nanmedian(np.abs(local - median[:, None]), axis=1)
            threshold = np.maximum(median + multiplier * 1.4826 * mad, 1.8 * median)
            rejected = means[start:end][eligible] > threshold + 1e-12
            labels[start + np.flatnonzero(eligible)[rejected]] = 2
            del local, median, mad, threshold, rejected
        del distance, indices, valid, safe, reference, usable, eligible
    _cancel(cancel)
    return labels


def run_batches(batches, work, ids_path, labels_path, settings, progress, cancel,
                selected_total=None, small_area_limit=150000):
    """Write every supplied original ID and label once, in deterministic order.

    Input is an iterator of (IDs, world XYZ, selection specification). Outputs
    must be new files; the existing surface workflow publishes them after save.
    small_area_limit is retained for API compatibility. All inputs now use the
    bounded disk path, so a stale small-selection estimate cannot fill RAM.
    """
    started = time.monotonic()
    radius = float(settings['noise_radius'])
    k = _integer(settings['noise_neighbours'], 'Neighbour count')
    minimum = _integer(settings['noise_minimum'], 'Minimum support')
    multiplier = float(settings['noise_sigma'])
    if not (.005 <= radius <= 2 and 3 <= k <= 64 and 1 <= minimum <= k
            and .5 <= multiplier <= 10):
        raise ValueError('Check the noise radius, support count, neighbours and strength.')
    if selected_total is not None:
        selected_total = _integer(selected_total, 'Selected point count')
        _check_total(selected_total)
    work, ids_path, labels_path = map(Path, (work, ids_path, labels_path))
    if not work.is_dir():
        raise ValueError('The cleaning work folder does not exist.')
    if ids_path.resolve() == labels_path.resolve():
        raise ValueError('Point IDs and labels require different output files.')
    if ids_path.exists() or labels_path.exists():
        raise ValueError('Cleaning output files must be new; existing results were not overwritten.')
    if selected_total is not None:
        _space([(work, 32 * selected_total), (ids_path.parent, 8 * selected_total),
                (labels_path.parent, 4 * selected_total)])

    def emit(message):
        _cancel(cancel)
        progress(message)

    budget = int(compute_runtime.memory_budget())
    index_budget = max(8 * MIB, budget // 8)
    working_budget = budget - index_budget - 32 * MIB
    if working_budget <= QUERY_RESERVE:
        raise ValueError('Not enough working memory for bounded tunnel noise screening.')
    max_index_entries = max(1, index_budget // 2048)
    cell = max(.5, 4 * radius)
    origin = None
    tile_counts = {}
    total = 0
    count = np.zeros(3, np.int64)
    centre_sum = np.zeros((3, 3), np.float64)
    created = []
    completed = False
    iterator = iter(batches)
    try:
        with tempfile.TemporaryDirectory(prefix='tunnel_noise_', dir=work) as tmp:
            folder = Path(tmp)

            def path(key):
                return folder / ('_'.join(map(str, key)) + '.bin')

            for raw_ids, raw_points, _ in iterator:
                _cancel(cancel)
                raw_ids, raw_points = np.asarray(raw_ids), np.asarray(raw_points)
                if (raw_ids.ndim != 1 or raw_points.ndim != 2
                        or raw_points.shape != (len(raw_ids), 3)):
                    raise ValueError('Cleaning batches require integer IDs and matching XYZ rows.')
                if not len(raw_ids):
                    continue
                if raw_ids.dtype.kind not in 'iu':
                    raise ValueError('Cleaning point IDs must be integers.')
                for start in range(0, len(raw_ids), CHUNK):
                    _cancel(cancel)
                    end = min(len(raw_ids), start + CHUNK)
                    source_ids = raw_ids[start:end]
                    if (source_ids < 0).any() or (source_ids > np.iinfo(np.int64).max).any():
                        raise ValueError('Source point IDs must fit non-negative signed 64-bit integers.')
                    ids = np.asarray(source_ids, dtype='<i8')
                    points = np.asarray(raw_points[start:end], dtype='<f8')
                    if not np.isfinite(points).all():
                        raise ValueError('The scan cache contains invalid coordinates.')
                    next_total = total + len(ids)
                    _check_total(next_total)
                    if selected_total is not None and next_total > selected_total:
                        raise ValueError('The selected point count changed. Run cleaning again.')
                    # Existing tile bytes are already deducted from free space.
                    _space([(work, 32 * len(ids)), (ids_path.parent, 8 * next_total),
                            (labels_path.parent, 4 * next_total)])
                    emit(f'Clean Tunnel · indexing {next_total:,} selected points')
                    if origin is None:
                        origin = points.min(axis=0).copy()
                    with np.errstate(over='ignore', invalid='ignore'):
                        scaled = np.floor((points - origin) / cell)
                    if not np.isfinite(scaled).all() or (np.abs(scaled) >= 2 ** 62).any():
                        raise ValueError('The coordinate extent is too large for spatial indexing.')
                    keys = scaled.astype(np.int64)
                    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
                    order = np.argsort(inverse, kind='stable')
                    offsets = np.r_[0, np.cumsum(np.bincount(inverse), dtype=np.int64)]
                    for index, key0 in enumerate(unique):
                        _cancel(cancel)
                        key = tuple(map(int, key0))
                        if key not in tile_counts and len(tile_counts) >= max_index_entries // 2:
                            raise ValueError('The spatial tile index exceeds the cleaning memory budget. Select a smaller area.')
                        use = order[offsets[index]:offsets[index + 1]]
                        records = np.empty(len(use), RECORD)
                        records['id'], records['xyz'] = ids[use], points[use]
                        with path(key).open('ab') as output:
                            records.tofile(output)
                        tile_counts[key] = tile_counts.get(key, 0) + len(use)
                    total = next_total
            if not total:
                raise ValueError('The selected area has no retained points.')
            if selected_total is not None and total != selected_total:
                raise ValueError('The selected point count changed. Run cleaning again.')
            # Release the final input arrays before scheduling spatial work.
            del raw_ids, raw_points, source_ids, ids, points, scaled
            del keys, unique, inverse, order, offsets, use, records, key0
            _space([(ids_path.parent, 8 * total), (labels_path.parent, 4 * total)])

            deltas = tuple(product((-1, 0, 1), repeat=3))
            keys = sorted(tile_counts)
            largest_halo = 0
            for key in keys:
                _cancel(cancel)
                neighbours = sum(tile_counts.get(tuple(key[i] + d[i] for i in range(3)), 0)
                                 for d in deltas)
                largest_halo = max(largest_halo, neighbours)
            estimate = largest_halo * BYTES_PER_SUPPORT_RECORD + QUERY_RESERVE
            per_worker = min(working_budget, max(128 * MIB, estimate))
            workers = min(len(keys), compute_runtime.workers(memory_per_worker=per_worker),
                          max(1, working_budget // per_worker))
            allowance = working_budget // workers
            halo_limit = (allowance - QUERY_RESERVE) // BYTES_PER_SUPPORT_RECORD
            if halo_limit < 4:
                raise ValueError('Not enough working memory for a noise support region.')
            window = min(CHUNK, max(1, halo_limit // 4))
            task_count = sum((n + window - 1) // window for n in tile_counts.values())
            if len(keys) + task_count > max_index_entries:
                raise ValueError('The cleaning task index exceeds the memory budget. Select a smaller area.')
            tasks = [(key, start, min(window, tile_counts[key] - start))
                     for key in keys for start in range(0, tile_counts[key], window)]
            emit(f'Clean Tunnel · {total:,} points · {len(keys):,} tiles · {workers} CPU workers')

            def read_records(stream, number):
                block = np.fromfile(stream, RECORD, count=number)
                if len(block) != number:
                    raise ValueError('A temporary noise tile is incomplete. Run cleaning again.')
                return block

            def process_window(task):
                key, first, size = task
                _cancel(cancel)
                with path(key).open('rb') as stream:
                    stream.seek(first * RECORD.itemsize)
                    core = read_records(stream, size)
                lower = core['xyz'].min(axis=0) - 2 * radius - 1e-10
                upper = core['xyz'].max(axis=0) + 2 * radius + 1e-10
                clouds = [core['xyz']]
                support_count = len(core)
                for delta in deltas:
                    adjacent = tuple(key[i] + delta[i] for i in range(3))
                    available = tile_counts.get(adjacent, 0)
                    if not available:
                        continue
                    if adjacent == key and first == 0 and size == available:
                        continue  # The complete owning tile is already in core.
                    with path(adjacent).open('rb') as stream:
                        for offset in range(0, available, CHUNK):
                            _cancel(cancel)
                            block = read_records(stream, min(CHUNK, available - offset))
                            pts = block['xyz']
                            inside = ((pts >= lower) & (pts <= upper)).all(axis=1)
                            if adjacent == key:
                                a, b = max(first, offset), min(first + size, offset + len(block))
                                if a < b:
                                    inside[a - offset:b - offset] = False
                            added = int(inside.sum())
                            if support_count + added > halo_limit:
                                raise ValueError(
                                    'A local 2R noise-support region exceeds the cleaning memory budget '
                                    f'({halo_limit:,} records per worker). No neighbours were discarded. '
                                    'Reduce the noise radius or process a smaller selected area; review its boundaries.')
                            if added:
                                clouds.append(pts[inside])
                                support_count += added
                _cancel(cancel)
                joined = np.concatenate(clouds)
                joined -= core['xyz'][0]
                del clouds
                unique, inverse = np.unique(joined, axis=0, return_inverse=True)
                core_inverse = inverse[:len(core)].copy()
                del joined, inverse
                labels = classify(unique, radius, k, minimum, multiplier,
                                  worker_count=1, cancel=cancel)[core_inverse]
                counts = np.bincount(labels, minlength=3).astype(np.int64, copy=False)
                sums = np.array([core['xyz'][labels == label].sum(axis=0) for label in range(3)])
                return core['id'].copy(), labels, counts, sums

            results = compute_runtime.ordered_map(process_window, tasks, workers, cancel or (lambda: False))
            written = 0
            try:
                with ids_path.open('xb') as idout:
                    created.append(ids_path)
                    with labels_path.open('xb') as labelout:
                        created.append(labels_path)
                        for ordinal, (ids, labels, counts, sums) in enumerate(results, 1):
                            _cancel(cancel)
                            ids.astype('<i8', copy=False).tofile(idout)
                            labels.astype('<i4', copy=False).tofile(labelout)
                            written += len(ids)
                            count += counts
                            centre_sum += sums
                            emit(f'Clean Tunnel: {100 * written / total:.1f}% · '
                                 f'{ordinal:,}/{task_count:,} windows done · {written:,}/{total:,} points')
            finally:
                results.close()
            if written != total or int(count.sum()) != total:
                raise ValueError('Cleaning output counts do not match the selected input.')
            _cancel(cancel)
        result = _result(count, centre_sum, total, len(tile_counts), radius, started,
                         'parallel bounded spatial windows' if workers > 1 else 'bounded spatial windows')
        result['statistics'].update(cpu_workers=workers, max_selected_points=MAX_POINTS,
                                    core_window_points=window, support_record_limit=halo_limit,
                                    estimated_working_budget_bytes=budget)
        completed = True
        return result
    finally:
        close = getattr(iterator, 'close', None)
        try:
            if close is not None:
                close()
        finally:
            if not completed:
                for filename in created:
                    filename.unlink(missing_ok=True)


def _result(count, centre_sum, total, tiles, radius, started, strategy):
    clusters = [dict(id=i, name=NAMES[i], points=int(count[i]), supervoxels=0,
                     centre=(centre_sum[i] / count[i]).tolist(), linearity=0.,
                     planar_fraction=0., overlap=0., automatic_keep=i == 0)
                for i in range(3) if count[i]]
    return dict(clusters=clusters, kept=[0] if count[0] else [],
                statistics=dict(input_points=total, unique_points=None,
                    duplicate_locations=None, supervoxels=0, expected_supervoxels=0,
                    merge_passes=0, initial_edges=0, removed_edges=0, retained_edges=0,
                    clusters=len(clusters), kept_points=int(count[0]), noise_points=0,
                    noise_candidates=int(count[1] + count[2]), spacing_m=None,
                    spatial_tiles=tiles, strategy=strategy, support_radius_m=radius,
                    seconds=round(time.monotonic() - started, 3)),
                warnings=['Preview the sparse and distance-outlier candidates before applying. '
                          'Small isolated features and the boundary of a selected area may need restoring.',
                          'Noise filtering preserves supported objects. Use the SMRG or lining mode '
                          'to separate pipes, equipment and other non-lining surfaces.'])
