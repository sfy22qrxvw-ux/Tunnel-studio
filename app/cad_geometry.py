# Copyright (c) 2026 Damian Gulczynski.
"""Survey-coordinate CAD geometry. Float64 world storage; local solid calculations.

Curved entities retain their analytic construction definition and a tessellated
display/solid representation. No operation reads or rewrites scan caches.
"""
from pathlib import Path
import copy, math, sys
import numpy as np

ENGINE = Path(__file__).resolve().parents[1] / 'runtime' / 'cad_engine_v0140'
if sys.platform=='win32' and ENGINE.is_dir() and str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

MAX_VERTICES = 50000
MAX_FACES = 100000
MAX_PROJECT_VERTICES = 300000
MAX_PROFILE = 2048
EPS = 1e-9

def engines():
    try:
        import manifold3d as manifold
        import shapely
        return manifold, shapely
    except ImportError as exc:
        raise ValueError('The modelling engine is missing. Reinstall the complete current Tunnel Studio installer.') from exc

def vector(value, label='Coordinates'):
    try: a = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError): raise ValueError(label + ' must contain three finite numbers.')
    if a.shape != (3,) or not np.isfinite(a).all() or np.max(np.abs(a)) > 1e12:
        raise ValueError(label + ' must contain three finite numbers within the supported coordinate range.')
    return a

def number(value, label, positive=False):
    try: value = float(value)
    except (TypeError, ValueError): raise ValueError(label + ' must be a finite number.')
    if not math.isfinite(value) or abs(value) > 1e12 or positive and value <= EPS:
        raise ValueError(label + (' must be positive.' if positive else ' must be finite.'))
    return value

def unit(value, label='Direction'):
    a = vector(value, label); n = np.linalg.norm(a)
    if n < EPS: raise ValueError(label + ' must have a non-zero length.')
    return a / n

def points(value, minimum=1, limit=MAX_PROFILE):
    try: p = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError): raise ValueError('Enter valid XYZ vertices.')
    if p.ndim != 2 or p.shape[1] != 3 or not minimum <= len(p) <= limit or not np.isfinite(p).all() or np.max(np.abs(p)) > 1e12:
        raise ValueError(f'Geometry needs {minimum} to {limit} finite XYZ vertices.')
    return p

def frame(raw=None):
    raw = raw or {}; origin = vector(raw.get('origin', [0, 0, 0]), 'Origin')
    if 'x_point' in raw:
        x = unit(vector(raw['x_point']) - origin, 'X-axis point')
        y0 = vector(raw['xy_point']) - origin
    else:
        x = unit(raw.get('x', [1, 0, 0]), 'X axis'); y0 = vector(raw.get('y', [0, 1, 0]), 'Y axis')
    y = unit(y0 - x * np.dot(y0, x), 'Y axis (must not be parallel to X)')
    z = np.cross(x, y)
    if 'z' in raw and np.dot(unit(raw['z']), z) < 1 - 1e-6:
        raise ValueError('X, Y and Z must describe a right-handed orthogonal frame.')
    return dict(name=str(raw.get('name', 'Working frame'))[:80], origin=origin.tolist(), x=x.tolist(), y=y.tolist(), z=z.tolist())

def matrix(raw=None):
    f = frame(raw)
    return np.asarray(f['origin']), np.asarray([f['x'], f['y'], f['z']]).T

def segment(a, b, radius=0):
    a=np.asarray(a,float);b=np.asarray(b,float);d=b-a;length=float(np.linalg.norm(d));r=float(radius)
    if not np.isfinite([*a,*b,r]).all() or length<1e-8:
        raise ValueError('Consecutive plan/profile points must be finite and different.')
    if abs(r)<1e-10:
        return length,lambda f:a+f*d,lambda f:d/length
    R=abs(r)
    if 2*R<length-1e-8:
        raise ValueError(f'Arc radius {R:g} m is too small for its {length:g} m chord; use at least {length/2:g} m.')
    sign=1 if r>0 else -1
    centre=(a+b)/2+sign*np.array([-d[1],d[0]])/length*math.sqrt(max(0,R*R-length*length/4))
    angle=math.atan2(a[1]-centre[1],a[0]-centre[0]);sweep=sign*2*math.asin(min(1,length/(2*R)))
    def position(f):return centre+R*np.array([math.cos(angle+f*sweep),math.sin(angle+f*sweep)])
    def tangent(f):return sign*np.array([-math.sin(angle+f*sweep),math.cos(angle+f*sweep)])
    return abs(sweep)*R,position,tangent

def plane_coordinates(vertices, working_frame):
    origin,basis=matrix(working_frame)
    local=(np.asarray(vertices,float)-origin)@basis
    if np.max(np.abs(local[:,2]))>1e-6:
        raise ValueError('Keep the geometry in the profile’s X/Y plane.')
    return local[:,:2]

def curve_nodes(obj, working_frame):
    """Return unsampled vertices and signed outgoing radii in a fixed plane."""
    spec=obj.get('definition') or {}
    vertices=obj['vertices']
    radii=np.zeros(len(vertices))
    transform=np.asarray(obj.get('transform',np.eye(4)))
    A=transform[:3,:3]
    _,basis=matrix(working_frame)
    if spec.get('kind') in ('circle','arc') or 'radii' in spec:
        source_origin,src=matrix(spec.get('frame'))
        sign=np.sign(np.dot(np.cross(A@src[:,0],A@src[:,1]),basis[:,2]))
        scale=np.linalg.norm(A@src[:,0])
        if spec['kind'] in ('circle','arc'):
            closed=spec['kind']=='circle'
            start=float(spec.get('start_angle',0))
            span=360 if closed else float(spec.get('end_angle',360))-start
            count=max(1,math.ceil(abs(span)/90))
            angles=np.radians(np.linspace(start,start+span,count if closed else count+1,endpoint=not closed))
            radius=float(spec['radius'])
            p=np.asarray(spec.get('centre',source_origin))+radius*(np.cos(angles)[:,None]*src[:,0]+np.sin(angles)[:,None]*src[:,1])
            radii=np.full(len(p),math.copysign(radius,span)*scale*sign)
            if not closed:radii[-1]=0
        else:
            p=np.asarray(spec['points']);radii=np.asarray(spec['radii'])*scale*sign
        vertices=p@A.T+transform[:3,3]
    return np.column_stack([plane_coordinates(vertices,working_frame),radii])

def planar_curve(nodes, working_frame, closed):
    rows=np.asarray(nodes,float)
    origin,basis=matrix(working_frame)
    return build(dict(kind='polyline',points=(rows[:,:2]@basis[:,:2].T+origin).tolist(),
                      radii=rows[:,2].tolist(),frame=working_frame,closed=closed))

def circle_edge(a,b,radius):
    length,_,tangent=segment(a,b,radius)
    tx,ty=tangent(0)
    centre=a+radius*np.array([-ty,tx])
    angle=math.atan2(a[1]-centre[1],a[0]-centre[0])
    return centre,angle,length/radius

def curve_parameter(a,b,radius,q):
    if abs(radius)<1e-10:
        d=b-a
        return float(np.clip((q-a)@d/(d@d),0,1))
    centre,angle,sweep=circle_edge(a,b,radius)
    bearing=math.atan2(q[1]-centre[1],q[0]-centre[0])
    turn=(math.copysign(1,sweep)*(bearing-angle))%(2*math.pi)
    if turn<=abs(sweep):return turn/abs(sweep)
    return float(np.linalg.norm(q-b)<np.linalg.norm(q-a))

def curve_edges(rows,closed):
    return zip(rows if closed else rows[:-1],np.roll(rows,-1,axis=0))

def curve_paths(rows,closed,split,*,wrap):
    """Divide signed-radius edges at exact curve parameters."""
    chunks=[[np.r_[rows[0,:2],0.]]]
    for i,(a,b) in enumerate(curve_edges(rows,closed)):
        radius=a[2];_,position,_=segment(a[:2],b[:2],radius)
        parameters=sorted({float(t) for t in split.get(i,[]) if 1e-9<t<=1})
        end_split=bool(parameters and parameters[-1]>=1-1e-9)
        for t in [v for v in parameters if v<1-1e-9]+[1.]:
            point=b[:2] if t==1. else position(t)
            chunks[-1][-1][2]=radius
            chunks[-1].append(np.r_[point,0.])
            if t<1. or end_split:
                chunks.append([np.r_[point,0.]])
    if closed and wrap and len(chunks)>1:
        chunks[0]=chunks[-1][:-1]+chunks[0]
        chunks.pop()
    return [np.asarray(path) for path in chunks if len(path)>=2]

def break_planar_curve(obj,point,working_frame):
    rows=curve_nodes(obj,working_frame);q=plane_coordinates([vector(point)],working_frame)[0]
    candidates=[]
    for i,(a,b) in enumerate(curve_edges(rows,obj['closed'])):
        t=curve_parameter(a[:2],b[:2],a[2],q)
        _,position,_=segment(a[:2],b[:2],a[2])
        candidates.append((np.linalg.norm(position(t)-q),i,t))
    if not candidates:raise ValueError('Select a line or curve to break.')
    _,i,t=min(candidates)
    if t<1e-9 and i>0:i-=1;t=1.
    chunks=curve_paths(rows,obj['closed'],{i:[t]},wrap=False)
    if len(chunks)!=2:raise ValueError('Choose a break point inside the curve, away from its ends.')
    return [planar_curve(path,working_frame,False) for path in chunks]

def arc_boundary_turns(a,b,radius,q,normal):
    centre,angle,sweep=circle_edge(a,b,radius)
    distance=(centre-q)@normal
    h=radius*radius-distance*distance
    if h < -EPS:return [],centre,sweep
    foot=centre-distance*normal
    delta=np.sqrt(max(0.,h))*np.array([-normal[1],normal[0]])
    turns=[]
    for point in (foot+delta,foot-delta):
        bearing=math.atan2(point[1]-centre[1],point[0]-centre[0])
        turns.append((math.copysign(1,sweep)*(bearing-angle))%(2*math.pi))
    return turns,centre,sweep

def trim_planar_curve(obj,boundary,working_frame,extend,end,keep):
    rows=curve_nodes(obj,working_frame)
    if len(rows)<2:raise ValueError('Select a line or curve to trim or extend.')
    line=plane_coordinates(boundary,working_frame)
    q=line[0];d=line[1]-q;normal=np.array([d[1],-d[0]])/np.linalg.norm(d)
    closed=obj['closed']
    if extend:
        if closed:raise ValueError('Extend requires an open line or polyline.')
        if end=='start':
            rows=rows[::-1].copy()
            rows[:,2]=np.r_[-rows[1:,2],0.]
        a,b=rows[-2:];radius=a[2]
        if abs(radius)<1e-10:
            direction=b[:2]-a[:2];den=direction@normal
            if abs(den)<EPS:raise ValueError('The endpoint direction is parallel to this boundary.')
            t=(q-b[:2])@normal/den
            if t<=EPS:raise ValueError('This boundary lies behind the selected endpoint. Choose the other end or Trim.')
            rows[-1,:2]+=t*direction
        else:
            turns,centre,sweep=arc_boundary_turns(a[:2],b[:2],radius,q,normal)
            valid=[t for t in turns if abs(sweep)+1e-9<t<2*math.pi-1e-9]
            if not valid:raise ValueError('This boundary does not intersect the forward continuation of the arc.')
            travel=min(valid)-abs(sweep)
            count=math.ceil(travel/(math.pi/2))
            angles=math.atan2(b[1]-centre[1],b[0]-centre[0])+math.copysign(1,sweep)*np.linspace(0,travel,count+1)[1:]
            points=centre+abs(radius)*np.column_stack([np.cos(angles),np.sin(angles)])
            rows[-1,2]=radius;rows=np.vstack([rows,np.column_stack([points,np.full(count,radius)])])
            rows[-1,2]=0.
        if end=='start':
            rows=rows[::-1].copy()
            rows[:,2]=np.r_[-rows[1:,2],0.]
        return planar_curve(rows,working_frame,False)
    if keep is None:raise ValueError('Pick a point on the portion to keep after trimming.')
    hits={}
    for i,(a,b) in enumerate(curve_edges(rows,closed)):
        radius=a[2]
        if abs(radius)<1e-10:
            den=(b[:2]-a[:2])@normal
            if abs(den)>EPS:hits[i]=[(q-a[:2])@normal/den]
        else:
            turns,_,sweep=arc_boundary_turns(a[:2],b[:2],radius,q,normal)
            if len(turns)==2 and abs(turns[0]-turns[1])>1e-9:
                hits[i]=[min(1.,t/abs(sweep)) for t in turns if t<=abs(sweep)+1e-9]
    paths=curve_paths(rows,closed,hits,wrap=True)
    if len(paths)<2:raise ValueError('The straight boundary does not divide this curve.')
    pick=plane_coordinates([vector(keep)],working_frame)[0]
    def distance(path):
        errors=[]
        for a,b in curve_edges(path,False):
            t=curve_parameter(a[:2],b[:2],a[2],pick)
            _,position,_=segment(a[:2],b[:2],a[2])
            errors.append(np.linalg.norm(position(t)-pick))
        return min(errors)
    return planar_curve(min(paths,key=distance),working_frame,False)

def plane(p, fallback=None):
    p = points(p, 2); origin = p[0]; d = p - origin
    x = unit(next((q for q in d[1:] if np.linalg.norm(q) > EPS), [0, 0, 0]), 'Profile edge')
    n = next((np.cross(x, q) for q in d[1:] if np.linalg.norm(np.cross(x, q)) > EPS), None)
    if n is None:
        n = np.asarray(frame(fallback)['z'])
        if abs(np.dot(x, n)) > 1e-7:
            helper = np.eye(3)[np.argmin(np.abs(x))]; n = np.cross(x, helper)
    # Fit all vertices: a nearly collinear first arc segment amplifies rounding
    # at large survey coordinates and incorrectly rejects planar profiles.
    _,singular,vt=np.linalg.svd(d,full_matrices=False)
    if len(singular)>=3 and singular[1]>max(EPS,singular[0]*1e-10):
        fitted=vt[-1];n=fitted if np.dot(fitted,n)>=0 else -fitted
    n = unit(n); y = unit(np.cross(n, x)); x=np.cross(y,n); basis = np.stack([x, y, n], axis=1)
    local = d @ basis
    tolerance = max(1e-8, np.ptp(local, axis=0).max() * 1e-9)
    if np.max(np.abs(local[:, 2])) > tolerance: raise ValueError('This operation requires a planar profile. Use a plane or edit its Z values; the profile will not be flattened.')
    return origin, basis, local[:, :2]

def polygon(p):
    m, sh = engines(); origin, basis, uv = plane(p)
    poly = sh.Polygon(uv)
    if not poly.is_valid or poly.area < EPS:
        raise ValueError('Use a non-self-intersecting closed profile with a non-zero area.')
    area2 = np.sum(uv[:, 0]*np.roll(uv[:, 1], -1) - uv[:, 1]*np.roll(uv[:, 0], -1))
    order = np.arange(len(p)) if area2 > 0 else np.arange(len(p)-1, -1, -1)
    triangles = order[np.asarray(m.triangulate([uv[order]]), dtype=np.int64)]
    return origin, basis, uv, triangles

def mesh_limits(solid):
    return (MAX_PROJECT_VERTICES, 2 * MAX_PROJECT_VERTICES) if solid else (MAX_VERTICES, MAX_FACES)


def geometry(vertices, faces=None, closed=False, solid=False, **metadata):
    max_vertices, max_faces = mesh_limits(solid)
    p = points(vertices, 1, max_vertices)
    f = np.asarray(faces if faces is not None else [], dtype=np.int64).reshape(-1, 3)
    if len(f) > max_faces or len(f) and (np.min(f) < 0 or np.max(f) >= len(p)):
        raise ValueError('Mesh exceeds the modelling limit or has invalid triangle indices.')
    if len(f):
        local = p - p[0]; triangles = local[f]
        cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        if np.any(np.linalg.norm(cross, axis=1) < 1e-14): raise ValueError('This operation produced a degenerate triangle. Adjust the input geometry.')
        area = float(np.linalg.norm(cross, axis=1).sum()/2)
        signed = float(np.einsum('ij,ij->i', triangles[:, 0], cross).sum()/6)
        if solid and signed < 0: f = f[:, [0, 2, 1]]; signed = -signed
    else: area = 0.; signed = 0.
    result = dict(vertices=p.tolist(), faces=f.tolist(), closed=bool(closed), solid=bool(solid),
                  minimum=p.min(0).tolist(), maximum=p.max(0).tolist(), area_m2=area,
                  volume_m3=signed if solid else None, **metadata)
    if solid:
        if signed <= 1e-12: raise ValueError('This operation has zero solid volume. Choose a direction outside the profile plane.')
        as_manifold(result, p[0])
    return result

def as_manifold(obj, origin):
    m, _ = engines()
    if not obj.get('solid'): raise ValueError('Select closed solid objects for this operation.')
    v = np.ascontiguousarray(np.asarray(obj['vertices'], dtype=np.float64)-origin)
    f = np.ascontiguousarray(obj['faces'], dtype=np.uint64)
    out = m.Manifold(m.Mesh64(v, f))
    if str(out.status()) != 'Error.NoError' or out.is_empty():
        raise ValueError('The result is not a valid oriented solid. Adjust the profiles or operation.')
    return out

def from_manifold(value, origin):
    if str(value.status()) != 'Error.NoError' or value.is_empty(): raise ValueError('The operation produced no valid solid. Check overlap and input shapes.')
    mesh = value.to_mesh64()
    return geometry(np.asarray(mesh.vert_properties)[:, :3] + origin, mesh.tri_verts, solid=True)

def profile(obj):
    if obj.get('faces'): raise ValueError('Choose a drawn profile. A solid is not a profile.')
    return points(obj['vertices'], 2), bool(obj.get('closed'))

def closed_profile_frame(obj):
    p, closed = profile(obj)
    if not closed or len(p) < 3:
        raise ValueError('Choose a closed CAD outline.')
    saved = (obj.get('definition') or {}).get('frame')
    if saved:
        origin, basis = matrix(saved)
        transform = np.asarray(obj.get('transform', np.eye(4)), float)
        origin = origin @ transform[:3, :3].T + transform[:3, 3]
        x, y = (transform[:3, :3] @ basis[:, i] for i in (0, 1))
        result = frame(dict(name='Profile plane', origin=origin, x=x, y=y))
        normal = np.asarray(result['z'])
        depth = (p - origin) @ normal
        if np.ptp(depth) > 1e-6:
            raise ValueError('The CAD outline is not planar in its drawing frame.')
        result['origin'] = (origin + depth.mean() * normal).tolist()
        return result
    origin, basis, local = plane(p)
    return frame(dict(name='Profile plane', origin=origin + basis[:, :2] @ local.mean(0),
                      x=basis[:, 0], y=basis[:, 1]))

def profile_vertex_editable(obj):
    return obj.get('shape') in ('polyline', 'rectangle') and obj.get('closed') and not obj.get('faces')

def face_profile(obj, face_index):
    """Extract the connected planar face around a picked triangle, not one facet."""
    p=points(obj.get('vertices'),1,MAX_VERTICES)
    faces=np.asarray(obj.get('faces',[]),dtype=int).reshape(-1,3)
    if type(face_index) is not int or not 0<=face_index<len(faces):
        raise ValueError('Pick a face on the selected surface or solid.')
    seed=p[faces[face_index]];normal=unit(np.cross(seed[1]-seed[0],seed[2]-seed[0]))
    tolerance=max(1e-8,float(np.ptp(p,axis=0).max())*1e-9)
    adjacent={}
    for i,f in enumerate(faces):
        for a,b in zip(f,np.roll(f,-1)):adjacent.setdefault(tuple(sorted((int(a),int(b)))),[]).append(i)
    group=set();queue=[face_index]
    while queue:
        i=queue.pop()
        if i in group:continue
        triangle=p[faces[i]];n=unit(np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]))
        if np.max(np.abs((triangle-seed[0])@normal))>tolerance or np.dot(n,normal)<1-1e-7:continue
        group.add(i)
        for a,b in zip(faces[i],np.roll(faces[i],-1)):
            queue.extend(j for j in adjacent[tuple(sorted((int(a),int(b))))] if j not in group)
    boundary=[]
    for i in group:
        for a,b in zip(faces[i],np.roll(faces[i],-1)):
            if sum(j in group for j in adjacent[tuple(sorted((int(a),int(b))))])==1:boundary.append((int(a),int(b)))
    next_vertex=dict(boundary)
    if not boundary or len(next_vertex)!=len(boundary):raise ValueError('This face has an ambiguous boundary. Use a simple closed profile.')
    order=[];at=boundary[0][0]
    while at not in order and at in next_vertex:
        order.append(at);at=next_vertex[at]
    if at!=order[0] or len(order)!=len(boundary):raise ValueError('Faces with holes or separate boundaries need separate closed profiles.')
    if len(order)>MAX_PROFILE:raise ValueError('Reduce face detail before extrusion.')
    return geometry(p[order],closed=True,shape='polyline')

def extrude(obj, displacement, symmetric=False):
    p, closed = profile(obj); d = vector(displacement, 'Extrusion XYZ displacement')
    if np.linalg.norm(d) < EPS: raise ValueError('Extrusion length must be non-zero.')
    if closed:
        _, basis, _, caps = polygon(p)
        if abs(np.dot(d, basis[:, 2])) < max(EPS, np.linalg.norm(d)*1e-9):
            raise ValueError('This direction lies in the profile plane and would create zero volume.')
    else: caps = []
    base = p - (d/2 if symmetric else 0); n = len(p); f = []
    for i in range(n if closed else n-1):
        j = (i+1) % n; f.extend([[i, j, j+n], [i, j+n, i+n]])
    for a, b, c in caps: f.extend([[int(c), int(b), int(a)], [int(a+n), int(b+n), int(c+n)]])
    return geometry(np.vstack([base, base+d]), f, solid=closed, shape='extrude')

def resample(p, count, closed):
    q = np.vstack([p, p[0]]) if closed else p
    lengths = np.linalg.norm(np.diff(q, axis=0), axis=1)
    if np.any(lengths < EPS): raise ValueError('Remove duplicate consecutive profile/path vertices.')
    dist = np.r_[0, np.cumsum(lengths)]; targets = np.linspace(0, dist[-1], count, endpoint=not closed)
    return np.column_stack([np.interp(targets, dist, q[:, i]) for i in range(3)])

def loft(profiles):
    if not 2 <= len(profiles) <= 2048: raise ValueError('Loft needs 2 to 2,048 ordered profiles.')
    rows = [profile(p) for p in profiles]; closed = rows[0][1]
    if any(c != closed for _, c in rows): raise ValueError('All loft profiles must be either open or closed.')
    count = max(len(p) for p, _ in rows)
    if len(rows)*count>MAX_VERTICES or 2*len(rows)*count>MAX_FACES:raise ValueError('This model is too dense. Increase profile spacing or reduce profile detail.')
    if count > 512: raise ValueError('Loft supports up to 512 vertices per profile.')
    loops = [p if len(p)==count else resample(p, count, closed) for p, _ in rows]
    if closed:
        for p in loops: polygon(p)
    return loft_rings(loops, closed=closed)


def loft_rings(loops, *, closed=True, shape='loft'):
    """Join matched, validated rings with caps at the two outer ends."""
    rings = np.asarray(loops, dtype=float)
    rows, count, _ = rings.shape
    offsets = (np.arange(rows - 1) * count)[:, None]
    i = np.arange(count if closed else count - 1)
    a = offsets + i
    b = offsets + (i + 1) % count
    first = np.stack([a, b, b + count], axis=-1)
    second = np.stack([a, b + count, a + count], axis=-1)
    faces = np.stack([first, second], axis=2).reshape(-1, 3)
    if closed:
        start = polygon(rings[0])[3][:, ::-1]
        end = polygon(rings[-1])[3] + (rows - 1) * count
        faces = np.concatenate([faces, start, end])
    return geometry(rings.reshape(-1, 3), faces, solid=closed, shape=shape)

def rotation(axis, degrees):
    x,y,z=unit(axis); a=math.radians(number(degrees,'Angle')); c=math.cos(a); s=math.sin(a); t=1-c
    return np.array([[t*x*x+c,t*x*y-s*z,t*x*z+s*y],[t*x*y+s*z,t*y*y+c,t*y*z-s*x],[t*x*z-s*y,t*y*z+s*x,t*z*z+c]])

def between(a,b):
    a=unit(a);b=unit(b); c=float(np.clip(a@b,-1,1)); n=np.cross(a,b)
    if c < -1+1e-8: raise ValueError('A sweep path cannot reverse through 180 degrees. Edit that path corner.')
    return np.eye(3) if np.linalg.norm(n)<EPS else rotation(n,math.degrees(math.acos(c)))

def sweep(obj, path):
    p,closed=profile(obj); path=points(path,2,256)
    direction=np.diff(path,axis=0); sizes=np.linalg.norm(direction,axis=1)
    if np.any(sizes<EPS):raise ValueError('Remove duplicate consecutive path vertices.')
    direction/=sizes[:,None]; tangents=[direction[0]]
    for a,b in zip(direction[:-1],direction[1:]):tangents.append(unit(a+b,'Path tangent'))
    tangents.append(direction[-1]); _,basis,_=plane(p)
    centre=p.mean(0); local=(p-centre)@basis; R=between(basis[:,2],tangents[0]); axes=R@basis; loops=[]
    for i,(q,t) in enumerate(zip(path,tangents)):
        if i:axes=between(tangents[i-1],t)@axes
        loops.append(dict(vertices=(local@axes.T+q).tolist(),closed=closed))
    return loft(loops)

def build(raw, resolve=lambda ident:None):
    spec=copy.deepcopy(raw); kind=str(spec.get('kind','')); f=frame(spec.get('frame')); origin,B=matrix(f)
    segments=int(spec.get('segments',64))
    if not 12<=segments<=512:raise ValueError('Curve detail must be between 12 and 512 segments.')
    def source(key):
        obj=spec.get(key)
        if not isinstance(obj,dict):obj=resolve(obj)
        if obj is None:raise ValueError('Select the required source geometry.')
        spec[key]={k:copy.deepcopy(obj[k]) for k in ('vertices','faces','closed','solid') if k in obj}
        return spec[key]
    if kind=='tunnel_design':
        return __import__('tunnel_designer').build(spec['design'],
            legacy_sampling='vertex_budget' not in spec,
            vertex_budget=spec.get('vertex_budget', MAX_PROJECT_VERTICES))[0]
    if kind in ('point','line','polyline','rectangle'):
        p=points(spec.get('points'),1 if kind=='point' else 2)
        closed=kind=='rectangle' or bool(spec.get('closed'))
        if closed and len(p)>2 and np.linalg.norm(p[0]-p[-1])<EPS:p=p[:-1]
        if len(p)>1 and np.any(np.linalg.norm(np.diff(p,axis=0),axis=1)<EPS):raise ValueError('Consecutive vertices must be different.')
        if kind=='point' and len(p)!=1 or kind=='line' and len(p)!=2:raise ValueError('Point needs one vertex; line needs two.')
        if closed and len(p)<3:raise ValueError('A closed profile needs at least three distinct vertices.')
        if 'radii' in spec:
            radii=np.asarray(spec['radii'],float)
            if kind!='polyline' or radii.shape!=(len(p),) or not np.isfinite(radii).all():
                raise ValueError('Provide one signed radius per curve vertex.')
            uv=plane_coordinates(p,f);rendered=[]
            for i in range(len(p) if closed else len(p)-1):
                radius=radii[i];length,position,_=segment(uv[i],uv[(i+1)%len(p)],radius)
                count=max(1,math.ceil(math.degrees(length/abs(radius))/3.75)) if abs(radius)>=1e-10 else 1
                rendered.extend(position(t) for t in np.linspace(0,1,count,endpoint=False))
            if not closed:rendered.append(uv[-1])
            spec['points']=p.tolist();spec['frame']=f
            p=np.asarray(rendered)@B[:,:2].T+origin
        out=geometry(p,closed=closed)
    elif kind=='plane':
        centre=vector(spec.get('origin',origin),'Plane centre');normal=unit(spec.get('normal',B[:,2]),'Plane normal')
        hint=vector(spec.get('x_direction',B[:,0]),'Plane width direction')
        x=hint-normal*np.dot(hint,normal)
        if np.linalg.norm(x)<EPS:
            hint=np.eye(3)[np.argmin(np.abs(normal))];x=hint-normal*np.dot(hint,normal)
        x=unit(x);y=np.cross(normal,x)
        width=number(spec.get('width',1),'Plane width',True);height=number(spec.get('height',1),'Plane height',True)
        p=centre+np.asarray([[-1,-1],[1,-1],[1,1],[-1,1]])@np.stack([x*width/2,y*height/2])
        out=geometry(p,[[0,1,2],[0,2,3]],closed=True,centre=centre.tolist())
    elif kind in ('circle','arc'):
        centre=vector(spec.get('centre',origin));r=number(spec.get('radius'),'Radius',True)
        start=number(spec.get('start_angle',0),'Start angle');end=number(spec.get('end_angle',360),'End angle')
        span=end-start if kind=='arc' else 360
        if abs(span)<1e-7 or abs(span)>360+1e-7:raise ValueError('Arc sweep must be non-zero and at most 360 degrees.')
        t=np.radians(np.linspace(start,start+span,segments if kind=='circle' else max(2,math.ceil(segments*abs(span)/360)+1),endpoint=kind!='circle'))
        p=centre+r*(np.cos(t)[:,None]*B[:,0]+np.sin(t)[:,None]*B[:,1])
        out=geometry(p,closed=kind=='circle',centre=centre.tolist(),radius=r)
    elif kind in ('box','cylinder','sphere'):
        m,_=engines();base=vector(spec.get('origin',origin))
        if kind=='box':
            size=vector(spec.get('size',[1,1,1]),'Size')
            if np.any(size<=EPS):raise ValueError('All box dimensions must be positive.')
            solid=m.Manifold.cube(size)
        elif kind=='cylinder':solid=m.Manifold.cylinder(number(spec.get('height',1),'Height',True),number(spec.get('radius',1),'Radius',True),circular_segments=segments)
        else:solid=m.Manifold.sphere(number(spec.get('radius',1),'Radius',True),circular_segments=segments)
        mesh=solid.to_mesh64();out=geometry(np.asarray(mesh.vert_properties)[:,:3]@B.T+base,mesh.tri_verts,solid=True)
    elif kind in ('alignment_profile','alignment_loft'):
        out=__import__('alignment_model').build(spec,resolve)
    elif kind=='extrude':
        obj=source('profile')
        if 'face' in spec:
            obj=face_profile(obj,spec.pop('face'))
            spec['profile']={k:copy.deepcopy(obj[k]) for k in ('vertices','faces','closed','solid')}
        out=extrude(obj,spec.get('vector'),bool(spec.get('symmetric')))
    elif kind=='sweep':
        obj=source('profile');path=source('path')
        if path.get('closed') or path.get('faces'):raise ValueError('Sweep needs an open line or polyline path.')
        out=sweep(obj,path['vertices'])
    elif kind=='loft':
        values=[]
        for row in spec.get('profiles',[]):
            obj=row if isinstance(row,dict) else resolve(row)
            if obj is None:raise ValueError('A loft source no longer exists.')
            values.append({k:copy.deepcopy(obj[k]) for k in ('vertices','closed')})
        spec['profiles']=values;out=loft(values)
    else:raise ValueError('Unknown drawing or solid command: '+kind)
    out['shape']=kind;out['definition']=spec
    return out

def curve_grip(obj, working_frame, index, grip, point):
    """Edit an analytic control point or segment on the CAD work plane."""
    q=plane_coordinates([vector(point)],working_frame)[0]
    origin,basis=matrix(working_frame)
    if obj['shape']=='circle' and grip in ('centre','radius'):
        centre=plane_coordinates([obj['centre']],working_frame)[0]
        radius=obj['radius']
        if grip=='centre':centre=q
        else:radius=float(np.linalg.norm(q-centre))
        return build(dict(kind='circle',centre=(origin+basis[:,:2]@centre).tolist(),
                          radius=radius,frame=working_frame,segments=96))
    rows=curve_nodes(obj,working_frame);closed=obj['closed']
    if not 0<=index<len(rows) or grip in ('edge','arc') and index==len(rows)-1 and not closed:
        raise ValueError('Choose a valid curve grip.')
    if grip=='node':
        if len(rows)==1:
            return build(dict(kind='point',points=[point],frame=working_frame))
        rows[index,:2]=q
    elif grip in ('edge','arc'):
        j=(index+1)%len(rows);a,b=rows[[index,j],:2];d=b-a
        length=float(np.linalg.norm(d));mid=(a+b)/2
        if grip=='edge':
            _,position,_=segment(a,b,rows[index,2])
            rows[[index,j],:2]+=q-position(.5)
        else:
            sag=float(np.dot(q-mid,[-d[1],d[0]])/length)
            sag=float(np.clip(sag,-length/2,length/2))
            rows[index,2]=-(length*length/(8*sag)+sag/2) if abs(sag)>1e-8 else 0.
    else:raise ValueError('Choose a node, edge or circle grip.')
    return planar_curve(rows,working_frame,closed)

def transformed(obj, operation, values):
    p=np.asarray(obj['vertices']);base=vector(values.get('base',[0,0,0])); A=np.eye(3); offset=np.zeros(3)
    if operation in ('move','copy'):offset=vector(values.get('vector'),'Displacement')
    elif operation=='rotate':A=rotation(values.get('axis',[0,0,1]),values.get('angle'))
    elif operation=='scale':A*=number(values.get('factor'),'Scale factor',True)
    elif operation=='mirror':n=unit(values.get('normal',[1,0,0]));A-=2*np.outer(n,n)
    elif operation=='align':
        a=points(values.get('from'),3);b=points(values.get('to'),3)
        if len(a)!=3 or len(b)!=3:raise ValueError('Alignment needs exactly three source and three destination points.')
        fa=frame(dict(origin=a[0],x_point=a[1],xy_point=a[2]));fb=frame(dict(origin=b[0],x_point=b[1],xy_point=b[2]));_,Ba=matrix(fa);_,Bb=matrix(fb)
        A=Bb@Ba.T;base=a[0];offset=b[0]-a[0]
    else:raise ValueError('Unknown transform.')
    q=(p-base)@A.T+base+offset;faces=np.asarray(obj.get('faces',[]),dtype=int).reshape(-1,3)
    if np.linalg.det(A)<0:faces=faces[:,[0,2,1]]
    out=geometry(q,faces,closed=obj.get('closed'),solid=obj.get('solid'),shape=obj['shape'])
    # A composed matrix allows later parameter edits to keep all prior transforms.
    old=np.asarray(obj.get('transform',np.eye(4)));T=np.eye(4);T[:3,:3]=A;T[:3,3]=base+offset-A@base
    out['definition']=copy.deepcopy(obj.get('definition'));out['transform']=(T@old).tolist()
    if obj.get('centre') is not None:out['centre']=((np.asarray(obj['centre'])-base)@A.T+base+offset).tolist()
    if obj.get('radius') is not None:out['radius']=obj['radius']*np.linalg.norm(A[:,0])
    return out

def update_parameters(obj, values):
    spec=copy.deepcopy(obj.get('definition'))
    if not spec:raise ValueError('This object has no editable construction parameters. Use its vertex/transform tools.')
    allowed={'radius','height','width','normal','x_direction','size','vector','symmetric','start_angle','end_angle','segments','points','centre','origin','start','end','step','horizontal','vertical','rotation'}
    if not set(values)<=allowed:raise ValueError('Unsupported construction parameter.')
    spec.update(values);out=build(spec)
    T=np.asarray(obj.get('transform',np.eye(4)));p=np.asarray(out['vertices'])@T[:3,:3].T+T[:3,3];faces=np.asarray(out['faces'],dtype=int).reshape(-1,3)
    if np.linalg.det(T[:3,:3])<0:faces=faces[:,[0,2,1]]
    result=geometry(p,faces,closed=out['closed'],solid=out['solid'],shape=out['shape'],definition=spec,transform=T.tolist())
    if out.get('centre') is not None:result['centre']=(np.asarray(out['centre'])@T[:3,:3].T+T[:3,3]).tolist()
    if out.get('radius') is not None:result['radius']=out['radius']*np.linalg.norm(T[:3,0])
    return result

def boolean(objects, operation):
    if len(objects)<2:raise ValueError('Select at least two solids; selection order determines the subtraction base.')
    m,_=engines();origin=np.asarray(objects[0]['vertices'][0]);kinds={'union':m.OpType.Add,'subtract':m.OpType.Subtract,'intersect':m.OpType.Intersect}
    if operation not in kinds:raise ValueError('Choose Union, Subtract or Intersect.')
    return from_manifold(m.Manifold.batch_boolean([as_manifold(o,origin) for o in objects],kinds[operation]),origin)

def cut(obj, point, normal, both=True):
    normal=unit(normal);point=vector(point);p=np.asarray(obj['vertices'])
    if obj.get('solid'):
        origin=p[0];m=as_manifold(obj,origin);offset=float((point-origin)@normal)
        parts=m.split_by_plane(normal,offset) if both else [m.trim_by_plane(normal,offset)]
        result=[from_manifold(v,origin) for v in parts if not v.is_empty()]
        if both and len(result)!=2:raise ValueError('The cutting plane does not cross this solid.')
        return result
    if obj.get('faces'):raise ValueError('Plane cut currently accepts solids or drawn lines/polylines.')
    q=np.vstack([p,p[0]]) if obj.get('closed') else p;chunks=[];current=[q[0]]; crossings=0
    for a,b in zip(q[:-1],q[1:]):
        da=float((a-point)@normal);db=float((b-point)@normal)
        if da*db < -EPS*EPS:
            mid=a+(b-a)*da/(da-db);current.append(mid);chunks.append(current);current=[mid];crossings+=1
        elif abs(da)<EPS and len(current)>1:
            chunks.append(current);current=[a];crossings+=1
        current.append(b)
    if len(current)>1:chunks.append(current)
    if not crossings:raise ValueError('The cutting plane does not cross this line/profile.')
    if obj.get('closed') and len(chunks)>1 and np.linalg.norm(np.asarray(chunks[-1][-1])-chunks[0][0])<EPS:
        chunks[0]=chunks[-1][:-1]+chunks[0];chunks.pop()
    if not both:chunks=[c for c in chunks if np.mean((np.asarray(c)-point)@normal)>EPS]
    return [geometry(c,shape='polyline') for c in chunks]

def break_curve(obj, point):
    p,closed=profile(obj)
    if closed:p=np.vstack([p,p[0]])
    q=vector(point);a=p[:-1];d=np.diff(p,axis=0);t=np.clip(np.einsum('ij,ij->i',q-a,d)/np.einsum('ij,ij->i',d,d),0,1);near=a+d*t[:,None];i=int(np.argmin(np.linalg.norm(near-q,axis=1)));v=near[i]
    left=np.vstack([p[:i+1],v]) if np.linalg.norm(v-p[i])>EPS else p[:i+1]
    right=np.vstack([v,p[i+1:]]) if np.linalg.norm(v-p[i+1])>EPS else p[i+1:]
    if len(left)<2 or len(right)<2:raise ValueError('Choose a break point inside the curve, away from its ends.')
    return [geometry(left,shape='polyline'),geometry(right,shape='polyline')]

def join(objects):
    if len(objects)<2:raise ValueError('Select two or more open lines/polylines in connection order.')
    arrays=[]
    for o in objects:
        p,c=profile(o)
        if c:raise ValueError('Join expects open profiles.')
        arrays.append(p)
    out=arrays.pop(0)
    for p in arrays:
        if np.linalg.norm(out[-1]-p[0])<1e-6:out=np.vstack([out,p[1:]])
        elif np.linalg.norm(out[-1]-p[-1])<1e-6:out=np.vstack([out,p[-2::-1]])
        elif np.linalg.norm(out[0]-p[-1])<1e-6:out=np.vstack([p[:-1],out])
        elif np.linalg.norm(out[0]-p[0])<1e-6:out=np.vstack([p[:0:-1],out])
        else:raise ValueError('Selected endpoints do not meet within 0.001 mm. Move them together before joining.')
    closed=np.linalg.norm(out[0]-out[-1])<1e-6
    return geometry(out[:-1] if closed else out,closed=closed,shape='polyline')

def offset(obj, distance, working_frame=None):
    distance=number(distance,'Offset')
    if abs(distance)<EPS:raise ValueError('Offset must be non-zero.')
    if obj.get('shape')=='circle' and obj.get('radius') is not None:
        f=closed_profile_frame(obj)
        return [build(dict(kind='circle',centre=obj['centre'],radius=obj['radius']+distance,frame=f,
                           segments=(obj.get('definition') or {}).get('segments',64)))]
    if working_frame and obj.get('shape')=='arc' and obj.get('definition',{}).get('kind')=='arc':
        rows=curve_nodes(obj,working_frame)
        radius=rows[0,2];centre,_,_=circle_edge(rows[0,:2],rows[1,:2],radius)
        new_radius=abs(radius)-math.copysign(1,radius)*distance
        if new_radius<=EPS:raise ValueError('The offset collapses this arc. Reduce the distance.')
        rows[:,:2]=centre+(rows[:,:2]-centre)*new_radius/abs(radius)
        rows[:-1,2]=math.copysign(new_radius,radius)
        return [planar_curve(rows,working_frame,False)]
    _,sh=engines();p,closed=profile(obj);origin,B,uv=plane(p,working_frame)
    shape=sh.Polygon(uv) if closed else sh.LineString(uv)
    if not shape.is_valid:raise ValueError('Repair the profile before offsetting.')
    value=shape.buffer(distance,join_style='mitre') if closed else sh.offset_curve(shape,distance,join_style='mitre')
    if value.is_empty:raise ValueError('The offset collapses this profile. Reduce the distance.')
    values=list(value.geoms) if hasattr(value,'geoms') else [value];out=[]
    for v in values:
        if closed and len(v.interiors):raise ValueError('This offset creates holes. Use solid subtraction for openings.')
        q=np.asarray(v.exterior.coords if closed else v.coords);q=q[:-1] if closed else q
        world=q@B[:,:2].T+origin;out.append(geometry(world,closed=closed,shape='polyline'))
    return out

def trim_extend(obj,boundary,working_frame,extend=False,end='end',keep=None):
    p,closed=profile(obj);b,_=profile(boundary)
    if len(b)!=2:raise ValueError('Choose a straight two-point line as the cutting boundary.')
    _,B=matrix(working_frame);n=B[:,2]
    if np.max(np.abs((np.vstack([p,b])-p[0])@n))>1e-6:raise ValueError('Target and boundary must lie in one plane parallel to the active work plane.')
    boundary_normal=unit(np.cross(b[1]-b[0],n),'Boundary normal')
    if not extend:
        parts=cut(obj,b[0],boundary_normal)
        if keep is None:raise ValueError('Pick a point on the portion to keep after trimming.')
        q=vector(keep)
        def distance(o):
            v=np.asarray(o['vertices']);a=v[:-1];d=np.diff(v,axis=0);t=np.clip(np.einsum('ij,ij->i',q-a,d)/np.einsum('ij,ij->i',d,d),0,1)
            return float(np.linalg.norm(a+d*t[:,None]-q,axis=1).min())
        return min(parts,key=distance)
    if closed:raise ValueError('Extend requires an open line or polyline.')
    q=p.copy() if end=='end' else p[::-1].copy();d=q[-1]-q[-2];den=float(d@boundary_normal)
    if abs(den)<EPS:raise ValueError('The endpoint direction is parallel to this boundary.')
    t=float((b[0]-q[-1])@boundary_normal/den)
    if t<=EPS:raise ValueError('This boundary lies behind the selected endpoint. Choose the other end or Trim.')
    q[-1]+=d*t
    return geometry(q if end=='end' else q[::-1],shape='polyline')
