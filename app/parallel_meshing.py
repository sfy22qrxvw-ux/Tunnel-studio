# Copyright (c) 2026 Damian Gulczynski.
"""Bounded CPU stages for projection and connected voxel reconstruction.

Independent work uses compute_runtime.ordered_map directly with explicit start
lists. Workers own their input buffers and never publish application state or
share writable file handles. Projection preserves one row per original point.
Voxel reconstruction uses one global scalar field and disjoint cell slabs with
a shared sample plane; shared vertices are welded deterministically afterwards.

These are mathematical reconstruction tools, not survey measurements. Global
Poisson, ball-pivoting and Delaunay topology are not approximated by disconnected
per-file-chunk meshes. Thread scheduling is not a guaranteed speed improvement.
"""
from pathlib import Path
import copy
import math
import os
import uuid

import numpy as np
from scipy.spatial import cKDTree

import compute_runtime

CHUNK = 100_000
SLAB_CELLS = 32
MAX_VOXELS = 7_000_000
MIB = 1024 ** 2


def _check(cancel):
    compute_runtime.check_cancel(cancel)


def _workers(starts, memory=128 * MIB):
    if len(starts) < 4:
        return 1
    return min(len(starts), compute_runtime.workers(memory_per_worker=memory))


def _report(progress, label, completed, total):
    progress(f'{label}: {int(100 * completed / max(1, total))}% · '
             f'{completed:,}/{total:,} chunks done')


def _xyz(points):
    q = np.asarray(points, dtype=np.float64)
    if q.ndim != 2 or q.shape[1] != 3 or not np.isfinite(q).all():
        raise ValueError('Meshing requires a finite N by 3 coordinate array.')
    return q


def _stamp(path):
    info = Path(path).stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def project_cloud_parallel(meta, axis, destination, progress=lambda _: None, cancel=None, *, coverage=None):
    """Project bounded owned reads; atomically install only a complete result."""
    import registration_pose
    from core import extend_chainage_range, retained_chainage_bounds
    original = meta
    meta = copy.deepcopy(meta)
    count = int(meta['count'])
    if count < 0:
        raise ValueError('The source point count must be non-negative.')
    destination = Path(destination)
    for key in ('file', 'mask_file', 'attributes_file'):
        if not meta.get(key):
            continue
        source = Path(meta[key])
        if (source.resolve() == destination.resolve()
                or (destination.exists() and source.exists() and destination.samefile(source))):
            raise ValueError('Projection output must not replace a source cache.')
    inputs = [(meta['file'], count * 24)]
    if meta.get('mask_file'):
        inputs.append((meta['mask_file'], count))
    revisions = [(path, _stamp(path)) for path, _ in inputs]
    for (path, expected), (_, revision) in zip(inputs, revisions):
        if revision[2] != expected:
            raise ValueError('A projection source cache is incomplete.')
    pose_revision = registration_pose.revision(meta)
    starts = list(range(0, count, CHUNK))
    workers = _workers(starts)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')

    def project_chunk(start):
        _check(cancel)
        size = min(CHUNK, count - start)
        with open(meta['file'], 'rb') as stream:
            stream.seek(start * 24)
            q = np.fromfile(stream, '<f8', count=size * 3)
        if q.size != size * 3:
            raise ValueError('The projection coordinate cache is incomplete.')
        flags = None
        if meta.get('mask_file'):
            with open(meta['mask_file'], 'rb') as stream:
                stream.seek(start)
                flags = np.fromfile(stream, 'u1', count=size)
            if len(flags) != size:
                raise ValueError('The projection source deletion mask is incomplete.')
        q = registration_pose.points(meta, q.reshape(size, 3))
        rows = np.asarray(axis.project(q), dtype=np.float64)
        if rows.shape != (size, 4):
            raise ValueError('Alignment projection returned invalid rows.')
        keep = np.isfinite(rows[:, 0])
        if flags is not None:
            keep &= flags == 0
        _check(cancel)
        kept = int(keep.sum())
        bounds = retained_chainage_bounds(rows[:, 0], keep, start) if coverage is not None and kept else None
        return rows, kept, bounds

    results = compute_runtime.ordered_map(project_chunk, starts, workers,
                                          cancel or compute_runtime.cancelled)
    valid = 0
    extent = {}
    try:
        with temporary.open('xb') as output:
            for ordinal, (rows, kept, bounds) in enumerate(results, 1):
                _check(cancel)
                rows.astype('<f8', copy=False).tofile(output)
                valid += kept
                if bounds is not None:
                    extend_chainage_range(extent, bounds)
                _report(progress, 'Calculating chainages', ordinal, len(starts))
        if any(_stamp(path) != before for path, before in revisions):
            raise ValueError('A projection source changed while calculating chainages.')
        if (registration_pose.revision(original) != pose_revision
                or any(original.get(key) != meta.get(key) for key in ('file', 'mask_file', 'count'))):
            raise ValueError('The scan pose or source changed while calculating chainages.')
        _check(cancel)
        os.replace(temporary, destination)
    finally:
        results.close()
        temporary.unlink(missing_ok=True)
    if coverage is not None:
        coverage.clear()
        coverage.update(extent)
    return str(destination), valid


def voxel_grid_parallel(points, origin, voxel, dimensions,
                        progress=lambda _: None, cancel=None):
    """Fill one shared grid on the coordinator; workers return owned indices."""
    points = _xyz(points)
    origin = np.asarray(origin, dtype=np.float64)
    dimensions = np.asarray(dimensions, dtype=np.int64)
    voxel = float(voxel)
    if (origin.shape != (3,) or not np.isfinite(origin).all()
            or dimensions.shape != (3,) or (dimensions < 2).any()
            or math.prod(map(int, dimensions)) > MAX_VOXELS
            or not np.isfinite(voxel) or voxel <= 0):
        raise ValueError('Invalid or oversized voxel grid.')
    grid = np.zeros(tuple(dimensions), np.uint8)
    starts = list(range(0, len(points), CHUNK))

    def indices(start):
        _check(cancel)
        q = np.array(points[start:start + CHUNK], dtype=np.float64, copy=True)
        with np.errstate(over='ignore', invalid='ignore'):
            positions = np.floor((q - origin) / voxel)
        if not np.isfinite(positions).all():
            raise ValueError('Point extent cannot be represented in the voxel grid.')
        return np.clip(positions, 0, dimensions - 1).astype(np.int64)

    results = compute_runtime.ordered_map(indices, starts, _workers(starts),
                                          cancel or compute_runtime.cancelled)
    try:
        for done, ix in enumerate(results, 1):
            _check(cancel)
            grid[ix[:, 0], ix[:, 1], ix[:, 2]] = 1
            _report(progress, 'Meshing · voxelising points', done, len(starts))
    finally:
        results.close()
    return grid


def smoothed_voxel_field_parallel(grid, sigma, progress=lambda _: None, cancel=None):
    """Morphology/Gaussian slabs with complete support, including global borders."""
    from scipy.ndimage import gaussian_filter, binary_closing, binary_dilation
    grid = np.asarray(grid)
    sigma = float(sigma)
    if (grid.ndim != 3 or min(grid.shape) < 2 or grid.size > MAX_VOXELS
            or not np.isfinite(sigma) or not .01 <= sigma <= 64):
        raise ValueError('Invalid voxel field or smoothing strength.')
    axis = int(np.argmax(grid.shape))
    extent = grid.shape[axis]
    starts = list(range(0, extent, SLAB_CELLS))
    # Dilation 1 + closing dilation 2 + closing erosion 2 + Gaussian support.
    halo = 5 + int(4 * sigma + .5)
    memory = max(128 * MIB, int(grid.size // extent * min(extent, SLAB_CELLS + 2 * halo) * 24))
    workers = _workers(starts, memory)

    def smooth(values):
        _check(cancel)
        values = binary_dilation(values, iterations=1)
        _check(cancel)
        values = binary_closing(values, iterations=2)
        _check(cancel)
        return gaussian_filter(values.astype(np.float32), sigma=sigma)

    if workers == 1:
        result = smooth(grid)
        _check(cancel)
        return result
    field = np.empty(grid.shape, np.float32)

    def slab(start):
        stop = min(extent, start + SLAB_CELLS)
        lo, hi = max(0, start - halo), min(extent, stop + halo)
        source = [slice(None)] * 3
        source[axis] = slice(lo, hi)
        inner = [slice(None)] * 3
        inner[axis] = slice(start - lo, stop - lo)
        return smooth(np.array(grid[tuple(source)], copy=True))[tuple(inner)].copy()

    results = compute_runtime.ordered_map(slab, starts, workers,
                                          cancel or compute_runtime.cancelled)
    try:
        for done, (start, values) in enumerate(zip(starts, results), 1):
            _check(cancel)
            target = [slice(None)] * 3
            target[axis] = slice(start, min(extent, start + SLAB_CELLS))
            field[tuple(target)] = values
            _report(progress, 'Meshing · voxel field', done, len(starts))
    finally:
        results.close()
    return field


def _grid_spec(points, voxel):
    voxel = float(voxel)
    if not np.isfinite(voxel):
        raise ValueError('Voxel spacing must be finite.')
    voxel = max(.005, voxel)
    low, high = points.min(axis=0), points.max(axis=0)
    for _ in range(96):
        with np.errstate(over='ignore', invalid='ignore'):
            span = (high - low) / voxel
        if not np.isfinite(span).all() or (span > 2 ** 62).any():
            raise ValueError('The selected extent is too large for voxel reconstruction.')
        dims = np.ceil(span).astype(np.int64) + 7
        cells = math.prod(map(int, dims))
        if cells <= MAX_VOXELS:
            return low - 3 * voxel, voxel, dims
        voxel *= max(1.01, (cells / MAX_VOXELS) ** (1 / 3))
    raise ValueError('The voxel grid could not be bounded. Increase the voxel spacing.')


def marching_cubes_field_parallel(field, level, progress=lambda _: None, cancel=None):
    """Extract disjoint voxel-cell slabs and weld exact shared-plane vertices.

    Adjacent slabs share one scalar sample plane, not any voxel cells. No face
    centroid clipping, arbitrary overlap blending or seam quantisation is used.
    Native-library GIL behaviour can limit concurrent triangle extraction.
    """
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:
        raise ValueError('Marching Cubes requires the bundled scikit-image engine.') from exc
    field = np.asarray(field, dtype=np.float32)
    level = float(level)
    if (field.ndim != 3 or min(field.shape) < 2 or field.size > MAX_VOXELS
            or not np.isfinite(field).all() or not np.isfinite(level)):
        raise ValueError('Invalid marching-cubes scalar field.')
    if not float(field.min()) < level < float(field.max()):
        raise ValueError('The voxel field contains no supported isosurface at this level.')
    axis = int(np.argmax(field.shape))
    extent = field.shape[axis] - 1
    starts = list(range(0, extent, SLAB_CELLS))
    slab_cells = int(field.size // field.shape[axis] * min(extent, SLAB_CELLS))
    memory = max(128 * MIB, slab_cells * 512)
    budget = int(compute_runtime.memory_budget())
    if memory + field.nbytes > budget:
        raise ValueError('Voxel triangle extraction exceeds the working-memory estimate. Increase voxel spacing.')
    # Retain at least half of the available budget for accumulated geometry and
    # the deterministic weld. A large single field must not occupy every byte
    # with simultaneous worker scratch before the first mesh can be collected.
    workers = min(_workers(starts, memory), max(1, (budget - field.nbytes) // (2 * memory)))
    _check(cancel)
    if len(starts) < 4:
        if field.size * 512 + field.nbytes > budget:
            raise ValueError('Voxel triangle extraction exceeds the working-memory estimate. Increase voxel spacing.')
        v, f, _, _ = marching_cubes(field, level=level, allow_degenerate=False)
        _check(cancel)
        return v.astype(np.float64), f.astype(np.uint32), 1

    def extract(start):
        _check(cancel)
        stop = min(extent, start + SLAB_CELLS)
        section = [slice(None)] * 3
        section[axis] = slice(start, stop + 1)
        values = np.array(field[tuple(section)], dtype=np.float32, order='C', copy=True)
        lo, hi = float(values.min()), float(values.max())
        if not lo <= level <= hi or lo == hi:
            return np.empty((0, 3), np.float64), np.empty((0, 3), np.uint32)
        try:
            v, f, _, _ = marching_cubes(values, level=level, allow_degenerate=False)
        except RuntimeError as exc:
            # skimage reports an empty contour when a cropped boundary only
            # touches the level. Other native failures must not be hidden.
            if 'No surface found' not in str(exc):
                raise
            return np.empty((0, 3), np.float64), np.empty((0, 3), np.uint32)
        v = v.astype(np.float64)
        v[:, axis] += start
        _check(cancel)
        return v, f.astype(np.uint32)

    results = compute_runtime.ordered_map(extract, starts, workers,
                                          cancel or compute_runtime.cancelled)
    vertex_parts, face_parts = [], []
    vertices = accumulated = 0
    try:
        for done, (v, f) in enumerate(results, 1):
            _check(cancel)
            accumulated += v.nbytes + f.nbytes
            # Keep room for queued slab results and deterministic merge arrays.
            if 8 * accumulated + workers * memory + field.nbytes > budget:
                raise ValueError('The reconstructed mesh exceeds the merge-memory estimate. Increase voxel spacing.')
            if len(f):
                if vertices + len(v) > np.iinfo(np.uint32).max:
                    raise ValueError('The reconstructed mesh exceeds the supported vertex index range.')
                vertex_parts.append(v)
                face_parts.append(f.astype(np.uint64) + vertices)
                vertices += len(v)
            _report(progress, 'Meshing · triangle tiles', done, len(starts))
    finally:
        results.close()
    if not face_parts:
        raise ValueError('Voxel reconstruction produced no triangle faces.')
    _check(cancel)
    joined = np.concatenate(vertex_parts)
    faces = np.concatenate(face_parts)
    del vertex_parts, face_parts
    unique, inverse = np.unique(joined, axis=0, return_inverse=True)
    del joined
    faces = inverse[faces].astype(np.uint32)
    del inverse
    keep = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
            & (faces[:, 0] != faces[:, 2]))
    faces = faces[keep]
    # Exact level crossings can place coincident faces on a shared plane.
    if len(faces):
        _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
        faces = faces[np.sort(first)]
    _check(cancel)
    return unique, faces, workers


def marching_cubes_parallel(points, voxel=.08, smoothing=1,
                            progress=lambda _: None, cancel=None):
    """Reconstruct one connected voxel field using bounded independent slabs."""
    points = _xyz(points)
    if len(points) < 3:
        raise ValueError('At least three points are required for voxel reconstruction.')
    sigma = float(smoothing or 1)
    if not np.isfinite(sigma):
        raise ValueError('Voxel smoothing must be finite.')
    sigma = max(.6, sigma)
    origin, voxel, dimensions = _grid_spec(points, voxel)
    progress(f'Voxelising cloud · {tuple(map(int, dimensions))} cells · {voxel:.4f} m')
    grid = voxel_grid_parallel(points, origin, voxel, dimensions, progress, cancel)
    field = smoothed_voxel_field_parallel(grid, sigma, progress, cancel)
    del grid
    level = max(.15, float(field.max()) * .35)
    progress('Extracting connected voxel triangle faces…')
    vertices, faces, workers = marching_cubes_field_parallel(field, level, progress, cancel)
    vertices *= voxel
    vertices += origin
    return vertices, faces, {
        'voxel_m': voxel,
        'cpu_triangle_workers': workers,
        'parallel_method': 'Shared scalar field; disjoint voxel-cell slabs and exact seam welding',
        'warning': 'Marching Cubes extracts the boundary of a smoothed occupied-voxel field. '
                   'It can thicken a scanned surface and produce inner and outer sheets; it does not '
                   'guarantee a watertight lining or survey accuracy. Inspect connected surfaces '
                   'and source deviation. Threaded speed depends on the bundled native library.',
    }


def noise_filter_parallel(points, max_gap, progress=lambda _: None, cancel=None):
    """Nearest-neighbour filtering over a bounded working sample, in owned queries."""
    points = _xyz(points)
    max_gap = float(max_gap or 0)
    if not np.isfinite(max_gap):
        raise ValueError('Noise gap must be finite.')
    if max_gap <= 0 or len(points) < 8:
        return points
    _check(cancel)
    tree = cKDTree(points)
    starts = list(range(0, len(points), CHUNK))
    keep = np.empty(len(points), bool)

    def query(start):
        _check(cancel)
        q = np.array(points[start:start + CHUNK], copy=True)
        distances, _ = tree.query(q, k=2, workers=1)
        _check(cancel)
        return np.isfinite(distances[:, 1]) & (distances[:, 1] <= max_gap)

    results = compute_runtime.ordered_map(query, starts, _workers(starts),
                                          cancel or compute_runtime.cancelled)
    try:
        for done, (start, chosen) in enumerate(zip(starts, results), 1):
            _check(cancel)
            keep[start:start + len(chosen)] = chosen
            _report(progress, 'Meshing · neighbour filter', done, len(starts))
    finally:
        results.close()
    out = points[keep]
    if len(out) < 3:
        raise ValueError('Noise filtering removed too many points. Increase the noise gap or turn the filter off.')
    _check(cancel)
    progress(f'Noise filter ≤ {max_gap:.4f} m neighbour gap · kept {len(out):,} / {len(points):,} points')
    return out


def triangle_filter_parallel(points, faces, max_edge, progress=lambda _: None, cancel=None):
    """Parallel geometric checks after a single global triangulation."""
    points = _xyz(points)
    faces = np.asarray(faces)
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in 'iu':
        raise ValueError('Triangle indices must be an integer N by 3 array.')
    if faces.size and ((faces < 0).any() or (faces >= len(points)).any()):
        raise ValueError('Triangle indices are outside the vertex array.')
    max_edge = float(max_edge)
    if not np.isfinite(max_edge):
        raise ValueError('Maximum edge must be finite.')
    starts = list(range(0, len(faces), CHUNK))

    def check(start):
        _check(cancel)
        f = np.array(faces[start:start + CHUNK], dtype=np.uint32, copy=True)
        q = points[f]
        edges = np.maximum.reduce((np.linalg.norm(q[:, 1] - q[:, 0], axis=1),
                                   np.linalg.norm(q[:, 2] - q[:, 1], axis=1),
                                   np.linalg.norm(q[:, 0] - q[:, 2], axis=1)))
        area = np.linalg.norm(np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0]), axis=1) / 2
        within = edges <= max_edge if max_edge > 0 else np.ones(len(f), bool)
        _check(cancel)
        return f[within & (area > 1e-14)], int((~within).sum())

    results = compute_runtime.ordered_map(check, starts, _workers(starts),
                                          cancel or compute_runtime.cancelled)
    parts, removed = [], 0
    try:
        for done, (f, count) in enumerate(results, 1):
            parts.append(f)
            removed += count
            _report(progress, 'Meshing · triangle checks', done, len(starts))
    finally:
        results.close()
    return (np.concatenate(parts) if parts else np.empty((0, 3), np.uint32)), removed
