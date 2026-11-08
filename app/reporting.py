# Copyright (c) 2026 Damian Gulczynski.
"""Standalone, printable survey reports. No external assets or network required."""
import html, math
from datetime import datetime, timezone
import numpy as np
import heatmap
from release import VERSION, COPYRIGHT
from core import slice_analysis

E=lambda v: html.escape(str(v))
def f(v,n=2):
    return f'{v:,.{n}f}' if isinstance(v,(int,float)) and math.isfinite(v) else '—'

def profile(r):
    radius=r['radius']; scale=190/(radius+max(.5,r['band'])); x=lambda a:260+a*scale; y=lambda a:245-a*scale
    out=['<svg viewBox="0 0 520 500" role="img" aria-label="Tunnel cross section">',f'<circle cx="260" cy="245" r="{radius*scale}" fill="none" stroke="#326fc0" stroke-width="2"/>','<path d="M40 245H480M260 25V465" stroke="#cbd5e1" stroke-dasharray="4 4"/>']
    pts=r['points']; stride=max(1,math.ceil(len(pts)/4000))
    for u,v,d in pts[::stride]:
        c='#cb633f' if d>r['tolerance_mm'] else '#3975c2' if d< -r['tolerance_mm'] else '#128778'
        out.append(f'<circle cx="{x(u):.2f}" cy="{y(v):.2f}" r="1.2" fill="{c}"/>')
    if r['fit_valid']:
        a=r['angle_deg']*math.pi/180; coords=[]
        for t in np.linspace(0,2*math.pi,181):
            u=r['ellipse_centre'][0]+r['a']*math.cos(t)*math.cos(a)-r['b']*math.sin(t)*math.sin(a)
            v=r['ellipse_centre'][1]+r['a']*math.cos(t)*math.sin(a)+r['b']*math.sin(t)*math.cos(a)
            coords.append(f'{x(u):.2f},{y(v):.2f}')
        out.append('<polyline points="'+' '.join(coords)+'" fill="none" stroke="#a057b2" stroke-width="1.5"/>')
    out+=['<text x="268" y="22">Up +</text><text x="444" y="263">Right +</text><text x="24" y="488">True scale · view along increasing chainage</text></svg>']
    return ''.join(out)

def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th>'+E(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+E(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>'

def trend(values,label,limit=None):
    vals=[(x,y) for x,y in values if y is not None and math.isfinite(y)]
    if not vals: return '<p>No valid observations for '+E(label)+'.</p>'
    lo=min(x for x,y in vals); hi=max(x for x,y in vals); low=min(0,min(y for x,y in vals),-(limit or 0)); high=max(1,max(y for x,y in vals),limit or 0); pad=(high-low)*.15; low-=pad; high+=pad
    X=lambda x:65+(x-lo)/max(hi-lo,1)*620; Y=lambda y:160-(y-low)/(high-low)*125
    out=['<svg viewBox="0 0 740 200"><text x="65" y="18">'+E(label)+'</text>']
    for v in sorted(set([round(low+pad,2),round((low+high)/2,2),round(high-pad,2),0]+([limit,-limit] if limit else []))):
        out.append(f'<path d="M65 {Y(v)}H685" stroke="#a5b4c0" stroke-dasharray="4 4"/><text x="5" y="{Y(v)+4}">{f(v,1)}</text>')
    # Separate points intentionally: withheld stations must not be bridged as measured data.
    for x,y in vals: out.append(f'<circle cx="{X(x)}" cy="{Y(y)}" r="3" fill="#007e72"><title>{f(x,3)} m: {f(y,3)}</title></circle>')
    out.append(f'<text x="65" y="190">{f(lo,3)} m</text><text x="595" y="190">{f(hi,3)} m</text></svg>')
    return ''.join(out)

def create(state,settings,progress=lambda x:None):
    axis=state['axis']; start=float(settings['start']); end=float(settings['end']); step=float(settings['step'])
    width=float(settings['width']); band=float(settings['band']); tolerance=float(settings['tolerance']); creep=float(settings['creep'])
    if not all(math.isfinite(x) for x in [start,end,step,width,band,tolerance,creep]) or end<start or step<=0 or creep<=0: raise ValueError('Use finite, increasing report stations and positive limits/interval.')
    axis.at(start); axis.at(end)
    n=int(math.floor((end-start)/step+1e-8))+1
    if n>100: raise ValueError('At most 100 sections per report. Increase the interval or shorten the range.')
    stations=[start+i*step for i in range(n)]
    reports=[]
    for i,s in enumerate(stations):
        progress(f'Calculating report section {i+1} / {n}')
        reports.append(slice_analysis(state['cloud'],state['derived'],axis,s,width,band,tolerance)[0])
    title=E(settings.get('project','Tunnel survey')); notes=E(settings.get('notes','')); surveyor=E(settings.get('surveyor','')); reference=E(settings.get('reference','')); date=E(settings.get('survey_date') or 'Not supplied')
    out=['''<!doctype html><html lang="en"><meta charset="utf-8"><title>Tunnel Studio survey report</title><style>
body{font:13px/1.5 Arial,sans-serif;color:#183645;max-width:1050px;margin:30px auto;padding:0 24px}h1{font-size:30px}h2{break-after:avoid;border-bottom:2px solid #08786e;padding-bottom:8px}h3{margin-bottom:6px}p{white-space:pre-wrap}table{width:100%;border-collapse:collapse;font-size:11px;margin:15px 0}th,td{padding:7px;text-align:left;border-bottom:1px solid #d5e0e5}th{background:#eaf1f3}svg{width:100%;font:11px Arial;break-inside:avoid}img{break-inside:avoid}.section{break-before:page}.section-grid{display:grid;grid-template-columns:55% 45%;gap:12px}.badge{padding:8px;background:#fff2df}.meta{background:#edf5f3;padding:14px}.foot{font-size:11px;color:#506c79}.actions{position:sticky;top:0;background:white;padding:10px}button{padding:10px 20px}thead{display:table-header-group}tr{break-inside:avoid}@page{size:A4;margin:14mm;@bottom-right{content:"Page " counter(page);font:9px Arial;color:#506c79}}@media print{body{margin:0;padding:0;font-size:11px}.actions{display:none}table{font-size:9px}h1{font-size:24px}.section-grid{gap:8px}svg{max-height:150mm}}
</style><div class="actions"><button onclick="window.print()">Print / save as PDF</button> <span>Use your browser’s Save as PDF printer. This HTML report also opens offline.</span></div>''',f'<h1>{title}</h1><h2>Tunnel profile and ring survey</h2><p class="badge">'+('SYNTHETIC DEMONSTRATION — NOT PROJECT MEASUREMENTS' if state['demo'] else 'PROTOTYPE — PROJECT VALIDATION PENDING')+'</p>',f'<div class="meta">Reference: {reference}<br>Surveyor: {surveyor}<br>Survey date: {date}<br>Generated UTC: {E(datetime.now(timezone.utc).isoformat())}<br>Design: {E(axis.name)}<br>Scan: {E(state["cloud"]["name"])}<br>Tunnel Studio 0.2.0</div>',f'<p>{notes}</p>',f'<p>Range {f(start,3)}–{f(stations[-1],3)} m · interval {f(step,3)} m · slice width {f(width,3)} m · wall band ±{f(band,3)} m · radial display limit ±{f(tolerance,1)} mm · creep display limit ±{f(creep,1)} mm.</p>', '<h2>Section summary</h2>']
    out.append(table(['Ch. m','Points','Right mm','Up mm','Ovality %','Min radial mm','Max radial mm','P95 |radial| mm','Fit'],[[f(r['chainage'],3),r['kept_points'],f(r.get('circle_centre',[None,None])[0]*1000 if r['fit_valid'] else None),f(r.get('circle_centre',[None,None])[1]*1000 if r['fit_valid'] else None),f(r.get('ovality_percent'),3),f(r.get('min_deviation_mm')),f(r.get('max_deviation_mm')),f(r.get('p95_abs_mm')),'Available' if r['fit_valid'] else 'Withheld'] for r in reports]))
    out.append(trend([(r['chainage'],r.get('ovality_percent') if r['fit_valid'] else None) for r in reports],'Ellipse ovality (%)'))
    out.append(trend([(r['chainage'],r.get('p95_abs_mm')) for r in reports],'95th percentile absolute radial deviation (mm)',tolerance))
    if end>start:
        progress('Building report heatmap')
        h=heatmap.calculate(state['cloud'],state['derived'],axis,start,end,max((end-start)/200,.05),5,band,'extreme')
        out += ['<h2>Scan versus circular design — unwrapped heatmap</h2>',f'<img alt="Radial deviation heatmap" style="width:100%;height:220px;image-rendering:pixelated" src="{heatmap.image_uri(h,tolerance)}">',f'<p>Left to right: Ch. {f(start,3)}–{f(end,3)} m. Top to bottom: crown (0°), right wall (90°), invert (180°), left wall (270°), crown (360°). Blue: inward (negative); pale: near zero; orange: outward (positive); grey: no retained data. Colour saturates at ±{f(tolerance,1)} mm. Each cell shows the signed greatest absolute deviation; cell size {f(h["step"],3)} m × 5°. Wall band ±{f(band,3)} m. This can highlight isolated noise as well as genuine extremes.</p>']
    out+=['<h2>Ring creep</h2>',trend([(r['design'],r['creep_mm']) for r in state['rings']],'Creep (mm)',creep),table(['Ring','Design Ch. m','Measured Ch. m','Creep mm','Check','Face / source'],[[r['ring_id'],f(r['design'],3),f(r['measured'],3),f(r['creep_mm']),'Outside limit' if abs(r['creep_mm'])>creep else 'Within limit',r['reference']+' / '+r['source']] for r in state['rings']]) if state['rings'] else '<p>No independent ring-face observations supplied. Scan joint suggestions are not creep measurements.</p>']
    out+=['<h2>Point-edit history (currently applied)</h2>',table(['UTC','Removed points','Reason'],[[r['time'],r['count'],r['reason']] for r in state.get('edits',[])]) if state.get('edits') else '<p>No manual point exclusions applied.</p>','<h2>Method and review</h2><p>Inputs: registered coordinates in metres and a circular design on a polyline alignment. No coordinate transformation or scan registration is performed. Chainage uses nearest plan projection. Section coordinates use the local plane normal to the 3D alignment tangent. Positive radial deviation is outside the design.</p><p>All retained points contribute to section statistics; figures are sampled. Circle and ellipse fits use balanced 1° angular bins. Ovality = 100 × (major diameter − minor diameter) / design diameter. Fits are withheld if angular coverage is inadequate. Missing values are not zero. The ellipse uses a normalised implicit residual, not a claimed Amberg or ITA-certified algorithm. Wall-band filtering and manual exclusion can remove genuine defects.</p><p>Creep = 1000 × (measured face chainage − design face chainage), in mm. Positive is ahead. User-entered display limits do not constitute acceptance criteria. All loaded ring observations are listed, including those outside the section-report range. Validate against independently checked project results before use.</p><p>Reviewed by: ____________________    Date: ____________________<br>Approval reference: ____________________</p>']
    for r in reports:
        rows=[['Chainage',f(r['chainage'],3)+' m'],['Design diameter',f(2*r['radius'],4)+' m'],['Retained wall points',r['kept_points']],['Excluded by wall band',r['excluded_points']],['Min / max radial',f(r.get('min_deviation_mm'))+' / '+f(r.get('max_deviation_mm'))+' mm'],['P95 |radial|',f(r.get('p95_abs_mm'))+' mm'],['Outside radial limit',r.get('outside_tolerance',0)]]
        if r['fit_valid']: rows += [['Centre right / up',f(r['circle_centre'][0]*1000)+' / '+f(r['circle_centre'][1]*1000)+' mm'],['Major / minor diameter',f(r['major_diameter'],4)+' / '+f(r['minor_diameter'],4)+' m'],['Horizontal / vertical diameter',f(r['horizontal_diameter'],4)+' / '+f(r['vertical_diameter'],4)+' m'],['Ovality',f(r['ovality_mm'])+' mm / '+f(r['ovality_percent'],3)+'%'],['Circle RMS',f(r['circle_rms_mm'])+' mm']]
        out += [f'<section class="section"><h2>Cross-section · Ch. {f(r["chainage"],3)} m</h2><div class="section-grid">',profile(r),'<div>'+table(['Result','Value'],rows)+f'<p>{E(r["quality"])}</p></div></div><p>Blue circle: design · purple: fitted ellipse (when available) · green points: within radial limit · blue points: beyond inward limit · orange points: beyond outward limit. True scale, no radial exaggeration.</p><p class="foot">{title} · {reference} · {E(state["cloud"]["name"])} · Tunnel Studio 0.2.0</p></section>']
    return ''.join(out).replace('Tunnel Studio 0.2.0',f'Tunnel Studio {VERSION} · {COPYRIGHT}')+'</html>',reports
