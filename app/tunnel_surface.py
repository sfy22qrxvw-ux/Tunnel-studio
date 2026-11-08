# Copyright (c) 2026 Damian Gulczynski.
"""Shared-vertex lining grid for one approximately straight tunnel branch.

The desktop path streams every retained measurement into exact support counts
and a bounded median reservoir. This is a radial-grid fit, not IPCMS or a
guarantee that scanner services have been separated from the lining.
"""
import math
import numpy as np
import mesh_geometry as geom
from mesh_sampling import coordinate_roundoff, stable_floor


def _fill_line(values, step, gap, periodic=False, angular=False):
    out = values.copy()
    known = np.flatnonzero(np.isfinite(values))
    if len(known) < 2 or gap <= 0:
        return out
    pairs = list(zip(known[:-1], known[1:]))
    if periodic:
        pairs.append((known[-1], known[0]+len(values)))
    for a, b in pairs:
        if b-a <= 1:
            continue
        ra, rb = values[a % len(values)], values[b % len(values)]
        span = (b-a)*step
        # Use local radius and combined radial/tangential distance; a diagonal
        # discontinuity is not admitted by testing each coordinate separately.
        if angular:
            if span >= math.pi:
                continue
            span *= max(ra, rb)
        if math.hypot(span, ra-rb) > gap+1e-12:
            continue
        ids = np.arange(a+1, b)
        out[ids % len(values)] = ra+(rb-ra)*(ids-a)/(b-a)
    return out


def _parameters(spacing, degrees, max_edge, gap):
    spacing, degrees, max_edge, gap = map(float, (spacing, degrees, max_edge, gap))
    if not np.isfinite([spacing, degrees, gap, max_edge]).all() or not .005 <= spacing <= 10 or not .25 <= degrees <= 20 or gap < 0 or max_edge < 0:
        raise ValueError('Check tunnel grid spacing, angle (0.25–20°), gap and edge limits.')
    return spacing, degrees, max_edge, gap


def _grid(length, spacing, degrees, progress, roundoff=0):
    if length < 2*spacing:
        raise ValueError('Select a longer single tunnel branch with lining around its axis.')
    ratio = length/spacing
    near = round(ratio)
    if abs(ratio-near) <= max(roundoff/spacing, 16*np.finfo(float).eps*abs(ratio)):
        ratio = near
    ns = max(2, int(math.ceil(ratio)))
    nt = max(18, int(math.ceil(360/degrees)))
    if ns*nt > 750000:
        ns = max(2, 750000//nt)
        progress(f'Automatic grid spacing {length/ns:.4f} m for the full selected extent')
    return ns, nt, length/ns, 2*math.pi/nt


def _assemble(centre, axes, radii, counts, s_min, ds, angle_step,
              requested_spacing, max_edge, gap, progress):
    ns, nt = radii.shape
    observed = counts > 0
    measured = radii[observed]
    if not len(measured) or float(np.median(measured)) < .05:
        raise ValueError('Select lining measurements around one tunnel branch, with a radius of at least 0.05 m.')
    if np.count_nonzero(observed.any(0)) < nt*.6:
        raise ValueError('Less than 60% angular coverage: choose a lining scan around one tunnel branch, or use a local surface method.')
    progress('Joining measured lining cells and bounded small gaps…')
    angular, longitudinal = radii.copy(), radii.copy()
    for row in range(ns):
        angular[row] = _fill_line(radii[row], angle_step, gap, True, True)
        if row and row % 500 == 0:
            progress(f'Joining supported lining rows: {row:,} / {ns:,}')
    for col in range(nt):
        longitudinal[:, col] = _fill_line(radii[:, col], ds, gap)
    # Both directions start from measured support. No iterative growth across
    # an opening or propagation through an already interpolated cell.
    both = np.isfinite(angular) & np.isfinite(longitudinal)
    # A small interpolation needs measured support in both directions. A
    # narrow but arbitrarily long unmeasured slit, a completely missing ring,
    # or a portal must not be filled one short cross-gap row at a time.
    joined = np.where(observed, radii, np.where(both, (angular+longitudinal)/2, np.nan))
    valid = np.isfinite(joined)
    idx = np.full((ns, nt), -1, dtype=np.int64)
    idx[valid] = np.arange(valid.sum())
    rows, cols = np.nonzero(valid)
    ss = s_min+(rows+.5)*ds
    tt = (cols+.5)*angle_step
    rr = joined[valid]
    vertices = centre+np.column_stack((ss, rr*np.cos(tt), rr*np.sin(tt)))@axes.T
    a = idx[:-1].ravel()
    b = np.roll(idx[:-1], -1, axis=1).ravel()
    c = idx[1:].ravel()
    d = np.roll(idx[1:], -1, axis=1).ravel()
    faces = np.vstack((np.column_stack((a, c, b)), np.column_stack((b, c, d))))
    faces = faces[(faces >= 0).all(1)]
    edge = max_edge or 2.5*math.hypot(ds, float(np.percentile(measured, 95))*angle_step)
    before_edges = len(faces)
    faces = geom.limit_edges(vertices, faces, edge)
    removed_edges = before_edges-len(faces)
    if not len(faces):
        raise ValueError('Measured lining cells produced no supported triangles. Increase the surface cell size or choose a denser/local lining selection; unsupported gaps were preserved.')
    q = vertices[faces]
    nondegenerate = np.linalg.norm(np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), axis=1) > 1e-14
    faces = faces[nondegenerate]
    if not len(faces):
        raise ValueError('The radial fit produced no non-degenerate lining triangles.')
    used = np.unique(faces)
    was_observed = observed[valid]
    meshed_observed = int(was_observed[used].sum())
    repaired = (~was_observed)[faces].any(1)
    vertices, faces = geom.clean_mesh(vertices, faces)
    q = vertices[faces]
    normal = np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0])
    radial = q.mean(1)-centre
    radial -= np.outer(radial@axes[:, 0], axes[:, 0])
    flip = np.einsum('ij,ij->i', normal, radial) < 0
    faces[flip] = faces[flip][:, [0, 2, 1]]
    coverage = meshed_observed/int(observed.sum())
    return vertices, faces, dict(_reconstructed_faces=repaired,
        requested_spacing_m=requested_spacing, frame='Shared-vertex radial lining grid / PCA',
        spacing_m=ds, angular_step_deg=360/nt, grid_rows=ns, grid_columns=nt,
        observed_cells=int(observed.sum()), meshed_observed_cells=meshed_observed,
        observed_cell_coverage_fraction=coverage, interpolated_cells=int((valid & ~observed).sum()),
        gap_limit_m=gap, effective_max_edge_m=edge, smoothing='None; median radius per occupied cell',
        triangles_before_edge_limit=before_edges, triangles_removed_by_edge_limit=removed_edges,
        support_coverage_note='Fraction of measured grid cells used by at least one triangle. '
        'This measures measured-support retention; it does not certify that an unmeasured region is complete.',
        warning='One approximately straight branch; grid vertices are fitted positions. '
        'Small gaps are interpolated in one pass only up to the stated local distance. '
        'Large gaps and ends stay open. For curved tunnels use Alignment-grid; '
        'separate junctions and clean equipment first. Radial medians do not identify services when services dominate a cell.')


def create(points, spacing=.12, degrees=2, max_edge=0, gap=.25, progress=lambda s: None):
    """Exact-median array path, also used as the numerical reference for tests."""
    from meshing import _tube_frame
    spacing, degrees, max_edge, gap = _parameters(spacing, degrees, max_edge, gap)
    points = np.asarray(points, dtype=np.float64)
    points = points[np.isfinite(points).all(1)]
    if len(points) < 3:
        raise ValueError('At least three finite points are required.')
    centre, axes, _ = _tube_frame(points)
    local = (points-centre)@axes
    s = local[:, 0]
    r = np.linalg.norm(local[:, 1:], axis=1)
    theta = np.mod(np.arctan2(local[:, 2], local[:, 1]), 2*np.pi)
    error = coordinate_roundoff(points.min(0), points.max(0))
    ns, nt, ds, da = _grid(float(np.ptp(s)), spacing, degrees, progress, error)
    si = np.clip(stable_floor((s-s.min())/ds, error/ds), 0, ns-1)
    ti = stable_floor(theta/da, error/(np.maximum(r, .05)*da)) % nt
    key = si*nt+ti
    order = np.argsort(key, kind='stable')
    unique, starts, counts = np.unique(key[order], return_index=True, return_counts=True)
    radii = np.full(ns*nt, np.nan)
    support = np.zeros(ns*nt, np.int64)
    rs = r[order]
    for number, (cell, start, count) in enumerate(zip(unique, starts, counts)):
        radii[cell] = np.median(rs[start:start+count])
        support[cell] = count
        if number and number % 20000 == 0:
            progress(f'Fitting exact lining medians: {number:,} / {len(unique):,}')
    return _assemble(centre, axes, radii.reshape(ns, nt), support.reshape(ns, nt),
                     float(s.min()), ds, da, spacing, max_edge, gap, progress)


def create_streamed(source, spacing=.12, degrees=2, max_edge=0, gap=.25,
                    quality='balanced', progress=lambda s: None):
    """Two read-only passes; every retained point contributes cell support."""
    from meshing import _tube_frame_covariance
    from mesh_sampling import CellReservoir
    spacing, degrees, max_edge, gap = _parameters(spacing, degrees, max_edge, gap)
    capacity = {'fast': 9, 'balanced': 17, 'fine': 33}.get(quality)
    if capacity is None:
        raise ValueError('Choose Fast, Balanced or Fine quality.')
    stats = source.stats(progress)
    centre = stats['centre']
    _, axes, variance = _tube_frame_covariance(centre, stats['covariance'])
    lo, hi = stats['minimum']-centre, stats['maximum']-centre
    # A conservative projection of the exact XYZ bounds avoids a third source
    # pass. Empty edge cells stay empty; no fitted cap or extrapolation is added.
    axis = axes[:, 0]
    s_min = float(np.minimum(lo*axis, hi*axis).sum())
    s_max = float(np.maximum(lo*axis, hi*axis).sum())
    error = coordinate_roundoff(stats['minimum'], stats['maximum'])
    ns, nt, ds, da = _grid(s_max-s_min, spacing, degrees, progress, error)
    if error > ds*.01:
        raise ValueError('The requested cell size is below reliable float64 bin resolution at these coordinate magnitudes. Use a larger cell size or a suitable local survey origin.')
    # At most 12 million radius/priority pairs (192 MB); the grid itself is
    # capped separately. Odd capacities give a measured central order statistic.
    capacity = max(3, min(capacity, max(3, 12000000//(ns*nt))))
    if capacity % 2 == 0:
        capacity -= 1
    reservoir = CellReservoir(ns*nt, capacity)
    actual_min, actual_max = np.inf, -np.inf
    for points, ids in source.chunks(progress, 'Fitting all retained lining support'):
        local = (points-centre)@axes
        s = local[:, 0]
        radius = np.linalg.norm(local[:, 1:], axis=1)
        theta = np.mod(np.arctan2(local[:, 2], local[:, 1]), 2*np.pi)
        si = np.clip(stable_floor((s-s_min)/ds, error/ds), 0, ns-1)
        ti = stable_floor(theta/da, error/(np.maximum(radius, .05)*da)) % nt
        reservoir.add(si*nt+ti, radius, ids, points)
        actual_min, actual_max = min(actual_min, float(s.min())), max(actual_max, float(s.max()))
    radii = reservoir.medians().reshape(ns, nt)
    counts = reservoir.counts.reshape(ns, nt)
    observed = counts > 0
    fit_points = reservoir.points[observed.ravel()].copy()
    fit_samples = int(np.minimum(counts, capacity).sum())
    approximate_cells = int((counts > capacity).sum())
    # Release the largest arrays before constructing adjacency/triangles.
    del reservoir
    v, f, extra = _assemble(centre, axes, radii, counts, s_min, ds, da,
                            spacing, max_edge, gap, progress)
    extra.update(support_mode='full_retained_grid', finite_source_points=int(stats['count']),
        source_passes=source.passes, grid_reservoir_per_cell=capacity,
        grid_fit_samples=fit_samples, approximate_median_cells=approximate_cells,
        exact_median_cells=int(observed.sum())-approximate_cells,
        grid_boundary_roundoff_m=error,
        measured_axial_extent_m=[actual_min, actual_max], pca_variance=variance.tolist(),
        median_approximation=f'Exact support counts from every finite retained measurement; '
        f'median of up to {capacity} reproducibly sampled measured radii per cell. '
        'Cells within that capacity use the exact median. Reservoir priorities use original point IDs, '
        'independent of input chunk boundaries. Larger cells use an approximate median.',
        point_spacing_note='Estimated distance between spatially distributed measured fit-reference points, not full-source point spacing.')
    return v, f, extra, fit_points
