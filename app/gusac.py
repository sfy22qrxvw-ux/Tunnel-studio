# Copyright (c) 2026 Damian Gulczynski.
"""Regional GuSAC-based tunnel cleaning, without moving or deleting source points.

Independent engineering implementation of Wang et al., Applied Sciences 15
(2025), 2926, doi:10.3390/app15062926, sections 2.1 and 4. A bounded spatial
sample learns the reference, RANSAC planes and normal neighbourhoods; EVERY
selected original point is classified. This is not an author-exact reproduction:
the article omits several parameters and has inconsistent normal equations.

Equation 4 is an ABSOLUTE distance. Equation 5 is interpreted as
abs(dot(n, np))/(norm(n)*norm(np)), not the printed product of norms. Retained
normal agreement uses >= mean-1.5*std (with an angular floor), reversing the
inconsistent inequality printed in equation 6. A measured curved reference,
overlapping patches, plane-orientation checks and uncertain-point retention are
explicit engineering safeguards. Source coordinates are never fitted/smoothed.

Labels: 0 supported lining; 1 inward obstruction candidates; 2 outward
joint/recess candidates; 3 insufficient evidence; 4 near-wall normal disagreement.
Only class 1 is excluded by default. Joints, recesses and uncertainty are retained.
"""
from pathlib import Path
import math
import os
import tempfile
import time
from contextlib import closing
import compute_runtime

import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import median_filter

from combined_denoising import sample_originals
import tunnel_lining


KEEP_CLASSES = (0, 2, 3, 4)
CLASS_NAMES = (
    'GuSAC supported lining',
    'Inward objects / services · review',
    'Joints / recesses / outward points · retained',
    'Insufficient lining evidence · retained',
    'Near-wall normal disagreement · retained',
)


def _emit(progress, cancel, message):
    tunnel_lining._emit(progress, cancel, message)


def _parameters(settings):
    values = dict(distance=float(settings.get('gusac_distance_mm', 20.))/1000.,
                  sector_deg=float(settings.get('gusac_sector_deg', 5.)),
                  window=float(settings.get('gusac_window_m', 1.)),
                  normal_deg=float(settings.get('gusac_normal_deg', 35.)),
                  min_support=int(settings.get('gusac_min_support', 20)))
    if not all(np.isfinite(v) for v in values.values()):
        raise ValueError('GuSAC settings must be finite numbers.')
    if not .001 <= values['distance'] <= .20:
        raise ValueError('GuSAC lining distance must be between 1 and 200 mm.')
    if not 1. <= values['sector_deg'] <= 15.:
        raise ValueError('GuSAC sectors must be between 1 and 15 degrees.')
    if not .10 <= values['window'] <= 5.:
        raise ValueError('GuSAC longitudinal windows must be between 0.1 and 5 m.')
    if not 1. <= values['normal_deg'] <= 85. or not 6 <= values['min_support'] <= 2000:
        raise ValueError('Check GuSAC normal agreement and minimum patch support.')
    values['nangle'] = int(math.ceil(360./values['sector_deg']))
    values['sector_rad'] = 2*np.pi/values['nangle']
    return values


def coordinates(points, model):
    """Physical coordinates for cleaning/TMatch; angle in radians, s in metres.

    s is projection on the reference axis, with measured transverse section
    centres interpolated along it. It is NOT a claimed design chainage. This
    chart supports a single gently curved branch; folds/junctions are unsupported.
    """
    seed = model['seed']
    p = np.asarray(points, dtype=np.float64)-seed['reference']
    s = p@seed['axis']; nodes = seed['nodes']
    x = p@seed['u']-np.interp(s, nodes[:, 0], nodes[:, 1])
    y = p@seed['v']-np.interp(s, nodes[:, 0], nodes[:, 2])
    radial = np.hypot(x, y)
    angle = np.mod(np.arctan2(y, x), 2*np.pi)
    deviation, supported, radial_unit = tunnel_lining.deviations(points, seed, True)
    return dict(s=s, angle=angle, radial=radial,
                expected_radius=radial-deviation, deviation=deviation,
                supported=supported, radial_unit=radial_unit)


def _point_normals(local, tree, sample_local, radius, progress=None, cancel=None, *, worker_count=1):
    """Local PCA on bounded original-coordinate model neighbours, in chunks.

    A radius bound prevents distant isolated points acquiring arbitrary normals.
    Degenerate/sparse neighbourhoods remain unknown, never automatic deletions.
    """
    n = len(local); normals = np.zeros((n, 3), np.float64)
    reliable = np.zeros(n, bool)
    k = min(16, len(sample_local))
    if k < 6:
        return normals, reliable
    for start in range(0, n, 8192):
        if cancel is not None:
            _emit(progress or (lambda _: None), cancel, 'Checking local surface normals')
        end = min(n, start+8192)
        distances, ix = tree.query(local[start:end], k=k, workers=worker_count)
        valid = distances <= radius
        count = valid.sum(1)
        neighbour = sample_local[ix]
        # Subtract each query first for numerical stability at survey coordinates.
        delta = neighbour-local[start:end, None, :]
        centre = np.sum(delta*valid[:, :, None], axis=1)/np.maximum(count, 1)[:, None]
        delta -= centre[:, None, :]
        weighted = delta*valid[:, :, None]
        covariance = np.einsum('nki,nkj->nij', weighted, weighted)/np.maximum(count, 1)[:, None, None]
        eigenvalues, vectors = np.linalg.eigh(covariance)
        good = ((count >= 6) & (eigenvalues[:, 1] > 1e-9)
                & (eigenvalues[:, 0] <= .30*eigenvalues[:, 1])
                & (eigenvalues[:, 1] >= .008*np.maximum(eigenvalues[:, 2], 1e-12)))
        normals[start:end] = vectors[:, :, 0]
        reliable[start:end] = good
    return normals, reliable


def _regional_plane(points, deviation, radial_unit, normals, normal_ok, params, key):
    """True local RANSAC plane with spatial coverage and lining-reference checks."""
    minimum = params['min_support']; tol = params['distance']
    if len(points) < minimum:
        return None
    radial = np.median(radial_unit, axis=0)
    length = np.linalg.norm(radial)
    if length < .5:
        return None
    radial /= length
    # Restrict model candidates to the measured wall reference; a dense service
    # farther inside cannot become the wall simply by winning a point count.
    band = max(.045, 2.5*tol)
    use = np.abs(deviation) <= band
    if use.sum() < minimum:
        return None
    p = points[use]; d = deviation[use]; ns = normals[use]; good_n = normal_ok[use]
    origin = np.median(p, axis=0); q = p-origin
    u, v = tunnel_lining.basis(radial)
    xy = np.c_[q@u, q@v]
    low = xy.min(0); span = np.maximum(np.ptp(xy, axis=0), .001)
    cells = np.minimum(3, np.maximum(0, ((xy-low)/span*4).astype(int)))
    cell = cells[:, 0]*4+cells[:, 1]
    # Each spatial cell contributes at most once: scan density is not wall truth.
    support_weight = np.exp(-np.abs(d)/max(.008, tol*.6))
    rng = np.random.default_rng(137+int(key)*1009)
    trial_tol = max(.004, min(.015, tol))
    best = None; best_score = -1.
    candidates = min(72, max(32, len(q)//2))
    for _ in range(candidates):
        a, b, c = q[rng.choice(len(q), 3, replace=False)]
        normal = np.cross(b-a, c-a); size = np.linalg.norm(normal)
        if size < 1e-8:
            continue
        normal /= size
        if normal@radial < 0:
            normal = -normal
        if normal@radial < math.cos(math.radians(55)):
            continue
        offset = -normal@a
        inliers = np.abs(q@normal+offset) <= trial_tol
        if inliers.sum() < minimum:
            continue
        occupied = np.zeros(16)
        np.maximum.at(occupied, cell[inliers], support_weight[inliers])
        score = float(occupied.sum())+.01*min(float(inliers.sum())/minimum, 5.)
        if score > best_score:
            best_score = score; best = (normal, offset, inliers)
    if best is None:
        return None
    normal, offset, inliers = best
    for _ in range(2):
        selected = q[inliers]; weights = support_weight[inliers]
        centre = np.average(selected, axis=0, weights=weights)
        centred = selected-centre
        covariance = (centred*weights[:, None]).T@centred/max(weights.sum(), 1e-12)
        eigenvalues, vectors = np.linalg.eigh(covariance)
        if eigenvalues[1] < 1e-7:
            return None
        normal = vectors[:, 0]
        if normal@radial < 0:
            normal = -normal
        offset = -normal@centre
        inliers = np.abs(q@normal+offset) <= trial_tol
        if inliers.sum() < minimum:
            return None
    if normal@radial < math.cos(math.radians(55)):
        return None
    # A predominantly inward candidate is not a trusted lining patch.
    if abs(float(np.median(d[inliers]))) > max(.025, 1.5*tol):
        return None
    agreement = np.abs(ns[inliers & good_n]@normal)
    floor = math.cos(math.radians(params['normal_deg']))
    threshold = floor
    if len(agreement) >= 6:
        # Small numerical allowance avoids classifying perfect surfaces as a
        # disagreement solely through floating-point/curvature differences.
        threshold = max(floor, min(.999, float(agreement.mean()-1.5*agreement.std()-.01)))
    residual = np.abs(q[inliers]@normal+offset)
    patch_tol = max(tol, min(2*tol, 3*float(np.median(residual))))
    return dict(normal=normal, anchor=origin-offset*normal,
                tolerance=patch_tol, normal_threshold=threshold,
                support=int(inliers.sum()), rms=float(np.sqrt(np.mean(residual**2))))


def fit_batches(batches, settings, progress=lambda _: None, cancel=lambda: False):
    """Fit a bounded model. batches MUST be a callable yielding fresh streams."""
    params = _parameters(settings)
    # 400k actual points, never voxel-centre coordinates. Halo fitting shares
    # support between patches; insufficient sampling remains explicitly unknown.
    sample, total = sample_originals(batches, .015, 400000, progress, cancel)
    _emit(progress, cancel, 'GuSAC · fitting a measured tunnel reference')
    seed = tunnel_lining.fit_model(sample, {**settings, 'envelope_thickness': max(.01, params['distance']),
                                          'envelope_angle_deg': min(3., params['sector_deg']),
                                          'resolution': min(.2, params['window']/5)}, progress, cancel)
    # A service can cover most measured returns in a narrow angular band. The
    # existing envelope's dominant radial mode can then follow the service.
    # Reject only DEEP, NARROW inward reference dents using a circumferential
    # median. Broad floor profiles remain; no source coordinate is altered.
    # Subsequent RANSAC must still find measured wall support, otherwise that
    # region stays uncertain. This never invents a lining plane behind a pipe.
    kernel = max(3, int(round(45./(360./seed['nangle']))) | 1)
    surrounding = median_filter(seed['profile'], size=kernel, mode='wrap')
    correction = surrounding-seed['profile']
    repaired = correction > max(.12, 5*params['distance'])
    seed['target'] = seed['target'].copy()
    seed['target'][:, repaired] = surrounding[repaired]
    seed['profile'] = seed['profile'].copy()
    seed['profile'][repaired] = surrounding[repaired]
    model = dict(seed=seed, sample=sample, sample_points=len(sample), input_points=total,
                 radius=seed['radius'], settings=params, inward_reference_bins_corrected=int(repaired.sum()))
    chart = coordinates(sample, model)
    s = chart['s']; angle = chart['angle']
    window = params['window']; na = params['nangle']; step = params['sector_rad']
    # Anchor station grid to the learned reference, not input chunk boundaries.
    s0 = math.floor(float(s.min())/window)*window
    ns = int(math.floor((float(s.max())-s0)/window))+1
    model.update(s0=s0, nstation=ns)
    local = sample-seed['reference']; tree = cKDTree(local)
    normal_radius = max(.06, min(.40, window*.45, seed['radius']*step*1.2))
    # Fitting owns the job's worker budget. Classification is already
    # parallel across input blocks and uses one KD-tree worker per block.
    workers = compute_runtime.workers(maximum=4) if len(local) >= 8192 else 1
    normals, normal_ok = _point_normals(local, tree, local, normal_radius, progress, cancel,
                                       worker_count=workers)
    model.update(tree=tree, sample_local=local, normal_radius=normal_radius)
    station = np.clip(np.floor((s-s0)/window).astype(np.int64), 0, ns-1)
    angular = np.minimum(na-1, np.floor(angle/step).astype(np.int64))
    keys = station*na+angular
    order = np.argsort(keys, kind='stable'); ordered = keys[order]
    unique, starts = np.unique(ordered, return_index=True)
    ends = np.r_[starts[1:], len(order)]
    groups = {int(k): order[lo:hi] for k, lo, hi in zip(unique, starts, ends)}
    patches = {}
    first = dict(points_per_call=[], seconds=0.)
    extended = dict(points_per_call=[], seconds=0.)

    def fit_patch(indices, key, workload):
        arguments = (local[indices], chart['deviation'][indices], chart['radial_unit'][indices],
                     normals[indices], normal_ok[indices], params, int(key))
        started = time.perf_counter()
        patch = _regional_plane(*arguments)
        workload['seconds'] += time.perf_counter() - started
        # Count the points entering the fitter, before its own support tests.
        workload['points_per_call'].append(len(indices))
        return patch

    for number, key in enumerate(unique):
        if number % 25 == 0:
            _emit(progress, cancel, f'GuSAC · regional RANSAC planes {number+1:,}/{len(unique):,}')
        si = int(key)//na; ai = int(key)%na
        neighbours = [groups.get(t*na+(a % na), np.empty(0, np.int64))
                      for t in (si-1, si, si+1) if 0 <= t < ns
                      for a in (ai-1, ai, ai+1)]
        ix = np.concatenate(neighbours)
        centre_s = s0+(si+.5)*window; centre_a = (ai+.5)*step
        delta_a = np.abs(np.arctan2(np.sin(angle[ix]-centre_a), np.cos(angle[ix]-centre_a)))
        ix = ix[(abs(s[ix]-centre_s) <= window*.85) & (delta_a <= step*.85)]
        patch = fit_patch(ix, key, first)
        if patch is None:
            # Sparse wall glimpses behind a service may not supply twenty
            # points in one metre. Extend only the longitudinal fitting halo,
            # retaining the same angular sector and all reference/plane tests.
            # Final point ownership still uses the original one-metre region.
            neighbours = [groups.get(t*na+(a % na), np.empty(0, np.int64))
                          for t in range(max(0, si-4), min(ns, si+5))
                          for a in (ai-1, ai, ai+1)]
            extra = np.concatenate(neighbours)
            delta_a = np.abs(np.arctan2(np.sin(angle[extra]-centre_a), np.cos(angle[extra]-centre_a)))
            extra = extra[(abs(s[extra]-centre_s) <= window*4.0) & (delta_a <= step*.85)]
            patch = fit_patch(extra, key, extended)
            if patch is not None:
                patch['extended_support'] = True
        if patch is not None:
            patches[int(key)] = patch
    if not patches:
        raise ValueError('GuSAC could not verify any lining patches. Include more wall and crown, increase the window or reduce minimum support. No source points were changed.')
    model['patches'] = patches
    model['attempted_patches'] = len(unique)
    model['regional_plane_seconds'] = first['seconds'] + extended['seconds']
    model['regional_plane_calls'] = len(first['points_per_call']) + len(extended['points_per_call'])
    model['regional_plane_workload'] = dict(first=first, extended=extended)
    return model


def classify(points, model, return_details=False, progress=None, cancel=None):
    """Classify every supplied original coordinate against regional planes.

    The regional plane and measured radial reference must BOTH support inward
    rejection. Insufficient support, recesses and near-wall disagreement remain.
    """
    p = np.asarray(points, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError('GuSAC requires finite XYZ coordinates.')
    chart = coordinates(p, model); params = model['settings']; na = params['nangle']
    si = np.floor((chart['s']-model['s0'])/params['window']).astype(np.int64)
    ai = np.minimum(na-1, np.floor(chart['angle']/params['sector_rad']).astype(np.int64))
    keys = si*na+ai
    labels = np.full(len(p), 3, np.int32)
    signed = np.full(len(p), np.nan); normal_similarity = np.full(len(p), np.nan)
    plane_known = np.zeros(len(p), bool); near_count = 0; near_regions = []
    local = p-model['seed']['reference']
    order = np.argsort(keys, kind='stable'); ordered = keys[order]
    unique, starts = np.unique(ordered, return_index=True); ends = np.r_[starts[1:], len(order)]
    for number, (key, lo, hi) in enumerate(zip(unique, starts, ends)):
        if cancel is not None and number % 30 == 0:
            _emit(progress or (lambda _: None), cancel, 'GuSAC · comparing original points with local lining planes')
        patch = model['patches'].get(int(key))
        if patch is None:
            continue
        ix = order[lo:hi]
        known = chart['supported'][ix] & (si[ix] >= 0) & (si[ix] < model['nstation'])
        if not known.any():
            continue
        ix = ix[known]; plane_known[ix] = True
        distance = (local[ix]-patch['anchor'])@patch['normal']
        signed[ix] = distance; deviation = chart['deviation'][ix]
        tol = patch['tolerance']
        inward = (distance < -tol) & (deviation < -params['distance'])
        outside = (distance > tol) | (deviation > max(.04, 2*params['distance']))
        labels[ix[inward]] = 1
        labels[ix[outside & ~inward]] = 2
        near = (np.abs(distance) <= tol) & (deviation >= -params['distance'])
        near_ix = ix[near]; near_count += len(near_ix)
        if len(near_ix):
            near_regions.append((near_ix, patch))
    # Query all near-wall points in bounded native batches. Starting a worker
    # team for every tiny regional patch was needlessly expensive. Each point
    # still uses its own regional plane and exactly the same PCA neighbours.
    if near_regions:
        near_ix = np.concatenate([ix for ix, _ in near_regions])
        normals, reliable = _point_normals(local[near_ix], model['tree'], model['sample_local'],
                                          model['normal_radius'], progress, cancel)
        start = 0
        for ix, patch in near_regions:
            end = start + len(ix)
            similarity = np.abs(normals[start:end]@patch['normal'])
            normal_similarity[ix] = similarity; good = reliable[start:end]
            labels[ix[good & (similarity >= patch['normal_threshold'])]] = 0
            labels[ix[good & (similarity < patch['normal_threshold'])]] = 4
            start = end
    if return_details:
        return labels, dict(**chart, plane_supported=plane_known, signed_plane_distance=signed,
                            normal_similarity=normal_similarity, normal_queries=near_count)
    return labels


def run_batches(batches, work, ids_path, labels_path, settings, progress, cancel, selected_total):
    started = time.monotonic()
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    model = fit_batches(batches, settings, progress, cancel)
    fit_seconds = time.monotonic()-started
    if model['input_points'] != selected_total:
        raise ValueError('The selected cloud changed while learning the GuSAC model. Run again.')
    counts = np.zeros(5, np.int64); sums = np.zeros((5, 3), np.float64)
    representatives = {k: [] for k in range(5)}; written = 0
    normal_queries = 0; plane_supported = 0
    def classify_chunk(chunk):
        ids,points,_=chunk
        ids=np.asarray(ids)
        if ids.ndim != 1 or len(ids) != len(points) or not np.issubdtype(ids.dtype, np.integer) or (ids < 0).any():
            raise ValueError('GuSAC received invalid original point identifiers.')
        # The learned model and KD tree are read-only. Native normal queries
        # use one worker within each outer chunk, avoiding nested thread teams.
        labels,details=classify(points,model,True)
        return ids,points,labels,details
    # Output is staged; cancellation/error cannot leave a partial new result at
    # the requested paths or remove an existing result. No mapped files remain.
    with tempfile.TemporaryDirectory(prefix='gusac_', dir=work) as folder:
        tmpids = Path(folder)/'points.ids'; tmplabels = Path(folder)/'points.labels'
        with tmpids.open('wb') as idout, tmplabels.open('wb') as labout:
          with closing(compute_runtime.process_chunks(classify_chunk,batches(),progress=progress,
                       label='GuSAC · classifying original points',cancel=cancel)) as results:
            for ids,points,labels,details in results:
                np.asarray(ids, dtype='<i8').tofile(idout)
                labels.astype('<i4', copy=False).tofile(labout)
                normal_queries += int(details['normal_queries'])
                plane_supported += int(details['plane_supported'].sum())
                for k in range(5):
                    choose = labels == k; counts[k] += int(choose.sum())
                    sums[k] += np.asarray(points)[choose].sum(0)
                    missing = 32-len(representatives[k])
                    if missing > 0:
                        representatives[k].extend((written+np.flatnonzero(choose)[:missing]).tolist())
                written += len(ids)
        if written != selected_total:
            raise ValueError('The selected cloud changed during GuSAC classification. Run again.')
        _emit(progress, cancel, 'GuSAC · completing review labels')
        Path(ids_path).parent.mkdir(parents=True, exist_ok=True)
        Path(labels_path).parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmpids, ids_path); os.replace(tmplabels, labels_path)
    rows = [dict(id=k, cleaning_class=k, name=CLASS_NAMES[k], points=int(counts[k]),
                 preview_indices=representatives[k], centre=(sums[k]/counts[k]).tolist(),
                 supervoxels=0, linearity=0., planar_fraction=0., overlap=0.,
                 automatic_keep=k in KEEP_CLASSES) for k in range(5) if counts[k]]
    kept = [k for k in KEEP_CLASSES if counts[k]]
    stats = dict(input_points=written, unique_points=None, duplicate_locations=None,
                 supervoxels=0, expected_supervoxels=0, merge_passes=0, initial_edges=0,
                 removed_edges=0, retained_edges=0, clusters=len(rows),
                 kept_points=int(sum(counts[k] for k in KEEP_CLASSES)), noise_points=0,
                 noise_candidates=int(counts[1]), inward_obstruction_candidates=int(counts[1]),
                 joint_recess_retained_points=int(counts[2]), uncertain_retained_points=int(counts[3]),
                 normal_disagreement_retained_points=int(counts[4]),
                 calibration_points=model['sample_points'], model_sample_cap=400000,
                 model_sample_spacing_m=.015, plane_supported_points=plane_supported,
                 regional_planes=len(model['patches']), attempted_regions=model['attempted_patches'],
                 fit_seconds=round(fit_seconds, 3),
                 regional_plane_seconds=round(model['regional_plane_seconds'], 3),
                 regional_plane_calls=model['regional_plane_calls'],
                 sparse_support_extended_regions=sum(bool(p.get('extended_support')) for p in model['patches'].values()),
                 normal_checked_points=normal_queries, normal_neighbourhood_radius_m=model['normal_radius'],
                 fitted_sections=len(model['seed']['nodes']), reference_radius_m=model['radius'],
                 inward_reference_bins_corrected=model['inward_reference_bins_corrected'],
                 gusac_distance_mm=model['settings']['distance']*1000,
                 gusac_sector_deg=360/model['settings']['nangle'], gusac_window_m=model['settings']['window'],
                 strategy='GuSAC-based regional RANSAC planes and bounded-neighbourhood normal agreement; every selected original point classified',
                 reference='Wang et al. (2025), Applied Sciences 15, 2926, doi:10.3390/app15062926',
                 seconds=round(time.monotonic()-started, 3))
    workload = {path: dict(points_per_call=details['points_per_call'], seconds=round(details['seconds'], 3))
                for path, details in model['regional_plane_workload'].items()}
    return dict(kept=kept, clusters=rows, statistics=stats, regional_plane_workload=workload, warnings=[
        'This GuSAC-based implementation corrects the paper\'s inconsistent normal equations and adds measured-reference and uncertainty safeguards. It is not an author-exact reproduction or a reproduction of the paper\'s reported accuracy.',
        'Every selected original point receives a label. Plane fitting and surface-normal neighbourhoods use a bounded, spatially balanced model sample; sparse or ambiguous lining is retained. Coordinates, RGB and intensity are unchanged.',
        'Inspect Inward objects / services before applying. Joints, recesses, uncertain regions and near-wall normal disagreements are retained separately. Geometry alone cannot reliably distinguish a service touching the wall from real lining detail.',
        'The model follows one approximately circular, gently curved branch with supported floor profiles. Junctions, severe bends, large gaps and irregular excavations need review. More sampling/support or a different method may be needed.'])
