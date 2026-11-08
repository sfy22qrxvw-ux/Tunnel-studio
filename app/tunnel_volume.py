# Copyright (c) 2026 Damian Gulczynski.
"""Measure the enclosed void between two perpendicular alignment sections.

Read source faces in blocks, shortlist the requested reach and clip indexed
triangles against the two exact section planes. Shared cut edges use the same
vertices. Cap only one simple portal surrounding each selected axis point,
then validate the closed oriented shell before asking Manifold for its volume.
All clipping and capping are temporary measurement geometry in local metres.
"""
from contextlib import ExitStack
import math

import numpy as np

import cad_geometry
import compute_runtime
import inspection_geometry as ig


PLANE_EPS_M = 1e-8
BLOCK = 8192


def _planes(axis, start, end):
    if axis is None:
        raise ValueError('Load the tunnel alignment before selecting volume chainages.')
    start = ig._number(start, 'Start chainage')
    end = ig._number(end, 'End chainage')
    if end <= start:
        raise ValueError('End chainage must be greater than start chainage.')
    c0, t0, *_ = axis.at(start)
    c1, t1, *_ = axis.at(end)
    if min((c1-c0) @ t0, (c1-c0) @ t1) <= 2*PLANE_EPS_M:
        raise ValueError('The end planes overlap or face away from this reach. Choose a shorter interval.')
    # The selected polyline must pass through this slab once. Test complete
    # line segments: an unselected segment could cross it with both ends out.
    stations = np.unique(np.r_[axis.a[:, 0], start, end])
    xyz = np.column_stack([np.interp(stations, axis.a[:, 0], axis.a[:, k]) for k in (1, 2, 3)])
    distances = np.column_stack(((xyz-c0) @ t0, (c1-xyz) @ t1))
    selected = (stations >= start) & (stations <= end)
    if (distances[selected] < -PLANE_EPS_M).any():
        raise ValueError('A section plane cuts back across the selected alignment. Choose a shorter interval.')
    outside = (stations[1:] <= start) | (stations[:-1] >= end)
    before = distances[:-1][outside]
    after = distances[1:][outside]
    low = np.zeros(len(before))
    high = np.ones(len(before))
    for col in range(2):
        a = before[:, col]
        step = after[:, col]-a
        advancing = step > 0
        receding = step < 0
        low[advancing] = np.maximum(low[advancing], (PLANE_EPS_M-a[advancing])/step[advancing])
        high[receding] = np.minimum(high[receding], (PLANE_EPS_M-a[receding])/step[receding])
        high[(step == 0) & (a <= PLANE_EPS_M)] = -1
    if (high > low).any():
        raise ValueError('Another part of the alignment enters these planes. Choose an unambiguous shorter reach.')
    return c0, c1, t0, t1


def _read_reach(meta, c0, c1, t0, t1, limit, progress, cancel):
    """Keep indices only for faces intersecting both inward half spaces."""
    parts = []
    kept = 0
    with ExitStack() as stack:
        reader = ig._VertexReader(meta)
        stack.callback(reader.close)
        stream = stack.enter_context(open(meta['faces_file'], 'rb')) if 'faces_file' in meta else None
        for begin in range(0, meta['triangles'], BLOCK):
            length = min(BLOCK, meta['triangles']-begin)
            if stream is None:
                faces = meta['_faces_array'][begin:begin+length]
            else:
                flat = np.fromfile(stream, '<u4', count=3*length)
                if flat.size != 3*length:
                    raise ValueError('The mesh face cache changed while measuring.')
                faces = flat.reshape(-1, 3)
            vertices = reader.read(faces, cancel)
            inside_start = ((vertices-c0) @ t0 >= -PLANE_EPS_M).any(axis=1)
            inside_end = ((c1-vertices) @ t1 >= -PLANE_EPS_M).any(axis=1)
            keep = inside_start & inside_end
            q = vertices[keep]
            cross = np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0])
            if not np.isfinite(cross).all() or (np.linalg.norm(cross, axis=1) == 0).any():
                raise ValueError('The selected reach contains a degenerate triangle. Repair the mesh before measuring volume.')
            part = faces[keep]
            kept += len(part)
            if kept > limit:
                raise ValueError('The selected reach exceeds the memory budget for solid validation. Choose a shorter chainage interval.')
            if len(part):
                parts.append(part)
            ig._emit(progress, cancel, f'Volume · reading triangles {begin+length:,}/{meta["triangles"]:,}')
        if not parts:
            raise ValueError('No mesh triangles span the selected section planes.')
        faces = np.concatenate(parts)
        parts.clear()
        unique, inverse = np.unique(faces, return_inverse=True)
        del faces
        vertices = np.empty((len(unique), 3), dtype=np.float64)
        for begin in range(0, len(unique), BLOCK):
            vertices[begin:begin+BLOCK] = reader.read(unique[begin:begin+BLOCK], cancel)-c0
        return vertices, inverse.reshape(-1, 3)


def _clip(vertices, faces, centre, inward, label, limit, progress, cancel):
    """Clip an oriented mesh against an inward plane; reuse the original edge ID."""
    distance = (vertices-centre) @ inward
    on_plane = abs(distance) <= PLANE_EPS_M
    vertices[on_plane] -= distance[on_plane, None]*inward
    distance[on_plane] = 0.
    extra = []
    crossing = {}
    parts = []
    count = 0

    def intersection(a, b):
        if distance[a] == 0:
            return a
        if distance[b] == 0:
            return b
        key = (min(a, b), max(a, b))
        index = crossing.get(key)
        if index is None:
            fraction = distance[a]/(distance[a]-distance[b])
            point = vertices[a]+fraction*(vertices[b]-vertices[a])
            index = len(vertices)+len(extra)
            extra.append(point)
            crossing[key] = index
        return index

    for begin in range(0, len(faces), BLOCK):
        f = faces[begin:begin+BLOCK]
        d = distance[f]
        # Existing caps exactly on the cut plane are replaced by a single
        # cap built from the retained wall. Discard outside zero-area contacts.
        positive = (d > 0).any(axis=1)
        inside = (d >= 0).all(axis=1)
        kept = f[positive & inside]
        parts.append(kept)
        clipped = []
        for tri in f[positive & ~inside]:
            polygon = []
            previous = int(tri[-1])
            for index in tri:
                current = int(index)
                if (distance[previous] < 0) != (distance[current] < 0):
                    cut = intersection(previous, current)
                    if not polygon or polygon[-1] != cut:
                        polygon.append(cut)
                if distance[current] >= 0 and (not polygon or polygon[-1] != current):
                    polygon.append(current)
                previous = current
            if len(polygon) > 1 and polygon[-1] == polygon[0]:
                polygon.pop()
            clipped.extend((polygon[0], polygon[i], polygon[i+1]) for i in range(1, len(polygon)-1))
        if clipped:
            parts.append(np.asarray(clipped, dtype=np.int64))
        count += len(kept)+len(clipped)
        if count > limit or len(extra) > limit//2:
            raise ValueError('Too many section intersections for this memory budget. Choose a shorter reach.')
        ig._emit(progress, cancel, f'Volume · cutting {label} section · {min(begin+BLOCK,len(faces)):,}/{len(faces):,} faces')
    if not count:
        raise ValueError('The selected planes leave an empty mesh. Check the mesh and chainages.')
    if extra:
        vertices = np.vstack((vertices, extra))
    return vertices, np.concatenate(parts)


def _boundary(faces):
    # Sort compact undirected edge keys, also retain direction for winding QA.
    a = faces.ravel()
    b = faces[:, [1, 2, 0]].ravel()
    key = np.minimum(a, b).astype(np.uint64) << np.uint64(32)
    key |= np.maximum(a, b).astype(np.uint64)
    order = np.argsort(key)
    k = key[order]
    starts = np.r_[0, np.flatnonzero(k[1:] != k[:-1])+1]
    counts = np.diff(np.r_[starts, len(k)])
    if (counts > 2).any():
        raise ValueError('The selected mesh contains non-manifold edges. Repair the lining before calculating volume.')
    pairs = starts[counts == 2]
    forward = (a < b)[order]
    if (forward[pairs] == forward[pairs+1]).any():
        raise ValueError('Adjacent wall triangles have inconsistent winding. Orient the mesh before calculating volume.')
    singles = order[starts[counts == 1]]
    return np.column_stack((a[singles], b[singles]))


def _portal(edges, vertices, centre, normal, label, cancel):
    """One unbranched simple cycle wholly within the plane, surrounding the axis."""
    if len(edges) < 3:
        raise ValueError(f'The mesh does not span a complete {label} section at the selected chainage.')
    if len(edges) > 200_000:
        raise ValueError(f'The {label} boundary is too large to validate. Inspect it for disconnected sections.')
    successors = dict(edges.tolist())
    if len(successors) != len(edges) or len(set(successors.values())) != len(edges):
        raise ValueError(f'The {label} section has a branched or touching boundary.')
    root = int(edges[0, 0])
    ring = []
    current = root
    while current in successors:
        if len(ring) % BLOCK == 0:
            ig._check(cancel)
        ring.append(current)
        current = successors.pop(current)
    if current != root or successors:
        raise ValueError(f'The {label} section has multiple or incomplete contours. Select one continuous tunnel bore.')
    ring = np.asarray(ring[::-1])  # cap edges must oppose the wall edges
    basis = ig._normal_basis(normal)
    uv = (vertices[ring]-centre) @ basis[:, :2]
    manifold, shapely = cad_geometry.engines()
    poly = shapely.Polygon(uv)
    if not poly.is_valid or poly.area <= 1e-12 or not poly.contains(shapely.Point(0., 0.)):
        raise ValueError(f'The {label} contour intersects itself or does not enclose the alignment. Check the selected branch.')
    # Use a centre fan when valid so collinear edge points survive exactly.
    # For a concave section that cannot see the axis, triangulate its polygon.
    next_uv = np.roll(uv, -1, axis=0)
    cross = uv[:, 0]*next_uv[:, 1]-uv[:, 1]*next_uv[:, 0]
    if (cross > 1e-14).all() or (cross < -1e-14).all():
        cap = np.column_stack((ring, np.roll(ring, -1), np.full(len(ring), len(vertices))))
        vertices = np.vstack((vertices, centre))
    else:
        positive = float(np.sum(cross)) > 0
        ordered = np.arange(len(ring)) if positive else np.arange(len(ring)-1, -1, -1)
        triangles = np.asarray(manifold.triangulate([uv[ordered]]), dtype=np.int64)
        if len(triangles) != len(ring)-2:
            raise ValueError(f'The {label} polygon could not be capped at its original resolution.')
        cap = ring[ordered[triangles]]
        if not positive:
            cap = cap[:, ::-1]
    return vertices, cap, float(poly.area)


def measure(mesh, axis, start, end, progress=lambda message: None, cancel=lambda: False):
    """Return enclosed m³, cap areas, section geometry and topology QA."""
    ig._emit(progress, cancel, 'Volume · checking the selected alignment and mesh…')
    c0, c1, t0, t1 = _planes(axis, start, end)
    meta = ig.surface_meta(mesh)
    # Allow for native mesh, sorted edges, input/output triangles and
    # dictionary-backed cut vertices. This is admission guidance, not peak RSS.
    limit = max(1000, min(4_000_000, compute_runtime.memory_budget()//1300))
    v, f = _read_reach(meta, c0, c1, t0, t1, limit, progress, cancel)
    o0 = np.zeros(3)
    o1 = c1-c0
    v, f = _clip(v, f, o0, t0, 'start', limit, progress, cancel)
    v, f = _clip(v, f, o1, -t1, 'end', limit, progress, cancel)
    ig._emit(progress, cancel, 'Volume · checking wall boundaries and winding…')
    edges = _boundary(f)
    at_start = (abs((v[edges]-o0) @ t0) <= PLANE_EPS_M).all(axis=1)
    at_end = (abs((v[edges]-o1) @ t1) <= PLANE_EPS_M).all(axis=1)
    if (at_start == at_end).any():
        raise ValueError('There are openings within the selected reach or intersecting section planes. Repair the wall or choose a shorter interval.')
    vertices, caps_a, area_a = _portal(edges[at_start], v, o0, t0, 'start', cancel)
    vertices, caps_b, area_b = _portal(edges[at_end], vertices, o1, t1, 'end', cancel)
    closed = np.vstack((f, caps_a, caps_b))
    if len(_boundary(closed)):
        raise ValueError('The capped mesh still has boundary edges. Inspect and repair the mesh.')
    ig._emit(progress, cancel, 'Volume · validating the closed solid…')
    used, ids = np.unique(closed, return_inverse=True)
    vertices = np.ascontiguousarray(vertices[used])
    faces = np.ascontiguousarray(ids.reshape(-1, 3), dtype=np.uint64)
    manifold, _ = cad_geometry.engines()
    solid = manifold.Manifold(manifold.Mesh64(vertices, faces))
    if str(solid.status()) != 'Error.NoError' or solid.is_empty():
        raise ValueError('The capped mesh is not a valid oriented manifold.')
    if len(solid.decompose()) != 1 or solid.genus() != 0:
        raise ValueError('The reach contains disconnected shells or a through-opening. Select one tunnel bore.')
    volume = float(solid.volume())
    if volume < 0:
        # A connected and consistently wound scan surface may face inwards.
        solid = manifold.Manifold(manifold.Mesh64(vertices, np.ascontiguousarray(faces[:, ::-1])))
        volume = float(solid.volume())
    if not math.isfinite(volume) or volume <= 0 or str(solid.status()) != 'Error.NoError':
        raise ValueError('The selected reach does not enclose a positive validated volume.')
    ig._emit(progress, cancel, 'Volume · both caps and closed shell validated.')
    return dict(volume_m3=volume, start_area_m2=area_a, end_area_m2=area_b,
                start_centre=c0.tolist(), end_centre=c1.tolist(), start_normal=t0.tolist(),
                end_normal=t1.tolist(), wall_triangles=len(f), cap_triangles=len(caps_a)+len(caps_b),
                boundary_edges=0)
