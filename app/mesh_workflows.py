# Copyright (c) 2026 Damian Gulczynski.
"""Create triangle assets privately and publish a complete scene item atomically.

All geometry is written into a unique operation directory. A cancellation,
source revision change, or failed project save removes only that directory and
leaves the previous workspace and immutable source assets in place.
"""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy
import json
import shutil
import uuid

import numpy as np

import compute_runtime
import job_api
import jobdata
import mesh_geometry
import meshing
import scene_objects
import structures
from core import project_cloud


def _cancelled(s):
    request = getattr(s, 'JOB_CANCEL_FILE', None)
    return job_api.CANCEL.is_set() or bool(request is not None and request.exists())


def _progress(s, message):
    compute_runtime.check_cancel(lambda: _cancelled(s))
    s.progress(message)


def _selection(source, body):
    selection = copy.deepcopy(body.get('selection'))
    if source.get('limit_box') and source.get('limit_hide_outside', True):
        selection = ({**selection, 'clip_box': source['limit_box']} if selection
                     else {'mode': 'box', **source['limit_box']})
    return jobdata.selection_spec(selection) if selection else None


def _source_identity(source):
    meta = source['meta']
    paths = ('file', 'mask_file') if source['kind'] == 'scan' else ('vertices_file', 'faces_file', 'face_flags_file')
    stamps = []
    for key in paths:
        value = meta.get(key)
        if value:
            path = Path(value)
            stat = path.stat()
            stamps.append((key, str(path.resolve()), stat.st_size, stat.st_mtime_ns))
    return (source['id'], source['kind'], tuple(stamps),
            scene_objects.source_revision(source),
            json.dumps([source.get('limit_box'),source.get('limit_hide_outside',True)],sort_keys=True))


def _publish(s, source_id, source_identity, meta, structure=None):
    if _source_identity(job_api.item(s, source_id)) != source_identity:
        raise ValueError('The source changed while meshing. Create the mesh again from the current source.')
    # Last cancellable checkpoint. No cancellable work follows the atomic save.
    _progress(s, 'Saving joined triangle faces and mesh result…')
    # The callback itself may receive a stop request. Check once more before
    # any metadata mutation; after this point the atomic save is completed.
    compute_runtime.check_cancel(lambda: _cancelled(s))
    previous = copy.deepcopy(s.STATE)
    try:
        if structure is not None:
            s.STATE.setdefault('structures', []).append(structure)
        result = job_api.append(s, 'mesh', meta)
    except BaseException:
        s.STATE.clear()
        s.STATE.update(previous)
        raise
    return result


def create_from_scan(s, body):
    compute_runtime.check_cancel(lambda: _cancelled(s))
    source = job_api.item(s, body.get('source_id'))
    if source['kind'] != 'scan':
        raise ValueError('Select the source scan in the Project Explorer first.')
    __import__('registered_cloud').require_current(source)
    identity = _source_identity(source)
    cloud = source['meta']
    selection = _selection(source, body)
    quality = 'fast' if body.get('preview') else body.get('quality', 'balanced')
    engine = str(body.get('engine', 'tunnel_surface'))
    name = ('Preview · ' if body.get('preview') else '') + str(body.get('name') or 'Measured surface mesh')[:100]
    folder = asset_folder(s.WORK,'mesh') / ('mesh-' + uuid.uuid4().hex)
    folder.mkdir()
    published = False
    try:
        structure = None
        if engine != 'alignment_grid':
            meta = meshing.create(
                cloud, folder, name, method=engine, selection=selection,
                max_points=meshing.working_budget(engine, quality), quality=quality,
                spacing=body.get('step', .1), degrees=body.get('degrees', 2),
                max_edge=body.get('max_edge', 0), smoothing=body.get('smoothing', 0),
                ball_radius=body.get('ball_radius'), sampling_distance=body.get('sampling_distance', 0),
                noise_gap=body.get('noise_gap', 0), hole_handling=body.get('hole_handling', 'preserve'),
                hole_size=body.get('hole_size', .5), alpha_radius=body.get('alpha_radius', 0),
                poisson_depth=body.get('poisson_depth', 8), tunnel_gap=body.get('tunnel_gap', .25),
                progress=lambda message: _progress(s, message), cancel=lambda: _cancelled(s))
        else:
            meta, structure = _alignment_mesh(s, source, folder, name, body, selection)
        meta.setdefault('mesh_summary', {}).update(
            quality_preset=quality, preview=bool(body.get('preview')),
            creation_engine=engine, source_item_id=source['id'], source_name=source['name'],
            source_geometry_revision=scene_objects.source_revision(source),
            source_scope='selection or visible limit box' if selection else 'all retained points')
        result = _publish(s, source['id'], identity, meta, structure)
        published = True
        return result
    finally:
        if not published:
            shutil.rmtree(folder, ignore_errors=True)


def _alignment_mesh(s, source, folder, name, body, selection):
    axis = s.STATE.get('axis')
    if axis is None:
        raise ValueError('Alignment-grid meshing needs a design alignment. Choose Create Tunnel Surface for a straight branch without an alignment.')
    if len(s.STATE.get('structures', [])) >= 30:
        raise ValueError('Maximum 30 alignment structures per project.')
    cloud = source['meta']
    temporary = None
    try:
        if (s.STATE.get('cloud') and s.STATE.get('derived') and
                scene_objects.source_revision({'meta':s.STATE['cloud']}) == scene_objects.source_revision(source)):
            derived = s.STATE['derived']
        else:
            _progress(s, 'Projecting selected scan onto the tunnel alignment…')
            temporary = folder / 'mesh-analysis.analysis64'
            derived, count = project_cloud(cloud, axis, temporary,
                                          lambda message: _progress(s, message), cancel=lambda: _cancelled(s))
            if not count:
                raise ValueError('No source points fall within the loaded alignment. Check positioning, units and alignment extent.')
        _progress(s, 'Creating alignment-grid tunnel mesh…')
        v, f, summary = structures.scan_mesh(
            cloud, derived, axis,
            *[body[k] for k in ('start', 'end', 'step', 'degrees', 'band', 'min_points', 'max_edge')],
            body.get('method', 'mean'), body.get('power', 2), body.get('smoothing', 0),
            selection=selection, progress=lambda message: _progress(s, message))
        compute_runtime.check_cancel(lambda: _cancelled(s))
        va, fa = mesh_geometry.clean_mesh(np.asarray(v, '<f8'), np.asarray(f, '<u4'))
        ident = uuid.uuid4().hex
        dest = folder / (ident + '.mesh.json')
        dest.write_text(json.dumps({'vertices': va.tolist(), 'faces': fa.tolist()}, separators=(',', ':')), encoding='utf8')
        vp, fp = folder / (ident + '.vertices64'), folder / (ident + '.faces32')
        np.asarray(va, '<f8').tofile(vp)
        np.asarray(fa, '<u4').tofile(fp)
        summary = {**summary, **mesh_geometry.topology(va, fa)}
        meta = dict(name=name, vertices_file=str(vp), faces_file=str(fp), vertices=len(va), triangles=len(fa),
                    minimum=va.min(0).tolist(), maximum=va.max(0).tolist(), degenerate_triangles=0,
                    units='Metres in project/alignment coordinates', mesh_summary=summary,
                    quality='Alignment-grid reconstruction; review against the scan.')
        jobdata.preview_cache(meta, folder, lambda message: _progress(s, message))
        structure = dict(id=ident, name=name, kind='scan_mesh', mesh_file=str(dest), summary=summary,
                         visible=True, tx=0, ty=0, tz=0, scale=1, rotation=0,
                         edit_revision=len(s.STATE.get('audit', [])))
        return meta, structure
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refine_mesh(s, body):
    compute_runtime.check_cancel(lambda: _cancelled(s))
    source = job_api.item(s, body.get('source_id'))
    if source['kind'] != 'mesh':
        raise ValueError('Select a triangle mesh in the Project Explorer first.')
    identity = _source_identity(source)
    operation = str(body.get('operation', 'smooth'))
    name = str(body.get('name') or source['name'] + ' · ' + operation.replace('_', ' '))[:120]
    folder = asset_folder(s.WORK,'mesh') / ('mesh-' + uuid.uuid4().hex)
    folder.mkdir()
    published = False
    try:
        meta = meshing.refine(source['meta'], folder, name, operation=operation,
                              smoothing=body.get('smoothing', 2), hole_size=body.get('hole_size', .5),
                              decimate_cell=body.get('decimate_cell', .03),
                              target_triangles=body.get('target_triangles', 50000),
                              gap_min_mm=body.get('gap_min_mm', 50), gap_max_mm=body.get('gap_max_mm', 150),
                              progress=lambda message: _progress(s, message), cancel=lambda: _cancelled(s))
        old = source['meta'].get('mesh_summary', {})
        summary = meta.setdefault('mesh_summary', {})
        for key in ('source_item_id', 'source_name', 'source_geometry_revision', 'source_scope'):
            if key in old:
                summary[key] = old[key]
        summary.update(source_mesh_id=source['id'], refinement_operation=operation)
        result = _publish(s, source['id'], identity, meta)
        published = True
        return result
    finally:
        if not published:
            shutil.rmtree(folder, ignore_errors=True)
