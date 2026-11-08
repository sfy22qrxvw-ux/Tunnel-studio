# Copyright (c) 2026 Damian Gulczynski.
"""Persistent, bounded comparison results for clouds and sampled mesh surfaces.

Every retained finite source cloud point is evaluated. Mesh source surfaces
are sampled at an explicit maximum spacing and statistics are area weighted.
Reference evaluators define the distance; this module never registers inputs.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
import time
import uuid

import numpy as np
import jobdata

RECORD = np.dtype([
    ('sample_id', '<u8'), ('element_id', '<u8'), ('xyz', '<f8', (3,)),
    ('deviation_m', '<f8'), ('weight', '<f8'), ('status', 'u1'),
])
STAT_RECORD = np.dtype([('value', '<f8'), ('weight', '<f8')])
BLOCK = 25000
MODES = {
    'cloud_mesh': ('Cloud vs Mesh', 'scan', 'mesh'),
    'cloud_cloud': ('Cloud vs Cloud', 'scan', 'scan'),
    'cloud_cad': ('Cloud vs CAD', 'scan', 'cad'),
    'cloud_geometry': ('Cloud vs Geometry', 'scan', 'geometry'),
    'mesh_mesh': ('Mesh vs Mesh', 'mesh', 'mesh'),
    'mesh_cad': ('Mesh vs CAD', 'mesh', 'cad'),
}


def check_cancel(cancel):
    if cancel():
        raise jobdata.Cancelled('Inspection cancelled. The previous result is unchanged.')


def fingerprint(value):
    """Fast immutable-cache revision, also detecting outside file changes."""
    def walk(v, key=''):
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in sorted(v.items())
                    if k not in ('visible', 'colour', 'name', 'preview_file',
                                 'preview_count', 'preview_stride', 'retained_properties',
                                 'retained_summary')}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, str) and (key.endswith('_file') or key == 'file'):
            p = Path(v)
            st = p.stat()
            return [str(p.resolve()), st.st_size, st.st_mtime_ns]
        return v
    data = json.dumps(walk(value), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf8')).hexdigest()


def cloud_batches(meta, counters, progress, cancel):
    import analysis_sources
    for ids, q in analysis_sources.blocks(meta, counters, progress, cancel, BLOCK):
        yield ids, q, np.ones(len(ids), dtype=np.float64)


class Quantiles:
    """Exact order/weighted-CDF quantiles using bounded disk passes.

    Histograms only locate candidate intervals. The actual values in the
    interval are sorted, or narrowed again when they exceed the memory budget.
    No result percentile is estimated from a preview or random sample.
    """
    LIMIT = 250000
    BINS = 4096

    def __init__(self, path, count, total_weight, minimum, maximum, weighted,
                 progress, cancel):
        self.path, self.count = Path(path), int(count)
        self.total_weight = float(total_weight)
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.weighted, self.progress, self.cancel = weighted, progress, cancel
        self.histograms = {}
        self.small = None
        if self.count <= self.LIMIT:
            self.small = np.fromfile(self.path, STAT_RECORD, count=self.count)

    def blocks(self, absolute=False):
        with self.path.open('rb') as stream:
            seen = 0
            while seen < self.count:
                check_cancel(self.cancel)
                rows = np.fromfile(stream, STAT_RECORD,
                                   count=min(self.LIMIT, self.count - seen))
                if not len(rows):
                    raise ValueError('Inspection statistics cache is incomplete.')
                seen += len(rows)
                values = np.abs(rows['value']) if absolute else rows['value']
                yield values, rows['weight']

    @staticmethod
    def inside(values, lo, hi, closed):
        return (values >= lo) & ((values <= hi) if closed else (values < hi))

    def select(self, target, absolute=False):
        if self.small is not None:
            values = np.abs(self.small['value']) if absolute else self.small['value']
            order = np.argsort(values, kind='stable')
            if self.weighted:
                sums = np.cumsum(self.small['weight'][order], dtype=np.float64)
                index = min(len(order) - 1, int(np.searchsorted(sums, target, side='left')))
            else:
                index = min(len(order) - 1, max(0, int(target)))
            return float(values[order[index]])
        lo = 0. if absolute else self.minimum
        hi = max(abs(self.minimum), abs(self.maximum)) if absolute else self.maximum
        closed = True
        if lo == hi:
            return lo
        for depth in range(32):
            check_cancel(self.cancel)
            self.progress('Inspection statistics · exact surface-weighted percentiles'
                          if self.weighted else 'Inspection statistics · exact point percentiles')
            key = (absolute, lo, hi, closed)
            histogram = self.histograms.get(key)
            if histogram is None:
                edges = np.linspace(lo, hi, self.BINS + 1)
                if np.unique(edges).size < 3:
                    # Adjacent float64 values: a two-bin split remains exact.
                    edges = np.unique(edges)
                counts = np.zeros(len(edges) - 1, dtype=np.int64)
                weights = np.zeros(len(edges) - 1, dtype=np.float64)
                actual_lo, actual_hi = math.inf, -math.inf
                for values, weight in self.blocks(absolute):
                    selected = self.inside(values, lo, hi, closed)
                    v, w = values[selected], weight[selected]
                    if not len(v):
                        continue
                    actual_lo = min(actual_lo, float(v.min()))
                    actual_hi = max(actual_hi, float(v.max()))
                    indices = np.minimum(np.searchsorted(edges, v, side='right') - 1,
                                         len(counts) - 1)
                    counts += np.bincount(indices, minlength=len(counts))
                    weights += np.bincount(indices, weights=w, minlength=len(counts))
                if not counts.sum():
                    raise ValueError('Could not resolve an inspection percentile.')
                histogram = (edges, counts, weights, actual_lo, actual_hi)
                # Only the two global distributions are worth retaining.
                if depth == 0:
                    self.histograms[key] = histogram
            edges, counts, weights, actual_lo, actual_hi = histogram
            if actual_lo == actual_hi:
                return actual_lo
            if np.nextafter(actual_lo, math.inf) >= actual_hi:
                low_weight = 0.
                for values, weight in self.blocks(absolute):
                    at_low = values == actual_lo
                    low_weight += float(weight[at_low].sum()) if self.weighted else int(at_low.sum())
                return actual_lo if (target <= low_weight if self.weighted else target < low_weight) else actual_hi
            cumulative = np.cumsum(weights if self.weighted else counts)
            side = 'left' if self.weighted else 'right'
            index = min(len(counts) - 1, int(np.searchsorted(cumulative, target, side=side)))
            while index < len(counts) - 1 and counts[index] == 0:
                index += 1
            before = float(cumulative[index - 1]) if index else 0.
            target -= before
            new_lo, new_hi = float(edges[index]), float(edges[index + 1])
            new_closed = closed and index == len(counts) - 1
            if counts[index] <= self.LIMIT:
                values_parts, weights_parts = [], []
                for values, weight in self.blocks(absolute):
                    selected = self.inside(values, new_lo, new_hi, new_closed)
                    if selected.any():
                        values_parts.append(values[selected])
                        weights_parts.append(weight[selected])
                values = np.concatenate(values_parts)
                weight = np.concatenate(weights_parts)
                order = np.argsort(values, kind='stable')
                if self.weighted:
                    chosen = int(np.searchsorted(np.cumsum(weight[order]), target, side='left'))
                else:
                    chosen = int(round(target))
                return float(values[order[min(max(0, chosen), len(order) - 1)]])
            if new_lo == lo and new_hi == hi:
                # A highly concentrated bin can be narrowed to actual values.
                lo, hi = actual_lo, actual_hi
                closed = True
            else:
                lo, hi, closed = new_lo, new_hi, new_closed
        raise ValueError('The distance distribution could not be resolved within the bounded statistics passes.')

    def percentile(self, percent, absolute=False):
        if self.weighted:
            return self.select(self.total_weight * percent / 100., absolute)
        rank = (self.count - 1) * percent / 100.
        low, high = math.floor(rank), math.ceil(rank)
        a = self.select(low, absolute)
        return a if low == high else a + (self.select(high, absolute) - a) * (rank - low)


def run(source, reference, work, mode, tolerance_mm=25, band_m=0,
        signed=False, spacing_m=.05, progress=lambda message: None,
        cancel=lambda: False):
    if mode not in MODES:
        raise ValueError('Choose a supported Cloud or Mesh comparison.')
    label, source_kind, _ = MODES[mode]
    if source.get('kind') != source_kind:
        raise ValueError(f'{label} needs a {"cloud" if source_kind == "scan" else "mesh"} to inspect.')
    tolerance, band, spacing = float(tolerance_mm) / 1000., float(band_m), float(spacing_m)
    if not np.isfinite([tolerance, band, spacing]).all() or tolerance <= 0 or band < 0:
        raise ValueError('Use a positive tolerance and a non-negative exclusion band.')
    if band and band < tolerance:
        raise ValueError('The comparison band must be zero (off) or at least the tolerance.')
    if spacing <= 0:
        raise ValueError('Mesh sample spacing must be a positive distance in metres.')
    if type(signed) is not bool:
        raise ValueError('Signed distance must be on or off.')
    if mode == 'cloud_cloud' and signed:
        raise ValueError('Cloud vs Cloud nearest-point distances are unsigned.')
    from project_data_layout import folder
    work = folder(work,'analysis')
    work.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    final = work / (ident + '.inspection-results')
    temporary = work / (ident + '.inspection-results.tmp')
    stats_path = work / (ident + '.inspection-stats.tmp')
    initial_source = fingerprint(source)
    initial_reference = fingerprint(reference)
    counters = dict(retained_points=0, deleted_points=0, invalid_points=0)
    evaluated = used = excluded = ambiguous = negative = positive = within = 0
    total_weight = within_weight = below_weight = above_weight = evaluated_weight = 0.
    total = total2 = 0.
    minimum, maximum = math.inf, -math.inf
    evaluator = None
    started = time.monotonic()
    evaluating = False
    def reference_progress(message):
        if evaluating:
            progress(f'{label} · {evaluated:,} samples completed overall · {message}')
        else:
            progress(message)
    try:
        check_cancel(cancel)
        if mode == 'cloud_cloud':
            from inspection_points import CloudReference
            progress('Preparing the full retained reference cloud…')
            evaluator = CloudReference(reference['meta'], work, reference_progress, cancel)
        else:
            from inspection_geometry import make_reference
            progress('Preparing the reference surface…')
            evaluator = make_reference(reference, work=work, signed=signed,
                                       tolerance_mm=float(tolerance_mm),
                                       progress=reference_progress, cancel=cancel)
        actual_signed = bool(getattr(evaluator, 'signed', False))
        if signed and not actual_signed:
            raise ValueError('This reference does not support the requested signed distance.')
        if source_kind == 'scan':
            source_count = int(source['meta']['count'])
            jobdata.room(work, source_count * (RECORD.itemsize + STAT_RECORD.itemsize))
            batches = cloud_batches(source['meta'], counters, progress, cancel)
        else:
            from inspection_geometry import mesh_surface_batches
            source_count = int(source['meta'].get('triangles', 0))
            jobdata.room(work, 16 * 1024 * 1024)
            batches = mesh_surface_batches(source, spacing, chunk_points=BLOCK,
                                           progress=progress, cancel=cancel)
        with temporary.open('xb') as output, stats_path.open('xb') as stats:
            for elements, points, weights in batches:
                check_cancel(cancel)
                p = np.asarray(points, dtype=np.float64)
                w = np.asarray(weights, dtype=np.float64)
                ids = np.asarray(elements, dtype=np.uint64)
                if p.ndim != 2 or p.shape[1] != 3 or len(w) != len(p) or len(ids) != len(p):
                    raise ValueError('Invalid inspection source block.')
                if not np.isfinite(p).all() or not np.isfinite(w).all() or np.any(w <= 0):
                    raise ValueError('Inspection samples must be finite with positive weights.')
                if not len(p):
                    continue
                evaluating = True
                try:
                    distances, valid = evaluator.evaluate(p)
                finally:
                    evaluating = False
                distances = np.asarray(distances, dtype=np.float64)
                valid = np.asarray(valid, dtype=bool) & np.isfinite(distances)
                if distances.shape != (len(p),) or valid.shape != (len(p),):
                    raise ValueError('The comparison engine returned an invalid block.')
                admitted = valid & ((np.abs(distances) <= band + 1e-12) if band else True)
                record = np.empty(len(p), dtype=RECORD)
                record['sample_id'] = np.arange(evaluated, evaluated + len(p), dtype=np.uint64)
                record['element_id'], record['xyz'] = ids, p
                record['deviation_m'], record['weight'] = distances, w
                record['status'] = np.where(~valid, 2, np.where(admitted, 0, 1))
                record.tofile(output)
                values, admitted_weights = distances[admitted], w[admitted]
                if len(values):
                    stat_rows = np.empty(len(values), STAT_RECORD)
                    stat_rows['value'], stat_rows['weight'] = values, admitted_weights
                    stat_rows.tofile(stats)
                    minimum, maximum = min(minimum, float(values.min())), max(maximum, float(values.max()))
                    tw = float(admitted_weights.sum())
                    total_weight += tw
                    total += float(np.dot(values, admitted_weights))
                    total2 += float(np.dot(values * values, admitted_weights))
                    low, high = values < -tolerance - 1e-12, values > tolerance + 1e-12
                    inside = ~(low | high)
                    negative += int(low.sum()); positive += int(high.sum()); within += int(inside.sum())
                    below_weight += float(admitted_weights[low].sum())
                    above_weight += float(admitted_weights[high].sum())
                    within_weight += float(admitted_weights[inside].sum())
                used += len(values)
                excluded += int((valid & ~admitted).sum())
                ambiguous += int((~valid).sum())
                evaluated += len(p)
                evaluated_weight += float(w.sum())
                if source_kind == 'mesh':
                    progress(f'{label} · {evaluated:,} surface samples · {used:,} included')
                else:
                    checked = counters['retained_points'] + counters['deleted_points']
                    progress(f'{label} · {checked:,} / {source_count:,} input points · {used:,} included')
                if evaluated % (BLOCK * 8) < len(p):
                    jobdata.room(work, 4 * BLOCK * (RECORD.itemsize + STAT_RECORD.itemsize))
        if used == 0 or total_weight <= 0:
            raise ValueError('No valid distances remain. Check the reference, coordinates and comparison band.')
        if fingerprint(source) != initial_source or fingerprint(reference) != initial_reference:
            raise ValueError('An input changed during inspection. The previous result is unchanged.')
        quantiles = Quantiles(stats_path, used, total_weight, minimum, maximum,
                              source_kind == 'mesh', progress, cancel)
        median = quantiles.percentile(50)
        p95 = quantiles.percentile(95)
        p95abs = quantiles.percentile(95, absolute=True) if actual_signed else p95
        check_cancel(cancel)
        temporary.replace(final)
        sampling_note = (
            'Every retained finite source point was evaluated; only the 3D display is sampled.'
            if source_kind == 'scan' else
            f'Triangle-interior surface samples at maximum spacing {spacing:g} m. '
            'Statistics and tolerance percentages are area weighted. Maximum deviation is the '
            'maximum at evaluated samples, not a proven continuous-surface maximum.')
        result = dict(
            id=ident, version=1, mode=mode, mode_label=label, source_kind=source_kind,
            source_id=source['id'], source_name=source.get('name', 'Source'),
            reference_id=reference['id'], reference_name=reference.get('name', 'Reference'),
            reference_kind=reference.get('kind'), reference_label=label,
            reference_representation=getattr(evaluator, 'representation', 'retained measured points'),
            results_file=str(final), record_count=evaluated, source_count=source_count,
            source_ranges=__import__('analysis_sources').source_ranges(source['meta']) if source_kind == 'scan' else [],
            evaluated=evaluated, used=used, excluded_by_band=excluded,
            outside_design_or_ambiguous=ambiguous, **counters,
            within_tolerance=within, within_percent=100. * within_weight / total_weight,
            below_tolerance=negative, above_tolerance=positive,
            evaluated_area_m2=evaluated_weight if source_kind == 'mesh' else None,
            included_area_m2=total_weight if source_kind == 'mesh' else None,
            within_area_m2=within_weight if source_kind == 'mesh' else None,
            min_mm=minimum * 1000., max_mm=maximum * 1000.,
            mean_mm=total / total_weight * 1000.,
            rms_mm=math.sqrt(max(0., total2 / total_weight)) * 1000.,
            median_mm=median * 1000., p95_mm=p95 * 1000., p95_absolute_mm=p95abs * 1000.,
            tolerance_mm=tolerance * 1000., band_m=band, signed=actual_signed,
            spacing_m=spacing if source_kind == 'mesh' else None,
            coordinate_step_mm=getattr(evaluator, 'coordinate_step_mm', None),
            source_fingerprint=initial_source, reference_fingerprint=initial_reference,
            note=getattr(evaluator, 'note', ''),
            sampling_note=sampling_note,
            statistics_note=('Area-weighted mesh surface samples.' if source_kind == 'mesh'
                             else 'Full retained finite source points.') +
                            ' The explicit comparison band excludes points/samples from statistics. '
                            'No automatic registration. Hidden display regions do not alter the calculation.',
            quantiles_note=('Weighted empirical-CDF quantiles of evaluated surface samples.'
                            if source_kind == 'mesh' else 'Exact linear-interpolated point percentiles.'),
            elapsed_seconds=time.monotonic() - started,
            reference_count=getattr(evaluator, 'reference_count', None),
        )
        return result
    except BaseException:
        temporary.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    finally:
        stats_path.unlink(missing_ok=True)
        if evaluator is not None and hasattr(evaluator, 'close'):
            evaluator.close()


def validate_result(result):
    p = Path(result.get('results_file', ''))
    count = result.get('record_count')
    if type(count) is not int or count < 1 or not p.is_file() or p.stat().st_size != count * RECORD.itemsize:
        raise ValueError('Inspection result data is missing or incomplete. Run the comparison again.')


def preview(result, maximum=100000, packed=False):
    validate_result(result)
    maximum = max(1, min(100000, int(maximum)))
    count = result['record_count']
    step = max(1, math.ceil(count / maximum))
    rows, remaining = [], maximum
    # Touch at most maximum sampled records/pages, not every byte of a huge
    # results file merely to display its preview. Close the mapping promptly.
    mapped = np.memmap(result['results_file'], RECORD, 'r', shape=(count,))
    try:
        chosen = np.array(mapped[::step][:maximum], copy=True)
    finally:
        mapped._mmap.close()
    chosen = chosen[chosen['status'] == 0]
    if len(chosen):
        rows.append(chosen); remaining -= len(chosen)
    if not rows:
        with Path(result['results_file']).open('rb') as stream:
            while remaining:
                part = np.fromfile(stream, RECORD, count=100000)
                if not len(part):
                    break
                chosen = part[part['status'] == 0][:remaining]
                if len(chosen):
                    rows.append(chosen); remaining -= len(chosen)
    if not rows:
        raise ValueError('This result has no included samples to display.')
    q = np.concatenate(rows)
    origin = q['xyz'].min(0) / 2 + q['xyz'].max(0) / 2
    deviations = q['deviation_m'] * 1000.
    colours = np.tile([.2, .82, .48], (len(q), 1))
    colours[deviations > result['tolerance_mm']] = [1., .22, .12]
    colours[deviations < -result['tolerance_mm']] = [.16, .45, 1.]
    points = np.column_stack([q['xyz'] - origin, colours])
    return dict(
        origin=origin.tolist(), points=points.astype('<f4') if packed else points.tolist(),
        deviations_mm=deviations if packed else deviations.tolist(), source_id=result['source_id'],
        result_id=result['id'], sample_count=len(q), used=result['used'],
        evaluated=result['evaluated'], source_kind=result['source_kind'],
        signed=result['signed'], tolerance_mm=result['tolerance_mm'],
        reference_label=result['mode_label'], reference_name=result['reference_name'],
        sampling_note=result['sampling_note'],
    )


def export_csv(result, destination, progress=lambda message: None, cancel=lambda: False):
    validate_result(result)
    destination = Path(destination)
    with destination.open('w', encoding='utf-8', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(['sample_index_0based', 'source_point_or_face_index_0based',
                         'E_m', 'N_m', 'H_m', 'deviation_mm', 'area_weight_m2',
                         'status', 'comparison', 'source', 'reference',
                         'tolerance_mm', 'comparison_band_m', 'mesh_spacing_m',
                         'source_scan_id', 'source_scan_name', 'original_scan_point_index_0based'])
        with Path(result['results_file']).open('rb') as stream:
            done = 0
            while True:
                check_cancel(cancel)
                rows = np.fromfile(stream, RECORD, count=BLOCK)
                if not len(rows):
                    break
                for row in rows:
                    value = float(row['deviation_m'])
                    source_row = next((r for r in result.get('source_ranges', []) if r['start'] <= int(row['element_id']) < r['start'] + r['count']), None)
                    writer.writerow([
                        int(row['sample_id']), int(row['element_id']),
                        *(format(float(x), '.17g') for x in row['xyz']),
                        format(value * 1000., '.12g') if math.isfinite(value) else '',
                        format(float(row['weight']), '.12g') if result['source_kind'] == 'mesh' else '',
                        ('included', 'excluded_by_band', 'unmatched_or_ambiguous')[int(row['status'])],
                        result['mode_label'], result['source_name'], result['reference_name'],
                        result['tolerance_mm'], result['band_m'], result.get('spacing_m') or '',
                        (source_row.get('id') or result['source_id']) if source_row else '',
                        source_row['name'] if source_row else '',
                        int(row['element_id']) - source_row['start'] if source_row else '',
                    ])
                done += len(rows)
                progress(f'Saving inspection CSV · {done:,} / {result["record_count"]:,} rows')
    return destination
