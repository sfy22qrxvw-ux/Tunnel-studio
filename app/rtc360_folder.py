"""Read-only RTC360 folder discovery and full-raster verification.

No calibrated point cloud is produced. Row coefficients, decoded range grids,
intensity and all source references stay associated with their original setup.
The observed SCV6 variant is supported; unknown variants fail explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import zlib

import numpy as np

from rtc360_scan_probe import read_setup, read_limited, split_scan, require, MAX_XML
from rtc360_image_probe import unwrap_cubemap

INDEX = 'VGFibGVPZkNvbnRlbnQ='
MAX_SETUPS = 200
# Camera right, down, forward in setup coordinates. Named cube-face convention
# checked against shared image edges and the scan-angle/depth comparison.
FACE_ROTATIONS = {
    'PosY': [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
    'NegY': [[-1, 0, 0], [0, 0, -1], [0, -1, 0]],
    'PosX': [[0, -1, 0], [0, 0, -1], [1, 0, 0]],
    'NegX': [[0, 1, 0], [0, 0, -1], [-1, 0, 0]],
    'PosZ': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    'NegZ': [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
}


def contained(root, name):
    p = (Path(root) / name).resolve()
    require(p.is_relative_to(Path(root).resolve()), 'A source reference escapes its setup folder.')
    return p


def xml_root(path):
    b = read_limited(path, MAX_XML)
    require(b'<!DOCTYPE' not in b.upper() and b'<!ENTITY' not in b.upper(),
            'XML declarations/entities are unsupported.')
    return ET.fromstring(b)


def discover(path):
    """Accept an individual setup, job folder, or RTC360Store directory.

    Only two child levels are visited; never follow directory symlinks. An
    invalid setup appears with a diagnostic instead of being silently omitted.
    """
    path = Path(str(path).strip().strip('"')).expanduser().resolve()
    if path.is_file():
        require(path.name in (INDEX, 'project.rtc360', 'SetupPositions'),
                'Select the scanner job/setup folder or its metadata file.')
        path = path.parent
    require(path.is_dir(), 'The scanner folder is missing or unavailable.')
    candidates, seen, visited = [], set(), 0
    queue = [(path, 0)]
    while queue:
        folder, level = queue.pop(0)
        visited += 1
        require(visited <= 1000, 'Too many folders. Choose one scanner job folder.')
        index = folder / INDEX
        if index.is_file():
            try:
                root = xml_root(index)
            except (ValueError, ET.ParseError) as exc:
                candidates.append(dict(folder=str(folder), name=folder.name, error=str(exc)))
                continue
            if root.tag == 'Setup':
                try:
                    s = read_setup(index)
                    require(s['id'] and s['id'] not in seen, 'Duplicate or missing setup ID.')
                    seen.add(s['id'])
                    scans = s['references']['ScanSlice']
                    require(0 < len(scans) <= 2000, 'Missing or excessive scan slices.')
                    missing, sizes = [], {}
                    for kind, refs in s['references'].items():
                        for r in refs:
                            p = contained(folder, r['filename'])
                            if not p.is_file():
                                missing.append(dict(kind=kind, filename=r['filename']))
                            else:
                                sizes[r['filename']] = p.stat().st_size
                    candidates.append(dict(folder=str(folder), name=s['name'], id=s['id'],
                        metadata_sha256=s['sha256'], scan_slices=len(scans),
                        raster=s['measured_raster'], source_bytes=sum(sizes.values()),
                        photographs=len(s['references']['ImagingSlice']),
                        preview_formats=[r['format'] for r in s['references']['LwpoSlice']],
                        missing=missing, creator=s['attributes'].get('CreatorDevice'),
                        captured=s['attributes'].get('Creation'),
                        status='inspection only; calibrated point import not enabled'))
                except (ValueError, KeyError, OSError) as exc:
                    candidates.append(dict(folder=str(folder), name=folder.name, error=str(exc)))
                require(len(candidates) <= MAX_SETUPS, 'Choose a job with at most 200 setups.')
                continue
        if level < 2:
            queue.extend((p, level + 1) for p in sorted(folder.iterdir())
                         if p.is_dir() and not p.is_symlink())
    require(candidates, 'No RTC360 setup metadata found. Choose the job folder containing the R0001-style folders.')
    return dict(root=str(path), setups=candidates, calibrated_xyz=False)


def source_bytes(folder, ref):
    data = read_limited(contained(folder, ref['filename']))
    if ref.get('crc32'):
        require(zlib.crc32(data) == int(ref['crc32'], 0), 'CRC mismatch: ' + ref['filename'])
    return data


def float_cube(folder, setup, code):
    import imagecodecs
    refs = [r for r in setup['references']['LwpoSlice'] if r['format'] == str(code)]
    require(len(refs) == 1, 'Expected one preview layer for format ' + str(code))
    faces = {}
    for name, (body, _) in unwrap_cubemap(source_bytes(folder, refs[0])).items():
        require(body.startswith(b'II\xbc\x01'), 'Unsupported float-cube encoding.')
        # Same validated JPEG XR header as the scan; read dimensions first and
        # then enforce the full framing/pixel-format constraints before decoding.
        import struct
        from rtc360_scan_probe import inspect_jxr_header
        off = struct.unpack_from('<I', body, 4)[0]
        require(8 <= off < len(body) - 2, 'Invalid cube IFD.')
        n = struct.unpack_from('<H', body, off)[0]
        require(0 < n <= 64 and off + 2 + 12*n + 4 <= len(body), 'Invalid cube IFD table.')
        tags = {struct.unpack_from('<HHII', body, off+2+12*i)[0]:
                struct.unpack_from('<HHII', body, off+2+12*i)[3] for i in range(n)}
        w, h = tags.get(0xBC80, 0), tags.get(0xBC81, 0)
        require(w == h and w in (512, 1024, 2048), 'Unsupported cube size.')
        inspect_jxr_header(body, w, h)
        a = imagecodecs.jpegxr_decode(body)
        require(a.dtype == np.float32 and a.shape == (h, w), 'Unexpected cube raster.')
        faces[name] = a
    return faces


def compare_depth(directions, ranges, cubes):
    """Internal consistency only: nearest preview pixels, not an E57 check."""
    errors, radial_errors, relative_errors = [], [], []
    for name, rot in FACE_ROTATIONS.items():
        c = directions @ np.asarray(rot).T
        chosen = (c[:, 2] > 0) & (np.argmax(np.abs(c), axis=1) == 2)
        v, r = c[chosen], ranges[chosen]
        if not len(r):
            continue
        side = cubes[name].shape[0]
        uv = np.rint((v[:, :2] / v[:, 2, None] + 1) * (side - 1) / 2).astype(int)
        depth = cubes[name][np.clip(uv[:, 1], 0, side-1), np.clip(uv[:, 0], 0, side-1)]
        mask = np.isfinite(depth) & (depth > .1) & (depth < 200) & (r > .1) & (r < 200)
        expected = r[mask] * v[mask, 2]
        errors.append(depth[mask] - expected)
        radial_errors.append(depth[mask] - r[mask])
        relative_errors.append(np.abs((depth[mask] - expected) / expected))
    if not errors:
        return dict(comparisons=0)
    err, radial, rel = np.concatenate(errors), np.concatenate(radial_errors), np.concatenate(relative_errors)
    return dict(comparisons=len(err), median_abs_depth_error_range_units=float(np.median(abs(err))),
                median_abs_radial_error_range_units=float(np.median(abs(radial))),
                median_relative_depth_error=float(np.median(rel)),
                depth_error_percentiles_1_25_50_75_99=np.percentile(err, [1,25,50,75,99]).tolist(),
                note='Nearest reduced preview pixel; sampling, occlusions and calibration affect differences. This is not an independent accuracy test.')


def verify(folder, output, progress=print):
    """Decode every slice sequentially, bounded to one slice plus overview."""
    import imagecodecs
    from PIL import Image
    folder, output = Path(folder).resolve(), Path(output).resolve()
    require(not output.is_relative_to(folder), 'Use an output directory outside the original setup.')
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    s = read_setup(folder/INDEX)
    rows, cols = s['measured_raster']['rows'], s['measured_raster']['columns']
    require(rows <= 100000 and cols <= 40000 and rows*cols <= 2_000_000_000, 'Unsupported raster size.')
    stride = max(1, math.ceil(max(rows, cols)/1600))
    overview = np.zeros((math.ceil(rows/stride), math.ceil(cols/stride)), np.uint8)
    covered = np.zeros(rows, bool)
    coefficients = np.empty((rows, 4), np.float64)
    reports, points, finite, positive = [], 0, 0, 0
    cubes = float_cube(folder, s, 15)
    comparisons = []
    for number, ref in enumerate(s['references']['ScanSlice']):
        progress(f"{s['name']}: slice {number+1}/{len(s['references']['ScanSlice'])}")
        require(ref.get('format') == '21', 'Unsupported scan format.')
        data = source_bytes(folder, ref)
        report, jxr, jpeg = split_scan(data)
        indices = np.array([r['index'] for r in report['rows']])
        require(report['width'] == cols and indices.min() >= 0 and indices.max() < rows,
                'Slice raster does not belong to this setup.')
        require(not covered[indices].any(), 'Overlapping/repeated rows in setup.')
        a = imagecodecs.jpegxr_decode(jxr)
        require(a.shape == (len(indices), cols) and a.dtype == np.float32, 'Unexpected range grid.')
        with Image.open(io.BytesIO(jpeg)) as im:
            require(im.size == (cols, len(indices)) and im.mode == 'L', 'Unexpected intensity grid.')
            intensity = np.asarray(im)
        selected = indices % stride == 0
        overview[indices[selected]//stride] = intensity[selected, ::stride]
        coeff = np.array([r['coefficients_unapplied'] for r in report['rows']])
        coefficients[indices] = coeff
        columns = np.arange(0, cols, 16)
        sample_rows = np.arange(0, len(indices), 16)
        cs = coeff[sample_rows]
        az = cs[:, 0, None] + cs[:, 2, None]*columns
        el = cs[:, 1, None] + cs[:, 3, None]*columns
        directions = np.stack([np.cos(el)*np.sin(az), np.cos(el)*np.cos(az), np.sin(el)], -1).reshape(-1, 3)
        comparisons.append(compare_depth(directions, a[np.ix_(sample_rows, columns)].ravel(), cubes))
        f = np.isfinite(a)
        good = f & (a > 0)
        covered[indices] = True
        points += a.size
        finite += int(f.sum())
        positive += int(good.sum())
        reports.append(dict(filename=ref['filename'], sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data), first_row=int(indices[0]), last_row=int(indices[-1]),
            raster_slots=a.size, finite=int(f.sum()), positive_finite=int(good.sum()),
            footer_bytes=report['footer_bytes'], all_bytes_accounted_for=True,
            first_coefficients=coeff[0].tolist(), last_coefficients=coeff[-1].tolist()))
    require(covered.all() and points == rows*cols, 'Setup is incomplete: not every raster row was supplied.')
    Image.fromarray(overview).save(output/'intensity_overview.png')
    np.save(output/'row_coefficients_unapplied.npy', coefficients, allow_pickle=False)
    (output/'setup_metadata.json').write_text(json.dumps(s, indent=2), encoding='utf8')
    result = dict(name=s['name'], id=s['id'], complete_raster=True, rows=rows, columns=cols,
        raster_slots=points, finite=finite, positive_finite=positive, nonpositive_finite=finite-positive,
        measured_valid_point_count=None, calibrated_xyz=False, slices=reports,
        range_units='Interpreted as metres; independent E57 verification pending.',
        preview_comparisons=comparisons, seconds=time.monotonic()-start,
        limitations=['Full point-validity/status semantics are not verified.',
            'Factory optical/temperature corrections and additional compressed row fields are not decoded.',
            'Positive finite ranges are not a confirmed valid-point count.',
            'No world positioning, registration, cleaning or calibrated XYZ export.'])
    (output/'verification.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    found = discover(args.folder)
    print(json.dumps(found, indent=2))
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        for setup in found['setups']:
            require(not setup.get('error'), setup.get('error'))
            verify(setup['folder'], args.output/setup['id'])
