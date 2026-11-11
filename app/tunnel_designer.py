# Copyright (c) 2026 Damian Gulczynski.
"""Separate plan, chainage/height, theoretical-profile and section definitions."""
import copy, math, uuid
import numpy as np
import cad_geometry as g

def default():
    return dict(name='Tunnel design',start=0,plan=[[0,0,0],[20,0,0]],vertical=[[0,0],[20,0]],
                profiles=[dict(id='p1',name='Section A',kind='circle',radius=3,width=6,height=6,x=0,y=0)],
                sections=[dict(start=0,end=20,first='p1',last='p1')],spacing=1,orientation='vertical',detail='balanced')

def from_active_alignment(axis):
    """Copy an active centreline into a new, independently editable design.

    Designer chainage follows horizontal plan length. The active alignment and
    its supplied station values remain unchanged; callers expose any station
    recalculation to the user. A vertical-only segment needs the advanced sweep
    modeller rather than a plan/height design.
    """
    rows=np.asarray(axis.a,dtype=float)
    if rows.ndim!=2 or rows.shape[1]<5 or not 2<=len(rows)<=10000 or not np.isfinite(rows).all():
        raise ValueError('The active alignment needs two to 10,000 finite coordinate nodes.')
    lengths=np.linalg.norm(np.diff(rows[:,1:3],axis=0),axis=1)
    if np.any(lengths<1e-8):
        raise ValueError('The active alignment has a segment with no horizontal length. Edit its coordinates or use Advanced profile sweep.')
    stations=np.r_[rows[0,0],rows[0,0]+np.cumsum(lengths)]
    design=default();design.update(name=(str(axis.name)+' model')[:120],start=float(stations[0]),
        plan=np.column_stack([rows[:,1:3],np.zeros(len(rows))]).tolist(),
        vertical=np.column_stack([stations,rows[:,3]]).tolist())
    if rows[0,4]>0:
        radius=float(rows[0,4]);design['profiles'][0].update(radius=radius,width=radius*2,height=radius*2)
    design['sections']=[dict(start=float(stations[0]),end=float(stations[-1]),first='p1',last='p1',type='constant')]
    return design,not np.allclose(stations,rows[:,0],rtol=0,atol=1e-6)

def finite(value,label):return g.number(value,label)

def distinct_stations(values):
    # Screen picks and entered heights can describe the same station with tiny
    # floating-point differences. Never build two almost coincident profile rings.
    values=np.sort(np.asarray(values,dtype=float));kept=[]
    for value in values:
        if not kept or value-kept[-1]>1e-8:kept.append(float(value))
    if len(kept)>1:kept[-1]=float(values[-1])
    return np.asarray(kept)

def segment(a,b,radius=0):
    a=np.asarray(a,float);b=np.asarray(b,float);d=b-a;length=float(np.linalg.norm(d));r=float(radius)
    if not np.isfinite([*a,*b,r]).all() or length<1e-8:raise ValueError('Consecutive plan/profile points must be finite and different.')
    if abs(r)<1e-10:return length,lambda f:a+f*d,lambda f:d/length
    R=abs(r)
    if 2*R<length-1e-8:raise ValueError(f'Arc radius {R:g} m is too small for its {length:g} m chord; use at least {length/2:g} m.')
    sign=1 if r>0 else -1;centre=(a+b)/2+sign*np.array([-d[1],d[0]])/length*math.sqrt(max(0,R*R-length*length/4))
    angle=math.atan2(a[1]-centre[1],a[0]-centre[0]);sweep=sign*2*math.asin(min(1,length/(2*R)))
    def position(f):return centre+R*np.array([math.cos(angle+f*sweep),math.sin(angle+f*sweep)])
    def tangent(f):return sign*np.array([-math.sin(angle+f*sweep),math.cos(angle+f*sweep)])
    return abs(sweep)*R,position,tangent

def alignment(design):
    plan=np.asarray(design.get('plan'),float);vertical=np.asarray(design.get('vertical'),float)
    if plan.ndim!=2 or plan.shape[1]!=3 or not 2<=len(plan)<=10000 or not np.isfinite(plan).all():raise ValueError('Enter at least two horizontal E/N nodes. Radius 0 means straight to the next node.')
    if vertical.ndim!=2 or vertical.shape[1]!=2 or not 2<=len(vertical)<=10000 or not np.isfinite(vertical).all():raise ValueError('Enter at least two chainage / height rows for this design.')
    if np.any(np.diff(vertical[:,0])<=0):raise ValueError('Vertical chainages must increase; each chainage has one height.')
    start=finite(design.get('start',0),'Start chainage');pieces=[];stations=[start]
    for a,b in zip(plan[:-1],plan[1:]):
        piece=segment(a[:2],b[:2],a[2]);pieces.append(piece);stations.append(stations[-1]+piece[0])
    stations=np.array(stations);end=float(stations[-1]);slopes=np.diff(vertical[:,1])/np.diff(vertical[:,0])
    if vertical[0,0]>start+1e-7 or vertical[-1,0]<end-1e-7:raise ValueError(f'Height rows must cover the alignment from {start:.3f} to {end:.3f} m. Add heights at both ends.')
    def at(ch):
        if not start-1e-7<=ch<=end+1e-7:raise ValueError('Section chainage falls outside the horizontal alignment.')
        i=max(0,min(len(pieces)-1,int(np.searchsorted(stations,ch,side='right')-1)));length,pos,tan=pieces[i];f=np.clip((ch-stations[i])/length,0,1)
        xy=pos(f);direction=tan(f)
        if i and abs(ch-stations[i])<1e-7:
            old=pieces[i-1][2](1);combined=direction+old
            if np.linalg.norm(combined)<1e-8:raise ValueError('The horizontal alignment reverses direction at a node.')
            direction=combined/np.linalg.norm(combined)
        j=max(0,min(len(slopes)-1,int(np.searchsorted(vertical[:,0],ch,side='right')-1)))
        centre=np.r_[xy,np.interp(ch,vertical[:,0],vertical[:,1])];right=np.array([direction[1],-direction[0],0.])
        if design.get('orientation','vertical')=='vertical':up=np.array([0.,0.,1.])
        else:
            tangent=np.r_[direction,slopes[j]];tangent/=np.linalg.norm(tangent);up=np.cross(right,tangent);up/=np.linalg.norm(up)
        return centre,right,up
    return stations,vertical,slopes,at


def join_drawing_curves(parts):
    """Join snapped endpoints while retaining the signed radius of each edge."""
    remaining = []
    for part in parts:
        nodes = np.asarray(part.get('nodes'), dtype=float)
        if nodes.ndim != 2 or nodes.shape[1] != 3 or not 2 <= len(nodes) <= 512 or not np.isfinite(nodes).all():
            raise ValueError('Finish the selected lines and polylines before joining them.')
        remaining.append(nodes + [finite(part.get('x', 0), 'Curve X offset'),
                                  finite(part.get('y', 0), 'Curve Y offset'), 0])

    def reverse(nodes):
        out = nodes[::-1].copy()
        out[:, 2] = np.r_[-nodes[-2::-1, 2], 0]
        return out

    def touches(a, b):
        return np.linalg.norm(a[:2] - b[:2]) <= 1e-6

    path = remaining.pop(0)
    while remaining:
        for i, nodes in enumerate(remaining):
            if touches(path[-1], nodes[0]):
                tail = nodes
            elif touches(path[-1], nodes[-1]):
                tail = reverse(nodes)
            elif touches(path[0], nodes[-1]):
                path, tail = nodes, path
            elif touches(path[0], nodes[0]):
                path, tail = reverse(nodes), path
            else:
                continue
            path = np.vstack([path[:-1], tail])
            remaining.pop(i)
            break
        else:
            raise ValueError('Snap the selected curve endpoints together before making a profile.')
    if len(path) < 4 or not touches(path[0], path[-1]):
        raise ValueError('The selected lines and polylines must meet in one closed loop.')
    return dict(kind='custom', nodes=path[:-1].tolist())


def with_drawing_parts(profile, drawings):
    if profile.get('kind') != 'composite':
        return profile
    ids = profile.get('drawing_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 128 or any(not isinstance(i, str) for i in ids):
        raise ValueError('Select the drawing objects that form this profile.')
    objects = {obj.get('id'): obj for obj in drawings if isinstance(obj, dict)}
    parts = [objects.get(ident) for ident in dict.fromkeys(ids)]
    if any(not p or p.get('kind') not in ('circle', 'rectangle', 'arch', 'custom', 'cad', 'polyline') for p in parts):
        raise ValueError('A drawing object used by this profile is missing or unsupported.')
    return {**profile, 'parts': parts}


def outline(profile,step_deg=5):
    kind=profile.get('kind');x=finite(profile.get('x',0),'Profile horizontal offset');y=finite(profile.get('y',0),'Profile vertical offset')
    if kind=='circle':
        r=g.number(profile.get('radius') or 0,'Profile radius',True);t=np.linspace(0,2*np.pi,max(16,math.ceil(360/step_deg)),endpoint=False)
        points=np.column_stack([r*np.cos(t),r*np.sin(t)])
    elif kind in ('rectangle','arch'):
        w=g.number(profile.get('width'),'Profile width',True);h=g.number(profile.get('height'),'Profile height',True)
        if kind=='rectangle':points=np.array([[-w/2,-h/2],[w/2,-h/2],[w/2,h/2],[-w/2,h/2]])
        else:
            if h<w/2:raise ValueError('An arched profile height must be at least half its width.')
            t=np.linspace(0,np.pi,max(8,math.ceil(180/step_deg))+1);points=np.vstack([[-w/2,-h/2],[w/2,-h/2],np.column_stack([w/2*np.cos(t),h/2-w/2+w/2*np.sin(t)])])
            points=points[np.r_[True,np.linalg.norm(np.diff(points,axis=0),axis=1)>1e-9]]
            if np.linalg.norm(points[0]-points[-1])<1e-9:points=points[:-1]
    elif kind=='custom':
        rows=np.asarray(profile.get('nodes'),float)
        if rows.ndim!=2 or rows.shape[1]!=3 or not 3<=len(rows)<=512 or not np.isfinite(rows).all():raise ValueError('A custom profile needs three or more X/Y nodes, with radius to the next node (0 = straight).')
        points=[]
        for a,b in zip(rows,np.roll(rows,-1,axis=0)):
            length,pos,_=segment(a[:2],b[:2],a[2]);n=max(1,math.ceil(math.degrees(length/abs(a[2]))/step_deg)) if a[2] else 1
            points.extend(pos(f) for f in np.linspace(0,1,n,endpoint=False))
        points=np.array(points)
    elif kind=='cad':
        obj=profile.get('geometry')
        if not obj:raise ValueError('Choose an existing closed CAD profile.')
        origin,basis=g.matrix(g.closed_profile_frame(obj))
        points=(np.asarray(obj['vertices'])-origin)@basis[:,:2]
    elif kind == 'composite':
        parts = profile.get('parts') or []
        if not parts:
            raise ValueError('Select the drawing objects that form this profile.')
        if all(part['kind'] == 'polyline' for part in parts):
            points = outline(join_drawing_curves(parts), step_deg)
        elif any(part['kind'] == 'polyline' for part in parts):
            raise ValueError('Close the open curves first, or select only the open curves to join.')
        elif len(parts) == 1:
            points = outline(parts[0], step_deg)
        else:
            _, shapely = g.engines()
            union = shapely.union_all([shapely.Polygon(outline(part, step_deg)) for part in parts])
            if union.geom_type != 'Polygon' or union.is_empty or len(union.interiors):
                raise ValueError('Select overlapping shapes that form one closed profile without internal openings.')
            points = np.asarray(union.exterior.coords[:-1])
    else:raise ValueError('Choose Circle, Rectangle, Arched, Lines/arcs or an existing profile.')
    points=points+[x,y]
    # Canonical perimeter direction and crown anchor make transitions reproducible.
    if np.sum(points[:,0]*np.roll(points[:,1],-1)-points[:,1]*np.roll(points[:,0],-1))<0:points=points[::-1]
    top=float(points[:,1].max());ids=np.flatnonzero(np.abs(points[:,1]-top)<1e-8)
    if len(ids)>1:
        middle=(points[ids].min(0)+points[ids].max(0))/2
        if not np.any(np.linalg.norm(points-middle,axis=1)<1e-8):
            for i in range(len(points)):
                if abs(points[i,1]-top)<1e-8 and abs(points[(i+1)%len(points),1]-top)<1e-8:
                    points=np.insert(points,i+1,middle,axis=0);break
        anchor=int(np.argmin(np.linalg.norm(points-middle,axis=1)))
    else:anchor=int(ids[0])
    points=np.roll(points,-anchor,axis=0);g.polygon(np.column_stack([points,np.zeros(len(points))]))
    return points

def curve_step(detail):
    return {'fast':10,'balanced':5,'fine':2}.get(detail,5)

def profile_copy(profile, operation, detail, distance=None):
    """Keep analytic dimensions and signed arc radii where the operation permits."""
    profile=copy.deepcopy(profile);kind=profile.get('kind');step=curve_step(detail)
    source=outline(profile,step);faceted=False
    source_profile_nodes=profile.get('nodes',[])
    if operation=='mirror':
        if kind in ('cad','composite'):
            profile=dict(kind='custom',nodes=np.column_stack([-source[:,0],source[:,1],np.zeros(len(source))]).tolist(),x=0,y=0)
        else:
            profile['x']=-finite(profile.get('x',0),'Profile horizontal offset')
            if kind=='custom':
                profile['nodes']=[[-x,y,-radius] for x,y,radius in np.asarray(profile['nodes'],dtype=float)]
    elif operation=='offset':
        distance=finite(distance,'Profile offset')
        if abs(distance)<g.EPS:
            raise ValueError('Enter a non-zero offset.')
        if kind=='circle':
            profile['radius']=finite(profile['radius'],'Radius')+distance
        elif kind=='rectangle' or (kind=='arch' and float(profile['height'])+distance>=float(profile['width'])/2):
            for key in ('width','height'):
                profile[key]=finite(profile[key],key.title())+2*distance
        else:
            points=np.column_stack([source,np.zeros(len(source))])
            offsets=g.offset(g.geometry(points,closed=True),distance)
            if len(offsets)!=1:
                raise ValueError('This offset splits the outline. Use a smaller distance.')
            profile=dict(kind='custom',nodes=offsets[0]['vertices'],x=0,y=0)
            faceted=kind in ('arch','cad','composite') or any(float(row[2]) for row in source_profile_nodes)
    else:
        raise ValueError('Choose Offset or Mirror.')
    if len(outline(profile,step))>512:
        raise ValueError('The new outline exceeds 512 vertices. Use a simpler profile or lower curve detail.')
    return profile,faceted

def alignment_preview(design):
    """Keep the centreline usable while profiles or section ranges are unfinished."""
    stations,vertical,slopes,at=alignment(design)
    samples=list(stations)+[v for v in vertical[:,0] if stations[0]<=v<=stations[-1]]
    # Resolve arcs closely for screen picking; straight segments need only their knots.
    for i,row in enumerate(design['plan'][:-1]):
        radius=abs(float(row[2]))
        if radius:
            angle=min(math.radians(.5),2*math.acos(max(-1,1-.002/radius)))
            count=min(2048,max(2,math.ceil((stations[i+1]-stations[i])/radius/max(angle,1e-6))))
            samples.extend(np.linspace(stations[i],stations[i+1],count+1))
    if len(samples)>50000:raise ValueError('This alignment has too much curve detail for one design. Split it into shorter designs.')
    samples=distinct_stations(samples)
    return dict(start=float(stations[0]),end=float(stations[-1]),horizontal_chainages=stations.tolist(),
                vertical=vertical.tolist(),gradients_percent=(slopes*100).tolist(),
                plan_chainages=samples.tolist(),plan=[at(x)[0].tolist() for x in samples])

class ModelRangeError(ValueError):
    """The selected range exceeds the model geometry budget."""


class ProfileError(ValueError):
    def __init__(self, profile, error):
        self.profile_id = profile['id']
        super().__init__(f"Profile “{profile.get('name') or self.profile_id}” · {error}")


def spaced_stations(mandatory, spacing, maximum):
    """Keep each design break and subdivide the gaps within the vertex budget."""
    gaps = np.diff(mandatory)

    def intervals(limit):
        return np.maximum(1, np.ceil(np.nextafter(gaps / limit, -np.inf)))

    counts = intervals(spacing)
    if counts.sum() + 1 > maximum:
        lower, upper = spacing, float(gaps.max())
        for _ in range(48):
            middle = (lower + upper) / 2
            if intervals(middle).sum() + 1 <= maximum:
                upper = middle
            else:
                lower = middle
        counts = intervals(upper)
    pieces = [np.linspace(a, b, n + 1)[:-1]
              for a, b, n in zip(mandatory, mandatory[1:], counts.astype(int))]
    return np.concatenate([*pieces, mandatory[-1:]])


def prepare(design, *, vertex_budget=g.MAX_PROJECT_VERTICES, legacy_sampling=False, preview=False):
    design = copy.deepcopy(design)
    stations, vertical, _, at = alignment(design)
    profiles = design.get('profiles', [])
    if not profiles or len(profiles) > 32:
        raise ValueError('Define between 1 and 32 theoretical profiles.')
    ids = [p.get('id') for p in profiles]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Profile identifiers must be unique.')
    sections = design.get('sections', [])
    if not sections or len(sections) > 1000:
        raise ValueError('Add at least one chainage section.')
    used = set()
    previous = None
    smooth_samples = []
    for section in sections:
        section['start'] = finite(section.get('start'), 'Section start')
        section['end'] = finite(section.get('end'), 'Section end')
        section.setdefault('last', section.get('first'))
        if section.get('type') not in (None, 'constant', 'interpolate'):
            raise ValueError('Choose Constant theoretical profile or Interpolate between profiles.')
        if section.get('type') == 'constant':
            section['last'] = section.get('first')
        if section.get('interpolation', 'linear') not in ('linear', 'smoothstep'):
            raise ValueError('Choose Linear or Smooth transition.')
        first, last = section.get('first'), section['last']
        if first not in ids or last not in ids:
            raise ValueError('Choose available start and end profiles for every section.')
        used.update((first, last))
        span = section['end'] - section['start']
        if span < 1e-6:
            raise ValueError('Every section or transition needs a positive length.')
        at(section['start'])
        at(section['end'])
        if previous and abs(section['start'] - previous['end']) > 1e-6:
            raise ValueError('Consecutive sections must meet exactly, without a gap or overlap.')
        if section.get('interpolation') == 'smoothstep' and first != last:
            smooth_samples.extend(section['start'] + span * t for t in (.25, .5, .75))
        previous = section

    if legacy_sampling:
        used = set(ids)
    outlines = {}
    step = curve_step(design.get('detail'))
    for profile in profiles:
        try:
            outlines[profile['id']] = outline(with_drawing_parts(profile, design.get('drawing_objects', [])), step)
        except ValueError as exc:
            if profile['id'] in used:
                raise ProfileError(profile, exc) from exc
    fractions = {}
    for ident in used:
        points = outlines[ident]
        lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        if np.any(lengths < 1e-9):
            raise ProfileError(profiles[ids.index(ident)], 'Remove duplicate profile nodes.')
        fractions[ident] = np.r_[0, np.cumsum(lengths)] / lengths.sum()
    # A single outline already supplies every corner and arc sample. Only
    # profile transitions need a common perimeter grid.
    if len(used) == 1 and not legacy_sampling:
        sampled = {ident: outlines[ident] for ident in used}
    else:
        grids = list(fractions.values())
        if legacy_sampling:
            grids.append(np.linspace(0, 1, 65))
        knots = np.unique(np.round(np.concatenate(grids), 12))
        knots = knots[knots < 1]
        sampled = {}
        for ident, fractions_at_nodes in fractions.items():
            points = outlines[ident]
            sampled[ident] = np.column_stack([
                np.interp(knots, fractions_at_nodes, np.r_[points[:, j], points[0, j]]) for j in range(2)
            ])
    count = len(next(iter(sampled.values())))
    if count > 512:
        raise ValueError('These profiles contain too much combined curve detail. Choose Balanced/Fast or simplify imported profiles.')
    for left, right in zip(sections, sections[1:]):
        if not np.allclose(sampled[left['last']], sampled[right['first']], atol=1e-7, rtol=0):
            raise ValueError('Profiles change abruptly at a section boundary. Add a positive-length transition or match the touching profiles.')

    spacing = g.number(design.get('spacing', 1), 'Profile spacing', True)
    start, end = sections[0]['start'], sections[-1]['end']
    mandatory = distinct_stations(np.r_[
        stations[(stations > start) & (stations < end)],
        vertical[(vertical[:, 0] > start) & (vertical[:, 0] < end), 0],
        smooth_samples, *[[section['start'], section['end']] for section in sections]
    ])
    if legacy_sampling:
        maximum = min(2048, g.MAX_VERTICES // count, (g.MAX_FACES - 2 * count) // (2 * count) + 1)
    else:
        maximum = max(0, min(g.MAX_PROJECT_VERTICES, int(vertex_budget)) // count)
    if len(mandatory) > maximum:
        raise ModelRangeError(f'This range needs {len(mandatory):,} cross-sections at alignment and profile stations. '
                              f'The project has room for {maximum:,} at this profile detail. Choose a shorter range.')
    if legacy_sampling:
        intervals = min(max(1, math.ceil((end - start) / spacing)), maximum - len(mandatory) + 1)
        samples = distinct_stations(np.r_[mandatory, np.linspace(start, end, intervals + 1)])
    else:
        samples = spaced_stations(mandatory, spacing, maximum)

    preview_ids = set(np.linspace(0, len(samples) - 1, min(30, len(samples))).astype(int))
    ring_ids = np.unique(np.r_[np.arange(0, count, max(1, count // 96)), count - 1])
    section_ids = np.minimum(len(sections) - 1, np.searchsorted(
        [section['end'] for section in sections], samples, side='right'))
    loops, centres, rings = [], [], []
    for xy in sampled.values():
        g.polygon(np.column_stack([xy, np.zeros(count)]))
    for index, (station, section_id) in enumerate(zip(samples, section_ids)):
        section = sections[section_id]
        first, last = section['first'], section['last']
        xy = sampled[first]
        if first != last or legacy_sampling:
            t = float(np.clip((station - section['start']) / (section['end'] - section['start']), 0, 1))
            if section.get('interpolation') == 'smoothstep':
                t = t * t * (3 - 2 * t)
            xy = xy * (1 - t) + sampled[last] * t
            if first != last:
                g.polygon(np.column_stack([xy, np.zeros(count)]))
        if preview and index not in preview_ids:
            continue
        centre, right, up = at(station)
        points = centre + xy[:, 0, None] * right + xy[:, 1, None] * up
        centres.append(centre.tolist())
        if not preview:
            loops.append(dict(vertices=points, closed=True))
        if index in preview_ids:
            rings.append(points[ring_ids].tolist())
    diagnostics = alignment_preview(design)
    diagnostics.update(profiles=[dict(id=p['id'], name=p.get('name', p['id']), points=outlines[p['id']].tolist())
                                 for p in profiles if p['id'] in outlines],
                       profile_count=len(samples), required_sections=len(mandatory), requested_spacing=spacing,
                       maximum_spacing=float(np.diff(samples).max()), vertices=len(samples) * count,
                       orientation=design.get('orientation', 'vertical'), rings=rings, centres=centres)
    return design, loops, diagnostics


def build(design, *, vertex_budget=g.MAX_PROJECT_VERTICES, legacy_sampling=False):
    clean, loops, info = prepare(design, vertex_budget=vertex_budget, legacy_sampling=legacy_sampling)
    value = g.loft_rings([ring['vertices'] for ring in loops], shape='tunnel_design')
    definition = dict(kind='tunnel_design', design=clean)
    if not legacy_sampling:
        definition['vertex_budget'] = vertex_budget
    value.update(definition=definition, design_summary={k: v for k, v in info.items()
                 if k not in ('rings', 'centres', 'profiles', 'plan', 'plan_chainages')})
    return value, info


def resolve_profiles(s,design, *, allow_incomplete=False):
    import cad_api
    design=copy.deepcopy(design)
    for p in [*design.get('profiles',[]), *design.get('drawing_objects',[])]:
        if p.get('kind')=='cad' and p.get('object_id'):
            obj=next((o for o in cad_api.current(s)['objects'] if o['id']==p['object_id']),None)
            if obj:
                if obj.get('faces') or not obj.get('closed'):
                    if allow_incomplete:
                        p.pop('geometry', None)
                        continue
                    raise ValueError('The linked CAD object must be a closed outline.')
                p['geometry']={k:copy.deepcopy(obj[k]) for k in ('vertices','closed','definition','transform') if k in obj}
            elif not p.get('geometry') and not allow_incomplete:
                raise ValueError('The selected CAD profile no longer exists. Choose another profile.')
    return design

def profile_operation(s,body):
    import cad_api
    cad_api.check(s,body)
    operation=body.get('operation')
    if operation == 'drawing_frame':
        station = finite(body.get('chainage'), 'Profile plane chainage')
        _, _, _, at = alignment({**body.get('design', {}), 'orientation': 'normal'})
        origin, right, up = at(station)
        return dict(frame=g.frame(dict(name=f'Tunnel section · Ch. {station:.3f} m', origin=origin, x=right, y=up)))
    if operation in ('link','edit_frame'):
        obj=cad_api.get_object(cad_api.current(s),body.get('object_id'))
        if operation=='edit_frame':
            if not g.profile_vertex_editable(obj):
                raise ValueError('Use a closed CAD polyline or rectangle for graphical vertex editing.')
            return dict(frame=g.closed_profile_frame(obj))
        profile=dict(kind='cad',name=obj['name'],object_id=obj['id'],x=0,y=0)
        resolved=resolve_profiles(s,dict(profiles=[profile]))['profiles'][0]
        if len(outline(resolved,curve_step(body.get('detail'))))>512:
            raise ValueError('Use a CAD outline with at most 512 profile vertices.')
        return dict(profile=profile)
    profile=body.get('profile')
    if not isinstance(profile,dict):
        raise ValueError('Choose a theoretical profile first.')
    profile=resolve_profiles(s,dict(profiles=[profile]))['profiles'][0]
    if profile.get('kind') == 'composite':
        drawings = resolve_profiles(s, dict(drawing_objects=body.get('drawing_objects', [])), allow_incomplete=True)
        profile = with_drawing_parts(profile, drawings['drawing_objects'])
    if operation=='outline':
        return dict(points=outline(profile,curve_step(body.get('detail'))).tolist())
    result,faceted=profile_copy(profile,operation,body.get('detail'),body.get('distance'))
    return dict(profile=result,faceted=faceted)

def get(s,h,path,args):
    if path!='/api/tunnel-design':return False
    fresh=args.get('new')==['1'] or not s.STATE.get('tunnel_design')
    value=copy.deepcopy(default() if fresh else s.STATE['tunnel_design']);ident=args.get('model',[None])[0]
    if ident:
        import cad_api
        obj=cad_api.get_object(cad_api.current(s),ident)
        if obj.get('definition',{}).get('kind')!='tunnel_design':raise ValueError('Select a model created with Tunnel designer.')
        value=dict(design=copy.deepcopy(obj['definition']['design']),model_id=ident)
    if 'design' not in value:value=dict(design=value,model_id=None)
    import cad_api
    value.update(context=cad_api.context(s),revision=cad_api.current(s)['revision'])
    axis=s.STATE.get('axis');value['active_alignment']=dict(name=axis.name,rows=axis.a.tolist()) if axis is not None else None
    value['initial_step']=2 if value.get('model_id') else 0
    if fresh and not ident and axis is not None:
        try:
            value['design'],value['alignment_chainages_recalculated']=from_active_alignment(axis)
            value.update(initial_step=2,initialized_from_alignment=True)
        except ValueError as exc:
            # Keep a new design usable when a 3D-only centreline cannot be
            # represented by this plan/height editor.
            value['initialization_warning']=str(exc)
    h.send(value);return True

def available_vertices(data, replacing=None):
    return g.MAX_PROJECT_VERTICES - sum(len(obj['vertices']) for obj in data['objects'] if obj['id'] != replacing)


def post(s,h,path,body):
    if path not in ('/api/tunnel-design-preview','/api/tunnel-design-action'):return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation.')
    import cad_api
    if path.endswith('preview'):
        if 'operation' in body:
            h.send(profile_operation(s,body));return True
        design=body.get('design',{})
        resolved=resolve_profiles(s, design, allow_incomplete=True)
        try:
            budget = available_vertices(cad_api.current(s), body.get('model_id'))
            _,_,info=prepare(resolved, vertex_budget=budget, preview=True)
        except ValueError as exc:
            # Profiles can be edited independently of the loft range or budget.
            info=alignment_preview(design)
            profiles=[]
            for profile in resolved.get('profiles', []):
                try:
                    points=outline(with_drawing_parts(profile, resolved.get('drawing_objects', [])), curve_step(resolved.get('detail')))
                except ValueError:
                    continue
                profiles.append(dict(id=profile.get('id'),name=profile.get('name'),points=points.tolist()))
            info.update(model_error=str(exc),range_limited=isinstance(exc,ModelRangeError),
                        profile_error_id=getattr(exc, 'profile_id', None),
                        profiles=profiles,rings=[],centres=[])
        h.send(info);return True
    design=resolve_profiles(s, body.get('design',{}), allow_incomplete=True)
    cad_api.check(s,body)
    action=body.get('action');old=None;old_selection=getattr(s,'SELECTION',None)
    try:
        if action=='save':
            # Drafts may be incomplete while the user creates a CAD profile.
            import json
            json.dumps(design,allow_nan=False)
            model_id=body.get('model_id')
        elif action=='build':
            current=cad_api.current(s);model_id=body.get('model_id')
            separate_copy=body.get('separate_copy',False)
            if not isinstance(separate_copy,bool):raise ValueError('Separate copy must be true or false.')
            if separate_copy and not model_id:raise ValueError('Choose the existing design model to make a separate copy.')
            obj=next((o for o in current['objects'] if o['id']==model_id),None)
            if model_id and obj is None:raise ValueError('The previous design model was removed. Create a new model instead.')
            if obj and obj.get('definition',{}).get('kind')!='tunnel_design':
                raise ValueError('Only a Tunnel designer model can be updated here.')
            budget=available_vertices(current, None if separate_copy else model_id)
            value,_=build(design, vertex_budget=budget)
            data=copy.deepcopy(current)
            if obj:
                obj=cad_api.get_object(data,model_id)
                T=np.asarray(obj.get('transform',np.eye(4)),float)
                if T.shape!=(4,4) or not np.isfinite(T).all() or not np.allclose(T[3],[0,0,0,1],rtol=0,atol=1e-12):raise ValueError('The saved model transform is invalid.')
                if not np.array_equal(T,np.eye(4)):
                    p=np.asarray(value['vertices']);faces=np.asarray(value['faces'],dtype=int)
                    if np.linalg.det(T[:3,:3])<0:faces=faces[:,[0,2,1]]
                    value=g.geometry(p@T[:3,:3].T+T[:3,3],faces,solid=True,shape='tunnel_design',definition=value['definition'],transform=T.tolist(),design_summary=value['design_summary'])
                if separate_copy:
                    name=(str(design.get('name') or 'Tunnel design')[:115]+' copy')[:120]
                    design['name']=name;value['definition']['design']['name']=name
                    new=cad_api.object_record(value,dict(name=name,layer=obj['layer'],colour=obj['colour'],bundle_id=obj.get('bundle_id')))
                    data['objects'].append(new);model_id=new['id']
                else:
                    cad_api.replace_geometry(obj,value);obj['name']=str(design.get('name') or 'Tunnel design')[:120]
            else:
                if not any(x['name']=='Model' for x in data['layers']):data['layers'].append(dict(name='Model',visible=True))
                obj=cad_api.object_record(value,dict(name=design.get('name') or 'Tunnel design',layer='Model',colour='#62bdcf'));data['objects'].append(obj);model_id=obj['id']
            if sum(len(o['vertices']) for o in data['objects'])>cad_api.MAX_PROJECT_VERTICES:raise ValueError('The project model capacity is reached. Split the design into another project.')
            cad_api.check(s,body);old=copy.deepcopy(s.STATE)
            data.update(revision=uuid.uuid4().hex,last_created=[model_id]);s.STATE['cad']=data
        elif action=='use_alignment':
            from core import Alignment
            stations,vertical,slopes,at=alignment(design);spacing=g.number(design.get('spacing',1),'Alignment spacing',True)
            samples=distinct_stations(np.r_[stations,vertical[(vertical[:,0]>=stations[0])&(vertical[:,0]<=stations[-1]),0],np.linspace(stations[0],stations[-1],min(10000,max(2,math.ceil((stations[-1]-stations[0])/spacing)+1)))])
            radius=g.number(body.get('radius',3),'Reference radius',True);rows=[dict(chainage=float(ch),e=at(ch)[0][0],n=at(ch)[0][1],h=at(ch)[0][2],radius=radius) for ch in samples]
            cad_api.check(s,body);old=copy.deepcopy(s.STATE)
            s.STATE.update(axis=Alignment(rows,str(design.get('name') or 'Designed alignment')),axis_provenance={'kind':'designed_alignment'},cloud=None,derived=None,rings=[],valid_points=0,joints=None,structures=[],edits=[],redo=[],audit=[],report_ready=False,clean_ready=False,export_ready=None,demo=False);s.SELECTION=None;model_id=body.get('model_id')
        else:raise ValueError('Choose Save design, Build model or Use alignment.')
        if old is None:cad_api.check(s,body);old=copy.deepcopy(s.STATE)
        s.STATE['tunnel_design']=dict(design=design,model_id=model_id);s.save()
    except Exception:
        if old is not None:s.STATE.clear();s.STATE.update(old);s.SELECTION=old_selection
        raise
    h.send(dict(saved=True,model_id=model_id,action=action));return True
