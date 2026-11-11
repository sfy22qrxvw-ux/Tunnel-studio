# Copyright (c) 2026 Damian Gulczynski.
"""Read metric LandXML centre lines into Tunnel Studio's alignment nodes."""
import bisect
import math
from pathlib import Path
from xml.etree import ElementTree as ET


# Maximum chord deviation for each of the horizontal and vertical curves.
CHORD_ERROR = 0.00025
JOIN_TOLERANCE = 0.002
MAX_NODES = 250_000


class _TreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('LandXML must not contain a document type declaration.')


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _children(element, tag):
    return [child for child in element if _tag(child) == tag]


def _one(element, tag):
    matches = _children(element, tag)
    if len(matches) != 1:
        raise ValueError(f'LandXML needs exactly one {tag} in {_tag(element)}.')
    return matches[0]


def _numbers(value, label, count):
    try:
        values = tuple(map(float, (value or '').split()))
    except ValueError:
        values = ()
    if len(values) != count or not all(map(math.isfinite, values)):
        raise ValueError(f'LandXML {label} needs {count} finite numeric value(s).')
    return values


def _number(element, key):
    return _numbers(element.get(key), key, 1)[0]


def _sample(start, end, span):
    count = math.ceil((end - start) / span)
    if count > MAX_NODES:
        raise ValueError('LandXML curve needs too many alignment nodes.')
    return [start + (end - start) * i / count for i in range(1, count)]


def _arc(element, start, end, tolerance):
    radius = _number(element, 'radius')
    centre = _numbers(_one(element, 'Center').text, 'Center', 2)[::-1]
    rotation = element.get('rot', '').lower()
    if (element.get('crvType', 'arc').lower() != 'arc' or radius <= 0 or
            rotation not in ('cw', 'ccw') or start == end or
            any(abs(math.dist(point, centre) - radius) > tolerance for point in (start, end))):
        raise ValueError('LandXML curve is not a supported circular arc.')
    angle, finish = (math.atan2(y - centre[1], x - centre[0]) for x, y in (start, end))
    direction = 1 if rotation == 'ccw' else -1
    sweep = direction * ((direction * (finish - angle)) % math.tau)
    return centre, radius, angle, sweep


def _endpoints(element):
    return tuple(_numbers(_one(element, tag).text, tag, 2)[::-1] for tag in ('Start', 'End'))


def _horizontal(alignment):
    station = _number(alignment, 'staStart')
    end_station = station + _number(alignment, 'length')
    segments, nodes = [], []
    for element in _one(alignment, 'CoordGeom'):
        kind = _tag(element)
        if kind not in ('Line', 'Curve'):
            raise ValueError(f'LandXML horizontal {kind} is unsupported. Export lines and circular arcs.')
        # LandXML writes northing first.
        start, end = _endpoints(element)
        supplied = _number(element, 'staStart') if 'staStart' in element.attrib else station
        if abs(supplied - station) > JOIN_TOLERANCE:
            raise ValueError('LandXML horizontal chainages are discontinuous.')
        station = supplied
        if segments and math.dist(start, segments[-1][2]) > JOIN_TOLERANCE:
            raise ValueError('LandXML horizontal geometry has disconnected segments.')
        arc = None
        length = math.dist(start, end)
        if kind == 'Curve':
            arc = _arc(element, start, end, JOIN_TOLERANCE)
            length = arc[1] * abs(arc[3])
        if length <= 0:
            raise ValueError('LandXML contains a zero-length horizontal segment.')
        segments.append((station, start, end, arc))
        station += length
    if not segments or abs(end_station - station) > JOIN_TOLERANCE:
        raise ValueError('LandXML horizontal geometry does not match its declared length.')
    starts = [segment[0] for segment in segments] + [end_station]
    if any(a >= b for a, b in zip(starts, starts[1:])):
        raise ValueError('LandXML horizontal chainages must increase.')
    for i, (_, _, _, arc) in enumerate(segments):
        nodes.append(starts[i])
        if arc:
            nodes.extend(_sample(starts[i], starts[i+1], math.sqrt(8 * arc[1] * CHORD_ERROR)))
            if len(nodes) > MAX_NODES:
                raise ValueError('LandXML needs too many alignment nodes.')
    nodes.append(end_station)

    def at(sta):
        i = min(bisect.bisect_right(starts, sta) - 1, len(segments) - 1)
        begin, start, end, arc = segments[i]
        if sta == begin:
            return start
        if sta == end_station:
            return end
        f = (sta - begin) / (starts[i+1] - begin)
        if arc:
            (e, n), radius, angle, sweep = arc
            angle += sweep * f
            return e + radius * math.cos(angle), n + radius * math.sin(angle)
        return tuple(a + (b - a) * f for a, b in zip(start, end))

    return nodes, at


def _vertical(alignment):
    profile = _one(_one(alignment, 'Profile'), 'ProfAlign')
    points = [_numbers(element.text, _tag(element), 2) for element in profile]
    stations = [point[0] for point in points]
    if len(points) < 2 or any(a >= b for a, b in zip(stations, stations[1:])):
        raise ValueError('LandXML vertical profile needs increasing chainages.')
    grades = [(z1 - z0) / (s1 - s0) for (s0, z0), (s1, z1) in zip(points, points[1:])]
    nodes, curves = stations.copy(), []
    for i, element in enumerate(profile):
        kind = _tag(element)
        if kind == 'PVI':
            continue
        if kind not in ('CircCurve', 'ParaCurve') or i == 0 or i == len(points) - 1:
            raise ValueError(f'Unsupported LandXML vertical profile element {kind}.')
        sta, height = points[i]
        before, after = grades[i-1:i+1]
        length = _number(element, 'length')
        if length <= 0:
            raise ValueError('LandXML vertical curve length must be positive.')
        if kind == 'CircCurve':
            radius = abs(_number(element, 'radius'))
            if radius <= 0 or before == after:
                raise ValueError('LandXML circular vertical curve needs a radius and a change in grade.')
            a, b = math.atan(before), math.atan(after)
            tangent = radius * math.tan(abs(b - a) / 2)
            start, end = sta - tangent * math.cos(a), sta + tangent * math.cos(b)
            sign = 1 if b > a else -1
            centre_s = start - sign * radius * math.sin(a)
            centre_h = height + (start - sta) * before + sign * radius * math.cos(a)
            if abs(length - (end - start)) > JOIN_TOLERANCE and abs(length - radius * abs(b-a)) > JOIN_TOLERANCE:
                raise ValueError('LandXML vertical curve length disagrees with the adjoining grades.')
            parameters = (centre_s, centre_h, radius, sign)
            # A horizontal station step is shorter than the arc length on a grade.
            step = math.sqrt(8 * radius * CHORD_ERROR) * min(math.cos(a), math.cos(b))
        else:
            start, end = sta - length / 2, sta + length / 2
            parameters = (height + (start - sta) * before, before, (after-before)/(2*length))
            step = math.sqrt(4 * CHORD_ERROR * length / abs(after-before)) if after != before else length
        if (start < stations[i-1] - JOIN_TOLERANCE or end > stations[i+1] + JOIN_TOLERANCE or
                (curves and start < curves[-1][1] - JOIN_TOLERANCE)):
            raise ValueError('LandXML vertical curve extends beyond the adjoining profile geometry.')
        if abs(start - stations[i-1]) < 1e-5:
            start = stations[i-1]
        if abs(end - stations[i+1]) < 1e-5:
            end = stations[i+1]
        curves.append((start, end, parameters))
        nodes.extend([start, end, *_sample(start, end, step)])
        if len(nodes) > MAX_NODES:
            raise ValueError('LandXML needs too many alignment nodes.')
    curve_starts = [curve[0] for curve in curves]

    def at(sta):
        curve = bisect.bisect_right(curve_starts, sta) - 1
        if curve >= 0:
            start, end, parameters = curves[curve]
            if sta <= end:
                if len(parameters) == 4:
                    e, h, radius, sign = parameters
                    return h - sign * math.sqrt(radius**2 - (sta - e)**2)
                h, grade, quadratic = parameters
                offset = sta - start
                return h + offset * (grade + offset * quadratic)
        i = min(bisect.bisect_right(stations, sta) - 1, len(grades) - 1)
        return points[i][1] + (sta - stations[i]) * grades[i]

    return sorted(nodes), at, (stations[0], stations[-1])


def _radius(alignment, fallback):
    containers = _children(alignment, 'TunnelProfiles')
    profiles = _children(_one(alignment, 'TunnelProfiles'), 'TunnelProfile') if containers else []
    if not profiles:
        if fallback in (None, ''):
            raise ValueError('This LandXML has no tunnel profile. Enter the design radius under Alignment options in the Import window, then import again.')
        radius = _numbers(str(fallback), 'design radius', 1)[0]
        if radius <= 0:
            raise ValueError('The design radius must be positive.')
        return radius
    radii = []
    for profile in profiles:
        surfaces = [surface for surface in _children(profile, 'TunnelProfSurf')
                    if 'intrados' in surface.get('name', '').lower()]
        if len(surfaces) != 1:
            raise ValueError('LandXML needs one circular tunnel intrados per profile.')
        curves = list(_one(surfaces[0], 'TunnelCoordGeom'))
        if not curves or any(_tag(curve) != 'Curve' for curve in curves):
            raise ValueError('LandXML intrados must be a closed circle.')
        endpoints = [_endpoints(curve) for curve in curves]
        arcs = [_arc(curve, start, end, 1e-6) for curve, (start, end) in zip(curves, endpoints)]
        sweep = sum(arc[3] for arc in arcs)
        if (any(math.hypot(*arc[0]) > 1e-6 or arc[3] * sweep <= 0 for arc in arcs) or
                not math.isclose(abs(sweep), math.tau, abs_tol=1e-6) or
                any(math.dist(end, next_start) > 1e-6
                    for (_, end), (next_start, _) in zip(endpoints, endpoints[1:] + endpoints[:1]))):
            raise ValueError('LandXML intrados must be a closed circle centred on the alignment.')
        radii.extend(arc[1] for arc in arcs)
    if not radii or min(radii) <= 0 or max(radii) - min(radii) > 1e-6:
        raise ValueError('LandXML needs a single constant tunnel intrados radius.')
    return radii[0]


def rows(path, fallback_radius=None):
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('LandXML alignment files are limited to 16 MiB.')
    try:
        root = ET.parse(path, parser=ET.XMLParser(target=_TreeBuilder())).getroot()
    except ET.ParseError as exc:
        raise ValueError(f'Could not read LandXML alignment: {exc}') from exc
    if _tag(root) != 'LandXML':
        raise ValueError('Choose a LandXML alignment export.')
    if _one(_one(root, 'Units'), 'Metric').get('linearUnit') != 'meter':
        raise ValueError('Export the LandXML alignment in metres.')
    alignment = _one(_one(root, 'Alignments'), 'Alignment')
    if any(_tag(element) in ('StaEquation', 'StationEquation') for element in alignment.iter()):
        raise ValueError('LandXML station equations are unsupported.')
    radius = _radius(alignment, fallback_radius)
    horizontal, xy = _horizontal(alignment)
    vertical, z, (first, last) = _vertical(alignment)
    lo, hi = max(horizontal[0], first), min(horizontal[-1], last)
    if lo >= hi:
        raise ValueError('LandXML horizontal and vertical chainage ranges do not overlap.')
    stations = [lo]
    for station in sorted(horizontal + vertical):
        if stations[-1] + 1e-6 < station < hi - 1e-6:
            stations.append(station)
    stations.append(hi)
    if len(stations) > MAX_NODES:
        raise ValueError('LandXML needs too many alignment nodes.')
    return [dict(chainage=s, e=e, n=n, h=z(s), radius=radius)
            for s in stations for e, n in [xy(s)]]
