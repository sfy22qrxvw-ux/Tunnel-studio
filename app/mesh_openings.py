# Copyright (c) 2026 Damian Gulczynski.
"""Read-only inventory of circular mesh rims with observed lining support."""
from collections import Counter
import math

import numpy as np
from scipy.spatial import distance

import mesh_repair


def current_layout(s, meta):
    """Only use a lining result from the same current scan geometry."""
    result = s.STATE.get('surface_result') or {}
    provenance = meta.get('mesh_summary') or {}
    if (not result.get('lining_frame') or not result.get('source_item_id') or
            result['source_item_id'] != provenance.get('source_item_id') or
            not provenance.get('source_geometry_revision')):
        return None
    from registration_pose import revision
    from surface_api import patch_source
    source = patch_source(s, result)
    if not source or revision(source['meta']) != provenance['source_geometry_revision']:
        return None
    if provenance.get('source_fingerprint', result['source_fingerprint']) != result['source_fingerprint']:
        return None
    return result


def _circle(points, adjacent_points, maximum, plane_limit):
    if len(points) < 8 or np.ptp(points, axis=0).max() > maximum:
        return None
    extent = float(distance.pdist(points).max())
    if extent > maximum:
        return None
    origin = points[0] + np.mean(points - points[0], axis=0)
    local = points - origin
    _, singular, basis = np.linalg.svd(local, full_matrices=False)
    deviation = float(np.max(np.abs(local @ basis[2])))
    if singular[1] < 1e-8 or deviation > min(plane_limit, .1 * extent):
        return None
    rim = local @ basis[:2].T
    length = np.linalg.norm(np.roll(rim, -1, axis=0) - rim, axis=1)
    weights = np.sqrt((length + np.roll(length, 1)) / (2 * length.mean()))
    matrix = np.column_stack((2 * rim / extent, np.ones(len(rim))))
    solution, _, rank, _ = np.linalg.lstsq(matrix * weights[:, None],
                                           np.sum((rim / extent)**2, axis=1) * weights, rcond=None)
    if rank < 3:
        return None
    centre = solution[:2] * extent
    radial = rim - centre
    radii = np.linalg.norm(radial, axis=1)
    radius = float(np.median(radii))
    if radius <= 0 or 2 * radius > maximum or np.max(np.abs(radii - radius)) > .12 * radius:
        return None
    angles = np.sort(np.arctan2(radial[:, 1], radial[:, 0]))
    if np.diff(np.r_[angles, angles[0] + math.tau]).max() > math.pi / 2:
        return None
    # Adjacent triangles must lie on the material side of the rim. This
    # distinguishes a hole in a sheet from its outside edge or an open pipe end.
    midpoints = (points + np.roll(points, -1, axis=0)) / 2
    outward = (radial + np.roll(radial, -1, axis=0)) / 2
    support = adjacent_points - midpoints
    projections = np.sum((support @ basis[:2].T) * outward, axis=1)
    supported = projections > .1 * np.linalg.norm(outward, axis=1) * np.linalg.norm(support, axis=1)
    if length[supported].sum() < .8 * length.sum():
        return None
    return dict(centre=(origin + centre @ basis[:2]).tolist(), diameter_mm=2000 * radius,
                extent_m=extent, plane_deviation_m=deviation)


def _attribution(rows, vertices, layout):
    if not layout or not rows:
        return []
    from shapely import Point, Polygon, STRtree
    from lining_features import frame

    model = {k: np.asarray(layout['lining_frame'][k]) for k in ('reference', 'axis', 'u', 'v')}
    model['nodes'] = np.asarray(layout['lining_frame'].get('nodes', [[0., 0., 0.]]))
    radius = layout['lining_frame'].get('radius_m')

    def section(points, middle):
        station, angle = frame(points, model)
        angle = middle + (angle - middle + math.pi) % math.tau - math.pi
        return np.column_stack((station, angle))

    polygons, identities = [], []
    for panel in layout['clusters']:
        if panel.get('boundary_sources') != ['measured'] * 4 or not panel.get('panel_outline'):
            continue
        _, angle = frame([panel['label_centre']], model)
        outline = section(np.concatenate(panel['panel_outline']), float(angle[0]))
        identity = panel['ring_interval'], panel['panel_number']
        for shift in (0., -math.tau, math.tau):
            polygon = Polygon(outline + [0, shift])
            if polygon.is_valid and polygon.area > 0 and polygon.bounds[1] <= math.tau and polygon.bounds[3] >= 0:
                polygons.append(polygon)
                identities.append(identity)

    tree = STRtree(polygons)
    counts = Counter()
    stations, angles = frame([row['centre'] for row in rows], model)
    for row, station, angle in zip(rows, stations, angles):
        row.update(station_m=float(station), arc_angle_deg=float(np.degrees(angle)),
                   arc_m=float(angle * radius) if radius else None)
        candidates = tree.query(Point(station, angle), predicate='within')
        if not len(candidates):
            continue
        footprint = Polygon(section(vertices[row['_loop']], angle))
        if not footprint.is_valid or footprint.is_empty:
            continue
        matches = {identities[i] for i in candidates if polygons[i].contains_properly(footprint)}
        if len(matches) == 1:
            ring, panel = matches.pop()
            row.update(ring_interval=ring, panel_number=panel, reason=f'Ring {ring} · panel {panel}')
            counts[ring, panel] += 1
    for row in rows:
        if 'panel_number' in row:
            row['panel_observed_count'] = counts[row['ring_interval'], row['panel_number']]
    return [dict(ring_interval=ring, panel_number=panel, observed_count=count)
            for (ring, panel), count in sorted(counts.items())]


def inspect(vertices, faces, flags, settings, layout, progress, cancel):
    cfg = mesh_repair.parameters(settings)
    progress('Finding open mesh rims…')
    adjacency, directed, nonmanifold, _ = mesh_repair._boundary_graph(faces, cancel)
    rows = []
    components = estimated = 0
    for row in mesh_repair._opening_rows(vertices, adjacency, cancel):
        components += 1
        if not row['_ordered'] or any(i in nonmanifold for i in row['_loop']):
            continue
        ids = row['_loop']
        following = np.roll(ids, -1)
        owners = [directed[min(a, b), max(a, b)][2] for a, b in zip(ids, following)]
        if np.any(flags[owners]):
            estimated += 1
            continue
        adjacent = faces[owners].astype(np.int64).sum(1) - ids - following
        rim = _circle(vertices[ids], vertices[adjacent], cfg['max_extent_m'],
                      cfg['max_plane_deviation_mm'] / 1000)
        if rim:
            row.update(rim, status='preserved', reason='Unassigned', reason_code='circular_rim')
            rows.append(row)
        if components % 100 == 0:
            progress(f'Inspected {components:,} open boundaries…')
    panel_counts = _attribution(rows, vertices, layout)
    mesh_repair._check(cancel)
    assigned = sum(p['observed_count'] for p in panel_counts)
    summary = dict(circular_rims=len(rows), other_boundaries=components-len(rows),
                   unassigned=len(rows)-assigned, inferred_boundaries=estimated,
                   panel_counts=panel_counts, layout_available=layout is not None)
    progress(f'{len(rows):,} circular rims inventoried')
    return dict(holes=rows, patches={}, settings=cfg, summary=summary)
