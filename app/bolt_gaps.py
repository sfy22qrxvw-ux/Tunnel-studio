# Copyright (c) 2026 Damian Gulczynski.
"""Estimate the lining across compact open mesh boundaries from the adjoining wall.

The outer ring keeps its original vertex IDs. Interior heights follow a quadratic
fit to the local sheet, with the rim residual blended in radially for continuity.
Opening size and shape alone cannot establish that a gap was made by a bolt.
"""
from collections import Counter
import math

import numpy as np
from scipy.spatial import cKDTree

import cad_geometry
import compute_runtime
import mesh_repair


def _incident_faces(vertices, geometry):
    """Use the incidence index from boundary inspection, never spatial neighbours on another sheet."""
    ordered = geometry['sorted_vertices']
    left = np.searchsorted(ordered, vertices)
    right = np.searchsorted(ordered, vertices, side='right')
    return np.unique(np.concatenate([geometry['incidence_order'][a:b] for a, b in zip(left, right)]) // 3)


def _quadratic(uv):
    x, y = uv.T
    return np.column_stack((np.ones(len(uv)), x, y, x*x, x*y, y*y))


def _patch(v, f, rim, frame, diameter, flags, geometry):
    centre, basis, polygon = frame
    radius = diameter / 2
    boundary = (v[rim] - centre) @ basis.T
    uv = boundary[:, :2] / radius
    next_uv = np.roll(uv, -1, axis=0)
    signed_wedges = uv[:, 0]*next_uv[:, 1] - uv[:, 1]*next_uv[:, 0]
    if np.min(signed_wedges*np.sign(signed_wedges.sum())) <= 1e-8:
        raise ValueError('Rim cannot be filled radially without crossing its boundary.')
    if 4*math.pi*polygon.area / polygon.length**2 < .64:
        raise ValueError('Opening is elongated or irregular for a bolt footprint.')

    # Use a two-ring face-connected stencil. Points alone do not determine
    # curvature when they all lie on the same circle around the missing centre.
    first = _incident_faces(rim, geometry)
    if flags is not None:
        first = first[~flags[first]]
    if not len(first):
        raise ValueError('The adjoining wall has no measured-support triangles.')
    ring_vertices = np.unique(f[first])
    second = _incident_faces(ring_vertices, geometry)
    if flags is not None:
        second = second[~flags[second]]
    ids = np.unique(f[second])
    local = (v[ids] - centre) @ basis.T
    local = local[np.linalg.norm(local, axis=1) <= 3*radius]
    if len(local) < 9:
        raise ValueError('Insufficient connected lining vertices around the rim.')
    scaled = local[:, :2] / radius
    weights = 1 / np.sqrt(1 + (scaled*scaled).sum(axis=1))
    matrix = _quadratic(scaled)
    coefficients, _, rank, singular = np.linalg.lstsq(matrix*weights[:, None], local[:, 2]*weights, rcond=None)
    if rank < 6 or singular[-1] < singular[0] * 1e-5:
        raise ValueError('The adjoining lining does not constrain local curvature.')
    residual = local[:, 2] - matrix @ coefficients
    rms = float(np.sqrt(np.mean(residual**2)))
    threshold = max(.001, diameter*.025)
    rim_error = boundary[:, 2] - _quadratic(uv) @ coefficients
    if rms > threshold or np.max(np.abs(rim_error)) > threshold*2:
        raise ValueError('Local lining is too uneven for a supported curved patch.')

    # Each spoke shares its rim vertex with the source. At the intermediate
    # ring, blend the quadratic surface with the measured end of that spoke.
    half = .5
    inner_uv = half * uv
    inner_z = _quadratic(inner_uv) @ coefficients + half**2 * rim_error
    heights = np.r_[inner_z, coefficients[0]]
    if np.max(np.abs(heights)) > diameter*.12:
        raise ValueError('Estimated curvature across the opening is too large.')
    new_local = np.column_stack((np.vstack((inner_uv*radius, [0., 0.])), heights))
    new_vertices = centre + new_local @ basis
    n = len(rim)
    outer = np.arange(n, dtype=np.uint32)
    after = np.roll(outer, -1)
    inner = outer + n
    next_inner = after + n
    faces = np.vstack((np.column_stack((outer, after, next_inner)),
                       np.column_stack((outer, next_inner, inner)),
                       np.column_stack((inner, next_inner, np.full(n, 2*n, dtype=np.uint32)))))
    band = max(float(np.max(np.abs(boundary[:, 2]))), float(np.max(np.abs(heights)))) + 1e-8
    return new_vertices, faces, band, rms


def fill_bolt_gaps(vertices, faces, gap_min_mm=50, gap_max_mm=150, progress=lambda _: None,
                   cancel=None, *, estimated_faces=None):
    """Return (vertices, faces, report). Appended faces are estimated and originals retain order.

    Diameter is the greatest 3D rim-vertex separation. Units in the input mesh
    are metres. estimated_faces, when supplied, excludes prior reconstructed
    triangles from the lining fit. No input array is modified.
    """
    try:
        lower, upper = float(gap_min_mm), float(gap_max_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError('Enter finite minimum and maximum bolt-gap diameters in millimetres.') from exc
    if not (math.isfinite(lower) and math.isfinite(upper) and 1 <= lower <= upper <= 500):
        raise ValueError('Choose bolt-gap diameters between 1 and 500 mm, with minimum at or below maximum.')
    cancel = cancel or compute_runtime.cancelled
    compute_runtime.check_cancel(cancel)
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces)
    flags = None if estimated_faces is None else np.asarray(estimated_faces, dtype=bool)
    if flags is not None and flags.shape != (len(f),):
        raise ValueError('Estimated face flags must match the input triangles.')
    analysis = mesh_repair.analyse(v, f, {'max_extent_m':upper/1000}, progress=progress,
                                   cancel=cancel, include_local_geometry=True)
    geometry = analysis['_geometry']
    _, shapely = cad_geometry.engines()
    gaps = analysis['holes']
    spatial = cKDTree([row['centre'] for row in gaps]) if gaps else None
    original = np.asarray(f, dtype=np.uint32)
    add_vertices, add_faces, previously_filled = [], [], {}
    next_vertex, next_face = len(v), len(original)
    reasons, repairs = Counter(), []

    for ordinal, row in enumerate(gaps):
        compute_runtime.check_cancel(cancel)
        if ordinal % 20 == 0:
            progress(f'Reconstructing bolt gaps · {ordinal}/{len(gaps)} openings reviewed')
        if row['status'] != 'repairable':
            reasons[row['reason']] += 1
            continue
        extent = row['extent_m']
        if extent*1000 < lower-1e-7:
            reasons['Below the selected minimum diameter.'] += 1
            continue
        rim = row['_loop']
        frame = geometry['frames'][row['id']]
        centre, basis, polygon = frame
        try:
            new_v, local_f, band, rms = _patch(v, original, rim, frame, extent, flags, geometry)
        except ValueError as problem:
            reasons[str(problem)] += 1
            continue
        area_tol = max(1e-14, extent**2*1e-10)
        # The planar cap passed inspection. Check again using the depth occupied
        # by the actual curved patch, including other new repairs.
        reach = np.linalg.norm(v[rim]-centre, axis=1).max() + band
        nearby = geometry['tree'].query_ball_point(centre, reach + geometry['max_face_radius'])
        if mesh_repair._overlaps_existing(v, original, nearby, centre, basis, polygon,
                                          band, area_tol, cancel, shapely):
            reasons['Estimated patch intersects or overlaps another surface.'] += 1
            continue
        collision = False
        for other in spatial.query_ball_point(centre, reach + upper/1000*1.2):
            if other in previously_filled:
                prior_v, prior_f = previously_filled[other]
                if mesh_repair._overlaps_existing(prior_v, prior_f, np.arange(len(prior_f)),
                                                  centre, basis, polygon, band, area_tol, cancel, shapely):
                    collision = True
                    break
        if collision:
            reasons['Estimated patch overlaps another repair.'] += 1
            continue
        if next_face+len(local_f) > mesh_repair.MAX_TRIANGLES or next_vertex+len(new_v) > mesh_repair.MAX_VERTICES:
            raise ValueError('Adding the estimated patches would exceed the interactive mesh capacity. Select a smaller section.')
        ids = np.r_[rim, np.arange(next_vertex, next_vertex+len(new_v), dtype=np.uint32)]
        add_vertices.append(new_v)
        add_faces.append(ids[local_f])
        previously_filled[ordinal] = (np.vstack([v[rim], new_v]), local_f)
        repairs.append(dict(id=row['id'], centre_m=row['centre'], diameter_mm=extent*1000,
                            fit_rms_mm=rms*1000, estimated_face_start=next_face, estimated_face_count=len(local_f)))
        next_face += len(local_f)
        next_vertex += len(new_v)
    progress(f'Bolt-gap review complete · {len(repairs)} reconstructed / {len(gaps)} open boundaries')
    compute_runtime.check_cancel(cancel)
    report = dict(filled=len(repairs), openings=len(gaps), preserved=len(gaps)-len(repairs), unfilled=0,
                  extent_metric=analysis['summary']['extent_metric'],
                  gap_min_mm=lower, gap_max_mm=upper, estimated_face_start=len(f),
                  added_triangles=next_face-len(f), repairs=repairs, preserved_reasons=dict(reasons),
                  method='Radial interpolation on a local quadratic lining fit')
    if add_faces:
        return np.vstack([v, *add_vertices]), np.vstack([original, *add_faces]), report
    return v, original, report
