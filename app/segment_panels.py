"""Build reviewable panels between observed ring boundaries in the fitted tunnel frame."""
import math
import numpy as np
from scipy.optimize import brentq


def angular_dividers(measured, count, offset_deg, mid, radius):
    """Preserve measured fits and apportion missing sectors among their angular gaps."""
    circumference = 2 * math.pi * radius
    if not measured:
        return [dict(arc_intercept_m=(math.radians(offset_deg) + 2*math.pi*i/count)*radius % circumference,
                     arc_slope=0., station_m=mid)
                for i in range(count)]
    if len(measured) >= count:
        return measured
    starts = np.array([s['arc_intercept_m'] for s in measured])
    gaps = np.diff(np.r_[starts, starts[0]+circumference])
    portions = np.ones(len(gaps), dtype=int)
    for _ in range(count-len(gaps)):
        # Allocate a missing joint to the widest remaining nominal panel.
        portions[np.argmax(gaps / portions)] += 1
    result = []
    for i, first in enumerate(measured):
        second = measured[(i+1) % len(measured)]
        result.append(first)
        for j in range(1, portions[i]):
            fraction = j / portions[i]
            result.append(dict(arc_intercept_m=(starts[i]+fraction*gaps[i]) % circumference,
                               station_m=mid,
                               arc_slope=(1-fraction)*first['arc_slope']+fraction*second['arc_slope']))
    return result


def panel_geometry(model, left, right, first, second, world, ring_at):
    """Trace all four intersections on the same varying lining profile used for labels."""
    radius = model['radius']
    def angle(seam, station):
        return (seam['arc_intercept_m']+seam['arc_slope']*(station-seam['station_m']))/radius
    def intersection(ring, seam):
        coef = ring['coefficients']
        width = math.hypot(*coef[1:])
        if width == 0:
            return coef[0]
        return brentq(lambda t: t-ring_at(coef, angle(seam,t)),
                      coef[0]-width-1e-9, coef[0]+width+1e-9)
    def ring_edge(ring, start, end):
        arc = (end-start) % (2*math.pi)
        steps = max(2, math.ceil(arc / math.radians(9)))
        angles = np.linspace(start,start+arc,steps+1)
        return world(ring_at(ring['coefficients'],angles),angles,model)
    def side_edge(seam, start, end):
        stations = np.linspace(start,end,4)
        return world(stations,angle(seam,stations),model)
    a,b = intersection(left,first),intersection(left,second)
    c,d = intersection(right,first),intersection(right,second)
    edges = [ring_edge(left, angle(first,a),angle(second,b)), side_edge(second,b,d),
             ring_edge(right, angle(first,c),angle(second,d))[::-1], side_edge(first,c,a)]
    mid = (left['station_m']+right['station_m'])/2
    start = angle(first,mid)
    middle = start+((angle(second,mid)-start) % (2*math.pi))/2
    mid_station = (ring_at(left['coefficients'],middle)+ring_at(right['coefficients'],middle))/2
    centre = world(mid_station, middle, model)
    return np.round(centre,6).tolist(),[np.round(edge,6).tolist() for edge in edges]


def build(detector, world, ring_at):
    circ = 2*math.pi*detector.radius
    for ri,(left,right) in enumerate(zip(detector.rings, detector.rings[1:]),1):
        detector.emit(f'4/5 Reviewing segment boundaries in ring interval {ri}')
        delta = np.subtract(right['coefficients'],left['coefficients'])
        if delta[0]-np.linalg.norm(delta[1:]) <= .10:
            continue
        mid = (left['station_m']+right['station_m'])/2
        seams = sorted((s for s in detector.longitudinal if s['ring_interval']==ri),
                       key=lambda s:s['arc_intercept_m'])
        # Legacy callers without a declared design keep their measured layout
        # when at least two longitudinal candidates already enclose panels.
        count = detector.s.get('lining_sector_count', 6 if len(seams)<2 else 0)
        reach = delta[0]/2+max(np.linalg.norm(left['coefficients'][1:]),np.linalg.norm(right['coefficients'][1:]))
        for seam in seams:
            conflicts = []
            for other in seams:
                if seam is other:
                    continue
                gap = abs(other['arc_intercept_m']-seam['arc_intercept_m'])
                if min(gap,circ-gap)-abs(other['arc_slope']-seam['arc_slope'])*reach <= .10:
                    conflicts.append(other['id'])
            seam['conflicting_boundary_ids'] = conflicts
            seam['segment_boundary_status'] = 'ambiguous' if conflicts else 'supported'
        # Crossing or near-coincident observations cannot anchor template edges.
        if any(s['conflicting_boundary_ids'] for s in seams):
            dividers = seams
        else:
            dividers = angular_dividers(seams,count,detector.s.get('lining_sector_offset_deg',0.),
                                        mid,detector.radius)
        if len(dividers)<2:
            continue
        for number,first in enumerate(dividers,1):
            second = dividers[number % len(dividers)]
            if first.get('conflicting_boundary_ids') or second.get('conflicting_boundary_ids'):
                continue
            measured = int('id' in first)+int('id' in second)
            confidence = ('low','medium','high')[measured]
            centre,outline = panel_geometry(detector.m,left,right,first,second,world,ring_at)
            sources = ['measured', 'measured' if 'id' in second else 'inferred',
                       'measured', 'measured' if 'id' in first else 'inferred']
            kind = 'segment candidate' if measured==2 else 'inferred segment'
            row = detector.add('segment',f'Ring interval {ri} · {kind} {number}',
                               ring_interval=ri, panel_number=number, station_m=mid,
                               inferred=measured<2, segment_confidence=confidence,
                               confidence_score=measured+1, label_centre=centre, panel_outline=outline,
                               boundary_sources=sources, boundaries=[s['id'] for s in (left,right,first,second) if 'id' in s],
                               evidence=f'Two observed ring boundaries and {measured} measured side boundaries. '
                                        'Estimated angular edges follow the configured layout.')
            if measured<2:
                row['status']='inferred'
            detector.segments.append((row,left,right,first,second))
