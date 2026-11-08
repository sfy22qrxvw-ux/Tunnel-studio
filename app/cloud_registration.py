"""Register overlapping point clouds with bounded, rigid point-to-plane ICP.

Both scans are streamed into bounded priority reservoirs. Reference normals
are estimated once by local PCA; cKDTree matching, actual random-sample
consensus and robust point-to-plane least squares refine an existing alignment.
Coordinates are metres, rotations are proper, and scale is fixed at one.
Weak geometry, insufficient overlap, cancellation and unconverged results
never change metadata. Successful registration stores a virtual pose only.

This is not a survey measurement; it is a mathematical alignment of point
clouds. Residuals describe sampled inlier tangent-plane distances, not a
probability, independent accuracy assessment or guarantee. ICP is local: the
scans need a reasonable initial alignment, normally from manual alignment,
targets or verified odometry. Repeated tunnel geometry can give a wrong local
minimum even with small residuals and a full-rank numerical system.

Sources: Besl and McKay (1992), doi:10.1109/34.121791; Chen and Medioni
(1991), doi:10.1109/ROBOT.1991.132043; Fischler and Bolles (1981),
doi:10.1145/358669.358692. RANSAC here estimates local linearised rigid
increments; it is not a global feature-based registration algorithm.

The caller owns the edit lock, linked-scan/group semantics, Undo, cache
invalidation and project persistence. No cache is rewritten, and work is
accepted for API compatibility only. The current pointio API is chunks(meta),
not chunks(meta, destination); it already honours point exclusion masks.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import time

import numpy as np
from scipy.spatial import cKDTree
from contextlib import closing
import compute_runtime
from scipy.spatial.transform import Rotation

METHOD = "Point-to-plane ICP with RANSAC outlier rejection"
MIN_OVERLAP = 500
MAX_SAMPLE = 500_000
BLOCK = 100_000
QUERY_BLOCK = 32768
QUALITY = {"fast": (25000, 50000), "balanced": (75000, 150000),
           "high": (150000, 300000)}
NOTICE = ("Residuals are sampled inlier point-to-plane distances, not survey "
          "accuracy, a probability or an independent validation.")


def capabilities() -> dict:
    """Return what this module can do, for the UI to display."""
    return {
        "name": "Cloud-to-cloud registration",
        "requires_targets": False,
        "requires_reference_cloud": True,
        "method": METHOD,
        "limits": {"min_overlap_points": 500, "max_iterations": 50,
                   "default_tolerance_mm": 5.0},
    }


class _Cancelled(Exception):
    pass


def _check(cancel):
    if cancel():
        raise _Cancelled()


def _points(value, label, minimum=0):
    try:
        if len(value) > MAX_SAMPLE:
            raise ValueError(f"{label} exceeds {MAX_SAMPLE:,} points; supply a bounded sample.")
        array = np.asarray(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be an (N, 3) numeric array.") from exc
    if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind not in "fiu":
        raise ValueError(f"{label} must be an (N, 3) real numeric array.")
    array = np.asarray(array, dtype=np.float64)
    if len(array) < minimum or not np.isfinite(array).all():
        raise ValueError(f"{label} needs at least {minimum} points and only finite coordinates.")
    return array


def _options(max_iterations, tolerance_mm, seed):
    if (isinstance(max_iterations, (bool, np.bool_))
            or not isinstance(max_iterations, (int, np.integer))
            or not 1 <= max_iterations <= 50):
        raise ValueError("max_iterations must be an integer from 1 to 50.")
    if isinstance(tolerance_mm, (bool, np.bool_)):
        raise ValueError("tolerance_mm must be a positive finite number.")
    try:
        tolerance = float(tolerance_mm) / 1000.0
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("tolerance_mm must be a positive finite number.") from exc
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance_mm must be a positive finite number.")
    if seed is not None and (isinstance(seed, (bool, np.bool_))
                            or not isinstance(seed, (int, np.integer)) or seed < 0):
        raise ValueError("seed must be a nonnegative integer or None, not an initial pose.")
    return int(max_iterations), tolerance, np.random.default_rng(0 if seed is None else seed)


def _centre(points):
    return points[0] + np.mean(points - points[0], axis=0)


def _query(tree, points, cancel, k=1, limit=np.inf):
    shape = (len(points),) if k == 1 else (len(points), k)
    distances, indices = np.empty(shape), np.empty(shape, dtype=np.intp)
    for start in range(0, len(points), QUERY_BLOCK):
        _check(cancel)
        stop = start + QUERY_BLOCK
        distances[start:stop], indices[start:stop] = tree.query(
            points[start:stop], k=k, distance_upper_bound=limit, workers=__import__('compute_runtime').workers())
    return distances, indices


def _normals(reference, k, progress, cancel):
    tree = cKDTree(reference, copy_data=True)
    normals = np.zeros_like(reference)
    centroid = _centre(reference)
    def normal_chunk(start):
        stop = min(start + 4096, len(reference))
        _, neighbours = tree.query(reference[start:stop], k=k, workers=1)
        local = reference[neighbours]
        local = local - local[:, :1]
        local -= local.mean(axis=1, keepdims=True)
        covariance = np.einsum("nki,nkj->nij", local, local) / k
        values, vectors = np.linalg.eigh(covariance)
        valid = ((values[:, 2] > 1e-18)
                 & (values[:, 1] > values[:, 2] * 1e-3)
                 & (values[:, 0] < values[:, 1] * 0.25))
        block = vectors[:, :, 0]
        inward = np.einsum("ij,ij->i", block, centroid - reference[start:stop])
        block[inward < 0] *= -1
        block[~valid] = 0
        return start,stop,block
    try:
        with closing(compute_runtime.process_chunks(normal_chunk,range(0,len(reference),4096),
                     progress=progress,label='ICP · reference normals',cancel=cancel)) as results:
            for start,stop,block in results:
                normals[start:stop] = block
    except compute_runtime.ProcessingCancelled:
        raise _Cancelled()
    return normals, tree


def estimate_normals(reference_xyz, k=12):
    """Return (float64 normals, cKDTree) for a bounded reference sample.

    Zero normals mark duplicate, collinear or insufficiently planar local
    neighbourhoods. Other normals face the reference centroid's side;
    correspondences() reorients them toward the query centroid. Signs do not
    change the squared point-to-plane objective. This is not a globally
    oriented surface model.
    """
    reference = _points(reference_xyz, "Reference cloud", 3)
    if isinstance(k, (bool, np.bool_)) or not isinstance(k, (int, np.integer)) or not 3 <= k <= 64:
        raise ValueError("k must be an integer from 3 to 64.")
    return _normals(reference, min(int(k), len(reference)), lambda m: None, lambda: False)


def _unit_normals(normals, count):
    normals = _points(normals, "Reference normals")
    if len(normals) != count:
        raise ValueError("Reference normals must match the reference point count.")
    lengths = np.linalg.norm(normals, axis=1)
    if not np.isfinite(lengths).all():
        raise ValueError("Reference normal magnitudes are not finite.")
    return np.divide(normals, lengths[:, None], out=np.zeros_like(normals),
                     where=lengths[:, None] > 1e-12)


def correspondences(query_xyz, reference_tree, reference_xyz, reference_normals):
    """Return (reference indices, query points, reference points, normals).

    This is nearest-neighbour matching only; solve_icp additionally applies
    distance gates, distinct-reference selection and RANSAC rejection.
    """
    query = _points(query_xyz, "Moving cloud")
    reference = _points(reference_xyz, "Reference cloud", 1)
    normals = _unit_normals(reference_normals, len(reference))
    if (not isinstance(reference_tree, cKDTree) or reference_tree.m != 3
            or reference_tree.n != len(reference)
            or not np.array_equal(reference_tree.data, reference)):
        raise ValueError("The reference tree must index reference_xyz in its original order.")
    distances, indices = _query(reference_tree, query, lambda: False)
    good = np.isfinite(distances) & (indices < len(reference))
    rows = np.flatnonzero(good)
    rows = rows[np.linalg.norm(normals[indices[rows]], axis=1) > 0]
    ids = indices[rows]
    matched = normals[ids].copy()
    if len(rows):
        side = np.einsum("ij,ij->i", matched, _centre(query) - reference[ids])
        matched[side < 0] *= -1
    return ids, query[rows], reference[ids], matched


def _matches(points, reference, normals, tree, capture, gate, cancel):
    distances, nearest = _query(tree, points, cancel, limit=capture)
    rows = np.flatnonzero(np.isfinite(distances) & (nearest < len(reference)))
    rows = rows[np.linalg.norm(normals[nearest[rows]], axis=1) > 0]
    residual = np.einsum("ij,ij->i", points[rows] - reference[nearest[rows]],
                         normals[nearest[rows]])
    # Fixed-domain, capped score: losing matches cannot artificially improve
    # the retained-inlier RMS used to select a best-effort iterate.
    score = ((len(points) - len(rows)) * capture**2
             + np.minimum(residual**2, capture**2).sum()) / max(1, len(points))
    rows = rows[distances[rows] <= gate]
    order = np.lexsort((distances[rows], nearest[rows]))
    rows = rows[order]
    if len(rows):
        keep = np.r_[True, np.diff(nearest[rows]) != 0]
        rows = rows[keep]
    ids = nearest[rows]
    return rows, ids, distances[rows], float(score)


def _jacobian(points, reference, normals):
    centre = _centre(points)
    shifted = points - centre
    lever = max(float(np.sqrt(np.mean(np.einsum("ij,ij->i", shifted, shifted)))), 1e-6)
    # One common length scale preserves weak rotational directions. Scaling
    # each column independently would hide near-unobservable tunnel motions.
    matrix = np.column_stack((np.cross(shifted / lever, normals), normals))
    rhs = -np.einsum("ij,ij->i", points - reference, normals)
    return matrix, rhs, centre, lever


def _weighted_solve(matrix, rhs, weights):
    normal = matrix.T @ (matrix * weights[:, None])
    values, vectors = np.linalg.eigh(normal)
    threshold = max(float(values[-1]) * 1e-5, 1e-12)
    observed = values > threshold
    rank = int(observed.sum())
    step = np.zeros(6)
    if rank:
        basis = vectors[:, observed]
        step = basis @ ((basis.T @ (matrix.T @ (weights * rhs))) / values[observed])
    condition = float(values[-1] / values[0]) if rank == 6 else None
    return step, rank, condition


def _robust_step(points, reference, normals, threshold, gate, rng, cancel):
    matrix, rhs, centre, lever = _jacobian(points, reference, normals)
    pilot = rng.choice(len(points), min(2048, len(points)), replace=False)
    a, b = matrix[pilot], rhs[pilot]
    models = [np.zeros(6)]
    # Real random-sample consensus on linearised point-to-plane constraints.
    # No claimed success probability: candidate geometry is not independent.
    for trial in range(64):
        if trial % 16 == 0:
            _check(cancel)
        pick = rng.choice(len(pilot), 8, replace=False)
        try:
            model, _, rank, singular = np.linalg.lstsq(a[pick], b[pick], rcond=1e-3)
        except np.linalg.LinAlgError:
            continue
        if (rank == 6 and singular[-1] > singular[0] * 1e-3
                and np.linalg.norm(model[:3]) / lever <= 0.2
                and np.linalg.norm(model[3:]) <= 2 * gate):
            models.append(model)
    errors = np.abs(a @ np.asarray(models).T - b[:, None])
    support = (errors <= threshold).sum(axis=0)
    ties = np.flatnonzero(support == support.max())
    winner = int(ties[np.argmin(np.median(errors[:, ties], axis=0))])
    model = models[winner]
    inliers = np.abs(matrix @ model - rhs) <= threshold
    if np.count_nonzero(inliers) < MIN_OVERLAP:
        return None, inliers, centre, lever, 0, None, len(models) > 1
    fit_a, fit_b = matrix[inliers], rhs[inliers]
    rank, condition = 0, None
    for _ in range(3):
        _check(cancel)
        error = np.abs(fit_a @ model - fit_b)
        weights = np.minimum(1.0, threshold * 0.5 / np.maximum(error, 1e-15))
        model, rank, condition = _weighted_solve(fit_a, fit_b, weights)
    return model, inliers, centre, lever, rank, condition, len(models) > 1


def _increment(model, centre, lever, gate):
    angle, distance = np.linalg.norm(model[:3]) / lever, np.linalg.norm(model[3:])
    fraction = min(1.0, 0.1 / max(angle, 1e-15), gate / max(distance, 1e-15))
    model = model * fraction
    rotation = Rotation.from_rotvec(model[:3] / lever).as_matrix()
    translation = centre - rotation @ centre + model[3:]
    movement = float(np.linalg.norm(model[3:]) + np.linalg.norm(model[:3]))
    return rotation, translation, movement


def _statistics(residual):
    if not len(residual):
        return {"rms_mm": None, "median_mm": None, "max_mm": None}
    mm = np.abs(residual) * 1000.0
    return {"rms_mm": float(np.sqrt(np.mean(mm**2))),
            "median_mm": float(np.median(mm)), "max_mm": float(mm.max())}


def _base_result(reason=None):
    result = {"applied": False, "rotation": np.eye(3).tolist(),
              "translation": [0., 0., 0.], "matrix": np.eye(4).tolist(),
              "source_centroid": [0., 0., 0.], "control_centroid": [0., 0., 0.],
              "scale": 1.0, "mode": "rigid", "units": "metres",
              "constraint": "Six rigid degrees of freedom; scale fixed at 1.",
              "iterations": 0, "converged": False, "inlier_count": 0,
              "residuals": [], "warnings": [NOTICE], "method": METHOD,
              **_statistics(np.empty(0))}
    result["fit"] = {"count": 0, **_statistics(np.empty(0))}
    result["check"] = {"count": 0, **_statistics(np.empty(0))}
    if reason:
        result["reason"] = reason
    return result


def _validate_icp_pose(query, reference, normals, tree, rotation, translation,
                       capture, gate, tolerance, rng, cancel):
    """Re-associate and validate one pose without applying another increment.

    The raw least-squares increment must be small. A clipped line-search step
    alone is not evidence of stationarity, and a low residual alone is not
    evidence that all six rigid motions are determined.
    """
    _check(cancel)
    points = query @ rotation.T + translation
    rows, ids, distances, score = _matches(
        points, reference, normals, tree, capture, gate, cancel)
    all_residual = np.einsum("ij,ij->i", points[rows] - reference[ids], normals[ids])
    keep = np.zeros(len(rows), dtype=bool)
    rank, robust_rank, condition, hypotheses, step_size = 0, 0, None, False, None
    if len(rows) >= MIN_OVERLAP:
        step, consensus, _, _, robust_rank, _, hypotheses = _robust_step(
            points[rows], reference[ids], normals[ids], 2.5 * tolerance, gate, rng, cancel)
        keep = consensus & (np.abs(all_residual) <= 3 * tolerance)
        if step is not None and np.isfinite(step).all():
            step_size = float(np.linalg.norm(step[:3]) + np.linalg.norm(step[3:]))
        if np.count_nonzero(keep) >= MIN_OVERLAP:
            matrix, rhs, _, _ = _jacobian(
                points[rows[keep]], reference[ids[keep]], normals[ids[keep]])
            _, rank, condition = _weighted_solve(matrix, rhs, np.ones(len(rhs)))
    residual = all_residual[keep]
    stats = _statistics(residual)
    if len(residual) < MIN_OVERLAP:
        reason = "insufficient overlap"
    elif rank < 6 or robust_rank < 6 or not hypotheses or step_size is None:
        reason = "degenerate or weakly constrained geometry"
    elif not np.isfinite(stats["rms_mm"]) or stats["rms_mm"] > tolerance * 1000:
        reason = "inlier residual exceeds tolerance"
    elif step_size > tolerance * 0.05:
        reason = "terminal point-to-plane refinement is not stationary"
    else:
        reason = None
    return dict(rows=rows, ids=ids, distances=distances, score=score,
                all_residual=all_residual, keep=keep, residual=residual,
                rank=rank, robust_rank=robust_rank, condition=condition,
                hypotheses=hypotheses, step_size=step_size, statistics=stats,
                valid=reason is None, reason=reason)


def _run_icp(query, reference, normals, tree, iterations, tolerance, rng, progress, cancel):
    result = _base_result()
    rotation, translation = np.eye(3), np.zeros(3)
    if min(len(query), len(reference), np.count_nonzero(np.linalg.norm(normals, axis=1))) < MIN_OVERLAP:
        result.update(reason="insufficient overlap")
        result["warnings"].append("Fewer than 500 sampled points with usable surface normals.")
        return result
    probes = reference[np.linspace(0, len(reference) - 1, min(4096, len(reference)), dtype=int)]
    spacing, _ = _query(tree, probes, cancel, k=2)
    nonzero = spacing[:, 1][spacing[:, 1] > 0]
    spacing = float(np.median(nonzero)) if len(nonzero) else tolerance
    capture = min(2.0, max(0.15, 8 * spacing, 40 * tolerance))
    final_gate = min(capture, max(3 * spacing, 5 * tolerance))
    best, stable, converged = None, 0, False
    reason = "maximum iterations reached"
    for iteration in range(1, iterations + 1):
        _check(cancel)
        gate = max(final_gate, capture * (0.5 ** (iteration - 1)))
        points = query @ rotation.T + translation
        rows, ids, _, score = _matches(points, reference, normals, tree, capture, gate, cancel)
        if best is None or score < best[0]:
            best = (score, rotation.copy(), translation.copy())
        result["iterations"] = iteration
        if len(rows) < MIN_OVERLAP:
            reason = "insufficient overlap"
            break
        threshold = max(2.5 * tolerance, 0.08 * gate)
        step, inliers, centre, lever, rank, condition, hypotheses = _robust_step(
            points[rows], reference[ids], normals[ids], threshold, gate, rng, cancel)
        if step is None:
            reason = "insufficient overlap"
            if not hypotheses:
                result["warnings"].append("No full-rank RANSAC hypothesis was found.")
            break
        fit_points, fit_ref, fit_normals = points[rows[inliers]], reference[ids[inliers]], normals[ids[inliers]]
        before = np.einsum("ij,ij->i", fit_points - fit_ref, fit_normals)
        old_cost = float(np.mean(before**2))
        accepted, movement, new_cost = False, np.inf, old_cost
        for fraction in (1., 0.5, 0.25, 0.125, 0.0625):
            delta_r, delta_t, movement = _increment(step * fraction, centre, lever, gate)
            moved = fit_points @ delta_r.T + delta_t
            after = np.einsum("ij,ij->i", moved - fit_ref, fit_normals)
            new_cost = float(np.mean(after**2))
            if np.isfinite(new_cost) and new_cost <= old_cost + 1e-18:
                rotation, translation = delta_r @ rotation, delta_r @ translation + delta_t
                accepted = True
                break
        progress(f"ICP {iteration}/{iterations}: {int(inliers.sum()):,} inliers; "
                 f"plane RMS {math.sqrt(max(new_cost, 0.0)) * 1000:.3f} mm")
        if rank < 6 or not hypotheses:
            reason = "degenerate or weakly constrained geometry"
            result["warnings"].append("The scaled normal matrix or RANSAC samples do not constrain all six rigid motions.")
            break
        if not accepted:
            reason = "point-to-plane refinement stalled"
            break
        improvement = abs(math.sqrt(old_cost) - math.sqrt(new_cost))
        stable = stable + 1 if (gate <= final_gate and movement <= tolerance * 0.05
                                and improvement <= tolerance * 0.02) else 0
        if iteration >= 3 and stable >= 2:
            converged, reason = True, "converged"
            break
    # Terminal-first validation: fixed-domain score and robust inlier
    # stationarity answer different questions. Do not replace a genuinely
    # converged terminal pose solely because an earlier broad score was lower.
    iteration_stop_reason = reason
    terminal = _validate_icp_pose(query, reference, normals, tree, rotation,
                                 translation, capture, final_gate, tolerance, rng, cancel)
    candidate = terminal
    earlier_better = best is not None and terminal['score'] > best[0] + tolerance**2 * 0.01
    retained_earlier = False
    if converged and terminal['valid']:
        if earlier_better:
            result['warnings'].append(
                'The converged terminal pose passed fresh overlap, rank, residual and '
                'stationarity checks. An earlier pose had a lower capped all-query '
                'score; review the alignment and independent check targets.')
        reason = 'converged'
    else:
        converged = False
        reason = terminal['reason'] if iteration_stop_reason == 'converged' else iteration_stop_reason
        if earlier_better:
            rotation, translation = best[1].copy(), best[2].copy()
            candidate = _validate_icp_pose(query, reference, normals, tree, rotation,
                                          translation, capture, final_gate, tolerance, rng, cancel)
            retained_earlier = True
        if candidate['reason'] and candidate['reason'] not in reason:
            reason += '; ' + candidate['reason']
        if retained_earlier:
            reason += '; best-effort earlier iterate retained'
    rows, ids, distances = candidate['rows'], candidate['ids'], candidate['distances']
    all_residual, keep, residual = candidate['all_residual'], candidate['keep'], candidate['residual']
    rank, condition, hypotheses = candidate['rank'], candidate['condition'], candidate['hypotheses']
    result.update(rotation=rotation.tolist(), translation=translation.tolist(),
                  inlier_count=int(keep.sum()), candidate_count=len(rows),
                  rejected_count=int(len(query) - keep.sum()),
                  sampled_inlier_fraction=float(keep.sum() / len(query)),
                  reference_sample_count=len(reference), moving_sample_count=len(query),
                  normal_matrix_rank=rank, normal_matrix_condition=condition,
                  robust_normal_matrix_rank=candidate['robust_rank'],
                  ransac_hypothesis_found=hypotheses, capture_distance_m=capture,
                  final_match_distance_m=final_gate, plane_rejection_mm=3 * tolerance * 1000,
                  all_candidate_residuals=_statistics(all_residual),
                  residual_metric="absolute inlier tangent-plane distance",
                  tolerance_mm=tolerance * 1000, iteration_stop_reason=iteration_stop_reason,
                  terminal_validation_passed=terminal['valid'],
                  terminal_validation_reason=terminal['reason'],
                  terminal_step_mm=None if terminal['step_size'] is None else terminal['step_size'] * 1000,
                  terminal_inlier_count=int(terminal['keep'].sum()),
                  terminal_normal_matrix_rank=terminal['rank'],
                  terminal_rms_mm=terminal['statistics']['rms_mm'],
                  terminal_fixed_domain_score_m2=terminal['score'],
                  best_fixed_domain_score_m2=None if best is None else best[0],
                  selected_fixed_domain_score_m2=candidate['score'],
                  best_effort_iterate_retained=retained_earlier,
                  **candidate['statistics'])
    if not converged:
        result["warnings"].append(reason + "; transform is best effort and must not be applied automatically.")
    if iterations < 3:
        result["warnings"].append("Fewer than three iterations were allowed; convergence cannot be established.")
    if result["sampled_inlier_fraction"] < 0.2:
        result["warnings"].append("Low sampled inlier fraction; this fraction is not an estimate of overlap area.")
    if condition is not None and condition > 1e4:
        result["warnings"].append("Weak geometric conditioning remains; review the alignment independently.")
    if result["max_mm"] is not None and result["max_mm"] > tolerance * 1000:
        result["warnings"].append("Some retained plane residuals exceed the requested RMS tolerance.")
    result.update(converged=converged, stop_reason=reason)
    if not converged:
        result["reason"] = reason
    chosen = np.flatnonzero(keep)[:128]
    result["residuals"] = [{"query_sample_index": int(rows[i]), "reference_sample_index": int(ids[i]),
                            "signed_plane_mm": float(all_residual[i] * 1000),
                            "nearest_distance_mm": float(distances[i] * 1000)} for i in chosen]
    result["residuals_truncated"] = int(keep.sum()) > len(chosen)
    result["residual_index_note"] = "Indices address supplied bounded samples, never raw-cache point IDs."
    result["fit"] = {"count": result["inlier_count"], **_statistics(residual)}
    progress(f"ICP final validation: {result['inlier_count']:,} inliers; "
             f"{reason}")
    return result


def _frame_result(result, origin, old_rotation=None, old_translation=None):
    delta_r, local_t = np.asarray(result["rotation"]), np.asarray(result["translation"])
    control = origin + local_t
    delta_t = origin - delta_r @ origin + local_t
    if old_rotation is None:
        rotation, translation, source = delta_r, delta_t, origin
        result["transform_frame"] = "input moving coordinates to reference coordinates"
    else:
        rotation = delta_r @ old_rotation
        translation = delta_r @ (old_translation - origin) + control
        source = (origin - old_translation) @ old_rotation
        result.update(delta_rotation=delta_r.tolist(), delta_translation=delta_t.tolist(),
                      transform_frame="stored moving-cache coordinates to reference world coordinates")
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = rotation, translation
    result.update(rotation=rotation.tolist(), translation=translation.tolist(), matrix=matrix.tolist(),
                  source_centroid=source.tolist(), control_centroid=control.tolist())
    return result


def solve_icp(query_xyz, ref_xyz, ref_normals, max_iterations=50, tolerance_mm=5.0, seed=None):
    """Refine bounded, roughly aligned samples without I/O or metadata changes.

    seed controls sampling/RANSAC randomness; None uses deterministic seed 0.
    Result coordinates follow q_out = q_in @ R.T + t. Applied is always False;
    only register() may store a pose. Convergence requires at least three
    iterations and two small accepted steps, sufficient distinct inliers and
    a full-rank scaled normal matrix. The capture gate is capped at two metres.
    """
    iterations, tolerance, rng = _options(max_iterations, tolerance_mm, seed)
    query, reference = _points(query_xyz, "Moving cloud"), _points(ref_xyz, "Reference cloud")
    normals = _unit_normals(ref_normals, len(reference))
    if min(len(query), len(reference)) < MIN_OVERLAP:
        return _base_result("insufficient overlap")
    # Duplicated coordinates must not inflate the distinct-inlier requirement.
    query = np.unique(query, axis=0)
    reference, first = np.unique(reference, axis=0, return_index=True)
    normals = normals[first]
    origin = _centre(reference)
    reference, query = reference - origin, query - origin
    result = _run_icp(query, reference, normals, cKDTree(reference, copy_data=True),
                      iterations, tolerance, rng, lambda m: None, lambda: False)
    result["residual_index_note"] = "Indices address lexicographically deduplicated samples, never raw-cache point IDs."
    return _frame_result(result, origin)


def _sample_chunks(chunks, cap, rng, progress, cancel, name):
    """Consume a chunks generator using an exact bounded priority reservoir."""
    buffer = np.empty((2 * cap + BLOCK, 3), dtype=np.float64)
    priorities = np.empty(len(buffer))
    filled, retained, invalid, threshold = 0, 0, 0, np.inf
    last_progress = time.monotonic()
    try:
        for xyz, _attributes in chunks:
            _check(cancel)
            array = np.asarray(xyz)
            if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind not in "fiu":
                raise ValueError("pointio.chunks yielded an invalid XYZ block.")
            for start in range(0, len(array), BLOCK):
                _check(cancel)
                block = np.asarray(array[start:start + BLOCK], dtype=np.float64)
                finite = np.isfinite(block).all(axis=1)
                retained += len(block)
                invalid += int((~finite).sum())
                block = block[finite]
                keys = rng.random(len(block))
                select = keys < threshold
                count = int(select.sum())
                buffer[filled:filled + count] = block[select]
                priorities[filled:filled + count] = keys[select]
                filled += count
                if filled >= 2 * cap:
                    chosen = np.argpartition(priorities[:filled], cap - 1)[:cap]
                    buffer[:cap], priorities[:cap] = buffer[chosen], priorities[chosen]
                    filled = cap
                    threshold = float(priorities[:cap].max())
                if time.monotonic() - last_progress >= 1:
                    progress(f"Sampling {name}: {retained:,} retained points read")
                    last_progress = time.monotonic()
        if filled > cap:
            chosen = np.argpartition(priorities[:filled], cap - 1)[:cap]
            sample = buffer[chosen].copy()
        else:
            sample = buffer[:filled].copy()
        return np.unique(sample, axis=0), {"retained_points_read": retained,
                                          "nonfinite_points_skipped": invalid}
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()


def register(meta, reference_meta, work, progress=lambda m: None, cancel=lambda: False,
             seed=None, tolerance_mm=5.0, max_iterations=50, quality="balanced"):
    """Stream two scans, solve ICP, and store only a converged virtual pose.

    Quality caps (moving/reference): fast 25k/50k, balanced 75k/150k,
    high 150k/300k. Sampling is uniform over retained finite records, followed
    by exact deduplication; it is not uniform over surface area. Reading all
    records still takes time proportional to source size. No sub-second timing
    guarantee is made. work is unused; no temporary/output point cache is made.

    Optional attributes are not read, because geometry needs XYZ and the mask
    only. Existing moving/reference poses are honoured. The returned transform
    maps stored moving XYZ directly to the reference's current world frame.
    Invalid arguments raise ValueError; unavailable/changed data and cancelled
    or failed solves return applied=False. Cancellation is cooperative between
    yielded blocks and numerical batches; it cannot interrupt a reader inside
    a blocked read. The caller must hold its edit lock.
    """
    iterations, tolerance, rng = _options(max_iterations, tolerance_mm, seed)
    if not isinstance(meta, dict) or not isinstance(reference_meta, dict):
        raise ValueError("Moving and reference metadata must be dictionaries.")
    if meta is reference_meta:
        raise ValueError("Choose two different scans.")
    if not isinstance(quality, str) or quality not in QUALITY:
        raise ValueError("quality must be 'fast', 'balanced' or 'high'.")
    if not callable(progress) or not callable(cancel):
        raise ValueError("progress and cancel must be callable.")
    try:
        import pointio
        import registration_pose
    except ImportError as exc:
        result = _base_result("required application module unavailable")
        result["warnings"].append(str(exc))
        return result

    def validate(item):
        count = item.get("count")
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1:
            raise ValueError("Scan metadata must contain a positive integer point count.")
        if not isinstance(item.get("file"), (str, Path)) or not str(item["file"]).strip():
            raise ValueError("Scan metadata must identify its XYZ cache file.")
        if item.get("units", "metres") not in {"metres", "meters", "m"}:
            raise ValueError("ICP requires point coordinates in metres.")
        rotation, translation = registration_pose.get(item)
        if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0)
                or abs(float(np.linalg.det(rotation)) - 1) > 1e-8):
            raise ValueError("Existing scan poses must be proper rigid transforms.")
        return rotation.copy(), translation.copy()

    old_r, old_t = validate(meta)
    ref_r, ref_t = validate(reference_meta)
    if Path(meta["file"]).resolve() == Path(reference_meta["file"]).resolve():
        raise ValueError("The moving and reference scans use the same XYZ cache.")

    def snapshot(item):
        assets = []
        for key, width in (("file", 24), ("mask_file", 1)):
            if not item.get(key):
                continue
            path = Path(item[key]).resolve()
            status = path.stat()
            if not path.is_file() or status.st_size != int(item["count"]) * width:
                raise ValueError(f"The {key} cache size does not match the recorded point count.")
            assets.append((key, str(path), status.st_dev, status.st_ino, status.st_size,
                           status.st_mtime_ns, status.st_ctime_ns))
        rotation, translation = registration_pose.get(item)
        return (tuple(assets), int(item["count"]), tuple(rotation.ravel()), tuple(translation))

    result = _base_result()
    try:
        _check(cancel)
        before = (snapshot(meta), snapshot(reference_meta))
        moving_cap, reference_cap = QUALITY[quality]
        progress("Reading bounded reference and moving samples; optional attributes are skipped…")
        reference, reference_info = _sample_chunks(
            pointio.chunks({**reference_meta, "attributes_file": None}), reference_cap,
            rng, progress, cancel, "reference scan")
        moving, moving_info = _sample_chunks(
            pointio.chunks({**meta, "attributes_file": None}), moving_cap,
            rng, progress, cancel, "moving scan")
        if min(len(reference), len(moving)) < MIN_OVERLAP:
            return _base_result("insufficient overlap")
        reference_centre, moving_centre = _centre(reference), _centre(moving)
        origin = ref_r @ reference_centre + ref_t
        reference = (reference - reference_centre) @ ref_r.T
        moving = ((moving - moving_centre) @ old_r.T
                  + (old_r @ moving_centre + old_t - origin))
        _check(cancel)
        progress("Building reference normals and nearest-neighbour index…")
        normals, tree = _normals(reference, 12, progress, cancel)
        result = _run_icp(moving, reference, normals, tree, iterations, tolerance, rng, progress, cancel)
        result = _frame_result(result, origin, old_r, old_t)
        result.update(quality=quality, moving_sampling=moving_info, reference_sampling=reference_info)
        digest = hashlib.sha256()
        for array in (moving, reference):
            digest.update(np.ascontiguousarray(array).tobytes())
        digest.update(np.asarray(result["matrix"], dtype="<f8").tobytes())
        digest.update(np.asarray([iterations, tolerance], dtype="<f8").tobytes())
        result["fingerprint"] = digest.hexdigest()
        result["id"] = "cloud-icp-" + result["fingerprint"][:24]
        result["warnings"].append("Local refinement only; repetitive surfaces can produce a wrong alignment with small residuals.")
        if moving_info["nonfinite_points_skipped"] or reference_info["nonfinite_points_skipped"]:
            result["warnings"].append("Non-finite XYZ records were skipped while sampling.")
        if not result["converged"]:
            return result
        progress("Checking source revisions before storing the virtual registration pose…")
        _check(cancel)
        if (snapshot(meta), snapshot(reference_meta)) != before:
            result.update(applied=False, reason="source data or pose changed")
            result["warnings"].append("The source data or registration changed; no transform was applied.")
            return result
        rotation, translation = np.asarray(result["rotation"]), np.asarray(result["translation"])
        if (not np.isfinite(translation).all() or not np.isfinite(rotation).all()
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0)
                or abs(float(np.linalg.det(rotation)) - 1) > 1e-8):
            raise ValueError("The solved transform is not a finite proper rigid transform.")
        _check(cancel)
        saved = {key: meta[key] for key in ("registration_rotation", "registration_translation", "virtual_registration") if key in meta}
        try:
            registration_pose.set_pose(meta, rotation, translation)
        except Exception:
            for key in ("registration_rotation", "registration_translation", "virtual_registration"):
                meta.pop(key, None)
            meta.update(saved)
            raise
        result["applied"] = True
        return result
    except _Cancelled:
        result.update(applied=False, reason="cancelled")
        result["warnings"].append("Cancelled before metadata application.")
        return result
    except (OSError, ValueError, np.linalg.LinAlgError) as exc:
        result.update(applied=False, reason="registration failed: " + str(exc), converged=False)
        result["warnings"].append(str(exc))
        return result
