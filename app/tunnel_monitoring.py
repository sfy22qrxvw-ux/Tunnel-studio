"""Persistent survey epochs, point displacement, settlement and chord convergence.

Observations are matched by explicit ring and physical point identifiers in one
declared survey datum. Epoch coordinates are never best-fitted to each other:
such a fit could remove genuine movement. Signed changes, gaps, elapsed-time
rates and user-defined review levels are reported against a selected baseline.
This is coordinate-difference monitoring, not automatic physical-point matching,
structural interpretation, an alarm service or a substitute for control checks.
"""
import copy
import csv
from datetime import datetime, timezone
import html
import io
import math
import re

MAX_ROWS = 100000
MAX_CSV_BYTES = 16 * 1024**2
METHOD = 'Matched survey-point epoch differences and chord-length convergence'
SECTION_COMPONENTS = ('settlement', 'transverse', 'longitudinal')
MAX_SECTION_TRIGGERS = 8


def _text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c)<32 for c in value):
        raise ValueError('Enter a valid ' + label + '.')
    return value.strip()


def _number(value, label, optional=False, nonnegative=False):
    if optional and (value is None or value == ''):return None
    try:
        if isinstance(value, bool) or value is None:raise ValueError()
        out = float(value)
        if not math.isfinite(out) or abs(out)>1e9 or nonnegative and out<0:raise ValueError()
        return out
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Enter a finite ' + label + '.') from None


def _timestamp(value):
    text = _text(value, 'ISO epoch date or timestamp', 50)
    try:
        stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            if len(text) != 10:raise ValueError()
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except ValueError:
        raise ValueError('Use an ISO date (2026-11-02) or a timestamp with timezone (2026-11-02T14:30:00Z). Date-only epochs use midnight UTC.') from None


def empty():
    return dict(schema=1, datum='', units='m', epochs=[], baseline=None, chords=[],
                limits=dict(warning_mm=None, alarm_mm=None), section_triggers={})


def section_triggers(value):
    """Validate and sort the absolute displacement thresholds for each chart."""
    if not isinstance(value, dict) or value.keys() - set(SECTION_COMPONENTS):
        raise ValueError('Choose settlement, transverse or longitudinal for each section trigger.')
    clean = {}
    for component, rows in value.items():
        name = component.capitalize()
        if not isinstance(rows, list) or len(rows) > MAX_SECTION_TRIGGERS:
            raise ValueError(f'{name} can have up to {MAX_SECTION_TRIGGERS} trigger levels.')
        levels = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f'Invalid {component} trigger level.')
            label = _text(row.get('label'), f'{component} trigger name', 40)
            threshold = _number(row.get('threshold_mm'), f'{component} trigger threshold in mm')
            if threshold <= 0:
                raise ValueError(f'{name} trigger thresholds must be greater than 0 mm.')
            colour = row.get('colour')
            if not isinstance(colour, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', colour):
                raise ValueError(f'Choose a valid colour for each {component} trigger.')
            levels.append(dict(label=label, threshold_mm=threshold, colour=colour.lower()))
        if len({level['threshold_mm'] for level in levels}) != len(levels):
            raise ValueError(f'{name} trigger thresholds must be different.')
        if levels:
            clean[component] = sorted(levels, key=lambda level: level['threshold_mm'])
    return clean


def import_csv(data, text, datum, source_name='Entered CSV'):
    """Append observations; conflicting IDs or dates never overwrite history."""
    datum = _text(datum, 'survey datum / coordinate frame')
    source_name = _text(source_name, 'source name', 500)
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_CSV_BYTES:
        raise ValueError('Monitoring CSV must be text no larger than 16 MB.')
    if data.get('datum') and data['datum'] != datum:
        raise ValueError('This monitoring series uses a different datum. Transform and independently verify the observations before combining them.')
    aliases = {'timestamp':'epoch','date':'epoch','easting':'e','northing':'n','height':'h',
               'ring':'ring_id','point':'point_id'}
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff'), newline=''))
    fields = [aliases.get(x.strip().lower(), x.strip().lower()) for x in reader.fieldnames or []]
    required = {'epoch','ring_id','point_id','e','n','h'}
    optional = {'sigma_e_mm','sigma_n_mm','sigma_h_mm','datum','units'}
    if not required.issubset(fields) or len(set(fields)) != len(fields) or set(fields)-required-optional:
        raise ValueError('CSV needs epoch, ring_id, point_id, e, n, h. Optional: sigma_e_mm, sigma_n_mm, sigma_h_mm, datum, units. Coordinates must be metres.')
    reader.fieldnames = fields
    out = copy.deepcopy(data)
    epochs = {e['id']:e for e in out['epochs']}
    seen = {(e['id'],p['ring_id'].casefold(),p['point_id'].casefold()) for e in out['epochs'] for p in e['points']}
    count = len(seen)
    imported = 0
    try:
        for line, row in enumerate(reader, 2):
            if None in row:raise ValueError(f'Row {line}: too many columns.')
            if not any(str(v or '').strip() for v in row.values()):continue
            if row.get('datum') and row['datum'].strip() != datum:raise ValueError(f'Row {line}: datum differs from this monitoring series.')
            if row.get('units') and row['units'].strip().lower() not in ('m','metres','meters'):
                raise ValueError(f'Row {line}: coordinates must be in metres.')
            stamp = _timestamp(row.get('epoch'))
            point = {key:_text(row.get(key), key, 100) for key in ('ring_id','point_id')}
            key = stamp,point['ring_id'].casefold(),point['point_id'].casefold()
            if key in seen:raise ValueError(f'Row {line}: that ring / point already has an observation at this epoch. Existing observations were not overwritten.')
            seen.add(key)
            point.update({key:_number(row.get(key), key+' coordinate') for key in ('e','n','h')})
            point.update({key:_number(row.get(key), key, optional=True, nonnegative=True) for key in ('sigma_e_mm','sigma_n_mm','sigma_h_mm')})
            point['source_name'] = source_name
            epoch = epochs.setdefault(stamp, dict(id=stamp, timestamp=stamp, points=[]))
            epoch['points'].append(point)
            imported += 1
            if count+imported > MAX_ROWS:raise ValueError('This monitoring series exceeds 100,000 point observations.')
    except csv.Error as exc:
        raise ValueError('The CSV could not be read: '+str(exc)) from exc
    if not imported:raise ValueError('The CSV contains no observations.')
    out.update(datum=datum, epochs=sorted(epochs.values(),key=lambda e:e['timestamp']))
    for epoch in out['epochs']:epoch['points'].sort(key=lambda p:(p['ring_id'],p['point_id']))
    if out.get('baseline') is None:out['baseline'] = out['epochs'][0]['id']
    return out


def configure(data, body):
    out = copy.deepcopy(data)
    baseline = body.get('baseline', data.get('baseline'))
    if baseline not in {e['id'] for e in data['epochs']}:raise ValueError('Choose an existing baseline epoch.')
    chords = body.get('chords', data.get('chords', []))
    if not isinstance(chords, list) or len(chords)>1000:raise ValueError('Enter at most 1,000 monitoring chords.')
    names=set();clean=[]
    available={(p['ring_id'],p['point_id']) for e in data['epochs'] for p in e['points']}
    for row in chords:
        if not isinstance(row, dict):raise ValueError('Invalid convergence chord.')
        value={key:_text(row.get(key), key, 100) for key in ('ring_id','id','a','b')}
        mode=row.get('mode','3d')
        if mode not in ('3d','horizontal'):raise ValueError('Choose 3D or horizontal chord length.')
        if value['a']==value['b']:raise ValueError('A convergence chord needs two different physical points.')
        if any((value['ring_id'],value[end]) not in available for end in ('a','b')):
            raise ValueError('A chord point does not exist in the selected ring.')
        key=value['ring_id'].casefold(),value['id'].casefold()
        if key in names:raise ValueError('Use unique chord names within each ring.')
        names.add(key);clean.append(dict(value, mode=mode))
    limits=body.get('limits', data.get('limits', {}))
    if not isinstance(limits, dict):raise ValueError('Invalid review levels.')
    limits={key:_number(limits.get(key), key, optional=True, nonnegative=True) for key in ('warning_mm','alarm_mm')}
    w,a=limits['warning_mm'],limits['alarm_mm']
    if (w is None)!=(a is None) or w is not None and not 0<w<a:
        raise ValueError('Enter both review levels, with 0 < warning < alarm, or leave both blank.')
    out.update(baseline=baseline, chords=clean, limits=limits)
    return out


def _level(value, limits):
    if value is None:return 'unavailable'
    if limits.get('alarm_mm') is None:return 'no levels set'
    return 'alarm level' if abs(value)>=limits['alarm_mm'] else 'warning level' if abs(value)>=limits['warning_mm'] else 'below warning'


def _set_observation_use(data, body):
    included = body.get('included')
    if not isinstance(included, bool):
        raise ValueError('Choose whether to use the observation.')
    epoch_id, ring_id, point_id = (body.get(key) for key in ('epoch', 'ring_id', 'point_id'))
    for epoch_index, epoch in enumerate(data['epochs']):
        if epoch['id'] != epoch_id:
            continue
        for point_index, point in enumerate(epoch['points']):
            if (point['ring_id'], point['point_id']) != (ring_id, point_id):
                continue
            points = list(epoch['points'])
            replacement = dict(point)
            if included:
                replacement.pop('excluded', None)
            else:
                replacement['excluded'] = True
            points[point_index] = replacement
            epochs = list(data['epochs'])
            epochs[epoch_index] = dict(epoch, points=points)
            return dict(data, epochs=epochs)
        break
    raise ValueError('That prism observation is unavailable.')


def calculate(data):
    epochs=sorted(data['epochs'],key=lambda e:e['timestamp'])
    if not epochs:
        return dict(method=METHOD, datum=data.get('datum',''), baseline=None, point_series=[], chord_series=[], epochs=[], rings=[], warnings=['Import two or more survey epochs to calculate movement trends.'])
    baseline=next((e for e in epochs if e['id']==data.get('baseline')), None)
    if baseline is None:raise ValueError('The selected baseline epoch is unavailable. Choose a remaining epoch.')
    base={(p['ring_id'],p['point_id']):p for p in baseline['points']}
    all_keys=sorted({(p['ring_id'],p['point_id']) for e in epochs for p in e['points']})
    if len(epochs)*(len(all_keys)+len(data.get('chords',[])))>300000:
        raise ValueError('This series would produce over 300,000 point/epoch entries. Keep separate monitoring areas in separate projects.')
    points=[];chords=[];previous={};previous_chords={};missing=0
    limits=data.get('limits',{})
    for epoch in epochs:
        now=datetime.fromisoformat(epoch['timestamp'].replace('Z','+00:00'))
        current={(p['ring_id'],p['point_id']):p for p in epoch['points']}
        for key in all_keys:
            reference,p=base.get(key),current.get(key)
            included = None if p is None else not p.get('excluded', False)
            row=dict(epoch=epoch['timestamp'], ring_id=key[0], point_id=key[1], observation_included=included,
                     dE_mm=None,dN_mm=None,dH_mm=None,settlement_mm=None,displacement_mm=None,
                     settlement_rate_mm_day=None,sigma_dE_mm=None,sigma_dN_mm=None,sigma_dH_mm=None,
                     status='unavailable', reason=None)
            if included is False:
                row['reason'] = 'Observation excluded'
            elif reference is None:
                row['reason'] = 'Missing baseline observation'
            elif reference.get('excluded'):
                row['reason'] = 'Baseline observation excluded'
            elif p is None:
                row['reason'] = 'Point not observed at this epoch'
            if row['reason']:
                missing+=1
                previous.pop(key,None)
            else:
                delta=[(p[k]-reference[k])*1000 for k in ('e','n','h')]
                row.update(dE_mm=delta[0],dN_mm=delta[1],dH_mm=delta[2],settlement_mm=-delta[2],
                           displacement_mm=math.sqrt(sum(d*d for d in delta)),
                           status=_level(max(map(abs,delta)),limits))
                for axis in ('E','N','H'):
                    sa,sb=reference.get('sigma_'+axis.lower()+'_mm'),p.get('sigma_'+axis.lower()+'_mm')
                    if sa is not None and sb is not None:
                        row['sigma_d'+axis+'_mm']=0. if epoch['id']==baseline['id'] else math.hypot(sa,sb)
                if key in previous:
                    then,height=previous[key];days=(now-then).total_seconds()/86400
                    if days>0:row['settlement_rate_mm_day']=(height-p['h'])*1000/days
                previous[key]=(now,p['h'])
            points.append(row)
        for chord in data.get('chords',[]):
            keys=[(chord['ring_id'],chord[end]) for end in ('a','b')]
            row=dict(epoch=epoch['timestamp'],ring_id=chord['ring_id'],chord_id=chord['id'],
                     a=chord['a'],b=chord['b'],mode=chord['mode'],baseline_length_m=None,
                     length_m=None,convergence_mm=None,convergence_rate_mm_day=None,status='unavailable',reason=None)
            key=chord['ring_id'],chord['id']
            if not all(k in base and k in current for k in keys):
                row['reason']='Both chord endpoints must be observed in the baseline and this epoch.'
            elif any(values[k].get('excluded') for values in (base, current) for k in keys):
                row['reason']='A chord endpoint observation is excluded in the baseline or this epoch.'
            if row['reason']:
                previous_chords.pop(key,None);missing+=1
            else:
                axes=('e','n','h') if chord['mode']=='3d' else ('e','n')
                def length(values):return math.sqrt(sum((values[keys[1]][k]-values[keys[0]][k])**2 for k in axes))
                initial,value=length(base),length(current)
                if initial<=1e-9:
                    row['reason']='Baseline chord has zero length.';missing+=1
                else:
                    closure=(initial-value)*1000
                    row.update(baseline_length_m=initial,length_m=value,convergence_mm=closure,status=_level(closure,limits))
                    if key in previous_chords:
                        then,old=previous_chords[key];days=(now-then).total_seconds()/86400
                        if days>0:row['convergence_rate_mm_day']=(old-value)*1000/days
                    previous_chords[key]=(now,value)
            chords.append(row)
    warnings=[
        'Compare the same physical points in the same independently checked datum. Epochs are not aligned or best-fitted to each other.',
        'Positive dH is upward; positive settlement is downward. Positive convergence is shortening of the chosen chord.',
        'Review levels are user-defined: point status uses the largest absolute E/N/H component; chord status uses absolute convergence. This is not an automated safety alarm.',
        'Rates use actual elapsed days between consecutive available epochs; gaps interrupt the rate series.',
        'Optional difference sigmas assume independent epoch errors and omit shared-control correlations. They are not an accuracy certificate.',
    ]
    if missing:warnings.append(f'{missing} point or chord entries are unavailable; missing or excluded observations appear as gaps.')
    if len(epochs)<2:warnings.append('Only one epoch is stored. Add another epoch to obtain time-series changes.')
    return dict(method=METHOD, datum=data['datum'], baseline=baseline['timestamp'], units='mm',
                epochs=[dict(id=e['id'],timestamp=e['timestamp'],points=len(e['points'])) for e in epochs],
                point_series=points,chord_series=chords,rings=sorted({k[0] for k in all_keys}),warnings=warnings)


def _csv_cell(value):
    # Keep numeric signs numeric, while preventing spreadsheet formula execution
    # from user-supplied identifiers and labels.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def export_csv(report, ring=None):
    out=io.StringIO(newline='');writer=csv.writer(out)
    fields=['datum','baseline','type','epoch','ring_id','point_id','chord_id','mode','dE_mm','dN_mm','dH_mm',
            'settlement_mm','displacement_mm','settlement_rate_mm_day','sigma_dE_mm','sigma_dN_mm','sigma_dH_mm',
            'baseline_length_m','length_m','convergence_mm','convergence_rate_mm_day','status','reason','observation_included']
    writer.writerow(fields)
    for kind, rows in (('point',report['point_series']),('chord',report['chord_series'])):
        for row in rows:
            if ring and row['ring_id']!=ring:continue
            data=dict(row,datum=report['datum'],baseline=report['baseline'],type=kind)
            writer.writerow([_csv_cell(data.get(k,'')) for k in fields])
    return out.getvalue()


def export_html(report, ring=None):
    escape=lambda v:html.escape(str(v),quote=True)
    number=lambda v:'—' if v is None else f'{v:.3f}'
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Tunnel Studio monitoring report</title>',
           '<style>body{font:14px system-ui;margin:35px;color:#183847}table{width:100%;border-collapse:collapse;margin-bottom:28px;font-size:12px}th,td{padding:7px;border-bottom:1px solid #ccd8dd;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}h1{font-size:24px}p{line-height:1.5}@media print{button{display:none}body{margin:0}}</style>',
           '<h1>Tunnel monitoring — deformation and settlement</h1>',
           '<p>Datum: '+escape(report['datum'])+'<br>Baseline: '+escape(report['baseline'])+'</p>',
           '<p>Positive settlement = downward. Positive convergence = chord shortening. Differences are in millimetres.</p>',
           '<h2>Point displacement and settlement</h2><table><thead><tr><th>Epoch (UTC)</th><th>Ring / point</th><th>ΔE</th><th>ΔN</th><th>ΔH</th><th>Settlement</th><th>mm/day</th><th>Review</th></tr></thead><tbody>']
    for row in report['point_series']:
        if ring and row['ring_id']!=ring:continue
        values=[row['epoch'],row['ring_id']+' / '+row['point_id']]+[number(row[k]) for k in ('dE_mm','dN_mm','dH_mm','settlement_mm','settlement_rate_mm_day')]+[row['reason'] or row['status']]
        parts.append('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in values)+'</tr>')
    parts.append('</tbody></table><h2>Chord convergence</h2><table><thead><tr><th>Epoch (UTC)</th><th>Ring / chord</th><th>Mode</th><th>Convergence (mm)</th><th>mm/day</th><th>Review</th></tr></thead><tbody>')
    for row in report['chord_series']:
        if ring and row['ring_id']!=ring:continue
        values=[row['epoch'],row['ring_id']+' / '+row['chord_id'],row['mode'],number(row['convergence_mm']),number(row['convergence_rate_mm_day']),row['reason'] or row['status']]
        parts.append('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in values)+'</tr>')
    parts.append('</tbody></table>'+''.join('<p>'+escape(w)+'</p>' for w in report['warnings'])+'</html>')
    return ''.join(parts)


def api(s,h,path,body=None):
    if path!='/api/tunnel-monitoring':return False
    data=s.STATE.get('tunnel_monitoring') or empty()
    if body is None:
        h.send(dict(data=data,result=calculate(data)));return True
    if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
    action=body.get('action')
    if action=='export':
        report=calculate(data);kind=body.get('format','csv');ring=body.get('ring_id')
        if not report['baseline']:raise ValueError('Import monitoring observations before exporting.')
        if kind=='section_html':
            import monitoring_graphics
            bearing=_number(body.get('heading_deg'), 'heading bearing in degrees')
            triggers=section_triggers(data.get('section_triggers', {}))
            text=monitoring_graphics.export_section(data,report,ring,bearing,triggers)
            h.send(dict(text=text,filename='TunnelStudio_Section_Monitoring.html',content_type='text/html'));return True
        if kind not in ('csv','html'):raise ValueError('Choose CSV or HTML report.')
        text=export_csv(report,ring) if kind=='csv' else export_html(report,ring)
        h.send(dict(text=text,filename='TunnelStudio_Monitoring.'+kind,content_type='text/csv' if kind=='csv' else 'text/html'));return True
    if action=='import':new=import_csv(data,body.get('csv'),body.get('datum'),body.get('source_name','Entered CSV'))
    elif action=='configure':new=configure(data,body)
    elif action=='configure_section_triggers':
        new=dict(data, section_triggers=section_triggers(body.get('section_triggers')))
    elif action=='set_observation_use':
        new=_set_observation_use(data, body)
    elif action=='remove_epoch':
        ident=body.get('id')
        if ident not in {e['id'] for e in data['epochs']}:raise ValueError('Choose an existing epoch.')
        new=copy.deepcopy(data);new['epochs']=[e for e in new['epochs'] if e['id']!=ident]
        if new['baseline']==ident:
            new['baseline']=new['epochs'][0]['id'] if new['epochs'] else None
        if not new['epochs']:new=dict(empty(), section_triggers=data.get('section_triggers', {}))
    else:raise ValueError('Unknown monitoring action.')
    result=calculate(new)
    old=s.STATE.get('tunnel_monitoring')
    try:
        s.STATE['tunnel_monitoring']=new;s.save()
    except BaseException:
        if old is None:s.STATE.pop('tunnel_monitoring',None)
        else:s.STATE['tunnel_monitoring']=old
        raise
    h.send(dict(data=new,result=result,saved=True));return True
