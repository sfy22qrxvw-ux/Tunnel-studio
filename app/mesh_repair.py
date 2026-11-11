# Copyright (c) 2026 Damian Gulczynski.
"""Conservative, reviewable repair of selected mesh boundary loops.

Original coordinates and faces are immutable. A cap reuses existing boundary
vertices and is always labelled inferred, even when nearby cloud points support
its sampled locations. Overall extent is the largest 3D boundary-vertex distance,
not the width of a long gap or a rotation-dependent bounding-box diagonal.
"""
from project_data_layout import folder as asset_folder
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid

import numpy as np
from scipy.spatial import cKDTree, distance
import cad_geometry
import mesh_geometry
import jobdata

MAX_TRIANGLES = 2_000_000
MAX_VERTICES = 2_000_000
MAX_BOUNDARY_EDGES = 200_000
MAX_LOOP_VERTICES = 512
MAX_HOLES = 6_000
MAX_SUPPORT_QUERIES = 60_000
UNSUPPORTED_EXTENT_M = .5
PRESERVE_MESSAGE = ('Workspace mesh openings are preserved. Use Meshing → Inspect openings '
                    'to review circular rims on the selected mesh.')


def _check(cancel):
    if cancel():
        raise jobdata.Cancelled('Mesh repair cancelled. The original mesh is unchanged.')


def parameters(settings=None):
    settings = settings or {}
    limits = {'max_extent_m': (.5, .001, 5.), 'max_plane_deviation_mm': (10., .1, 50.),
              'max_normal_angle_deg': (60., 1., 80.), 'support_distance_m': (.05, .001, .5)}
    out = {}
    for key, (default, lower, upper) in limits.items():
        value = float(settings.get(key, default))
        if not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'{key} must be between {lower:g} and {upper:g}.')
        out[key] = value
    return out


def _arrays(vertices, faces, cancel):
    v, f = np.asarray(vertices, dtype=np.float64), np.asarray(faces)
    if v.ndim != 2 or v.shape[1] != 3 or not 3 <= len(v) <= MAX_VERTICES:
        raise ValueError(f'Interactive hole repair supports three to {MAX_VERTICES:,} vertices. Reduce a separate mesh copy first if necessary.')
    if f.ndim != 2 or f.shape[1] != 3 or not 1 <= len(f) <= MAX_TRIANGLES or not np.issubdtype(f.dtype, np.integer):
        raise ValueError(f'Interactive hole repair supports one to {MAX_TRIANGLES:,} triangular faces. Reduce a separate mesh copy first if necessary.')
    if f.min() < 0 or f.max() >= len(v):
        raise ValueError('The source mesh contains an invalid vertex index.')
    for start in range(0, len(v), 100_000):
        _check(cancel)
        if not np.isfinite(v[start:start+100_000]).all():
            raise ValueError('The source mesh contains non-finite coordinates.')
    return v, f.astype(np.uint32, copy=False)


def _edge_data(faces):
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    low, high = np.minimum(edges[:, 0], edges[:, 1]), np.maximum(edges[:, 0], edges[:, 1])
    keys = (low.astype(np.uint64) << np.uint64(32)) | high.astype(np.uint64)
    unique, first, counts = np.unique(keys, return_index=True, return_counts=True)
    chosen = first[counts == 1]
    boundary = edges[chosen]
    return boundary, chosen % len(faces), unique, counts


def _boundary_graph(faces, cancel):
    _check(cancel)
    boundary, owners, unique_edges, edge_counts = _edge_data(faces)
    if len(boundary) > MAX_BOUNDARY_EDGES:
        raise ValueError(f'This mesh has {len(boundary):,} open edges. Inspect a smaller section '
                         f'(interactive limit {MAX_BOUNDARY_EDGES:,}).')
    adjacency, directed = {}, {}
    for (a, b), owner in zip(boundary, owners):
        a, b = int(a), int(b)
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
        directed[(min(a, b), max(a, b))] = (a, b, int(owner))
    nonmanifold = unique_edges[edge_counts > 2]
    vertices = set(int(v) for v in np.r_[nonmanifold >> 32, nonmanifold & 0xffffffff])
    return adjacency, directed, vertices, len(nonmanifold)


def _opening_rows(vertices, adjacency, cancel):
    visited = set()
    count = 0
    for start in sorted(adjacency):
        if start in visited:
            continue
        _check(cancel)
        count += 1
        if count > MAX_HOLES:
            raise ValueError(f'More than {MAX_HOLES:,} separate openings need review. Inspect a smaller section.')
        component, stack = [], [start]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(adjacency[current])
        ids = np.asarray(component, dtype=np.uint32)
        points = vertices[ids]
        centre = points[0] + np.mean(points - points[0], axis=0)
        row = dict(id=f'hole-{count}', vertices=len(ids), centre=centre.tolist(), status='unfilled',
                   reason='', reason_code='', extent_m=None, width_m=None, plane_deviation_m=None,
                   support_fraction=None, support_samples=0, support_status='No reference cloud selected',
                   _ordered=False, _loop=ids)
        if any(len(adjacency[x]) != 2 for x in component):
            row.update(reason_code='branched_boundary', reason='Boundary branches or ends.')
        elif len(ids) > MAX_LOOP_VERTICES:
            row.update(reason_code='complex_boundary', reason=f'More than {MAX_LOOP_VERTICES} boundary vertices.')
        else:
            previous, current = start, min(adjacency[start])
            loop = [start]
            while current != start:
                loop.append(current)
                following = next(x for x in adjacency[current] if x != previous)
                previous, current = current, following
            row.update(_ordered=True, _loop=np.asarray(loop, dtype=np.uint32))
        yield row


def _vertex_fan_ok(vertex, incident, faces, boundary_neighbours):
    """A manifold boundary vertex has one connected link path, not two fans."""
    links = {}
    for face_id in incident:
        others = [int(x) for x in faces[face_id] if x != vertex]
        if len(others) != 2 or others[0] == others[1]:
            return False
        a, b = others
        links.setdefault(a, []).append(b); links.setdefault(b, []).append(a)
    ends = {v for v, neighbours in links.items() if len(neighbours) == 1}
    if ends != set(boundary_neighbours) or any(len(n) not in (1, 2) for n in links.values()):
        return False
    seen, stack = set(), [next(iter(links))]
    while stack:
        cur = stack.pop()
        if cur in seen: continue
        seen.add(cur); stack.extend(links[cur])
    return len(seen) == len(links)


def _overlaps_existing(v, f, candidates, centre, basis, polygon, plane_band, area_tol, cancel, shapely):
    """Conservatively reject existing faces crossing the proposed patch slab.

    Coplanar area overlap and non-coplanar faces crossing the thin patch slab are
    checked in the same local frame. Boundary-only contact has zero area and is
    allowed. This test may preserve an uncertain folded region; it never assumes
    that a projected overlap is safe to fill.
    """
    for start in range(0, len(candidates), 256):
        _check(cancel)
        ids = np.asarray(candidates[start:start+256], dtype=np.int64)
        q = (v[f[ids]] - centre) @ basis.T
        hit = (q[:, :, 2].min(1) <= plane_band) & (q[:, :, 2].max(1) >= -plane_band)
        for tri in q[hit, :, :2]:
            other = shapely.Polygon(tri)
            if other.area > area_tol and polygon.intersection(other).area > area_tol:
                return True
            if other.area<=area_tol:
                # A vertical existing face can cross the cap while projecting
                # to a line, so area-only tests would miss that intersection.
                line=shapely.MultiPoint(tri).convex_hull
                margin=max(1e-10,math.sqrt(area_tol))
                if line.intersection(polygon.buffer(-margin)).length>margin:return True
    return False


def _support_locations(v, faces, spacing, frame, maximum=4096):
    # A planar grid avoids excessive sampling along diagonals of narrow, long
    # triangulations. Lift every grid location onto its actual 3D cap triangle.
    centre,basis,polygon=frame; low=np.array(polygon.bounds[:2]); high=np.array(polygon.bounds[2:])
    counts=np.maximum(2,np.ceil((high-low)/spacing).astype(int)+1)
    if int(counts.prod())>maximum*4:return None
    xx,yy=np.meshgrid(np.linspace(low[0],high[0],counts[0]),np.linspace(low[1],high[1],counts[1]))
    uv=np.column_stack([xx.ravel(),yy.ravel()]); _,shapely=cad_geometry.engines()
    uv=uv[shapely.contains_xy(polygon,uv[:,0],uv[:,1])]
    if len(uv)+len(np.unique(faces))+len(faces)>maximum:return None
    points=[];remaining=np.ones(len(uv),bool)
    for face in faces:
        triangle=v[face];projected=(triangle-centre)@basis[:2].T
        matrix=np.column_stack([projected[1]-projected[0],projected[2]-projected[0]])
        if abs(np.linalg.det(matrix))<1e-16:return None
        ids=np.flatnonzero(remaining)
        weights=np.linalg.solve(matrix,(uv[ids]-projected[0]).T).T
        chosen=(weights>=-1e-10).all(1)&(weights.sum(1)<=1+1e-10)
        if chosen.any():
            w=weights[chosen];points.extend(triangle[0]+w[:,0,None]*(triangle[1]-triangle[0])+w[:,1,None]*(triangle[2]-triangle[0]));remaining[ids[chosen]]=False
        points.append(triangle[0]+(triangle[1]-triangle[0]+triangle[2]-triangle[0])/3)
    if remaining.any():return None
    points.extend(v[np.unique(faces)])
    return np.unique(np.asarray(points),axis=0)


def analyse(vertices, faces, settings=None, reference_batches=None, progress=lambda _: None, cancel=lambda: False,
            *, include_local_geometry=False):
    """Inspect every boundary component; return bounded review records and caps.

    reference_batches, when supplied, yields retained XYZ arrays. Cloud support
    checks all needed source batches until every sampled patch location has a
    neighbour within support_distance_m; they do not subsample the source cloud.
    The checks establish nearby point support, not survey accuracy.
    """
    cfg = parameters(settings); v, f = _arrays(vertices, faces, cancel)
    _, shapely = cad_geometry.engines()
    progress('Inspecting mesh edges and boundary connectivity…'); _check(cancel)
    adjacency, directed, nonmanifold_vertices, nonmanifold_edges = _boundary_graph(f, cancel)
    normals = np.empty((len(f), 3)); centres = np.empty((len(f), 3)); radii = np.empty(len(f))
    for start in range(0, len(f), 100_000):
        _check(cancel); q = v[f[start:start+100_000]]
        n = np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]); length = np.linalg.norm(n, axis=1)
        if np.any(length <= 1e-14):
            raise ValueError('The source mesh has zero-area triangles. Clean a separate copy before hole repair.')
        normals[start:start+len(q)] = n/length[:, None]
        centres[start:start+len(q)] = q[:, 0]+(q[:, 1]-q[:, 0]+q[:, 2]-q[:, 0])/3
        radii[start:start+len(q)] = np.linalg.norm(q-centres[start:start+len(q), None], axis=2).max(1)
    tree = cKDTree(centres)
    max_face_radius = float(radii.max())
    flat = f.reshape(-1); incidence_order = np.argsort(flat, kind='stable'); sorted_vertices = flat[incidence_order]
    records, patches, frames = [], {}, {}
    plane_limit = cfg['max_plane_deviation_mm']/1000
    cosine_limit = math.cos(math.radians(cfg['max_normal_angle_deg']))
    for row in _opening_rows(v, adjacency, cancel):
        records.append(row)
        if len(records)%20==1:progress(f'Inspecting opening {len(records):,}; {len(patches):,} local caps available…')
        def reject(code, message, preserved=False):
            row.update(reason_code=code, reason=message, status='preserved' if preserved else 'unfilled')
        if not row['_ordered']:
            continue
        loop = row['_loop'].tolist()
        centre = np.asarray(row['centre'])
        if any(x in nonmanifold_vertices for x in loop):
            reject('nonmanifold_boundary', 'A boundary vertex touches a non-manifold edge.'); continue
        good_fan = True
        for vertex in loop:
            left, right = np.searchsorted(sorted_vertices, [vertex, vertex+1])
            incident = incidence_order[left:right]//3
            if not _vertex_fan_ok(vertex, incident, f, adjacency[vertex]): good_fan = False; break
        if not good_fan:
            reject('nonmanifold_vertex', 'A boundary vertex has disconnected or branching surface fans.'); continue
        winding = [1 if directed[(min(a,b),max(a,b))][:2] == (a,b) else -1 for a,b in zip(loop,loop[1:]+loop[:1])]
        if min(winding) != max(winding):
            reject('inconsistent_winding', 'Adjacent face winding is inconsistent around this boundary.'); continue
        if winding[0] == 1: loop.reverse()
        ids = np.asarray(loop, dtype=np.uint32); row['_loop'] = ids; pts = v[ids]
        extent = float(distance.pdist(pts).max())
        row['extent_m'] = extent
        if extent > cfg['max_extent_m']+1e-10:
            reject('extent_limit', 'Overall extent exceeds the selected maximum; the opening is preserved.', True); continue
        q = pts-centre; _, singular, basis = np.linalg.svd(q, full_matrices=False)
        uv = q@basis[:2].T; deviation = float(np.abs(q@basis[2]).max())
        row['plane_deviation_m'] = deviation
        if singular[1] < 1e-10 or deviation > plane_limit+1e-10:
            reject('nonplanar_boundary', 'The boundary is too non-planar or nearly collinear for the selected plane tolerance.'); continue
        polygon = shapely.Polygon(uv)
        area_tol = max(1e-14, extent*extent*1e-10)
        if not polygon.is_valid or polygon.area <= area_tol:
            reject('self_intersecting_boundary', 'The projected boundary self-intersects or has no usable area.'); continue
        rectangle = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords)[:4]
        row['width_m'] = float(np.linalg.norm(rectangle-np.roll(rectangle, 1, axis=0), axis=1).min())
        local = np.column_stack([uv, np.zeros(len(uv))])
        triangles = mesh_geometry.cap_loop(local, list(range(len(ids))))
        _check(cancel)
        if len(triangles) != len(ids)-2:
            reject('triangulation_failed', 'The planar boundary could not be triangulated without a gap.'); continue
        tf = np.asarray(triangles, dtype=np.uint32)
        polygons = [shapely.Polygon(uv[t]) for t in tf]
        covered = shapely.union_all(polygons)
        if any(p.area <= area_tol or not polygon.buffer(extent*1e-10).covers(p) for p in polygons) or abs(sum(p.area for p in polygons)-covered.area) > area_tol or polygon.symmetric_difference(covered).area > area_tol:
            reject('overlapping_cap', 'Candidate faces would overlap or extend outside the hole.'); continue
        cap = ids[tf]; cap_q = v[cap]; cap_normals = np.cross(cap_q[:,1]-cap_q[:,0],cap_q[:,2]-cap_q[:,0]); cap_normals /= np.linalg.norm(cap_normals,axis=1)[:,None]
        patch_edge_normals = {}
        for triangle, normal in zip(cap, cap_normals):
            for a,b in zip(triangle,np.roll(triangle,-1)):
                key=(min(int(a),int(b)),max(int(a),int(b)))
                if key in directed: patch_edge_normals[key]=normal
        agreements = [float(normals[directed[key][2]]@normal) for key,normal in patch_edge_normals.items()]
        if len(agreements) != len(ids) or min(agreements) < cosine_limit:
            reject('portal_or_sharp_opening', 'The cap would turn sharply from the neighbouring surface; preserve this portal, intentional opening or uncertain fold.', True); continue
        neighbours = tree.query_ball_point(centre, float(np.linalg.norm(q,axis=1).max())+max_face_radius+plane_limit)
        if _overlaps_existing(v,f,neighbours,centre,basis,polygon,deviation+1e-8,area_tol,cancel,shapely):
            reject('existing_surface_overlap', 'The patch would overlap or cross existing surface triangles.'); continue
        conflict = False
        for previous_id, previous_cap in patches.items():
            previous_pts=v[previous_cap]
            if np.any(previous_pts.max(axis=(0,1)) < pts.min(0)-plane_limit) or np.any(previous_pts.min(axis=(0,1)) > pts.max(0)+plane_limit): continue
            if _overlaps_existing(v,previous_cap,np.arange(len(previous_cap)),centre,basis,polygon,deviation+1e-8,area_tol,cancel,shapely): conflict=True; break
        if conflict:
            reject('other_patch_overlap', 'This cap would overlap another proposed repair.'); continue
        row.update(status='repairable',reason_code='validated_local_cap',reason='Simple, locally continuous cap; all added faces will be inferred.',triangles=len(cap),area_m2=float(polygon.area))
        patches[row['id']] = cap; frames[row['id']] = (centre, basis, polygon)
        if len(records)%20 == 0: progress(f'Inspected {len(records):,} openings; {len(patches):,} local caps available…')
    _check(cancel)
    if reference_batches is not None and patches:
        query_sets, query_rows, total = [], [], 0
        for row in records:
            if row['id'] not in patches: continue
            points = _support_locations(v, patches[row['id']], cfg['support_distance_m'], frames[row['id']])
            if points is None or total+len(points) > MAX_SUPPORT_QUERIES:
                row['support_status']='Not checked: patch sampling budget exceeded'; continue
            query_sets.append(points); query_rows.append((row,total,total+len(points))); total += len(points)
        if query_sets:
            queries=np.concatenate(query_sets); supported=np.zeros(len(queries),bool)
            lower=queries.min(0)-cfg['support_distance_m']; upper=queries.max(0)+cfg['support_distance_m']
            seen=0; iterator=iter(reference_batches())
            try:
                for batch in iterator:
                    _check(cancel); points=np.asarray(batch,dtype=float)
                    if points.ndim!=2 or points.shape[1]!=3: raise ValueError('Reference cloud batches must contain XYZ coordinates.')
                    seen+=len(points); points=points[np.isfinite(points).all(1)&np.all(points>=lower,axis=1)&np.all(points<=upper,axis=1)]
                    if len(points):
                        unanswered=np.flatnonzero(~supported)
                        if not len(unanswered): break
                        distances,_=cKDTree(points).query(queries[unanswered],distance_upper_bound=cfg['support_distance_m'])
                        supported[unanswered[np.isfinite(distances)]]=True
                    progress(f'Checking inferred patch locations against retained cloud points: {seen:,} read…')
                    if supported.all(): break
            finally:
                close=getattr(iterator,'close',None)
                if close: close()
            for row, left, right in query_rows:
                row.update(support_fraction=float(supported[left:right].mean()),support_samples=right-left,
                           support_status='Nearby cloud points at sampled patch locations; geometry remains inferred')
    for row in records:
        if row['id'] in patches and row['extent_m'] > UNSUPPORTED_EXTENT_M+1e-10 and (row['support_fraction'] is None or row['support_fraction'] < .8):
            row.update(status='preserved',reason_code='unsupported_large_gap',reason='Gaps above 0.5 m need nearby cloud support at at least 80% of sampled patch locations. This opening is preserved.')
            patches.pop(row['id'])
    summary=dict(openings=len(records),repairable=sum(r['status']=='repairable' for r in records),
                 preserved=sum(r['status']=='preserved' for r in records),unfilled=sum(r['status']=='unfilled' for r in records),
                 boundary_edges=len(directed),nonmanifold_edges=nonmanifold_edges,
                 extent_metric='Largest 3D distance between boundary vertices; metres',settings=cfg,
                 note='Geometry checks reduce repair risks; they do not certify the original mesh or establish survey accuracy.')
    progress(f"Hole review ready: {summary['repairable']} repairable, {summary['preserved']} preserved, {summary['unfilled']} unfilled.")
    _check(cancel)
    analysis = dict(holes=records,patches=patches,summary=summary,settings=cfg)
    if include_local_geometry:
        # The curved-gap refinement needs the same local sheet and collision
        # candidates. Keep these transient indices out of ordinary UI reviews.
        analysis['_geometry'] = dict(frames=frames, sorted_vertices=sorted_vertices,
                                     incidence_order=incidence_order, tree=tree,
                                     max_face_radius=max_face_radius)
    return analysis


def repaired_faces(faces, analysis, selected_ids=None):
    selected = list(analysis['patches']) if selected_ids is None else list(dict.fromkeys(selected_ids))
    if any(ident not in analysis['patches'] for ident in selected):
        raise ValueError('Select only repairable holes from the current preview.')
    added=[analysis['patches'][ident] for ident in selected]
    result=np.concatenate([np.asarray(faces,dtype=np.uint32),*added]) if added else np.asarray(faces,dtype=np.uint32).copy()
    if len(result)>MAX_TRIANGLES: raise ValueError('The repaired mesh exceeds the interactive triangle capacity. Reduce a separate copy first.')
    summary={**analysis['summary'],'filled':len(selected),'selected_holes':selected,
             'preserved':analysis['summary']['preserved']+analysis['summary']['repairable']-len(selected),
             'added_triangles':len(result)-len(faces)}
    chosen=set(selected);reasons={}
    for row in analysis['holes']:
        state='filled' if row['id'] in chosen else ('preserved' if row['status']=='repairable' else row['status'])
        reason='Selected validated local cap' if state=='filled' else ('Not selected for repair' if row['status']=='repairable' else row['reason'])
        key=state+': '+reason;reasons[key]=reasons.get(key,0)+1
    summary['reason_counts']=reasons
    return result,summary


def fill_small(vertices, faces, max_extent_m=.5, progress=lambda _:None, cancel=lambda:False, reference_batches=None):
    analysis=analyse(vertices,faces,dict(max_extent_m=max_extent_m),reference_batches,progress,cancel)
    result,summary=repaired_faces(faces,analysis)
    return np.asarray(vertices),result,summary


def _source(s, ident):
    if not isinstance(ident,str): raise ValueError('Select one triangular mesh or CAD solid.')
    if ident.startswith('cad:'):
        import cad_api
        obj=cad_api.get_object(cad_api.current(s),ident)
        if not obj.get('faces'): raise ValueError('Select triangular geometry; an open curve has no repairable surface.')
        return dict(id=ident,name=obj['name'],kind='cad',bundle_id=obj.get('bundle_id'),object=obj,units='Metres in project CAD coordinates')
    import job_api
    item=job_api.item(s,ident)
    if item.get('kind')!='mesh': raise ValueError('Select a triangular mesh for hole repair.')
    return dict(id=ident,name=item['name'],kind='mesh',bundle_id=item.get('bundle_id'),item=item,meta=item['meta'],units=item['meta'].get('units','Unconfirmed; original coordinate values preserved'))


def _revision(s, source):
    digest=hashlib.sha256(str(s.STATE.get('project_id')).encode()+source['id'].encode())
    if source['kind']=='cad':
        for key,dtype in (('vertices','<f8'),('faces','<u4')):
            digest.update(np.asarray(source['object'][key],dtype=dtype).tobytes())
    else:
        meta=source['meta']
        digest.update(str((meta.get('vertices'),meta.get('triangles'))).encode())
        for key in ('vertices_file','faces_file','face_flags_file','vertex_colours_file'):
            if meta.get(key):
                path=Path(meta[key]); stat=path.stat()
                digest.update(str((str(path.resolve()),stat.st_size,stat.st_mtime_ns)).encode())
    return digest.hexdigest()


def _read_source(source,cancel=lambda:False):
    if source['kind']=='cad':
        v,f=_arrays(source['object']['vertices'],source['object']['faces'],cancel)
        return v,f,np.zeros(len(f),dtype=np.uint8)
    meta=source['meta']; nv=int(meta.get('vertices',0)); nf=int(meta.get('triangles',0))
    if not 3<=nv<=MAX_VERTICES or not 1<=nf<=MAX_TRIANGLES:
        raise ValueError(f'Interactive repair supports at most {MAX_VERTICES:,} vertices and {MAX_TRIANGLES:,} faces. Reduce a separate copy first.')
    arrays=[]
    for key,dtype,count,width in (('vertices_file','<f8',nv,3),('faces_file','<u4',nf,3)):
        path=Path(meta[key]); expected=count*width*np.dtype(dtype).itemsize
        if path.stat().st_size!=expected: raise ValueError('The selected mesh cache has an incomplete or unexpected length.')
        values=np.empty((count,width),dtype=dtype)
        with path.open('rb') as stream:
            for start in range(0,count,100_000):
                _check(cancel); n=min(100_000,count-start); block=np.fromfile(stream,dtype=dtype,count=n*width)
                if len(block)!=n*width: raise ValueError('The mesh changed or was truncated while reading.')
                values[start:start+n]=block.reshape(n,width)
        arrays.append(values)
    v,f=_arrays(*arrays,cancel)
    flags=np.zeros(nf,dtype=np.uint8)
    if meta.get('face_flags_file'):
        path=Path(meta['face_flags_file'])
        if path.stat().st_size!=nf: raise ValueError('The inferred-face flags do not match this mesh.')
        flags=np.fromfile(path,dtype=np.uint8,count=nf)
        if len(flags)!=nf: raise ValueError('The inferred-face flags were truncated while reading.')
    if meta.get('vertex_colours_file') and Path(meta['vertex_colours_file']).stat().st_size!=nv*12:
        raise ValueError('The vertex-colour cache does not match this mesh.')
    return v,f,flags


def _reference(s,ident,progress=lambda _:None,cancel=lambda:False):
    if not ident: return None,None
    import job_api
    from contextlib import ExitStack
    item=job_api.item(s,ident)
    if item.get('kind')!='scan': raise ValueError('Choose a point-cloud scan for the optional support check.')
    meta=copy.deepcopy(item['meta'])
    signature=[]
    for key in ('file','mask_file'):
        if meta.get(key):
            path=Path(meta[key]); st=path.stat(); signature.append((str(path.resolve()),st.st_size,st.st_mtime_ns))
    def batches():
        # A fully masked block must still yield control and check cancellation.
        # Geometry support does not need the scan's intensity or RGB cache.
        total=int(meta['count'])
        with ExitStack() as files:
            xyz=files.enter_context(open(meta['file'],'rb'))
            mask=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
            for start in range(0,total,100_000):
                _check(cancel);count=min(100_000,total-start)
                raw=np.fromfile(xyz,'<f8',count=count*3)
                if len(raw)!=count*3:raise ValueError('The reference point cache is incomplete.')
                points=raw.reshape(count,3)
                if mask is not None:
                    flags=np.fromfile(mask,'u1',count=count)
                    if len(flags)!=count:raise ValueError('The reference exclusion mask is incomplete.')
                    points=points[flags==0]
                progress(f'Reading reference geometry: {start+count:,} / {total:,} original points…')
                yield points
    return batches,dict(id=ident,name=item['name'],signature=signature)


def _public_preview(source,v,f,analysis,token,revision,reference):
    origin=v[0]+(v.min(0)-v[0]+v.max(0)-v[0])/2
    holes=[]
    for record in analysis['holes']:
        row={key:value for key,value in record.items() if not key.startswith('_')}
        ids=record['_loop']; display_ids=ids[::max(1,math.ceil(len(ids)/MAX_LOOP_VERTICES))]
        row['boundary']=(v[display_ids]-origin).tolist(); row['boundary_ordered']=record['_ordered']
        cap=analysis['patches'].get(record['id'])
        if cap is not None:
            lookup={int(ident):index for index,ident in enumerate(ids)}
            row['cap_faces']=[[lookup[int(ident)] for ident in face] for face in cap]
        else: row['cap_faces']=[]
        holes.append(row)
    inventory_only = source['kind'] == 'mesh'
    if len(f)<=25_000:
        used,inverse=np.unique(f,return_inverse=True)
        context=dict(vertices=(v[used]-origin).tolist(),faces=inverse.reshape(-1,3).tolist(),mode='full',note='Full source triangles. Selected orange repair triangles use the exact saved boundary coordinates.')
    else:
        stride=max(1,math.ceil(len(v)/20_000))
        context=dict(vertices=(v[::stride]-origin).tolist(),faces=[],mode='points',note='Large-mesh context shown as sampled vertices. All detected boundaries and selected orange repair triangles are shown; source and output meshes keep every triangle.')
    if inventory_only:
        context['note'] = ('Sampled mesh context. ' if context['mode'] == 'points' else '') + 'Gold marks inventoried circular rims.'
    return dict(source_id=source['id'],source_name=source['name'],source_revision=revision,preview_token=token,
                origin=origin.tolist(),holes=holes,summary=analysis['summary'],settings=analysis['settings'],
                context=context,reference=reference,source_triangles=len(f),source_vertices=len(v),source_units=source['units'],
                inventory_only=inventory_only,
                unit_assumption='Distance controls assume one source coordinate unit is one metre; source coordinates are never rescaled.')


def create_preview(s,source_id,settings=None,reference_id=None,progress=lambda _:None,cancel=lambda:False):
    source=_source(s,source_id); revision=_revision(s,source)
    v,f,flags=_read_source(source,cancel)
    reference, layout = None, None
    if source['kind'] == 'mesh':
        import mesh_openings
        layout = mesh_openings.current_layout(s, source['meta'])
        analysis = mesh_openings.inspect(v, f, flags, settings, layout, progress, cancel)
        if layout is not mesh_openings.current_layout(s, source['meta']):
            raise ValueError('The lining layout changed during inspection. Inspect openings again.')
        reference_id = None
    else:
        batches,reference=_reference(s,reference_id,progress,cancel)
        analysis=analyse(v,f,settings,batches,progress,cancel)
    if revision!=_revision(s,_source(s,source_id)): raise ValueError('The mesh changed during inspection. Preview it again.')
    if reference_id and _reference(s,reference_id)[1]!=reference:raise ValueError('The reference cloud changed during inspection. Preview it again.')
    token=None if source['kind']=='mesh' else uuid.uuid4().hex
    public=_public_preview(source,v,f,analysis,token,revision,reference)
    _check(cancel)
    cache=dict(source_id=source_id,source_revision=revision,analysis=analysis,public=public,reference_id=reference_id,
               reference=reference,layout_revision=layout.get('revision') if layout else None)
    s._mesh_repair_preview=cache
    return public


def _remove_written(meta):
    for key in ('vertices_file','faces_file','face_flags_file','preview_file'):
        if meta.get(key): Path(meta[key]).unlink(missing_ok=True)


def _write_result(work,name,v,f,flags,summary,source_id,source_units,progress,cancel):
    ident=uuid.uuid4().hex; root=Path(work)
    meta=dict(name=name,vertices_file=str(root/(ident+'.vertices64')),faces_file=str(root/(ident+'.faces32')),
              face_flags_file=str(root/(ident+'.face_flags')),preview_file=str(root/(ident+'.preview64')),
              vertices=len(v),triangles=len(f),minimum=v.min(0).tolist(),maximum=v.max(0).tolist(),
              units=source_units,degenerate_triangles=0,
              quality='Selected local caps are inferred geometry. Original coordinates and faces are preserved in this derived copy.')
    try:
        jobdata.room(work,int(v.nbytes+f.nbytes+flags.nbytes)+6_000_000)
        for key,values,dtype in (('vertices_file',v,'<f8'),('faces_file',f,'<u4'),('face_flags_file',flags,'u1')):
            with Path(meta[key]).open('wb') as stream:
                for start in range(0,len(values),100_000):
                    _check(cancel); np.asarray(values[start:start+100_000],dtype=dtype).tofile(stream)
        stride=max(1,math.ceil(len(v)/250_000)); v[::stride].astype('<f8',copy=False).tofile(meta['preview_file'])
        meta.update(preview_stride=stride,preview_count=len(v[::stride]))
        progress('Checking connectivity of the repaired mesh…'); _check(cancel)
        topology=mesh_geometry.topology(v,f)
        meta['mesh_summary']={**topology,'method':'selected_hole_repair','vertices':len(v),'triangles':len(f),
                              'filled_boundary_loops':summary['filled'],'preserved_openings':summary['preserved'],
                              'unfilled_openings':summary['unfilled'],'reconstructed_triangles':int(np.count_nonzero(flags)),
                              'reconstruction_note':'Orange faces are inferred repairs, including any pre-existing inferred faces. Original mesh coordinates and faces are unchanged.'}
        meta['repair_report']={**summary,'source_id':source_id,'parameter_unit_assumption':'One source coordinate unit = one metre; no rescaling was performed.'}
        _check(cancel); return meta
    except BaseException:
        _remove_written(meta); raise


def apply_preview(s,source_id,token,selected_ids,name=None,hide_source=False,progress=lambda _:None,cancel=lambda:False):
    source=_source(s,source_id)
    if source['kind']=='mesh': raise ValueError(PRESERVE_MESSAGE)
    cache=getattr(s,'_mesh_repair_preview',None)
    if not cache or cache['source_id']!=source_id or cache['public']['preview_token']!=token:
        raise ValueError('Preview this mesh again before applying selected repairs.')
    if not isinstance(selected_ids,list) or not selected_ids or len(selected_ids)>MAX_HOLES or any(not isinstance(i,str) for i in selected_ids):
        raise ValueError('Select at least one repairable opening.')
    if _revision(s,source)!=cache['source_revision']: raise ValueError('The mesh changed. Preview its openings again.')
    if cache['reference_id']:
        _,reference=_reference(s,cache['reference_id'])
        if reference!=cache['reference']: raise ValueError('The reference cloud changed. Preview the support check again.')
    v,f,old_flags=_read_source(source,cancel); new_faces,summary=repaired_faces(f,cache['analysis'],selected_ids)
    before=cache['analysis']['summary']
    flags=np.r_[old_flags,np.ones(len(new_faces)-len(f),dtype=np.uint8)]
    reason_counts={}
    chosen=set(summary['selected_holes'])
    for row in cache['analysis']['holes']:
        status='filled' if row['id'] in chosen else ('preserved' if row['status']=='repairable' else row['status'])
        reason='Selected validated local cap' if status=='filled' else ('Not selected for repair' if row['status']=='repairable' else row['reason'])
        key=status+': '+reason; reason_counts[key]=reason_counts.get(key,0)+1
    summary['reason_counts']=reason_counts
    result=_write_result(asset_folder(s.WORK,'mesh'),str(name or source['name']+' repaired')[:120],v,new_faces,flags,summary,source_id,source['units'],progress,cancel)
    if source['kind']=='mesh' and source['meta'].get('vertex_colours_file'):
        # Every boundary vertex keeps its original index and coordinate. The
        # immutable colour cache is therefore shared, never owned by this copy's
        # cleanup routine (_remove_written deliberately excludes this key).
        result['vertex_colours_file']=source['meta']['vertex_colours_file']
        if 'coloured_vertices' in source['meta']:result['coloured_vertices']=source['meta']['coloured_vertices']
    saved=None
    try:
        # Boundary edges of every selected simple loop must disappear, while
        # original non-manifold edges must not increase.
        expected=before['boundary_edges']-sum(len(row['_loop']) for row in cache['analysis']['holes'] if row['id'] in chosen)
        if result['mesh_summary']['boundary_edges']!=expected or result['mesh_summary']['nonmanifold_edges']!=before['nonmanifold_edges']:
            raise ValueError('The candidate repair failed the final edge-connectivity check. No result was saved.')
        if _revision(s,_source(s,source_id))!=cache['source_revision']: raise ValueError('The mesh changed before saving. Preview it again.')
        _check(cancel); saved=copy.deepcopy(s.STATE)
        import workspace_tools
        bundle=workspace_tools.destination_bundle(s,source.get('bundle_id'))
        item=dict(id=uuid.uuid4().hex,bundle_id=bundle,kind='mesh',name=result['name'],meta=result,visible=True,
                  source_bytes=0,position_status='Derived repair in original mesh coordinates',hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[])
        s.STATE.setdefault('job_items',[]).append(item)
        if hide_source:
            if source['kind']=='cad':
                source['object']['visible']=False; s.STATE['cad']['revision']=uuid.uuid4().hex
            else: source['item']['visible']=False
        _check(cancel); s.save()
        cache['public']['last_apply']=dict(result_id=item['id'],summary=summary,name=result['name'])
        return item
    except BaseException:
        if saved is not None: s.STATE.clear(); s.STATE.update(saved)
        _remove_written(result); raise


def _source_choices(s):
    """Offer supported source kinds without loading mesh coordinates or faces."""
    rows=[dict(id=x['id'],name=x.get('name','Mesh'),kind='mesh',
               triangles=int((x.get('meta') or {}).get('triangles') or 0))
          for x in s.STATE.get('job_items',[]) if x.get('kind')=='mesh']
    import cad_api
    rows.extend(dict(id=x['id'],name=x.get('name','CAD surface'),kind='cad',triangles=len(x['faces']))
                for x in cad_api.current(s).get('objects',[]) if x.get('faces'))
    return rows


def get(s,h,path,args):
    if path!='/api/mesh-repair': return False
    ident=args.get('source_id',[None])[0]
    source=_source(s,ident) if ident else None
    cache=getattr(s,'_mesh_repair_preview',None)
    preview=cache['public'] if cache and source and cache['source_id']==ident and cache['source_revision']==_revision(s,source) else None
    if preview and source['kind']=='mesh':
        from mesh_openings import current_layout
        layout=current_layout(s,source['meta'])
        if not preview.get('inventory_only') or cache.get('layout_revision')!=(layout.get('revision') if layout else None):
            preview=None
    clouds=[dict(id=x['id'],name=x['name']) for x in s.STATE.get('job_items',[]) if x.get('kind')=='scan']
    h.send(dict(preview=preview,source=dict(id=source['id'],name=source['name'],units=source['units'],
                                           inventory_only=source['kind']=='mesh') if source else None,
                sources=_source_choices(s),clouds=clouds,defaults=parameters()))
    return True


def post(s,h,path,body):
    if path!='/api/mesh-repair': return False
    if s.JOB.get('busy'): raise ValueError('Wait for the current operation, or cancel it first.')
    ident=body.get('source_id'); source=_source(s,ident)
    action=body.get('action')
    cancel=lambda:bool(getattr(s,'JOB_CANCEL_FILE',None) and s.JOB_CANCEL_FILE.exists())
    if action=='analyse':
        cfg=parameters(body.get('settings'))
        run=lambda:create_preview(s,ident,cfg,body.get('reference_id'),s.progress,cancel)
    elif action=='apply':
        if source['kind']=='mesh': raise ValueError(PRESERVE_MESSAGE)
        import workspace_history
        workspace_history.configure('/api/mesh-repair',dict(action='repair_selected_holes'))
        run=lambda:apply_preview(s,ident,body.get('preview_token'),body.get('selected_ids'),body.get('name'),bool(body.get('hide_source')),s.progress,cancel)
    else: raise ValueError('Choose Inspect openings or Apply selected repairs.')
    s.start_job(run,'mesh',native_progress=True); h.send(dict(started=True)); return True
