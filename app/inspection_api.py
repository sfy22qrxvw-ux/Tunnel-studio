# Copyright (c) 2026 Damian Gulczynski.
"""Scene input resolution and transactional six-mode inspection endpoints."""
from project_data_layout import folder as asset_folder
from pathlib import Path
from types import SimpleNamespace
import copy
import math

import inspection
import inspection_geometry
import jobdata
import analysis_sources


def resolve(s, ident):
    if not isinstance(ident, str) or not ident:
        raise ValueError('Choose an object from the project.')
    if ident == analysis_sources.ALL_ID or ident.startswith(analysis_sources.BUNDLE_PREFIX):
        return analysis_sources.resolve(s, ident)
    item = next((x for x in s.STATE.get('job_items', []) if x['id'] == ident), None)
    if item:
        analysis_sources.require_current(item)
        return dict(id=item['id'], name=item['name'], kind=item['kind'], meta=item['meta'])
    cad = next((x for x in (s.STATE.get('cad') or {}).get('objects', [])
                if x['id'] == ident), None)
    if cad:
        return {**cad, 'kind': 'cad'}
    if ident.startswith('structure:'):
        spec = next((x for x in s.STATE.get('structures', [])
                     if 'structure:' + x['id'] == ident), None)
        if spec:
            row = dict(id=ident, name=spec['name'], kind='geometry',
                       definition=copy.deepcopy(spec))
            primitive=spec.get('kind')
            if primitive=='plane':
                row['definition'].setdefault('length',1.)
                row['definition'].setdefault('width',1.)
            elif primitive in ('box','cylinder','sphere'):
                yaw=math.radians(float(spec.get('yaw',0)))
                x=[math.cos(yaw),math.sin(yaw),0.]
                y=[-math.sin(yaw),math.cos(yaw),0.]
                centre=[float(spec.get(k,0)) for k in ('e','n','h')]
                definition=dict(kind=primitive,origin=centre,frame=dict(x=x,y=y))
                if primitive=='box':
                    length,width,height=[float(spec.get(k,1)) for k in ('length','width','height')]
                    definition.update(size=[length,width,height],
                        origin=[centre[i]-x[i]*length/2-y[i]*width/2 for i in range(3)])
                else:
                    definition.update(radius=float(spec.get('radius',1)))
                    if primitive=='cylinder':definition['height']=float(spec.get('height',1))
                row['definition']=definition
            else:
                import structures
                axis = s.STATE.get('axis')
                if spec.get('kind') == 'tunnel' and axis is None:
                    raise ValueError('This tunnel surface needs its saved alignment.')
                vertices, faces = structures.mesh(spec, axis)
                row.update(vertices=vertices, faces=faces)
                if primitive == 'tunnel':
                    row['alignment_rows'] = axis.a.tolist()
            return row
    raise ValueError('The selected inspection object is no longer available.')


def _reference_modes(item, info=None):
    kind = item['kind']
    if kind == 'scan':
        return ['cloud_cloud']
    if kind == 'mesh':
        return ['cloud_mesh', 'mesh_mesh']
    if kind == 'cad' and info:
        return ['cloud_cad', 'mesh_cad', 'cloud_geometry']
    if kind == 'geometry' and info:
        return ['cloud_geometry']
    return []


def _row(item, modes):
    meta = item.get('meta', {})
    row = dict(id=item['id'], name=item['name'], kind=item['kind'], modes=modes)
    if item['kind'] == 'scan':
        row.update(count=int(meta.get('count', 0)), representation='Retained measured points',
                   signed_supported=False, source_scan_ids=[r.get('id') or item['id'] for r in analysis_sources.parts(meta)],
                   note=('Every retained point in all constituent scans; no merged cloud is created.'
                         if meta.get('cloud_parts') else 'Every retained original point.'))
    elif item['kind'] == 'mesh':
        row.update(triangles=int(meta.get('triangles', 0)),
                   representation='Triangle mesh', signed_supported=True)
    return row


def options(s):
    sources, references, unavailable = [], [], []
    for item in [*analysis_sources.collections(s), *s.STATE.get('job_items', [])]:
        if item.get('kind') not in ('scan', 'mesh') or item.get('merge_stale') or item.get('merge_archived'):
            continue
        resolved = resolve(s, item['id'])
        modes = [name for name, spec in inspection.MODES.items() if spec[1] == item['kind']]
        row=_row(resolved,modes)
        if resolved['kind']=='scan':row['has_limit_box']=any(analysis_sources.saved_boxes(s,resolved))
        sources.append(row)
        row = _row(resolved, _reference_modes(resolved))
        if item['kind'] == 'mesh':
            row['note'] = 'Nearest distance to triangle interiors. Optional sign follows face winding.'
        references.append(row)
    identifiers = [x['id'] for x in (s.STATE.get('cad') or {}).get('objects', [])]
    identifiers += ['structure:' + x['id'] for x in s.STATE.get('structures', [])]
    for ident in identifiers:
        try:
            item = resolve(s, ident)
            info = inspection_geometry.reference_info(item)
            if not info:
                unavailable.append(dict(id=ident, name=item['name'],
                                        reason='This entity has no supported comparison surface or curve.'))
                continue
            row = _row(item, _reference_modes(item, info))
            row.update({k: v for k, v in info.items() if k != 'kind'})
            row['geometry_kind'] = info['kind']
            references.append(row)
        except (ValueError, OSError, KeyError) as error:
            unavailable.append(dict(id=ident, reason=str(error)))
    return dict(sources=sources, references=references, unavailable=unavailable,
                modes=[dict(id=key, label=value[0]) for key, value in inspection.MODES.items()])


def _fingerprint(s, item):
    if item['kind'] != 'cad':
        return inspection.fingerprint(item)
    # CAD is immutable between committed revisions. Do not serialise a large
    # model again on every status poll or merely on changing its visibility.
    key = (s.STATE.get('project_id'), (s.STATE.get('cad') or {}).get('revision'),
           id(s.STATE.get('cad')), item['id'])
    cache = getattr(s, '_inspection_cad_fingerprints', {})
    if key not in cache:
        if len(cache) >= 32:
            cache.clear()
        cache[key] = inspection.fingerprint(item)
        s._inspection_cad_fingerprints = cache
    return cache[key]


def current(s):
    result = s.STATE.get('inspection_result')
    if not result:
        return None
    if result.get('stale'):
        return result
    try:
        source, reference = resolve(s, result['source_id']), resolve(s, result['reference_id'])
        if _fingerprint(s, source) != result['source_fingerprint']:
            raise ValueError('The inspected object changed.')
        analysis_sources.check_scope(s,source,result.get('processing_scope'))
        if _fingerprint(s, reference) != result['reference_fingerprint']:
            raise ValueError('The comparison reference changed.')
        inspection.validate_result(result)
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {**result, 'stale': True,
                'stale_reason': str(error) + ' Run the comparison again.'}
    return {**result, 'stale': False}


def public(result):
    return None if not result else {
        k: v for k, v in result.items()
        if not k.endswith('_file') and not k.endswith('_fingerprint') and k != 'input_snapshots'
    }


def _require_result(s, ident=None):
    result = current(s)
    if not result:
        raise ValueError('Run a comparison first.')
    if ident and ident != result['id']:
        raise ValueError('A newer comparison is available. Refresh the result.')
    if result.get('stale'):
        raise ValueError(result['stale_reason'])
    return result


def _busy(s):
    if s.JOB.get('busy'):
        raise ValueError('Wait for the current operation to finish.')


def _cancelled(s):
    request = getattr(s, 'JOB_CANCEL_FILE', None)
    return bool(request and request.exists())


def get(s, h, path, args):
    if path not in ('/api/inspection-options', '/api/inspection',
                    '/api/inspection-preview', '/api/inspection.csv'):
        return False
    if path == '/api/inspection-options':
        h.send(options(s)); return True
    if path == '/api/inspection':
        h.send(dict(result=public(current(s)))); return True
    _busy(s)
    result = _require_result(s, args.get('id', [None])[0])
    if path == '/api/inspection-preview':
        if args.get('format', [''])[0] == 'binary':
            import display_buffers
            h.send(display_buffers.cached_result(result, lambda: inspection.preview(result, packed=True)),
                   'application/octet-stream')
            return True
        h.send(inspection.preview(result)); return True
    csv_file = asset_folder(s.WORK,'export') / (result['id'] + '.inspection.csv')
    if not csv_file.is_file():
        raise ValueError('Prepare the result CSV using Download result CSV first.')
    h.send_file(csv_file, 'text/csv; charset=utf-8',
                result['mode_label'].replace(' ', '_') + '.csv')
    return True


def post(s, h, path, body):
    if path not in ('/api/inspect', '/api/inspection-export'):
        return False
    _busy(s)
    if path == '/api/inspection-export':
        result = _require_result(s, body.get('id'))
        def export():
            destination = asset_folder(s.WORK,'export') / (result['id'] + '.inspection.csv')
            temporary = Path(str(destination) + '.tmp')
            try:
                if destination.is_file():
                    s.progress('Inspection CSV is already prepared.')
                    return
                jobdata.room(s.WORK, result['record_count'] * 220)
                inspection.export_csv(result, temporary, s.progress, lambda: _cancelled(s))
                inspection.check_cancel(lambda: _cancelled(s))
                temporary.replace(destination)
                s.progress('Inspection CSV prepared. Choose where to save the download.')
            finally:
                temporary.unlink(missing_ok=True)
        s.start_job(export, 'inspection-export', native_progress=True)
        h.send(dict(started=True)); return True
    mode = body.get('mode')
    if mode not in inspection.MODES:
        raise ValueError('Choose one of the six inspection comparison modes.')
    source, reference = resolve(s, body.get('source_id')), resolve(s, body.get('reference_id'))
    if source['kind'] != inspection.MODES[mode][1]:
        raise ValueError('The inspected object type does not match the chosen comparison.')
    info = None if reference['kind'] in ('scan', 'mesh') else inspection_geometry.reference_info(reference)
    if mode not in _reference_modes(reference, info):
        raise ValueError('The reference type does not match the chosen comparison.')
    if mode == 'cloud_cloud' and analysis_sources.overlap(source['meta'], reference['meta']):
        raise ValueError('Source and reference contain the same scan. Choose separate scans or disjoint registered bundles.')
    scope=analysis_sources.processing_scope(s,source,body.get('scope','auto'),body.get('selection'))
    # Snapshots hold file-backed input metadata only. Native CAD definitions
    # are already in the project and must not be duplicated into its manifest.
    snapshots = {role: copy.deepcopy(item) for role, item in
                 (('source', source), ('reference', reference)) if item['kind'] in ('scan', 'mesh')}
    def calculate():
        result = None
        previous = s.STATE.get('inspection_result')
        try:
            result = inspection.run(
                source, reference, s.WORK, mode,
                tolerance_mm=body.get('tolerance_mm', 25),
                band_m=body.get('band_m', 0), signed=body.get('signed', False),
                spacing_m=body.get('spacing_m', .05),
                progress=s.progress, cancel=lambda: _cancelled(s),scope=scope)
            result['input_snapshots'] = snapshots
            s.STATE['inspection_result'] = result
            s.save()
        except BaseException:
            s.STATE['inspection_result'] = previous
            if result:
                Path(result['results_file']).unlink(missing_ok=True)
            raise
        s.progress(f'{result["mode_label"]} complete · {result["used"]:,} included · '
                   f'{result["within_percent"]:.1f}% within tolerance.')
    s.start_job(calculate, 'inspection', native_progress=True)
    h.send(dict(started=True)); return True


def restored(data, portable=False):
    """Relink only validated copies, never revive a previously stale result."""
    result = data.get('inspection_result')
    if not result:
        return
    try:
        inspection.validate_result(result)
        if not portable or result.get('stale'):
            return
        context = SimpleNamespace(STATE=data)
        for role in ('source', 'reference'):
            item = resolve(context, result[role + '_id'])
            snapshot = result.get('input_snapshots', {}).get(role)
            if snapshot is None:
                if inspection.fingerprint(item) != result[role + '_fingerprint']:
                    raise ValueError('Saved reference geometry no longer matches the result.')
                continue
            if inspection.fingerprint(snapshot) != inspection.fingerprint(item):
                raise ValueError('Copied input metadata no longer matches the result.')
            result[role + '_fingerprint'] = inspection.fingerprint(item)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result.update(stale=True, stale_reason=str(error) + ' Run the comparison again.')
