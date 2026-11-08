"""Printable vector diagrams and displacement trends from survey epochs."""

from collections import defaultdict
from datetime import datetime
from html import escape
import math

COLORS = ('#bf2540', '#21639e', '#17724c', '#94430b', '#73469c',
          '#007480', '#8b3b5e', '#536322', '#284889', '#6d5141')


def _project(east, north, sine, cosine):
    return east * cosine - north * sine, east * sine + north * cosine


def _svg(title, width, height, body):
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title>{"".join(body)}</svg>')


def _line(x1, y1, x2, y2, color, width=1):
    return (f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{color}" stroke-width="{width}"/>')


def _label(x, y, text, anchor='start'):
    return (f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}">'
            f'{escape(str(text))}</text>')


def _arrow(x, y, end_x, end_y, color):
    dx, dy = end_x - x, end_y - y
    length = math.hypot(dx, dy)
    if length < 1:
        return ''
    size = min(6, length * .65)
    ux, uy = dx / length, dy / length
    back_x, back_y = end_x - size * ux, end_y - size * uy
    points = (f'{end_x:.2f},{end_y:.2f} '
              f'{back_x-size*.48*uy:.2f},{back_y+size*.48*ux:.2f} '
              f'{back_x+size*.48*uy:.2f},{back_y-size*.48*ux:.2f}')
    return (_line(x, y, end_x, end_y, color, 2)
            + f'<polygon points="{points}" fill="{color}"/>')


def _vectors(title, axes, points, exaggeration):
    width, height = 420, 270
    i, j, x_name, y_name = axes
    starts = [(base[i], base[j]) for _, base, _, _ in points]
    ends = [(x + move[i] * exaggeration / 1000, y + move[j] * exaggeration / 1000)
            if move is not None else (x, y)
            for (x, y), (_, _, move, _) in zip(starts, points)]
    xs, ys = zip(*(starts + ends))
    scale = min((width - 70) / max(1, max(xs) - min(xs)),
                (height - 70) / max(1, max(ys) - min(ys)))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def screen(point):
        return width / 2 + (point[0] - cx) * scale, height / 2 - (point[1] - cy) * scale

    body = [_label(16, 20, y_name), _label(width - 14, height - 10, x_name, 'end')]
    metre = next(size for size in (1, .5, .25, .1) if size == .1 or size * scale <= 90)
    body.extend([_line(16, height - 22, 16 + metre * scale, height - 22, '#526170', 2),
                 _label(16, height - 7, f'{metre:g} m')])
    for start, end, (number, _, move, color) in zip(starts, ends, points):
        x, y = screen(start)
        end_x, end_y = screen(end)
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="white" stroke="{color}"/>')
        if move is not None:
            body.append(_arrow(x, y, end_x, end_y, color))
        body.append(_label(x + 6, y - 7, number))
    return _svg(title, width, height, body)


def _trend(title, times, series, levels):
    width, height = 720, 181
    left, right, top, bottom = 66, 698, 24, 145
    start, end = times[0], times[-1]
    maximum = max((abs(value) for _, _, values in series for value in values if value is not None),
                  default=0.)
    highest_trigger = levels[-1]['threshold_mm'] if levels else 0.
    half = max(1., maximum * 1.1, highest_trigger * 1.18)

    def x(time):
        return left + (time - start) / (end - start) * (right - left) if start != end else (left + right) / 2

    def y(value):
        return top + (half - value) / (2 * half) * (bottom - top)

    body = [_label(left - 6, 12, 'mm', 'end')]
    for index, level in enumerate(levels):
        low = level['threshold_mm']
        high = levels[index + 1]['threshold_mm'] if index + 1 < len(levels) else half
        colour = level['colour']
        band_height = y(low) - y(high)
        for band_top, boundary in ((y(high), y(low)), (y(-low), y(-low))):
            body.append(f'<rect x="{left}" y="{band_top:.2f}" width="{right-left}" '
                        f'height="{band_height:.2f}" fill="{colour}" fill-opacity=".28"/>')
            body.append(_line(left, boundary, right, boundary, colour))
    for value in (half, half / 2, 0, -half / 2, -half):
        row_y = y(value)
        body.extend([_line(left, row_y, right, row_y, '#a4afba' if value == 0 else '#dce2e8'),
                     _label(left - 6, row_y + 4, f'{value:.3g}', 'end')])
    ticks = sorted(set((0, len(times) - 1)))
    same_date = start.date() == end.date() and start != end
    for tick in ticks:
        time = times[tick]
        label = time.strftime('%H:%M:%S UTC' if same_date else '%d %b %Y')
        anchor = 'middle' if start == end else 'start' if tick == 0 else 'end'
        body.append(_label(x(time), bottom + 20, label, anchor))
    for number, color, values in series:
        path = []
        connected = False
        for time, value in zip(times, values):
            if value is None:
                connected = False
                continue
            px, py = x(time), y(value)
            path.append(f'{"L" if connected else "M"}{px:.2f},{py:.2f}')
            connected = True
        if path:
            dash = ('', '5 3', '2 2', '7 3 2 3')[((number - 1) // len(COLORS)) % 4]
            body.append(f'<path d="{" ".join(path)}" fill="none" stroke="{color}" '
                        f'stroke-width="1.8" stroke-dasharray="{dash}"><title>Point {number}</title></path>')
            # Keep isolated observations and a zero-motion baseline visible.
            for index, (time, value) in enumerate(zip(times, values)):
                if value is not None and (len(times) <= 80 or
                        ((index == 0 or values[index - 1] is None) and
                         (index + 1 == len(times) or values[index + 1] is None))):
                    body.append(f'<circle cx="{x(time):.2f}" cy="{y(value):.2f}" r="2.4" fill="{color}"/>')
    return _svg(title, width, height, body)


def _trigger_key(levels):
    if not levels:
        return ''
    items = []
    for index, level in enumerate(levels):
        low = format(level['threshold_mm'], '.12g')
        if index + 1 < len(levels):
            high = format(levels[index + 1]['threshold_mm'], '.12g')
            band = f'{low} to under {high} mm'
        else:
            band = f'{low} mm and above'
        items.append(f'<li><span class="swatch" style="background:{level["colour"]}" '
                     f'aria-hidden="true"></span>{escape(level["label"])} · {band}</li>')
    return '<ul class="trigger-key" aria-label="Absolute displacement bands">' + ''.join(items) + '</ul>'


def export_section(data, report, section, bearing, triggers=None):
    """Render a measured section and three signed trends in a declared heading frame."""
    if section not in report['rings']:
        raise ValueError('Choose a monitoring section.')
    if not math.isfinite(bearing) or not 0 <= bearing < 360:
        raise ValueError('Enter a heading bearing from 0 to under 360 degrees clockwise from grid north.')
    observations = {epoch['timestamp']: [p for p in epoch['points'] if p['ring_id'] == section]
                    for epoch in data['epochs']}
    baseline = report['baseline']
    reference = sorted((p for p in observations[baseline] if not p.get('excluded')),
                       key=lambda p: p['point_id'])
    if not reference:
        raise ValueError('The selected section has no included observations in the saved baseline epoch.')
    dates = sorted(observations)
    observed = [date for date in dates if observations[date]]
    latest = observed[-1]
    latest_excluded = {p['point_id'] for p in observations[latest] if p.get('excluded')}
    dates = [date for date in dates if observed[0] <= date <= latest]
    times = [datetime.fromisoformat(date) for date in dates]
    rows = defaultdict(dict)
    angle = math.radians(bearing)
    sine, cosine = math.sin(angle), math.cos(angle)
    for row in report['point_series']:
        if row['ring_id'] == section and row['dH_mm'] is not None:
            right, forward = _project(row['dE_mm'], row['dN_mm'], sine, cosine)
            rows[row['point_id']][row['epoch']] = right, forward, row['dH_mm']

    origin = [sum(p[key] for p in reference) / len(reference) for key in ('e', 'n', 'h')]
    points, traces, readings = [], [], []
    for number, point in enumerate(reference, 1):
        name = point['point_id']
        right, forward = _project(point['e'] - origin[0], point['n'] - origin[1], sine, cosine)
        base = right, forward, point['h'] - origin[2]
        color = COLORS[(number - 1) % len(COLORS)]
        series = rows[name]
        move = series.get(latest)
        points.append((number, base, move, color))
        traces.append((number, color, [series.get(date) for date in dates]))
        unavailable = 'Excluded' if name in latest_excluded else '—'
        values = [unavailable] * 3 if move is None else [f'{v:+.2f}' for v in (-move[2], move[0], move[1])]
        readings.append(f'<tr><td><span style="color:{color}">●</span> {number}</td>'
                        f'<th scope="row">{escape(name)}</th>'
                        + ''.join(f'<td>{value}</td>' for value in values) + '</tr>')

    extent = max((max(p[1][i] for p in points) - min(p[1][i] for p in points) for i in range(3)))
    motion_m = max((math.hypot(*p[2]) / 1000 for p in points if p[2] is not None), default=0.)
    exaggeration = min(100., max(1., max(1., extent) * .12 / motion_m)) if motion_m else 1.
    exaggeration = math.floor(exaggeration * 10) / 10
    diagrams = []
    for title, axes in (
        ('Front · looking along the heading', (0, 2, 'Right +', 'Height +')),
        ('Side', (1, 2, 'Along heading +', 'Height +')),
        ('Plan', (0, 1, 'Right +', 'Along heading +')),
    ):
        diagrams.append(f'<figure><figcaption>{title}</figcaption>{_vectors(title, axes, points, exaggeration)}</figure>')

    plots = []
    for component, index, sign, title in (
        ('settlement', 2, -1, 'Settlement · positive down'),
        ('transverse', 0, 1, 'Transverse · positive right'),
        ('longitudinal', 1, 1, 'Longitudinal · positive along heading'),
    ):
        values = [(number, color, [None if value is None else sign * value[index] for value in trace])
                  for number, color, trace in traces]
        levels = (triggers or {}).get(component, [])
        plots.append(f'<figure><figcaption>{title}</figcaption>{_trend(title, times, values, levels)}'
                     f'{_trigger_key(levels)}</figure>')

    trigger_note = ('<p>Shaded bands show the current project triggers for absolute movement in either direction. '
                    'Each band starts at its named threshold. Prism traces retain their marker colours.</p>'
                    if triggers else '')

    excluded = len({p['point_id'] for epoch in observations.values() for p in epoch} -
                   {p['point_id'] for p in reference})
    details = (f'<p>Baseline <strong>{escape(baseline)}</strong> · Latest recorded section epoch '
               f'<strong>{escape(latest)}</strong><br>Datum <strong>{escape(report["datum"])}</strong>'
               f' · Heading <strong>{bearing:.3f}°</strong> clockwise from grid north</p>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Tunnel Studio · Section monitoring</title><style>
*{{box-sizing:border-box}}body{{max-width:920px;margin:0 auto;padding:22px;color:#202b37;font:14px system-ui}}
h1{{font-size:25px;margin:4px 0}}h2{{font-size:19px;margin-bottom:8px}}p{{line-height:1.5}}
.kicker{{color:#a12542;font-weight:700;text-transform:uppercase;letter-spacing:.12em}}
.grid{{display:grid;grid-template-columns:2fr 1fr;gap:10px;align-items:start}}
.grid figure:first-child{{grid-row:span 2}}
figure{{margin:0 0 10px;border:1px solid #d8dfe7;border-radius:6px;padding:8px;break-inside:avoid}}
figcaption{{font-weight:650}}svg{{display:block;width:100%;height:auto}}svg text{{fill:#344352;font:12px system-ui}}
.trigger-key{{display:flex;flex-wrap:wrap;gap:3px 12px;margin:0;padding:0;list-style:none;font-size:11px}}
.swatch{{display:inline-block;width:.9em;height:.9em;margin-right:.4em;border:1px solid #526170}}
table{{border-collapse:collapse;width:100%;text-align:left}}th,td{{padding:5px;border-bottom:1px solid #d8dfe7}}
thead{{display:table-header-group}}tr{{break-inside:avoid}}
@media screen and (max-width:500px){{.grid{{display:block}}}}
@media print{{@page{{size:A4;margin:13mm}}body{{padding:0;print-color-adjust:exact}}.trends{{break-before:page}}}}
</style></head><body><section><span class="kicker">Tunnel Studio · Survey monitoring</span>
<h1>Section {escape(section)}</h1>{details}<h2>Displacement vectors</h2>
<p>Markers show measured baseline prism positions. Arrows compare the latest section epoch with the baseline.
Vector scale <strong>×{exaggeration:g}</strong> relative to the geometry in each view.
The diagrams show measured points without a lining outline.</p>
<div class="grid">{''.join(diagrams)}</div>
<table><thead><tr><th>Marker</th><th>Prism</th><th>Settlement (mm)</th><th>Transverse (mm)</th>
<th>Longitudinal (mm)</th></tr></thead><tbody>{''.join(readings)}</tbody></table>
<p>— indicates no observation at the latest section epoch. {excluded} prism(s) without an included baseline are omitted.</p>
</section><section class="trends"><span class="kicker">Tunnel Studio · Survey monitoring</span>
<h1>Section {escape(section)} · Displacement trends</h1>{details}{''.join(plots)}
{trigger_note}<p>Traces use the marker colours in the vector table. Missing or excluded observations break the line.
Changes use matched prism IDs in the declared datum and the entered heading. Verify survey control and the heading before interpreting movement.</p>
</section></body></html>'''
