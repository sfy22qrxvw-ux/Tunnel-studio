# Copyright (c) 2026 Damian Gulczynski.
"""Area-weighted, bounded sampling of saved triangle meshes into new clouds.

The output is generated geometry, never a survey observation. Saved mesh
vertices already use the frame shown by the reference-mesh preview and export;
no datum, pose or additional transform is inferred here.
"""
from project_data_layout import folder as asset_folder
from pathlib import Path
import hashlib
import json
import math
import uuid

import numpy as np

import jobdata

FACE_BLOCK = 32768  # At most 98,304 gathered vertices per read.
POINT_BLOCK = 100000
PREVIEW_LIMIT = 250000


def _options(values):
    mode = values.get('mode', 'spacing')
    if mode not in ('spacing', 'count'):
        raise ValueError('Choose average spacing or a requested point count.')
    if mode == 'spacing':
        try:
            spacing = float(values.get('spacing', .05))
        except (TypeError, ValueError):
            raise ValueError('Enter a positive finite average spacing.')
        if not math.isfinite(spacing) or spacing <= 0:
            raise ValueError('Enter a positive finite average spacing.')
        return {'mode': mode, 'spacing': spacing}
    raw = values.get('count', 100000)
    try:
        count = int(raw)
        valid = not isinstance(raw, bool) and float(raw) == count
    except (TypeError, ValueError, OverflowError):
        valid = False
        count = 0
    if not valid or not 1 <= count <= 2**53 - 1:
        raise ValueError('Enter a positive whole point count within the exact integer range.')
    return {'mode': mode, 'count': count}


def _signature(meta):
    nv, nf = int(meta.get('vertices', 0)), int(meta.get('triangles', 0))
    if nv < 3 or nf < 1:
        raise ValueError('Select a mesh containing triangle surfaces.')
    files = []
    for key, count, width in [('vertices_file', nv, 24), ('faces_file', nf, 12),
                              ('vertex_colours_file', nv, 12)]:
        if key == 'vertex_colours_file' and not meta.get(key):
            continue
        path = Path(meta.get(key, ''))
        if not path.is_file():
            raise ValueError('The selected mesh cache is missing: ' + key)
        stat = path.stat()
        if stat.st_size != count * width:
            raise ValueError('The selected mesh cache is incomplete: ' + key)
        files.append([key, str(path.resolve()), stat.st_size, stat.st_mtime_ns])
    return hashlib.sha256(json.dumps([nv, nf, files], sort_keys=True).encode()).hexdigest()


def _gather(path, count, indices, dtype='<f8'):
    ids, inverse = np.unique(indices, return_inverse=True)
    values = jobdata._display_rows(path, count, ids, 3, dtype)
    return values[inverse].reshape(-1, 3, 3)


def _triangles(meta, progress, phase):
    """Owned triangle arrays; no source mapping survives across a yield."""
    nv, nf = int(meta['vertices']), int(meta['triangles'])
    with Path(meta['faces_file']).open('rb') as stream:
        for start in range(0, nf, FACE_BLOCK):
            count = min(FACE_BLOCK, nf - start)
            faces = np.fromfile(stream, '<u4', count=count * 3)
            if len(faces) != count * 3:
                raise ValueError('The mesh topology changed during sampling.')
            faces = faces.reshape(count, 3)
            if np.any(faces >= nv):
                raise ValueError('The mesh refers to missing vertex indices.')
            points = _gather(meta['vertices_file'], nv, faces)
            if not np.isfinite(points).all():
                raise ValueError('The mesh contains nonfinite vertex coordinates.')
            areas = np.linalg.norm(np.cross(points[:, 1] - points[:, 0],
                                            points[:, 2] - points[:, 0]), axis=1) * .5
            if not np.isfinite(areas).all():
                raise ValueError('The mesh triangle areas exceed finite numeric range.')
            colours = None
            if meta.get('vertex_colours_file'):
                colours = _gather(meta['vertex_colours_file'], nv, faces, '<f4')
                invalid = np.isfinite(colours) & ((colours < 0) | (colours > 255))
                if invalid.any():
                    raise ValueError('Mesh vertex colours must be in the 0–255 range.')
            progress(f'{phase}: {start + count:,} / {nf:,} triangles')
            yield points, areas, colours, start + count == nf


def _revision(signature, options):
    return hashlib.sha256(json.dumps([signature, options], sort_keys=True).encode()).hexdigest()


def estimate(meta, values, progress=lambda _: None):
    options = _options(values)
    signature = _signature(meta)
    total = compensation = 0.
    valid = degenerate = coloured = 0
    for _, areas, colours, _ in _triangles(meta, progress, 'Measuring mesh surface'):
        positive = areas > 0
        valid += int(positive.sum())
        degenerate += int((~positive).sum())
        if colours is not None:
            coloured += int((positive & np.isfinite(colours).all(axis=(1, 2))).sum())
        # Compensated accumulation bounds roundoff across arbitrarily many
        # triangle batches without retaining an area array for the whole mesh.
        part = math.fsum(areas.tolist()) - compensation
        updated = total + part
        compensation = (updated - total) - part
        total = updated
    if not valid or not math.isfinite(total) or total <= 0:
        raise ValueError('The mesh has no non-degenerate triangle area to sample.')
    if _signature(meta) != signature:
        raise ValueError('The source mesh changed. Estimate the sampled cloud again.')
    if options['mode'] == 'count':
        count = options['count']
    else:
        divisor = options['spacing'] * options['spacing']
        if not divisor:
            raise ValueError('The requested spacing produces too many points. Increase the spacing.')
        requested = total / divisor
        if not math.isfinite(requested) or requested > 2**53 - 1:
            raise ValueError('The requested spacing produces too many points. Increase the spacing.')
        count = max(1, math.ceil(requested))
    stride = max(1, math.ceil(count / PREVIEW_LIMIT))
    preview_count = math.ceil(count / stride)
    return {
        'revision': _revision(signature, options), 'source_signature': signature,
        'settings': options, 'count': count, 'surface_area': total,
        'average_spacing': math.sqrt(total / count),
        'valid_triangles': valid, 'degenerate_triangles_skipped': degenerate,
        'colour_available': bool(coloured), 'coloured_triangles': coloured,
        'estimated_cache_bytes': count * (40 if coloured else 24) + preview_count * 24,
        'preview_count': preview_count,
        'sampling': 'Area-weighted stratified triangle selection and uniform barycentric samples',
        'spacing_note': 'Spacing controls average area per point; minimum point separation is not enforced.',
        'coordinate_frame': 'Saved mesh coordinates; no additional transform applied',
        'units': meta.get('units', 'Source mesh units'),
    }


def sample(meta, work, values, name, progress=lambda _: None, prepared=None):
    """Write exactly the estimated count in chunks of at most 100,000 points."""
    options = _options(values)
    signature = _signature(meta)
    prepared = prepared or estimate(meta, values, progress)
    if prepared.get('revision') != _revision(signature, options):
        raise ValueError('The mesh or sampling settings changed. Estimate the cloud again.')
    count = int(prepared['count'])
    jobdata.room(work, int(prepared['estimated_cache_bytes']))
    work = asset_folder(work,'derived')
    ident = uuid.uuid4().hex
    destination, attributes, preview = (work / (ident + suffix) for suffix in
                                         ('.xyz64', '.attr32', '.preview64'))
    paths = [destination, attributes, preview]
    rng = np.random.default_rng(241024)
    offset = float(rng.random())
    step = float(prepared['surface_area']) / count
    written = preview_written = rgb_count = 0
    stride = max(1, math.ceil(count / PREVIEW_LIMIT))
    lo = np.full(3, np.inf)
    hi = -lo
    colour_min = np.full(3, np.inf)
    colour_max = -colour_min
    cumulative = compensation = 0.
    colour_file = None
    try:
        with destination.open('xb') as output, preview.open('xb') as display:
            if prepared['colour_available']:
                colour_file = attributes.open('xb')
            try:
                for triangles, areas, colours, last in _triangles(meta, progress, 'Sampling mesh surface'):
                    use = areas > 0
                    triangles, areas = triangles[use], areas[use]
                    if colours is not None:
                        colours = colours[use]
                    if not len(areas):
                        continue
                    cdf = cumulative + np.cumsum(areas, dtype=np.float64)
                    part = math.fsum(areas.tolist()) - compensation
                    updated = cumulative + part
                    compensation = (updated - cumulative) - part
                    cumulative = updated
                    cdf[-1] = cumulative
                    while written < count:
                        stop = min(count, written + POINT_BLOCK)
                        positions = (np.arange(written, stop, dtype=np.float64) + offset) * step
                        take = len(positions) if last else int(np.searchsorted(positions, cumulative, side='left'))
                        if not take:
                            break
                        positions = positions[:take]
                        face = np.searchsorted(cdf, positions, side='right')
                        # The final rounding ulp belongs to the final supported
                        # triangle, never to an absent/out-of-bounds face.
                        face = np.minimum(face, len(triangles) - 1)
                        r = rng.random((take, 2))
                        root = np.sqrt(r[:, 0])
                        b, c = root * (1 - r[:, 1]), root * r[:, 1]
                        tri = triangles[face]
                        points = tri[:, 0] + b[:, None] * (tri[:, 1] - tri[:, 0]) + c[:, None] * (tri[:, 2] - tri[:, 0])
                        if not np.isfinite(points).all():
                            raise ValueError('The sampled coordinates exceed finite numeric range.')
                        points.astype('<f8', copy=False).tofile(output)
                        lo, hi = np.minimum(lo, points.min(0)), np.maximum(hi, points.max(0))
                        first = (-written) % stride
                        shown = points[first::stride]
                        shown.astype('<f8', copy=False).tofile(display)
                        preview_written += len(shown)
                        if colour_file is not None:
                            values_rgb = np.full((take, 3), np.nan, dtype='<f4')
                            if colours is not None:
                                source = colours[face]
                                has_rgb = np.isfinite(source).all(axis=(1, 2))
                                if has_rgb.any():
                                    rgb = (source[has_rgb, 0] + b[has_rgb, None] * (source[has_rgb, 1] - source[has_rgb, 0])
                                           + c[has_rgb, None] * (source[has_rgb, 2] - source[has_rgb, 0]))
                                    values_rgb[has_rgb] = np.clip(rgb, 0, 255)
                                    rgb_count += int(has_rgb.sum())
                                    colour_min = np.minimum(colour_min, rgb.min(0))
                                    colour_max = np.maximum(colour_max, rgb.max(0))
                            values_attr = np.full((take, 4), np.nan, dtype='<f4')
                            values_attr[:, 1:] = values_rgb
                            values_attr.tofile(colour_file)
                        written += take
                        progress(f'Creating sampled cloud: {written:,} / {count:,} points')
            finally:
                if colour_file is not None:
                    colour_file.close()
        if written != count:
            raise ValueError('The source mesh area changed while sampling. Estimate the cloud again.')
        if _signature(meta) != signature:
            raise ValueError('The source mesh changed while sampling. The generated cloud was discarded.')
        out = {
            'name': str(name or (meta.get('name', 'Mesh') + ' · sampled cloud'))[:150],
            'file': str(destination), 'count': written,
            'minimum': lo.tolist(), 'maximum': hi.tolist(),
            'preview_file': str(preview), 'preview_count': preview_written, 'preview_stride': stride,
            'units': meta.get('units', 'Source mesh units'),
            'position_status': 'Sampled mesh geometry · saved mesh coordinates · not survey observations',
            'generated_geometry': True,
            'mesh_sampling': {k: v for k, v in prepared.items() if k != 'source_signature'},
            'sampled_from_mesh': meta.get('name', 'Mesh'),
        }
        if rgb_count:
            out.update(attributes_file=str(attributes), attribute_counts=[0, rgb_count, rgb_count, rgb_count],
                       attribute_min=[None, *colour_min.tolist()], attribute_max=[None, *colour_max.tolist()])
        else:
            attributes.unlink(missing_ok=True)
        progress(f'Sampled cloud ready: {written:,} / {count:,} generated points; source mesh unchanged')
        return out
    except Exception:
        if colour_file is not None and not colour_file.closed:
            colour_file.close()
        for path in paths:
            path.unlink(missing_ok=True)
        raise
