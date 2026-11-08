# Copyright (c) 2026 Damian Gulczynski.
"""Review correspondence consistency without changing targets or scan geometry.

This is a bounded engineering adaptation of Pankaj and Nidamanuri (2016),
doi:10.5566/ias.1378: minimal rigid fits, consensus and local optimisation.
When genuine correspondence quality is supplied, a growing ranked sampling
schedule is followed by uniform exploration. Named survey-target pairs have no
such quality: small sets use every minimal combination, larger sets use uniform
sampling. A checker-pattern score is NOT a correspondence-quality probability.

The paper's printed algorithm is inconsistent with its prose. Local samples
use max(m, ceil(inlier_count / 2)), never the printed min. The 5,4,3,2,1 threshold
schedule and bounded uniform fallback are explicit engineering choices. We do
not implement its malformed binomial expression or claim its stopping/confidence
guarantees. All checks and excluded rows remain outside model selection.

Results are proposals for human review. This module never changes a row's role,
never applies a transformation, never reads a cloud, and never writes a file.
"""
from dataclasses import dataclass
from itertools import combinations
import math

import numpy as np


MAX_PAIRS = 200
MAX_ITERATIONS = 20000


class _Degenerate(ValueError):
    pass


@dataclass
class _Model:
    rotation: np.ndarray
    source_centre: np.ndarray
    target_centre: np.ndarray
    spread: float
    errors: np.ndarray = None
    inliers: np.ndarray = None
    count: int = 0
    cost: float = math.inf


def _centre(a):
    # The differences are small even in a large survey coordinate system.
    return a[0] + (a - a[0]).mean(axis=0)


def _fit(p, q, mode):
    minimum = 2 if mode == 'vertical' else 3
    if len(p) < minimum:
        raise _Degenerate('Not enough observations for this fit.')
    pc, qc = _centre(p), _centre(q)
    pp, qq = p - pc, q - qc
    ratios = []
    for values in (pp, qq):
        if mode == 'vertical':
            if np.linalg.norm(values[:, :2], axis=1).max() < 1e-4:
                raise _Degenerate('Keep-vertical targets must be separated horizontally.')
            ratios.append(1.0)
        else:
            singular = np.linalg.svd(values, compute_uv=False)
            if singular[0] < 1e-5 or singular[1] / singular[0] < 1e-3:
                raise _Degenerate('Fit targets are collinear or nearly collinear. Add targets spread across the scene.')
            ratios.append(float(singular[1] / singular[0]))
    if mode == 'vertical':
        cross = np.sum(pp[:, 0] * qq[:, 1] - pp[:, 1] * qq[:, 0])
        dot = np.sum(pp[:, :2] * qq[:, :2])
        if math.hypot(float(cross), float(dot)) < 1e-15:
            raise _Degenerate('The selected pairs do not determine a stable heading.')
        angle = math.atan2(float(cross), float(dot))
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    else:
        u, _, vt = np.linalg.svd(pp.T @ qq)
        correction = np.eye(3)
        correction[2, 2] = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
        rotation = vt.T @ correction @ u.T
    if not np.isfinite(rotation).all():
        raise _Degenerate('The selected coordinates do not give a finite rigid fit.')
    return _Model(rotation, pc, qc, min(ratios))


def _residual(model, p, q):
    # Subtract local coordinates before adding a large target origin. This also
    # avoids cancellation when evaluating sub-millimetre fit residuals.
    return (p - model.source_centre) @ model.rotation.T - (q - model.target_centre)


def _evaluate(model, p, q, threshold):
    model.errors = np.linalg.norm(_residual(model, p, q), axis=1)
    if not np.isfinite(model.errors).all():
        raise ValueError('Coordinate magnitudes do not permit finite review residuals.')
    model.inliers = model.errors <= threshold
    model.count = int(model.inliers.sum())
    # For equal support, RMS and squared-error sum give the same ordering.
    # Scaling first avoids overflow when the caller supplies very large units.
    if model.count:
        values = model.errors[model.inliers]
        maximum = float(values.max())
        model.cost = maximum * float(np.sqrt(np.mean((values / maximum) ** 2))) if maximum else 0.0
    else:
        model.cost = math.inf
    return model


def _better(a, b):
    return b is None or a.count > b.count or (a.count == b.count and a.cost < b.cost)


def _stats(errors):
    a = np.asarray(errors, dtype=float)
    maximum = float(a.max()) if len(a) else 0.0
    rms = maximum * float(np.sqrt(np.mean((a / maximum) ** 2))) if maximum else 0.0
    if maximum and not math.isfinite(maximum * 1000):
        raise ValueError('Coordinate magnitudes do not permit finite millimetre residuals.')
    return {'count': int(len(a)),
            'rms_mm': rms * 1000 if len(a) else None,
            'max_mm': maximum * 1000 if len(a) else None}


def _validate(rows, mode, tolerance_mm, max_iterations, seed, quality):
    if mode not in ('rigid', 'vertical'):
        raise ValueError('Choose full 3D or keep vertical geometry.')
    if not isinstance(rows, list) or len(rows) > MAX_PAIRS:
        raise ValueError(f'Supply a list of no more than {MAX_PAIRS} target pairs.')
    try:
        tolerance = float(tolerance_mm)
    except (TypeError, ValueError):
        raise ValueError('The review limit must be positive millimetres.') from None
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('The review limit must be positive millimetres.')
    if isinstance(max_iterations, (bool, np.bool_)) or not isinstance(max_iterations, (int, np.integer)) or not 1 <= max_iterations <= MAX_ITERATIONS:
        raise ValueError(f'The review budget must be an integer from 1 to {MAX_ITERATIONS}.')
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)) or not 0 <= seed <= 2**63 - 1:
        raise ValueError('The review seed must be a nonnegative integer.')
    clean, ids = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each target pair must be an object.')
        name = str(row.get('id', '')).strip()
        if not name or name in ids:
            raise ValueError('Every target pair needs a unique, non-empty ID.')
        ids.add(name)
        role = row.get('role', 'fit')
        if role not in ('fit', 'check', 'off'):
            raise ValueError('Target role must be fit, check or off.')
        item = {'id': name, 'role': role,
                'protected': bool(row.get('keep')) or bool(row.get('protected'))}
        if role != 'off':
            try:
                p, q = np.asarray(row.get('source'), dtype=float), np.asarray(row.get('control'), dtype=float)
            except (TypeError, ValueError):
                raise ValueError(f'{name}: enter all six finite coordinates in metres.') from None
            if p.shape != (3,) or q.shape != (3,) or not np.isfinite([p, q]).all():
                raise ValueError(f'{name}: enter all six finite coordinates in metres.')
            item.update(source=p.tolist(), control=q.tolist())
        clean.append(item)
    fit_indices = [i for i, row in enumerate(clean) if row['role'] == 'fit']
    fit = [clean[i] for i in fit_indices]
    p = np.array([row['source'] for row in fit], dtype=float).reshape((-1, 3))
    q = np.array([row['control'] for row in fit], dtype=float).reshape((-1, 3))
    for label, values in (('moving', p), ('fixed', q)):
        if len(values) > 1:
            distance = np.linalg.norm(values[:, None] - values[None, :], axis=2)
            np.fill_diagonal(distance, math.inf)
            if distance.min() < 1e-6:
                raise ValueError(f'Duplicate {label} fit-target positions cannot define independent matches.')
    ranked_quality = None
    if quality is not None:
        try:
            if isinstance(quality, dict):
                values = [quality[row['id']] for row in fit]
            else:
                values = list(quality)
                if len(values) == len(clean):
                    values = [values[i] for i in fit_indices]
                elif len(values) != len(fit):
                    raise ValueError('wrong length')
            ranked_quality = np.asarray(values, dtype=float)
        except (KeyError, TypeError, ValueError):
            raise ValueError('Provide a finite correspondence-quality value for every Fit pair.') from None
        if ranked_quality.shape != (len(fit),) or not np.isfinite(ranked_quality).all():
            raise ValueError('Provide a finite correspondence-quality value for every Fit pair.')
        if not len(ranked_quality) or np.all(ranked_quality == ranked_quality[0]):
            ranked_quality = None
    return clean, fit, p, q, tolerance / 1000.0, ranked_quality


def _samples(n, minimum, budget, quality, rng):
    """Yield bounded sample draws. A yielded duplicate still consumes a draw."""
    total = math.comb(n, minimum)
    if total <= budget:
        for sample in combinations(range(n), minimum):
            yield sample, 'exhaustive'
        return
    if quality is None:
        for _ in range(budget):
            yield tuple(sorted(rng.choice(n, minimum, replace=False).tolist())), 'uniform'
        return
    # Break equal-quality ties randomly, reproducibly, without using Check rows.
    order = np.lexsort((rng.random(n), -quality))
    guided_budget = max(1, budget // 2)
    pool = minimum
    expected = guided_budget / total  # C(m,m) = 1
    scheduled_end = 1
    for draw in range(1, budget + 1):
        if draw <= guided_budget:
            while draw > scheduled_end and pool < n:
                next_expected = expected * (pool + 1) / (pool + 1 - minimum)
                scheduled_end += max(1, int(math.ceil(next_expected - expected)))
                expected = next_expected
                pool += 1
            prefix = rng.choice(pool - 1, minimum - 1, replace=False)
            indices = np.r_[prefix, pool - 1]
            yield tuple(sorted(order[indices].tolist())), 'ranked'
        else:
            yield tuple(sorted(rng.choice(n, minimum, replace=False).tolist())), 'uniform_fallback'


def review(rows, tolerance_mm=5, mode='rigid', quality=None,
           progress=lambda message: None, max_iterations=2000, seed=0):
    """Return a non-mutating review of explicitly supplied correspondences.

    ``quality`` is optional genuine feature-match quality (larger is better):
    a mapping from Fit-row ID, a sequence for Fit rows, or one for all rows.
    Check/off values in an all-row sequence are ignored. Do not pass target
    recognition confidence as though it measured correspondence correctness.

    The caller must separately confirm a shared vertical before mode='vertical'.
    A result cannot be adopted automatically. Proposed exclusions require a
    unique observed consensus with >= m+1 support and a strict Fit-row majority.
    A protected/kept mismatch blocks all proposed exclusions until manual review.
    """
    if not callable(progress):
        raise ValueError('The progress callback must be callable.')
    progress('Checking target-pair inputs…')
    clean, fit, p, q, threshold, quality = _validate(
        rows, mode, tolerance_mm, max_iterations, seed, quality)
    if max_iterations == 1:
        # One draw cannot provide both ranked sampling and its uniform fallback.
        quality = None
    n, minimum = len(fit), 2 if mode == 'vertical' else 3
    required = max(minimum + 1, n // 2 + 1)
    count_checks = sum(row['role'] == 'check' for row in clean)
    diag = {'fit_pair_count': n, 'check_pair_count': count_checks,
            'off_pair_count': sum(row['role'] == 'off' for row in clean),
            'minimal_sample_size': minimum, 'minimum_consensus': required,
            'sampling': 'none', 'quality_used': False, 'seed': int(seed),
            'max_iterations': int(max_iterations), 'iterations': 0,
            'models': 0, 'local_models': 0, 'consensus_refits': 0,
            'degenerate_samples': 0, 'duplicate_draws': 0,
            'uniform_fallback_draws': 0, 'complete_minimal_search': False,
            'search_truncated': False, 'consensus_count': 0,
            'competing_consensuses': 0, 'threshold_mm': float(tolerance_mm),
            'local_threshold_multipliers': [5, 4, 3, 2, 1],
            'local_repetitions_max': 10,
            'statistical_confidence': None,
            'baseline_unavailable_reason': None,
            'baseline_fit': {'count': n, 'rms_mm': None, 'max_mm': None}}
    warnings = ['This is a geometric consistency review, not a target-recognition result or an accuracy certificate.']
    if mode == 'vertical':
        warnings.append('Keep vertical fixes roll and pitch. The caller must confirm the same vertical direction in both scans.')
    if not count_checks:
        warnings.append('No Check observations are available. Fit residuals alone do not verify independent accuracy.')
    baseline = None
    baseline_error = None
    if n >= minimum:
        try:
            baseline = _evaluate(_fit(p, q, mode), p, q, threshold)
        except _Degenerate as exc:
            # A distant mismatched pair can dominate the all-pair spread test
            # even when a well-spread majority determines a valid rigid fit.
            # This diagnostic baseline must not prevent robust subset search.
            baseline_error = exc
            diag['baseline_unavailable_reason'] = str(exc)
            warnings.append('The all-pair baseline fit has degenerate geometry; individual target subsets were reviewed instead.')
        except (np.linalg.LinAlgError, FloatingPointError):
            raise ValueError('The target coordinates do not support a stable finite rigid fit.') from None
        if baseline is not None:
            diag['baseline_fit'] = _stats(baseline.errors)
    best = None
    equal_support = {}
    if n >= minimum + 1:
        rng = np.random.default_rng(int(seed))
        total = math.comb(n, minimum)
        exhaustive = total <= max_iterations
        diag['sampling'] = 'exhaustive' if exhaustive else ('ranked_with_uniform_fallback' if quality is not None else 'uniform')
        diag['quality_used'] = bool(quality is not None and not exhaustive)
        diag['minimal_combinations'] = int(total)
        diag['search_truncated'] = not exhaustive
        attempted = set()

        def fit_model(indices, counter):
            try:
                model = _fit(p[indices], q[indices], mode)
            except (_Degenerate, np.linalg.LinAlgError):
                diag['degenerate_samples'] += 1
                return None
            diag[counter] += 1
            return _evaluate(model, p, q, threshold)

        def remember(model):
            nonlocal best, equal_support
            if model is None:
                return False
            improved = _better(model, best)
            if best is None or model.count > best.count:
                equal_support = {}
            if best is None or model.count >= best.count:
                key = np.packbits(model.inliers).tobytes()
                old = equal_support.get(key)
                if old is None or _better(model, old):
                    # At most one model per sampled support mask: <= budget.
                    equal_support[key] = model
            if improved:
                best = model
            return improved

        def stabilise(model):
            current = model
            for _ in range(5):
                if current is None or current.count < minimum:
                    break
                fitted = fit_model(np.flatnonzero(current.inliers), 'consensus_refits')
                if fitted is None:
                    break
                remember(fitted)
                if not _better(fitted, current):
                    break
                unchanged = np.array_equal(current.inliers, fitted.inliers)
                current = fitted
                if unchanged:
                    break
            return current

        def local_optimise(model):
            support = np.flatnonzero(model.inliers)
            if len(support) < minimum:
                return
            amount = max(minimum, int(math.ceil(len(support) / 2)))
            repetitions = 1 if len(support) == minimum else 10
            for repeat in range(repetitions):
                progress(f'Refining target consensus: {repeat + 1} / {repetitions}; support {best.count} / {n}')
                selected = rng.choice(support, amount, replace=False)
                candidate = fit_model(selected, 'local_models')
                if candidate is None:
                    continue
                remember(candidate)
                for multiplier in (5, 4, 3, 2, 1):
                    indices = np.flatnonzero(candidate.errors <= threshold * multiplier)
                    if len(indices) < minimum:
                        break
                    candidate = fit_model(indices, 'local_models')
                    if candidate is None:
                        break
                    remember(candidate)
                if candidate is not None:
                    stabilise(candidate)

        for draw, (sample, kind) in enumerate(_samples(n, minimum, int(max_iterations), quality, rng), 1):
            diag['iterations'] = draw
            if kind == 'uniform_fallback':
                diag['uniform_fallback_draws'] += 1
            if draw == 1 or draw % 16 == 0:
                progress(f'Reviewing target matches: {draw} / {min(total, max_iterations)} samples; best support {best.count if best else 0} / {n}')
            if sample in attempted:
                diag['duplicate_draws'] += 1
                continue
            attempted.add(sample)
            candidate = fit_model(np.asarray(sample, dtype=int), 'models')
            if candidate is None:
                continue
            # A full-consensus fit is considered before random inner refinement.
            improved = remember(candidate)
            if candidate.count >= minimum + 1 and candidate.count >= best.count:
                before = best
                candidate = stabilise(candidate)
                improved = improved or _better(best, before)
            if improved:
                local_optimise(best)
        diag['complete_minimal_search'] = bool(exhaustive and diag['iterations'] == total)
        if best is not None and best.count >= minimum:
            stabilise(best)
    elif baseline is not None:
        best = baseline

    if best is None and baseline_error is not None:
        # Preserve the clear geometry error when every sampled subset is also
        # degenerate (including fully collinear / vertical-only observations).
        raise baseline_error

    progress('Preparing target-match review…')
    has_model = best is not None and best.count >= minimum
    adequate = has_model and n >= minimum + 1 and best.count >= required
    competing = []
    if adequate:
        for other in equal_support.values():
            if other.count != best.count or np.array_equal(other.inliers, best.inliers):
                continue
            prediction_difference = _residual(other, p, q) - _residual(best, p, q)
            if np.linalg.norm(prediction_difference, axis=1).max() > threshold * .5:
                competing.append(other)
    diag['consensus_count'] = best.count if has_model else 0
    diag['competing_consensuses'] = len(competing)
    fit_ids = [row['id'] for row in fit]
    suspect_ids = [fit_ids[i] for i in range(n) if has_model and not best.inliers[i]]
    protected = [row['id'] for row in fit if row['protected'] and row['id'] in suspect_ids]
    if not adequate:
        status = 'insufficient'
        message = (f'Robust review needs at least {minimum + 1} Fit pairs with useful spread; the normal solver remains available.'
                   if n < minimum + 1 else
                   f'No sufficiently supported consensus was found. At least {required} of {n} Fit pairs must agree before exclusions can be proposed.')
    elif competing:
        status = 'ambiguous'
        message = 'Different transformations have equally strong support. Review the target identities; no exclusions are proposed.'
    elif not suspect_ids:
        status = 'consistent'
        message = 'All selected Fit pairs agree within the review limit. Review Check observations before solving or applying geometry.'
    elif protected:
        status = 'suspects'
        message = 'Possible mismatches include kept target coordinates. Review those pairs and kept coordinates before adopting any exclusions.'
    else:
        status = 'suspects'
        message = f'{len(suspect_ids)} Fit pair(s) disagree with the strongest observed consensus. Review the suggested exclusions; no targets or geometry have changed.'
    can_adopt = bool(adequate and not competing and suspect_ids and not protected)
    exclusions = suspect_ids if can_adopt else []
    result_rows = []
    for row in clean:
        result = dict(row, error_mm=None, residual_mm=None, inlier=None,
                      suggested_role='off' if row['id'] in exclusions else row['role'])
        if row['role'] != 'off' and has_model:
            residual = _residual(best, np.array([row['source']]), np.array([row['control']]))[0]
            error = float(np.linalg.norm(residual))
            result.update(error_mm=error * 1000, residual_mm=(residual * 1000).tolist(),
                          inlier=bool(error <= threshold))
        result_rows.append(result)
    solution = None
    if has_model:
        translation = best.target_centre - best.rotation @ best.source_centre
        if not np.isfinite(translation).all():
            raise ValueError('Coordinate magnitudes do not permit a finite transformation.')
        matrix = np.eye(4)
        matrix[:3, :3], matrix[:3, 3] = best.rotation, translation
        solution = {'rotation': best.rotation.tolist(), 'translation': translation.tolist(),
                    'source_centroid': best.source_centre.tolist(), 'control_centroid': best.target_centre.tolist(),
                    'matrix': matrix.tolist(), 'scale': 1.0, 'units': 'metres', 'mode': mode,
                    'method': 'Diagnostic robust correspondence consensus; centred fixed-scale rigid least squares. No geometry applied.',
                    'fit': _stats(best.errors[best.inliers]), 'all_fit': _stats(best.errors),
                    'check': _stats([row['error_mm'] / 1000 for row in result_rows if row['role'] == 'check']),
                    'spread_ratio': float(best.spread)}
        if best.spread < .05:
            warnings.append('The supported targets have weak geometric spread. Add observations across width, height and length.')
        if any(row['role'] == 'check' and not row['inlier'] for row in result_rows):
            warnings.append('At least one Check observation exceeds the review limit. Check observations were not used to choose this model.')
    if diag['search_truncated']:
        warnings.append('The bounded search did not test every minimal combination. No probability of correctness is claimed.')
    if protected:
        warnings.append('Kept target coordinates are protected. Change their pairing or kept-coordinate selection explicitly before any exclusions can be adopted.')
    diag['total_models'] = diag['models'] + diag['local_models'] + diag['consensus_refits']
    return {'status': status, 'message': message, 'can_adopt': can_adopt,
            'proposed_exclusions': exclusions, 'suspect_ids': suspect_ids,
            'protected_suspects': protected, 'rows': result_rows, 'solution': solution,
            'diagnostics': diag, 'warnings': warnings,
            'method': 'Paper-informed minimal-sample consensus and local refinement; proposals only.'}
