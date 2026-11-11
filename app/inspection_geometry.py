# Copyright (c) 2026 Damian Gulczynski.
"""Reference geometry and bounded surface quadrature for inspection.

Analytic distances are float64 and use preserved primitive definitions only
when their transforms and stored geometry are consistent. Other CAD surfaces
are explicitly compared as triangles, never advertised as analytic BREP.
Mesh-source samples cover every triangle with equal-area subtriangle centroids.
They estimate surface statistics; they are not an exact continuous Hausdorff
calculation. No input geometry is changed or silently simplified.
"""
from collections import OrderedDict
from pathlib import Path
import math

import numpy as np


FACE_BLOCK = 8192
VERTEX_PAGE = 32768
VERTEX_CACHE_PAGES = 8
QUERY_BLOCK = 25000
# A conservative component estimate, not a measured whole-process RAM bound.
REFERENCE_WORKSPACE_LIMIT_BYTES = 2 * 1024**3
REFERENCE_WORKSPACE_BASE_BYTES = 256 * 1024**2
MAX_ORDINAL = np.iinfo(np.int64).max
SAMPLING_METHOD = 'Equal-area barycentric subtriangle centroids'
SAMPLING_NOTE = ('Every non-degenerate triangle contributes. Subtriangle edges '
                 'are no longer than the requested spacing. Area weights sum '
                 'to the original mesh area. Statistics are surface quadrature '
                 'estimates, not exact continuous-surface extrema or Hausdorff distance.')


def _check(cancel):
    if cancel is not None and cancel():
        from jobdata import Cancelled
        raise Cancelled('Inspection cancelled. Source geometry is unchanged.')


def _emit(progress, cancel, message):
    _check(cancel)
    if progress is not None:
        progress(message)
    _check(cancel)


def _number(value, name, positive=False):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(name + ' must be finite.') from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(name + (' must be positive and finite.' if positive else ' must be finite.'))
    return value


def _vector(value, name):
    try:
        value = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(name + ' needs three finite coordinates.') from exc
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError(name + ' needs three finite coordinates.')
    return value


def _unit(value, name):
    value = _vector(value, name)
    length = np.linalg.norm(value)
    if not np.isfinite(length) or length <= 1e-15:
        raise ValueError(name + ' needs a non-zero direction.')
    return value / length


def _frame(raw=None):
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError('A construction frame must provide named origin and direction coordinates.')
    origin = _vector(raw.get('origin', [0, 0, 0]), 'Frame origin')
    if 'x_point' in raw and 'xy_point' not in raw:
        raise ValueError('A construction frame needs both its X and XY points.')
    x = _unit(_vector(raw['x_point'], 'Frame X point') - origin if 'x_point' in raw
              else raw.get('x', [1, 0, 0]), 'Frame X direction')
    y0 = (_vector(raw['xy_point'], 'Frame XY point') - origin if 'x_point' in raw
          else _vector(raw.get('y', [0, 1, 0]), 'Frame Y direction'))
    y = _unit(y0 - x * np.dot(y0, x), 'Frame Y direction')
    z = np.cross(x, y)
    if 'z' in raw and np.dot(_unit(raw['z'], 'Frame Z direction'), z) < 1 - 1e-7:
        raise ValueError('The construction frame is not right-handed and orthogonal.')
    return origin, np.column_stack((x, y, z))


def _normal_basis(normal, hint=None):
    z = _unit(normal, 'Plane / cylinder normal')
    x0 = _vector(hint, 'In-plane direction') if hint is not None else np.eye(3)[np.argmin(abs(z))]
    x = x0 - z * np.dot(x0, z)
    if np.linalg.norm(x) <= 1e-12:
        x0 = np.eye(3)[np.argmin(abs(z))]
        x = x0 - z * np.dot(x0, z)
    x /= np.linalg.norm(x)
    return np.column_stack((x, np.cross(z, x), z))


def _similarity(raw=None):
    value = np.asarray(np.eye(4) if raw is None else raw, dtype=np.float64)
    if (value.shape != (4, 4) or not np.isfinite(value).all()
            or not np.allclose(value[3], [0, 0, 0, 1], atol=1e-12, rtol=0)):
        raise ValueError('The stored transform is not a finite affine transform.')
    a = value[:3, :3]
    scale = float(np.linalg.norm(a[:, 0]))
    if scale <= 1e-15 or not math.isfinite(scale):
        raise ValueError('The stored transform is singular.')
    r = a / scale
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-9, rtol=1e-9):
        raise ValueError('Non-uniform scale or shear requires triangle-surface comparison.')
    return a, value[:3, 3], r, scale


def _unwrap(item):
    if not isinstance(item, dict):
        raise ValueError('Choose a mesh, CAD object or reference geometry.')
    return item['meta'] if isinstance(item.get('meta'), dict) else item


def _faces_array(faces, vertices):
    """Triangulate only verified convex planar polygons; CAD is already triangular."""
    try:
        array = np.asarray(faces)
    except ValueError:
        array = np.asarray(faces, dtype=object)
    if array.ndim == 2 and array.shape[1] == 3 and array.dtype.kind in 'iu':
        result = np.asarray(array, dtype=np.int64)
    else:
        triangles = []
        for face in faces:
            indices = np.asarray(face)
            if indices.ndim != 1 or len(indices) < 3 or indices.dtype.kind not in 'iu':
                raise ValueError('Mesh faces need at least three integer vertex indices.')
            indices = indices.astype(np.int64, copy=False)
            if indices.min() < 0 or indices.max() >= len(vertices):
                raise ValueError('Mesh triangle indices are outside the vertex array.')
            if len(indices) > 3:
                p = vertices[indices] - vertices[indices[0]]
                normal = np.cross(p[1], p[2])
                size = np.linalg.norm(normal)
                if size <= 0:
                    raise ValueError('A polygon face has degenerate leading vertices.')
                normal /= size
                tolerance = max(1e-10, float(np.linalg.norm(p, axis=1).max()) * 1e-9)
                turns = np.einsum('ij,j->i', np.cross(np.roll(p, -1, axis=0)-p,
                                                     np.roll(p, -2, axis=0)-np.roll(p, -1, axis=0)), normal)
                if np.max(abs(p @ normal)) > tolerance or np.any(turns < -tolerance**2):
                    raise ValueError('Triangulate non-convex or non-planar faces before inspection.')
            triangles.extend((int(indices[0]), int(indices[i]), int(indices[i+1]))
                             for i in range(1, len(indices)-1))
        result = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if not len(result) or result.min() < 0 or result.max() >= len(vertices):
        raise ValueError('Choose a non-empty surface with valid triangle indices.')
    return result


def surface_meta(item_or_cad, work=None):
    """Return a non-writing adapter for file-backed meshes or bounded CAD arrays.

    ``work`` is accepted for caller compatibility; no temporary files are made.
    Array-backed adapters are private working objects, not JSON project metadata.
    """
    meta = _unwrap(item_or_cad)
    if meta.get('_inspection_surface'):
        return meta
    if meta.get('vertices_file') and meta.get('faces_file'):
        try:
            nv, nf = int(meta['vertices']), int(meta['triangles'])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError('Mesh vertex / triangle counts are missing or invalid.') from exc
        if nv < 3 or nf < 1 or nv > np.iinfo(np.uint32).max or nf > MAX_ORDINAL:
            raise ValueError('Mesh vertex / triangle counts are outside the supported cache format.')
        vp, fp = Path(meta['vertices_file']), Path(meta['faces_file'])
        if not vp.is_file() or vp.stat().st_size != nv*24 or not fp.is_file() or fp.stat().st_size != nf*12:
            raise ValueError('The mesh vertex or triangle cache is missing or has the wrong size.')
        return dict(_inspection_surface=True, vertices=nv, triangles=nf,
                    vertices_file=str(vp), faces_file=str(fp), name=item_or_cad.get('name', meta.get('name', 'Mesh')))
    try:
        vertices = np.asarray(meta.get('vertices'), dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Choose a triangle mesh or a CAD surface with vertices and faces.') from exc
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3 or not np.isfinite(vertices).all():
        raise ValueError('Mesh vertices must be finite XYZ coordinates.')
    faces = _faces_array(meta.get('faces', []), vertices)
    return dict(_inspection_surface=True, vertices=len(vertices), triangles=len(faces),
                _vertices_array=vertices, _faces_array=faces, name=meta.get('name', 'CAD surface'))


class _VertexReader:
    """Bounded vertex page cache; no whole-file memory mapping."""
    def __init__(self, meta):
        self.meta = meta
        self.file = open(meta['vertices_file'], 'rb') if 'vertices_file' in meta else None
        self.pages = OrderedDict()

    def read(self, ids, cancel=None):
        ids = np.asarray(ids, dtype=np.int64)
        if not len(ids):
            return np.empty((*ids.shape, 3), dtype=np.float64)
        if ids.min() < 0 or ids.max() >= self.meta['vertices']:
            raise ValueError('Mesh triangle indices are outside the vertex cache.')
        if self.file is None:
            result = self.meta['_vertices_array'][ids]
        else:
            flat = ids.ravel(); page_ids = flat // VERTEX_PAGE
            result = np.empty((len(flat), 3), dtype=np.float64)
            for page in np.unique(page_ids):
                _check(cancel)
                key = int(page)
                values = self.pages.pop(key, None)
                if values is None:
                    start = key*VERTEX_PAGE
                    count = min(VERTEX_PAGE, self.meta['vertices']-start)
                    self.file.seek(start*24)
                    values = np.fromfile(self.file, '<f8', count=count*3)
                    if len(values) != count*3:
                        raise ValueError('The mesh vertex cache changed during inspection.')
                    values = values.reshape(count, 3)
                self.pages[key] = values
                if len(self.pages) > VERTEX_CACHE_PAGES:
                    self.pages.popitem(last=False)
                use = page_ids == key
                result[use] = values[flat[use]-key*VERTEX_PAGE]
            result = result.reshape(*ids.shape, 3)
        if not np.isfinite(result).all():
            raise ValueError('A mesh triangle references non-finite coordinates.')
        return result

    def close(self):
        self.pages.clear()
        if self.file is not None:
            self.file.close(); self.file = None


def _triangle_batches(meta, progress=None, cancel=None):
    reader = _VertexReader(meta)
    face_file = None
    try:
        face_file = open(meta['faces_file'], 'rb') if 'faces_file' in meta else None
        for start in range(0, meta['triangles'], FACE_BLOCK):
            _check(cancel)
            count = min(FACE_BLOCK, meta['triangles']-start)
            if face_file is not None:
                faces = np.fromfile(face_file, '<u4', count=count*3)
                if len(faces) != count*3:
                    raise ValueError('The mesh triangle cache changed during inspection.')
                faces = faces.reshape(count, 3)
            else:
                faces = meta['_faces_array'][start:start+count]
            triangles = reader.read(faces, cancel)
            ab = triangles[:, 1]-triangles[:, 0]; ac = triangles[:, 2]-triangles[:, 0]
            area = np.linalg.norm(np.cross(ab, ac), axis=1)*.5
            if np.any(~np.isfinite(area)) or np.any(area <= 0):
                raise ValueError('The surface contains a degenerate or non-finite triangle; repair it before inspection.')
            yield start, triangles, area
    finally:
        if face_file is not None:
            face_file.close()
        reader.close()


def _subdivisions(triangles, spacing):
    edges = triangles-np.roll(triangles, -1, axis=1)
    longest = np.linalg.norm(edges, axis=2).max(axis=1)
    division = np.maximum(1, np.ceil(longest/spacing))
    if np.any(~np.isfinite(division)) or np.any(division > math.isqrt(MAX_ORDINAL)):
        raise ValueError('The requested spacing would exceed the int64 surface-sample index range.')
    return division.astype(np.int64)


def mesh_sample_info(item_or_cad, spacing_m, progress=None, cancel=None):
    """Preflight all triangles without retaining a global sample or triangle array."""
    spacing = _number(spacing_m, 'Mesh sampling spacing', True)
    meta = surface_meta(item_or_cad); count = 0
    # Keep compensated summation constant in size even for arbitrarily many faces.
    area_total = area_compensation = 0.
    for start, triangles, area in _triangle_batches(meta, progress, cancel):
        n = _subdivisions(triangles, spacing)
        count += sum(int(v)*int(v) for v in n)
        if count > MAX_ORDINAL:
            raise ValueError('The requested mesh samples exceed the int64 record index range.')
        part = math.fsum(map(float, area))
        corrected = part-area_compensation; updated = area_total+corrected
        area_compensation = (updated-area_total)-corrected; area_total = updated
        _emit(progress, cancel, f'Inspecting surface coverage · {start+len(area):,}/{meta["triangles"]:,} triangles · {count:,} samples')
    return dict(triangles=meta['triangles'], sample_count=count, total_samples=count,
                area_m2=area_total, spacing_m=spacing, maximum_cell_edge_m=spacing,
                method=SAMPLING_METHOD, note=SAMPLING_NOTE)


def mesh_surface_batches(item_or_cad, spacing_m, chunk_points=QUERY_BLOCK, progress=None, cancel=None):
    """Yield (original face IDs, float64 XYZ, area weights) in bounded batches.

    Face IDs repeat for subtriangles. The caller may assign separate sequential
    sample ordinals. Changing batch size does not change samples or their order.
    Every triangle is split into n² congruent cells with n=ceil(longest edge /
    spacing); no global sample array and no silent total-sample cap are used.
    """
    spacing = _number(spacing_m, 'Mesh sampling spacing', True)
    if isinstance(chunk_points, bool) or int(chunk_points) != chunk_points or not 1 <= int(chunk_points) <= 100000:
        raise ValueError('Surface output batches must contain 1 to 100,000 samples.')
    chunk_points = int(chunk_points); meta = surface_meta(item_or_cad)
    ids = np.empty(chunk_points, dtype=np.int64)
    points = np.empty((chunk_points, 3), dtype=np.float64)
    weights = np.empty(chunk_points, dtype=np.float64)
    filled = produced = 0
    for first, triangles, areas in _triangle_batches(meta, progress, cancel):
        ns = _subdivisions(triangles, spacing)
        for face_index, (triangle, area, n_raw) in enumerate(zip(triangles, areas, ns)):
            if face_index % 256 == 0:
                _check(cancel)
            n = int(n_raw); face = first+face_index
            if produced+n*n > MAX_ORDINAL:
                raise ValueError('The requested mesh samples exceed the int64 record index range.')
            a = triangle[0]; ab = triangle[1]-a; ac = triangle[2]-a
            weight = float(area)/(n*n)
            if weight <= 0 or not math.isfinite(weight):
                raise ValueError('Mesh sample area is below the supported numeric range.')
            for i in range(n):
                # Upright then inverted cells: n² centroids, all strictly interior.
                for offset, columns in ((1/3, n-i), (2/3, n-i-1)):
                    j = 0
                    while j < columns:
                        _check(cancel)
                        take = min(columns-j, chunk_points-filled)
                        u = (i+offset)/n
                        v = (np.arange(j, j+take, dtype=np.float64)+offset)/n
                        points[filled:filled+take] = a+u*ab+v[:, None]*ac
                        ids[filled:filled+take] = face; weights[filled:filled+take] = weight
                        filled += take; produced += take; j += take
                        if filled == chunk_points:
                            _emit(progress, cancel, f'Sampling mesh surface · {produced:,} samples · triangle {face+1:,}/{meta["triangles"]:,}')
                            yield ids, points, weights
                            ids = np.empty(chunk_points, dtype=np.int64)
                            points = np.empty((chunk_points, 3), dtype=np.float64)
                            weights = np.empty(chunk_points, dtype=np.float64)
                            filled = 0
        _check(cancel)
    if filled:
        _emit(progress, cancel, f'Sampling mesh surface · {produced:,} samples complete')
        yield ids[:filled].copy(), points[:filled].copy(), weights[:filled].copy()


class _Reference:
    signed = False
    coordinate_step_mm = None
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()
    def close(self):
        pass
    @property
    def info(self):
        return dict(kind=self.kind, representation=self.representation,
                    signed=self.signed, signed_supported=self.signed_supported,
                    note=self.note, coordinate_step_mm=self.coordinate_step_mm)


class AnalyticReference(_Reference):
    def __init__(self, item, spec, signed=False, cancel=None):
        self.cancel = cancel; self.kind = str(spec.get('kind', ''))
        self.signed_supported = self.kind not in ('point', 'line', 'circle')
        if signed and not self.signed_supported:
            raise ValueError('Distances to curve / point geometry are unsigned; choose unsigned comparison.')
        self.signed = bool(signed)
        origin, basis = _frame(spec.get('frame'))
        if self.kind == 'plane' and any(k in spec for k in ('e', 'n', 'h')):
            origin = np.array([_number(spec.get(k, 0), 'Plane position') for k in ('e', 'n', 'h')])
            yaw = math.radians(_number(spec.get('yaw', 0), 'Plane yaw'))
            basis = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
        elif self.kind in ('plane', 'sphere', 'box', 'cylinder'):
            origin = _vector(spec.get('origin', spec.get('centre', origin)), 'Primitive origin')
        elif self.kind == 'circle':
            origin = _vector(spec.get('centre', origin), 'Circle centre')
        if self.kind == 'plane' and 'normal' in spec:
            basis = _normal_basis(spec['normal'], spec.get('x_direction', spec.get('x_axis', basis[:, 0])))
        if self.kind == 'cylinder' and 'axis' in spec:
            basis = _normal_basis(spec['axis'], basis[:, 0])
        a, translation, rotation, scale = _similarity(item.get('transform'))
        self.origin = a@origin+translation
        self.basis = rotation@basis
        self.extent = 1.
        if self.kind in ('sphere', 'cylinder', 'circle'):
            self.radius = _number(spec.get('radius', 1 if self.kind != 'circle' else None), 'Radius', True)*scale
            self.extent = self.radius
        if self.kind == 'cylinder':
            self.height = _number(spec.get('height', 1), 'Cylinder height', True)*scale
            self.extent = max(self.extent, self.height)
        if self.kind == 'box':
            self.size = _vector(spec.get('size', [1, 1, 1]), 'Box size')*scale
            if np.any(self.size <= 0):
                raise ValueError('Box sizes must be positive.')
            self.extent = float(self.size.max())
        self.finite_plane = self.kind == 'plane' and ('length' in spec or 'width' in spec)
        if self.finite_plane:
            # Editable CAD planes use width/height along local X/Y. Legacy
            # survey structures use length/width; preserve that interpretation.
            editable = 'height' in spec and 'length' not in spec
            self.length = _number(spec.get('width' if editable else 'length'), 'Plane length', True)*scale
            self.width = _number(spec.get('height' if editable else 'width'), 'Plane width', True)*scale
            self.extent = max(self.length, self.width)
        if self.kind in ('line', 'point'):
            points = np.asarray(spec.get('points'), dtype=np.float64)
            required = 2 if self.kind == 'line' else 1
            if points.shape != (required, 3) or not np.isfinite(points).all():
                raise ValueError(f'{self.kind.title()} requires {required} finite XYZ point(s).')
            self.curve_points = points@a.T+translation
            if self.kind == 'line' and np.linalg.norm(self.curve_points[1]-self.curve_points[0]) <= 1e-15:
                raise ValueError('A reference line segment must have two distinct endpoints.')
            self.extent = max(1., float(np.ptp(self.curve_points, axis=0).max()))
        suffix = {'plane': 'finite rectangular plane' if self.finite_plane else 'infinite plane',
                  'sphere': 'sphere', 'cylinder': 'finite capped cylinder', 'box': 'finite box',
                  'line': 'finite line segment (curve)', 'circle': 'circle (curve)', 'point': 'point'}[self.kind]
        self.representation = 'Analytic '+suffix
        if self.kind in ('sphere', 'cylinder', 'box'):
            self.note = ('Exact float64 shortest distance to the analytic boundary, including cylinder caps / box faces. '
                         'Signed mode is positive outside and negative inside the closed primitive.')
        elif self.kind == 'plane':
            self.note = ('Exact float64 distance to the '+suffix+'. Signed mode follows its normal; '
                         'a tangential displacement beyond a finite plane edge has no defined sign and is excluded.')
        else:
            self.note = 'Exact float64 unsigned distance to '+suffix+'; this is not a surface deviation.'
        if not np.isfinite(self.origin).all() or not math.isfinite(self.extent) or self.extent <= 0:
            raise ValueError('Primitive transformation exceeds finite coordinate limits.')

    def _distance(self, points):
        q = (points-self.origin)@self.basis
        if self.kind == 'sphere':
            d = np.linalg.norm(q, axis=1)-self.radius
        elif self.kind == 'box':
            v = abs(q-self.size*.5)-self.size*.5
            d = np.linalg.norm(np.maximum(v, 0), axis=1)+np.minimum(np.max(v, axis=1), 0)
        elif self.kind == 'cylinder':
            v = np.c_[np.hypot(q[:, 0], q[:, 1])-self.radius, abs(q[:, 2]-self.height*.5)-self.height*.5]
            d = np.linalg.norm(np.maximum(v, 0), axis=1)+np.minimum(np.max(v, axis=1), 0)
        elif self.kind == 'plane':
            d = q[:, 2].copy()
            if self.finite_plane:
                edge = np.maximum(abs(q[:, :2])-[self.length*.5, self.width*.5], 0)
                distance = np.sqrt(np.sum(edge*edge, axis=1)+d*d)
                ambiguous = (abs(d) <= 1e-12) & (distance > 1e-12)
                d = distance*np.where(d < 0, -1., 1.)
                if self.signed:
                    d[ambiguous] = np.nan
        elif self.kind == 'circle':
            d = np.hypot(np.hypot(q[:, 0], q[:, 1])-self.radius, q[:, 2])
        elif self.kind == 'point':
            d = np.linalg.norm(points-self.curve_points[0], axis=1)
        else:
            start, end = self.curve_points; delta = end-start
            t = np.clip((points-start)@delta/np.dot(delta, delta), 0, 1)
            d = np.linalg.norm(points-(start+t[:, None]*delta), axis=1)
        return d if self.signed else abs(d)

    def evaluate(self, points):
        p = np.asarray(points, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 3:
            raise ValueError('Inspection points need an N by 3 XYZ array.')
        distance = np.full(len(p), np.nan, dtype=np.float64)
        for start in range(0, len(p), QUERY_BLOCK):
            _check(self.cancel)
            part = p[start:start+QUERY_BLOCK]; finite = np.isfinite(part).all(axis=1)
            if finite.any():
                distance[start:start+len(part)][finite] = self._distance(part[finite])
        return distance, np.isfinite(distance)


def _analytic(item, signed=False, cancel=None):
    spec = item.get('definition') or item.get('analytic_definition')
    if spec is None and item.get('kind') in ('plane', 'sphere', 'cylinder', 'box', 'point', 'line', 'circle'):
        spec = item
    if not isinstance(spec, dict) or spec.get('kind') not in ('plane', 'sphere', 'cylinder', 'box', 'point', 'line', 'circle'):
        return None
    reference = AnalyticReference(item, spec, signed, cancel)
    # Imported / edited CAD must not receive a stale primitive's analytic label.
    if isinstance(item.get('vertices'), (list, tuple, np.ndarray)):
        points = np.asarray(item['vertices'], dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError('The stored CAD vertices are invalid.')
        if spec['kind'] in ('sphere', 'cylinder', 'box') and item.get('solid') is False:
            raise ValueError('An open CAD surface cannot be treated as a closed analytic primitive.')
        check = AnalyticReference(item, spec, False, cancel)
        distance, valid = check.evaluate(points)
        coordinate_step = float(np.spacing(max(1., float(np.max(abs(points))))))
        tolerance = max(1e-8, reference.extent*1e-8, coordinate_step*8)
        if not valid.all() or np.any(distance > tolerance):
            raise ValueError('Stored CAD vertices differ from the preserved construction definition.')
    return reference


def _triangle_info(meta, reason=None):
    estimate = REFERENCE_WORKSPACE_BASE_BYTES+int(meta['vertices'])*128+int(meta['triangles'])*256
    note = ('Nearest point on the full triangle surface; no decimation. Optional sign follows '
            'nearest-face winding, not closed-volume inside/outside. Tangential edge signs are excluded. '
            'Open3D uses local float32 geometry with an explicit coordinate-step / tolerance guard; '
            'the reported step is the largest seen in the reference or evaluated queries. '
            'Reference workspace is conservatively estimated; this is not a measured whole-process RAM bound.')
    if reason:
        note += ' Analytic definition not used: '+reason
    return dict(kind='triangle_surface', representation='Triangle surface (tessellated)',
                signed_supported=True, note=note, coordinate_step_mm=None,
                vertices=int(meta['vertices']), triangles=int(meta['triangles']),
                reference_workspace_estimate_bytes=estimate,
                reference_workspace_limit_bytes=REFERENCE_WORKSPACE_LIMIT_BYTES)


def reference_info(item_or_geometry):
    """Describe supported input without constructing a triangle acceleration tree."""
    item = _unwrap(item_or_geometry); reason = None
    try:
        analytic = _analytic(item)
        if analytic is not None:
            return analytic.info
    except ValueError as exc:
        reason = str(exc)
    try:
        return _triangle_info(surface_meta(item_or_geometry), reason)
    except ValueError:
        if reason:
            raise ValueError(reason)
        return None


def _tunnel_rim_size(item):
    """Recognize the ordered ring layout emitted by CAD loft_rings."""
    definition = item.get('definition')
    if (item.get('shape') != 'tunnel_design' or item.get('solid') is not True
            or not isinstance(definition, dict) or definition.get('kind') != 'tunnel_design'):
        return None
    faces, vertices = item.get('faces', ()), item.get('vertices', ())
    try:
        first = sorted(faces[0])
        if len(first) != 3 or first[:2] != [0, 1]:
            return None
        rim = int(first[2])-1
        count = len(vertices)
        if 3 <= rim <= 512 and count >= 2*rim and count % rim == 0 and len(faces) == 2*count-4:
            return rim
    except (IndexError, TypeError, ValueError, OverflowError):
        pass
    return None


def tunnel_wall_supported(item):
    return _tunnel_rim_size(_unwrap(item)) is not None


def _loft_end_planes(vertices, faces, rim, origin):
    """Validate cap and wall ordering before interpreting closest triangle IDs."""
    wall_count = 2*(len(vertices)-rim)
    caps = faces[wall_count:]
    cap_size = rim-2
    starts = ((caps[:cap_size], 0), (caps[cap_size:], len(vertices)-rim))
    planes = []
    for triangles, start in starts:
        if len(triangles) != cap_size or np.any((triangles < start) | (triangles >= start+rim)):
            raise ValueError('Tunnel wall review needs the original ordered tunnel loft. Rebuild the model in Tunnel Designer.')
        a, b, c = vertices[triangles[0]]
        normal = _unit(np.cross(b-a, c-a), 'Tunnel end face normal')
        ring = vertices[start:start+rim]-a
        if np.max(abs(ring @ normal)) > max(1e-8, float(np.max(abs(ring)))*1e-8):
            raise ValueError('Tunnel wall review needs planar end rings. Rebuild the model in Tunnel Designer.')
        planes.append((a-origin, normal))
    for start in range(0, wall_count, FACE_BLOCK):
        triangles = faces[start:min(start+FACE_BLOCK, wall_count)]
        strip = np.arange(start, start+len(triangles)) // (2*rim)
        if np.any(triangles.min(axis=1)//rim != strip) or np.any(triangles.max(axis=1)//rim != strip+1):
            raise ValueError('Tunnel wall review needs the original ordered tunnel loft. Rebuild the model in Tunnel Designer.')
    return wall_count, planes


def _wall_hits(local, closest, primitive, rim, wall_count, planes, allowance):
    # The raycaster may return a wall triangle for an end-rim tie. Treat
    # its rim and anything beyond that end plane as unassessed as well.
    valid = primitive < wall_count
    strips = (primitive < 2*rim, primitive >= wall_count-2*rim)
    for strip, (origin, normal) in zip(strips, planes):
        use = np.flatnonzero(valid & strip)
        if len(use):
            rim_distance = np.einsum('ij,j->i', closest[use]-origin, normal)
            query_distance = np.einsum('ij,j->i', local[use]-origin, normal)
            valid[use] = (rim_distance < -allowance) & (query_distance <= allowance)
    return valid


class TriangleReference(_Reference):
    def __init__(self, item, signed=False, tolerance_mm=25, progress=None, cancel=None, reason=None, *, tunnel_rim=None):
        self.cancel = cancel; self.signed = bool(signed); self.signed_supported = True
        self.meta = surface_meta(item); self._info = _triangle_info(self.meta, reason)
        self.kind = self._info['kind']; self.representation = self._info['representation']; self.note = self._info['note']
        self.scene = None; self.coordinate_step_mm = None
        self.tunnel_rim = tunnel_rim
        if tunnel_rim is not None:
            self.representation = 'Tunnel wall (tessellated)'
            self.note = ('Nearest tunnel wall face. Negative distances point into the bore. '
                         'End cap and rim matches, matches beyond the adjacent end plane, and ambiguous signs are unassessed. '
                         'Distances are to the faceted CAD model.')
        self.query_precision_limit_mm = _number(tolerance_mm, 'Comparison tolerance', True)/10
        estimate = self._info['reference_workspace_estimate_bytes']
        if estimate > REFERENCE_WORKSPACE_LIMIT_BYTES:
            raise ValueError(f'The complete reference triangle engine is estimated to need {estimate/1024**3:.2f} GiB, '
                             f'above its {REFERENCE_WORKSPACE_LIMIT_BYTES/1024**3:.2f} GiB component budget. '
                             'Split the reference into a smaller surface; it will not be silently decimated.')
        _emit(progress, cancel, 'Preparing complete reference triangle surface')
        import mesh_geometry  # Registers the installed optional-engine directory.
        try:
            import open3d as o3d
        except ImportError as exc:
            raise ValueError('Triangle-surface inspection requires Open3D from the complete Tunnel Studio runtime.') from exc
        self.o3d = o3d
        nv, nf = self.meta['vertices'], self.meta['triangles']
        vertices = np.empty((nv, 3), dtype=np.float64)
        if 'vertices_file' in self.meta:
            with open(self.meta['vertices_file'], 'rb') as source:
                for start in range(0, nv, 100000):
                    _check(cancel); count = min(100000, nv-start)
                    part = np.fromfile(source, '<f8', count=count*3)
                    if len(part) != count*3:
                        raise ValueError('Reference vertices changed during inspection.')
                    vertices[start:start+count] = part.reshape(count, 3)
        else:
            vertices[:] = self.meta['_vertices_array']
        if not np.isfinite(vertices).all():
            raise ValueError('Reference mesh contains non-finite vertices.')
        self.origin = vertices.min(axis=0)+(vertices.max(axis=0)-vertices.min(axis=0))*.5
        local = np.empty((nv, 3), dtype=np.float32); maximum = 0.
        for start in range(0, nv, 100000):
            _check(cancel); part = vertices[start:start+100000]-self.origin
            maximum = max(maximum, float(np.max(abs(part)))); local[start:start+len(part)] = part
        self.coordinate_step_mm = float(abs(np.spacing(np.float32(maximum))))*1000
        if not np.isfinite(local).all() or self.coordinate_step_mm > self.query_precision_limit_mm:
            raise ValueError('Reference extent exceeds the local-coordinate precision allowed by this tolerance. Split the reference surface.')
        faces = np.empty((nf, 3), dtype=np.uint32)
        if 'faces_file' in self.meta:
            with open(self.meta['faces_file'], 'rb') as source:
                for start in range(0, nf, FACE_BLOCK):
                    _check(cancel); count = min(FACE_BLOCK, nf-start)
                    part = np.fromfile(source, '<u4', count=count*3)
                    if len(part) != count*3:
                        raise ValueError('Reference faces changed during inspection.')
                    faces[start:start+count] = part.reshape(count, 3)
        else:
            faces[:] = self.meta['_faces_array']
        for start in range(0, nf, FACE_BLOCK):
            _check(cancel); indices = faces[start:start+FACE_BLOCK]
            if indices.max() >= nv:
                raise ValueError('Reference triangle index is outside the vertex array.')
            q = vertices[indices]
            area = np.linalg.norm(np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), axis=1)
            if np.any(~np.isfinite(area)) or np.any(area <= 0):
                raise ValueError('Reference mesh contains a degenerate triangle.')
            q32 = local[indices].astype(np.float64)
            if np.any(np.linalg.norm(np.cross(q32[:, 1]-q32[:, 0], q32[:, 2]-q32[:, 0]), axis=1) <= 0):
                raise ValueError('A reference triangle collapses at the local float32 engine precision. Split the reference surface.')
        if tunnel_rim is not None:
            self.wall_count, self.end_planes = _loft_end_planes(vertices, faces, tunnel_rim, self.origin)
        del vertices
        _emit(progress, cancel, 'Building reference triangle distance index')
        scene = o3d.t.geometry.RaycastingScene(nthreads=2)
        scene.add_triangles(o3d.core.Tensor(local), o3d.core.Tensor(faces))
        _check(cancel)
        self.scene = scene

    @property
    def info(self):
        return {**self._info, 'signed': self.signed, 'coordinate_step_mm': self.coordinate_step_mm,
                'representation': self.representation, 'note': self.note}

    def evaluate(self, points):
        if self.scene is None:
            raise ValueError('The reference geometry has already been closed.')
        p = np.asarray(points, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 3:
            raise ValueError('Inspection points need an N by 3 XYZ array.')
        distances = np.full(len(p), np.nan, dtype=np.float64)
        for start in range(0, len(p), QUERY_BLOCK):
            _check(self.cancel); part = p[start:start+QUERY_BLOCK]
            valid = np.isfinite(part).all(axis=1)
            if not valid.any():
                continue
            local = part[valid]-self.origin
            step = float(abs(np.spacing(np.float32(np.max(abs(local))))))*1000
            if not math.isfinite(step) or step > self.query_precision_limit_mm:
                raise ValueError('Query points are too far from the reference origin for this tolerance. Check coordinates or use a smaller section.')
            self.coordinate_step_mm = max(self.coordinate_step_mm, step)
            hit = self.scene.compute_closest_points(self.o3d.core.Tensor(local.astype(np.float32)), nthreads=2)
            closest = hit['points'].numpy().astype(np.float64)
            delta = local-closest
            d = np.linalg.norm(delta, axis=1)
            if self.signed:
                side = np.einsum('ij,ij->i', delta, hit['primitive_normals'].numpy())
                ambiguous = (abs(side) < 1e-10) & (d > 1e-8)
                d *= np.where(side < 0, -1., 1.); d[ambiguous] = np.nan
            if self.tunnel_rim is not None:
                primitive = hit['primitive_ids'].numpy()
                wall = _wall_hits(local, closest, primitive, self.tunnel_rim, self.wall_count, self.end_planes,
                                  max(1e-8, 4*self.coordinate_step_mm/1000))
                d[~wall] = np.nan
            distances[start:start+len(part)][valid] = d
        return distances, np.isfinite(distances)

    def close(self):
        self.scene = None


def make_reference(item_or_geometry, work=None, signed=False, progress=None, cancel=None, tolerance_mm=25,
                   *, tunnel_wall_only=False):
    """Create an analytic or explicitly tessellated distance evaluator."""
    _number(tolerance_mm, 'Comparison tolerance', True)
    _check(cancel); item = _unwrap(item_or_geometry); reason = None
    if tunnel_wall_only:
        rim = _tunnel_rim_size(item)
        if not signed or rim is None:
            raise ValueError('Tunnel wall review requires oriented distances and a closed model built in Tunnel Designer.')
        return TriangleReference(item, signed, tolerance_mm, progress, cancel, tunnel_rim=rim)
    try:
        reference = _analytic(item, bool(signed), cancel)
        if reference is not None:
            return reference
    except ValueError as exc:
        # Cancellation must never turn into a fallback geometry computation.
        from jobdata import Cancelled
        if isinstance(exc, Cancelled):
            raise
        reason = str(exc)
    try:
        meta = surface_meta(item_or_geometry)
    except ValueError as exc:
        raise ValueError(reason or 'This object has no supported analytic definition or triangle surface. Choose a plane, solid, supported curve, or mesh.') from exc
    return TriangleReference(meta, bool(signed), tolerance_mm, progress, cancel, reason)
