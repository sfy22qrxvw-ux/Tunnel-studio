# Copyright (c) 2026 Damian Gulczynski.
"""Survey measurements with explicit geometry, persistent edits and exact units.

Point coordinates and all calculations are float64. Measurements are fixed
snapshots, never live constraints on source geometry. Imported mesh areas visit
every triangle through the bounded inspection reader. Tunnel volume clips a
working copy of a mesh at two explicit alignment chainages and validates the
resulting enclosed solid. Preview calculations cannot mutate project metadata.
"""
import copy
import csv
import hashlib
import io
import json
import math
import time
import uuid
from pathlib import Path

import numpy as np
import cad_api
import cad_geometry as g
import inspection_geometry as ig


OPERATIONS = {
    'point_point': 'Between points',
    'point_plane': 'Point to plane',
    'point_triangle': 'Point to triangle',
    'plane_plane': 'Between planes',
    'angle_points': 'Three-point angle',
    'angle_lines': 'Angle between lines',
    'angle_planes': 'Angle between planes',
    'surface': 'Measure surface',
    'geometry': 'Measure geometry',
    'tunnel_volume': 'Tunnel volume',
}
MAX_MEASURES = 10000


def vector(value, name='Point'):
    try:
        p = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(name + ' needs three finite coordinates.') from exc
    if p.shape != (3,) or not np.isfinite(p).all():
        raise ValueError(name + ' needs three finite coordinates.')
    return p


def points(value, count, name='Points'):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != count:
        raise ValueError(name + f' needs exactly {count} points.')
    return np.asarray([vector(p, name + f' {i+1}') for i, p in enumerate(value)])


def unit(value, name='Direction'):
    p = vector(value, name)
    n = float(np.linalg.norm(p))
    if not math.isfinite(n) or n <= 1e-12:
        raise ValueError(name + ' needs two different points or a non-zero normal.')
    return p/n


def plane_three(value):
    p = points(value, 3, 'Plane')
    a, b = p[1]-p[0], p[2]-p[0]
    scale = max(float(np.linalg.norm(a)), float(np.linalg.norm(b)))
    cross = np.cross(a, b)
    if scale <= 1e-10 or np.linalg.norm(cross) <= max(1e-14, scale*scale*1e-10):
        raise ValueError('The three plane points are coincident or nearly collinear. Pick a wider triangle.')
    return p[0], unit(cross, 'Plane normal'), p


def plane_patch(value, extra=None):
    """Finite display patch only; distances still use the infinite plane."""
    origin,normal,p=plane_three(value)
    x=unit(p[1]-origin);y=np.cross(normal,x)
    extent=p if extra is None else np.vstack((p,np.asarray(extra,dtype=float).reshape(-1,3)))
    uv=(extent-origin)@np.column_stack((x,y));lo=uv.min(0);hi=uv.max(0)
    pad=max(.05,float(np.max(hi-lo))*.15);lo-=pad;hi+=pad
    return [(origin+x*a+y*b).tolist() for a,b in ((lo[0],lo[1]),(hi[0],lo[1]),(hi[0],hi[1]),(lo[0],hi[1]))]


def closest_triangle(point, triangle):
    """Exact Euclidean closest point on a non-degenerate closed triangle."""
    q = vector(point)
    a, normal, p = plane_three(triangle)
    b, c = p[1], p[2]
    projected = q-normal*float(np.dot(q-a, normal))
    u, v, w = b-a, c-a, projected-a
    uu, uv, vv = float(u@u), float(u@v), float(v@v)
    wu, wv = float(w@u), float(w@v)
    det = uu*vv-uv*uv
    if det <= 0:
        raise ValueError('The triangle has no stable area.')
    beta, gamma = (vv*wu-uv*wv)/det, (uu*wv-uv*wu)/det
    if beta >= 0 and gamma >= 0 and beta+gamma <= 1:
        return projected, 'triangle interior'
    candidates = []
    for x, y in ((a, b), (b, c), (c, a)):
        d = y-x
        t = float(np.clip(np.dot(q-x, d)/np.dot(d, d), 0, 1))
        foot = x+t*d
        candidates.append((float(np.dot(q-foot, q-foot)), foot, 'triangle vertex' if t in (0, 1) else 'triangle edge'))
    _, foot, location = min(candidates, key=lambda row: row[0])
    return foot, location


def _objects(s):
    return [o for o in s.STATE.get('scene_objects', []) if o.get('kind') == 'measurement_v2']


def _context(s):
    key = (id(s.STATE), s.STATE.get('project_id'), id(s.STATE.get('scene_objects')), id(s.STATE.get('cad')))
    if getattr(s, '_measure_context_key', None) != key:
        s._measure_context_key = key
        s._measure_context = uuid.uuid4().hex
    return s._measure_context


def _revision(s):
    import scan_export
    # Geometry caches are immutable during application operations. Include
    # their on-disk fingerprints so a replaced/missing source cannot be saved
    # under an old preview, even if its scene ID has not changed.
    axis = s.STATE.get('axis')
    axis_stamp = None if axis is None else [axis.name, hashlib.sha256(axis.a.tobytes()).hexdigest()]
    values = [cad_api.current(s)['revision'], axis_stamp, _objects(s),
              [(x['id'], scan_export.signature(x)) for x in s.STATE.get('job_items', [])]]
    return hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _check_request(s, body):
    if body.get('context') != _context(s) or body.get('revision') != _revision(s):
        raise ValueError('The workspace changed. Reopen this measurement and calculate again.')


def _cad(s, ident):
    return cad_api.get_object(cad_api.current(s), ident)


def _plane(s, raw):
    if not isinstance(raw, dict):
        raise ValueError('Choose a plane or define it with three points.')
    if raw.get('id'):
        obj = _cad(s, raw['id'])
        vertices = np.asarray(obj['vertices'], dtype=np.float64)
        if len(vertices) < 3:
            raise ValueError('Choose a planar surface, not a point or line.')
        if raw.get('face_index') is not None:
            face = _face(obj, raw['face_index'])
            p = vertices[face]
        else:
            origin = vertices[0]
            local = vertices-origin
            _, singular, basis = np.linalg.svd(local, full_matrices=False)
            if len(singular) < 2 or singular[1] <= max(1e-10, singular[0]*1e-9):
                raise ValueError('This geometry does not define a stable plane.')
            normal = basis[-1]
            span = max(float(np.linalg.norm(local, axis=1).max()), 1)
            if float(np.max(np.abs(local@normal))) > max(1e-7, span*1e-8):
                raise ValueError('This model is not planar. Choose a plane or enter three plane points.')
            x = basis[0]
            y = np.cross(normal, x)
            p = np.asarray([origin, origin+x, origin+y])
        origin, normal, p = plane_three(p)
        return origin, normal, p, obj['name']
    origin, normal, p = plane_three(raw.get('points'))
    return origin, normal, p, 'Three-point plane'


def _face(obj, index):
    if type(index) is not int or index < 0 or index >= len(obj.get('faces', [])):
        raise ValueError('Choose a valid triangle number from this model (starting at 1 in the tool).')
    face = obj['faces'][index]
    if len(face) != 3:
        raise ValueError('This model face is not triangular.')
    return face


def _triangle(s, raw):
    if not isinstance(raw, dict):
        raise ValueError('Choose a model triangle or define three triangle points.')
    if raw.get('id'):
        obj = _cad(s, raw['id'])
        p = np.asarray(obj['vertices'], dtype=np.float64)[_face(obj, raw.get('face_index', 0))]
        plane_three(p)
        return p, obj['name'] + ' · triangle ' + str(raw.get('face_index', 0)+1)
    p = points(raw.get('points'), 3, 'Triangle')
    plane_three(p)
    return p, 'Three-point triangle'


def _line(s, raw):
    if not isinstance(raw, dict):
        raise ValueError('Choose a line or define it with two points.')
    if raw.get('id'):
        obj = _cad(s, raw['id'])
        p = points(obj['vertices'], 2, 'Line geometry')
        name = obj['name']
    else:
        p = points(raw.get('points'), 2, 'Line')
        name = 'Two-point line'
    unit(p[1]-p[0], 'Line')
    return p, name


def _metric(label, value, unit_name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('The coordinates produce a non-finite measurement. Check their units and magnitude.')
    return dict(label=label, value=value, unit=unit_name)


def _cancel(s):
    if callable(getattr(s, '_cancel_requested', None)) and s._cancel_requested():
        return True
    flag = getattr(s, 'JOB_CANCEL_FILE', None)
    return bool(flag and flag.exists())


def _surface(s, raw, progress, cancel):
    ident = raw.get('object_id')
    obj = next((o for o in cad_api.current(s)['objects'] if o['id'] == ident), None)
    if obj is None:
        obj = next((o for o in s.STATE.get('job_items', []) if o['id'] == ident and o['kind'] == 'mesh'), None)
    if obj is None:
        raise ValueError('Select an existing CAD object or imported mesh.')
    cad = 'meta' not in obj
    metrics, notes = [], []
    p = np.asarray(obj['vertices'], dtype=np.float64) if cad else None
    faces = obj.get('faces', []) if cad else None
    result_points = []
    if cad and not len(faces):
        if len(p) == 1:
            metrics.extend(_metric(label, value, 'm') for label, value in zip(('E / X', 'N / Y', 'H / Z'), p[0]))
            result_points = p.tolist()
        else:
            length = math.fsum(map(float, np.linalg.norm(np.diff(p, axis=0), axis=1)))
            if obj.get('closed'):
                length += float(np.linalg.norm(p[-1]-p[0]))
            metrics.append(_metric('Curve length', length, 'm'))
            result_points = [p[0].tolist(), p[-1].tolist()]
            if obj.get('closed'):
                origin, normal, _, _ = _plane(s, {'id': ident})
                local = p-origin
                area = abs(float(np.dot(np.cross(local, np.roll(local, -1, axis=0)).sum(0), normal)))*.5
                metrics.append(_metric('Enclosed planar area', area, 'm²'))
                if raw['operation'] == 'surface':
                    metrics = metrics[-1:]+metrics[:-1]
            notes.append('Curve measurements use the stored vertices and segments. Curved outlines use their stored tessellation.')
        if raw['operation'] == 'surface' and len(p) == 1:
            raise ValueError('A point has no surface area. Choose a closed planar profile or triangle surface.')
        if raw['operation'] == 'surface' and not obj.get('closed'):
            raise ValueError('An open line has no enclosed area. Choose a closed planar profile or triangle surface.')
    else:
        meta = ig.surface_meta(obj)
        area_total = correction = 0.
        lo = np.full(3, np.inf)
        hi = -lo
        for start, triangles, area in ig._triangle_batches(meta, progress, cancel):
            part = math.fsum(map(float, area))
            corrected = part-correction
            total = area_total+corrected
            correction = (total-area_total)-corrected
            area_total = total
            lo = np.minimum(lo, triangles.min(axis=(0, 1)))
            hi = np.maximum(hi, triangles.max(axis=(0, 1)))
            ig._emit(progress, cancel, f'Measuring every triangle · {start+len(area):,}/{meta["triangles"]:,}')
        metrics.extend([_metric('Triangle surface area', area_total, 'm²'), _metric('Triangles', meta['triangles'], 'count')])
        result_points = [[float(hi[j] if i & (1 << j) else lo[j]) for j in range(3)] for i in (0, 1, 3, 2, 0, 4, 5, 7, 6, 4)]
        if raw['operation'] == 'geometry' and cad and obj.get('solid'):
            ig._emit(progress, cancel, 'Validating closed solid for volume…')
            manifold = g.as_manifold(obj, p[0])
            volume = float(manifold.volume())
            if not math.isfinite(volume) or volume <= 0:
                raise ValueError('The selected model does not have a positive validated solid volume.')
            metrics.insert(0, _metric('Closed solid volume', volume, 'm³'))
            notes.append('Volume uses the validated oriented triangle solid. Curved primitives use their stored tessellation.')
        elif raw['operation'] == 'geometry':
            notes.append('Volume is unavailable for this open or imported surface. No missing surfaces are invented to close it. Use a validated closed CAD solid for volume.')
        notes.append('Area includes every stored triangle, including any reconstructed faces. It is the sum of triangle areas, not a projected footprint.')
    bounds = (obj.get('minimum'), obj.get('maximum')) if cad else (obj['meta'].get('minimum'), obj['meta'].get('maximum'))
    if all(v is not None for v in bounds):
        extent = vector(bounds[1])-vector(bounds[0])
        metrics.extend(_metric(label, value, 'm') for label, value in zip(('E / X extent', 'N / Y extent', 'H / Z extent'), extent))
    return metrics, result_points, notes, obj['name']


def calculate(s, raw, progress=lambda message: None, cancel=lambda: False):
    """Pure measurement calculation; no project state or source file writes."""
    if not isinstance(raw, dict) or raw.get('operation') not in OPERATIONS:
        raise ValueError('Choose an available measurement tool.')
    op = raw['operation']
    result_points, edges, metrics, notes, inputs, sources = [], [], [], [], {'operation': op}, []
    ig._emit(progress, cancel, 'Measuring · checking coordinates and references…')
    if op == 'point_point':
        p = points(raw.get('points'), 2)
        d = p[1]-p[0]
        length = float(np.linalg.norm(d))
        if length <= 1e-12:
            raise ValueError('Pick two different points for a distance.')
        metrics = [_metric('3D distance', length, 'm'), _metric('Horizontal distance', np.hypot(d[0], d[1]), 'm'),
                   _metric('Height difference · second minus first', d[2], 'm'),
                   _metric('E / X difference', d[0], 'm'), _metric('N / Y difference', d[1], 'm')]
        inputs['points'] = result_points = p.tolist()
    elif op in ('point_plane', 'point_triangle'):
        q = vector(raw.get('point'))
        inputs['point'] = q.tolist()
        if op == 'point_plane':
            origin, normal, p, name = _plane(s, raw.get('plane'))
            signed = float(np.dot(q-origin, normal))
            foot = q-normal*signed
            metrics = [_metric('Perpendicular distance', abs(signed), 'm'), _metric('Signed distance along plane normal', signed, 'm')]
            inputs['plane'] = {'points': p.tolist()}
            notes.append('Distance is to the infinite plane. The signed value follows its stored normal; it is not inside/outside classification.')
        else:
            p, name = _triangle(s, raw.get('triangle'))
            foot, location = closest_triangle(q, p)
            metrics = [_metric('Shortest distance to finite triangle', np.linalg.norm(q-foot), 'm')]
            inputs['triangle'] = {'points': p.tolist()}
            notes.append('The nearest location is on the ' + location + '. The triangle has finite edges.')
        sources.append(name)
        result_points = [q.tolist(), foot.tolist()]
        edges = [[p[i].tolist(), p[(i+1) % 3].tolist()] for i in range(3)]
    elif op in ('plane_plane', 'angle_planes'):
        a, na, pa, name_a = _plane(s, raw.get('plane_a'))
        b, nb, pb, name_b = _plane(s, raw.get('plane_b'))
        inputs.update(plane_a={'points': pa.tolist()}, plane_b={'points': pb.tolist()})
        sources.extend((name_a, name_b))
        angle = float(np.degrees(np.arccos(np.clip(abs(np.dot(na, nb)), 0, 1))))
        if op == 'angle_planes':
            metrics = [_metric('Smaller plane angle', angle, '°')]
            notes.append('Planes are undirected: the smaller angle is between 0° and 90°.')
            result_points = [a.tolist(), b.tolist()]
        else:
            parallel = float(np.linalg.norm(np.cross(na, nb))) <= 1e-10
            distance = abs(float(np.dot(b-a, na))) if parallel else 0.
            metrics = [_metric('Infinite plane separation', distance, 'm'), _metric('Smaller plane angle', angle, '°')]
            foot = b-na*float(np.dot(b-a, na))
            result_points = [b.tolist(), foot.tolist()] if parallel else []
            notes.append('Parallel infinite planes have this perpendicular separation.' if parallel else 'The infinite planes intersect, so their shortest separation is zero. This is not the gap between finite plane patches.')
        edges = [[p[i].tolist(), p[(i+1) % 3].tolist()] for p in (pa, pb) for i in range(3)]
    elif op == 'angle_points':
        p = points(raw.get('points'), 3)
        u, v = unit(p[0]-p[1], 'First angle arm'), unit(p[2]-p[1], 'Second angle arm')
        value = np.degrees(np.arctan2(np.linalg.norm(np.cross(u, v)), np.dot(u, v)))
        metrics = [_metric('Angle at middle point B', value, '°')]
        inputs['points'] = result_points = p.tolist()
        notes.append('The angle is at the second point B, from B→A to B→C, between 0° and 180°.')
    elif op == 'angle_lines':
        pa, name_a = _line(s, raw.get('line_a'))
        pb, name_b = _line(s, raw.get('line_b'))
        u, v = unit(pa[1]-pa[0]), unit(pb[1]-pb[0])
        value = np.degrees(np.arctan2(np.linalg.norm(np.cross(u, v)), abs(np.dot(u, v))))
        metrics = [_metric('Smaller line angle', value, '°')]
        inputs.update(line_a={'points': pa.tolist()}, line_b={'points': pb.tolist()})
        sources.extend((name_a, name_b))
        edges = [pa.tolist(), pb.tolist()]
        result_points = []
        notes.append('Lines are undirected: the smaller angle is between 0° and 90°. Their positions do not affect this direction angle.')
    elif op == 'tunnel_volume':
        import tunnel_volume
        ident = raw.get('object_id')
        mesh = next((item for item in s.STATE.get('job_items', [])
                     if item.get('id') == ident and item.get('kind') == 'mesh'), None)
        if mesh is None:
            raise ValueError('Select a tunnel triangle mesh in the project.')
        start = ig._number(raw.get('start_chainage'), 'Start chainage')
        end = ig._number(raw.get('end_chainage'), 'End chainage')
        axis = s.STATE.get('axis')
        measured = tunnel_volume.measure(mesh, axis, start, end, progress, cancel)
        metrics = [_metric('Enclosed tunnel volume', measured['volume_m3'], 'm³'),
                   _metric('Start chainage', start, 'm'), _metric('End chainage', end, 'm'),
                   _metric('Start section area', measured['start_area_m2'], 'm²'),
                   _metric('End section area', measured['end_area_m2'], 'm²')]
        inputs.update(object_id=ident, start_chainage=start, end_chainage=end)
        result_points = [measured['start_centre'], measured['end_centre']]
        sources.extend([mesh['name'], axis.name])
        notes.append('Enclosed internal space between perpendicular sections. '
                     'Volume includes reconstructed lining where present and assumes an end plane at each chainage.')
        notes.append(f"{measured['wall_triangles']:,} clipped wall triangles and "
                     f"{measured['cap_triangles']:,} cap triangles formed one closed shell with zero boundary edges.")
    else:
        metrics, result_points, notes, name = _surface(s, raw, progress, cancel)
        sources.append(name)
        inputs['object_id'] = raw.get('object_id')
    ig._emit(progress, cancel, 'Measurement ready · review the result before saving.')
    result = dict(schema=1, operation=op, title=OPERATIONS[op], inputs=inputs,
                  metrics=metrics, value=metrics[0]['value'], unit=metrics[0]['unit'],
                  points=result_points, edges=edges, notes=notes, sources=sources,
                  coordinate_frame='project/world', coordinate_units='metres', fixed_snapshot=True)
    if op == 'tunnel_volume':
        result['volume_check'] = measured
    json.dumps(result, allow_nan=False)
    return result


def _metadata(body, result, previous=None):
    previous = previous or {}
    out = dict(name=str(body.get('name', previous.get('name', result['title']))).strip()[:150],
               comment=str(body.get('comment', previous.get('comment', ''))).strip()[:2000],
               show_label=bool(body.get('show_label', previous.get('show_label', True))))
    if not out['name']:
        raise ValueError('Enter a measurement name.')
    nominal = body.get('nominal', previous.get('nominal'))
    tolerance = body.get('tolerance', previous.get('tolerance'))
    if nominal in ('', None) and tolerance in ('', None):
        out.update(nominal=None, tolerance=None, within_tolerance=None)
    else:
        try:
            nominal, tolerance = float(nominal), float(tolerance)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('Enter both a nominal value and a non-negative tolerance, or leave both blank.') from exc
        if not math.isfinite(nominal) or not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('Nominal and tolerance must be finite, with tolerance zero or positive.')
        difference = abs(result['value']-nominal)
        # Float64 roundoff at an exactly specified tolerance boundary should
        # not turn the comparison into a spurious failure.
        slack = np.finfo(np.float64).eps*max(1., abs(result['value']), abs(nominal))*4
        out.update(nominal=nominal, tolerance=tolerance, within_tolerance=bool(difference <= tolerance+slack))
    return out


def public_record(obj):
    d = obj['data']
    value = d['value']
    suffix = '' if d.get('within_tolerance') is None else (' · within tolerance' if d['within_tolerance'] else ' · outside tolerance')
    planes=[]
    if d.get('operation')=='point_plane' and d.get('inputs',{}).get('plane',{}).get('points'):
        planes=[plane_patch(d['inputs']['plane']['points'],d.get('points',[]))]
    return dict(id=obj['id'], kind='measurement_v2', name=obj['name'], visible=obj.get('visible', True),
                points=copy.deepcopy(d.get('points', [])), edges=copy.deepcopy(d.get('edges', [])),
                planes=planes,
                editable=True, removable=True, source_name=' / '.join(d.get('sources', [])),
                summary=f'{d["title"]} · {value:.6g} {d["unit"]}{suffix} · saved coordinates',
                value=value, unit=d['unit'], show_label=d.get('show_label', True),
                label=f'{obj["name"]} · {value:.6g} {d["unit"]}', data=copy.deepcopy(d))


def state(s):
    references = []
    for obj in cad_api.current(s)['objects']:
        vertices, faces = obj.get('vertices', []), obj.get('faces', [])
        references.append(dict(id=obj['id'], name=obj['name'], kind='cad', vertices=len(vertices),
                               triangles=len(faces), line=len(vertices) == 2, plane=len(vertices) >= 3,
                               solid=bool(obj.get('solid'))))
    references.extend(dict(id=obj['id'], name=obj['name'], kind='mesh', vertices=obj['meta'].get('vertices', 0),
                           triangles=obj['meta'].get('triangles', 0), line=False, plane=False, solid=False)
                      for obj in s.STATE.get('job_items', []) if obj['kind'] == 'mesh')
    revision, context = _revision(s), _context(s)
    preview = getattr(s, '_measure_preview', None)
    if preview and (preview['revision'] != revision or preview['context'] != context):
        preview = None
    axis = s.STATE.get('axis')
    alignment = None if axis is None else dict(name=axis.name, start=float(axis.a[0, 0]), end=float(axis.a[-1, 0]))
    return dict(context=context, revision=revision, alignment=alignment, measurements=[public_record(o) for o in _objects(s)],
                references=references, preview=copy.deepcopy(preview), operations=OPERATIONS)


def _commit(s, objects):
    previous = s.STATE.get('scene_objects')
    s.STATE['scene_objects'] = objects
    try:
        s.save()
    except BaseException:
        if previous is None:
            s.STATE.pop('scene_objects', None)
        else:
            s.STATE['scene_objects'] = previous
        raise


def _saved(s, ident):
    value = next((o for o in _objects(s) if o['id'] == ident), None)
    if value is None:
        raise ValueError('This saved measurement is no longer available.')
    return value


def export_bytes(values, fmt):
    if fmt == 'json':
        return json.dumps(dict(format='Tunnel Studio measurements', version=1, coordinate_units='metres',
                               coordinate_frame='project/world', measurements=values), ensure_ascii=False, indent=2,
                          allow_nan=False).encode('utf8')
    if fmt != 'csv':
        raise ValueError('Choose measurement CSV or JSON.')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['measurement_id', 'name', 'operation', 'metric', 'value', 'unit', 'nominal_primary',
                     'tolerance_primary', 'within_tolerance', 'comment', 'fixed_coordinate_snapshot'])
    for row in values:
        d = row['data']
        for i, metric in enumerate(d['metrics']):
            writer.writerow([row['id'], row['name'], d['title'], metric['label'], format(metric['value'], '.17g'), metric['unit'],
                             '' if i or d.get('nominal') is None else format(d['nominal'], '.17g'),
                             '' if i or d.get('tolerance') is None else format(d['tolerance'], '.17g'),
                             '' if i or d.get('within_tolerance') is None else str(d['within_tolerance']).lower(),
                             d.get('comment', ''), 'true'])
    return ('\ufeff'+stream.getvalue()).encode('utf8')


def get(s, h, path, args):
    if path != '/api/measures':
        return False
    h.send(state(s))
    return True


def post(s, h, path, body):
    if path not in ('/api/measure-chainage', '/api/measure-plane', '/api/measure-preview', '/api/measure-action', '/api/measure-export'):
        return False
    if s.JOB.get('busy'):
        raise ValueError('Wait for the current operation to finish.')
    _check_request(s, body)
    if path == '/api/measure-chainage':
        axis = s.STATE.get('axis')
        if axis is None:
            raise ValueError('Load the tunnel alignment before picking a chainage.')
        point = vector(body.get('point'), 'Picked point')
        station = float(axis.project(point.reshape(1, 3))[0, 0])
        if not math.isfinite(station):
            raise ValueError('Pick a point within the alignment chainage range.')
        h.send(dict(chainage=station))
        return True
    if path == '/api/measure-plane':
        origin,normal,points,name=_plane(s,body.get('plane'))
        h.send(dict(origin=origin.tolist(),normal=normal.tolist(),points=points.tolist(),name=name));return True
    if path == '/api/measure-preview':
        raw = copy.deepcopy(body.get('input'))
        captured_revision, captured_context = _revision(s), _context(s)
        if body.get('live') is True:
            if not isinstance(raw,dict) or raw.get('operation')!='point_plane':
                raise ValueError('Live calculation is available for point-to-plane measurements.')
            value=calculate(s,raw,lambda _:None,lambda:False)
            if _revision(s)!=captured_revision or _context(s)!=captured_context:
                raise ValueError('The workspace changed while measuring. Reopen the measurement.')
            s._measure_preview=dict(token=uuid.uuid4().hex,result=value,revision=captured_revision,context=captured_context)
            h.send(dict(preview=copy.deepcopy(s._measure_preview)));return True
        s._measure_preview = None
        def run():
            value = calculate(s, raw, s.progress, lambda: _cancel(s))
            if _revision(s) != captured_revision or _context(s) != captured_context:
                raise ValueError('The workspace changed while measuring. Calculate again.')
            s._measure_preview = dict(token=uuid.uuid4().hex, result=value, revision=captured_revision, context=captured_context)
        s.start_job(run, 'measurement', native_progress=True)
        h.send(dict(started=True))
        return True
    if path == '/api/measure-export':
        ids = body.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise ValueError('Choose saved measurements to export.')
        # Reuse exactly the public rows consumed by scan_export.current so
        # option overrides and future added fields cannot break export checks.
        import scene_objects
        public = {o['id']: o for o in scene_objects.public(s)}
        for ident in ids:
            _saved(s, ident)
        values = [public[ident] for ident in ids]
        fmt = body.get('format')
        content = export_bytes(values, fmt)
        ident = uuid.uuid4().hex
        destination = Path(s.WORK)/('measurements_'+ident+'.'+fmt)
        temporary = destination.with_suffix('.tmp')
        previous = s.STATE.get('export_ready')
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
            s.STATE['export_ready'] = dict(id=ident, file=str(destination), format=fmt, source='objects',
                                          name='Tunnel_Studio_Measurements.'+fmt, count=None,
                                          object_ids=ids, object_revision=hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest(),
                                          warnings=['Fixed saved measurement snapshots; values use the units stated in each row.'])
            s.save()
        except BaseException:
            s.STATE['export_ready'] = previous
            destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        h.send(dict(prepared=True, id=ident, filename='Tunnel_Studio_Measurements.'+fmt, bytes=len(content)))
        return True
    action = body.get('action')
    objects = copy.deepcopy(s.STATE.get('scene_objects', []))
    if action == 'save':
        preview = getattr(s, '_measure_preview', None)
        if not preview or body.get('token') != preview['token'] or preview['revision'] != _revision(s) or preview['context'] != _context(s):
            raise ValueError('Calculate and review this measurement before saving it.')
        data = copy.deepcopy(preview['result'])
        data.update(_metadata(body, data), created_at=time.time(), updated_at=time.time())
        previous = _saved(s, body['id']) if body.get('id') else None
        if previous:
            data['created_at'] = previous['data'].get('created_at', data['created_at'])
            objects = [o for o in objects if o['id'] != previous['id']]
        elif len(_objects(s)) >= MAX_MEASURES:
            raise ValueError('This project already has 10,000 measurements. Export and remove unneeded measures first.')
        ident = previous['id'] if previous else 'measurement:'+uuid.uuid4().hex
        objects.append(dict(id=ident, kind='measurement_v2', name=data.pop('name'), visible=previous.get('visible', True) if previous else True, data=data))
    elif action in ('update', 'remove', 'visibility'):
        previous = _saved(s, body.get('id'))
        ident = previous['id']
        obj = next(o for o in objects if o['id'] == ident)
        if action == 'remove':
            objects.remove(obj)
        elif action == 'visibility':
            obj['visible'] = bool(body.get('visible', not obj.get('visible', True)))
        else:
            meta = _metadata(body, obj['data'], dict(obj['data'], name=obj['name']))
            obj['name'] = meta.pop('name')
            obj['data'].update(meta, updated_at=time.time())
    else:
        raise ValueError('Choose save, update, remove or visibility.')
    _commit(s, objects)
    s._measure_preview = None
    h.send(dict(saved=True, id=ident))
    return True
