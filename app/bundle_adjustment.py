"""Joint rigid scan and shared-target adjustment with surface loop constraints.

Scan poses and common target XYZ are optimised simultaneously, with one datum
held fixed. Cloud links contribute re-associated point-to-plane observations;
they are not sequential pairwise transforms. Sparse trust-region least squares,
robust residuals, bounded samples and a sparse observability check avoid dense
whole-cloud or whole-network allocations. The caller owns I/O and acceptance.
References: Chen & Medioni (1991), point-to-plane registration; Triggs et al.
(2000), Bundle Adjustment -- A Modern Synthesis. SciPy's sparse TRF/LSMR is used.
This is a local mathematical adjustment, not an accuracy certificate, a scanner
calibration adjustment, or a reproduction of Leica's proprietary optimiser.
"""
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import LinearOperator, onenormest, splu

METHOD = 'Global scan bundle adjustment with shared targets and point-to-plane surfaces'
MIN_OVERLAP = 500
MAX_RESIDUALS = 600000


class Cancelled(Exception):
    pass


class NonConvergence(ValueError):
    """A finite, observable solve exhausted its numerical refinement budget.

    Only this failure permits the caller to attempt independent pairwise fits.
    Invalid observations, weak geometry and cancellation remain hard failures.
    """
    def __init__(self, reason, iterations, evaluations, condition_estimate):
        super().__init__(reason)
        self.iterations = int(iterations)
        self.evaluations = int(evaluations)
        self.condition_estimate = float(condition_estimate)


def check(cancel):
    if cancel():
        raise Cancelled('Bundle adjustment cancelled; no positions were applied.')


def statistics(values):
    values = np.asarray(values, float)
    if not len(values):
        return dict(count=0, rms_mm=None, median_mm=None, max_mm=None)
    values = np.abs(values) * 1000
    return dict(count=len(values), rms_mm=float(np.sqrt(np.mean(values ** 2))),
                median_mm=float(np.median(values)), max_mm=float(np.max(values)))


def observability(jacobian):
    """Check a column-scaled sparse normal matrix without dense SVD or damping.

The condition estimate is a numerical diagnostic, not coordinate covariance.
Refuse singular/very weak networks rather than accepting TRF regularisation as
evidence that the geometry supplies the missing degrees of freedom.
"""
    J = jacobian.tocsr()
    if J.shape[0] < J.shape[1] or not np.isfinite(J.data).all():
        raise ValueError('The observations do not constrain the whole bundle.')
    norms = np.sqrt(np.asarray(J.power(2).sum(axis=0)).ravel())
    if not len(norms) or np.any(norms < 1e-10):
        raise ValueError('The bundle has an unconstrained motion. Add useful targets or overlapping surfaces.')
    scaled = J @ diags(1 / norms)
    normal = (scaled.T @ scaled).tocsc()
    try:
        lu = splu(normal)
        inverse = LinearOperator(normal.shape, matvec=lu.solve,
                                 rmatvec=lambda x: lu.solve(x, trans='T'), dtype=float)
        condition = float(onenormest(normal) * onenormest(inverse))
    except (RuntimeError, ValueError) as exc:
        raise ValueError('The bundle is singular. Its geometry does not determine every scan motion.') from exc
    if not np.isfinite(condition) or condition > 1e12:
        raise ValueError('The bundle is too weakly constrained. Add targets with more spread or surfaces with different orientations.')
    return condition


def transformed(points, ident, transforms, centres):
    R, t = transforms[ident]
    return (points - centres[ident]) @ R.T + centres[ident] + t


def cloud_matches(edge, nodes, transforms, centres, capture, cap, cancel, guide=None):
    """One batched nearest-neighbour search, then distinct-support rejection."""
    check(cancel)
    a, b = edge['a'], edge['b']
    aa, bb = nodes[a], nodes[b]
    if min(len(aa['points']), len(bb['points'])) < MIN_OVERLAP:
        return None
    q = transformed(bb['points'], b, transforms, centres)
    A, ta = transforms[a]
    B, _ = transforms[b]
    if guide is not None:
        G, g = guide
        q = q @ G.T + g
        B = G @ B
    local_query = (q - centres[a] - ta) @ A + centres[a]
    distance, indices = aa['tree'].query(local_query, workers=__import__('compute_runtime').workers(), distance_upper_bound=capture)
    good = np.flatnonzero(np.isfinite(distance))
    if not len(good):
        return None
    ids = indices[good]
    n = aa['normals'][ids]
    bn = bb['normals'][good] @ B.T @ A
    angle = np.abs(np.einsum('ij,ij->i', n, bn))
    good = good[(np.linalg.norm(n, axis=1) > .9) & (angle > .5)]
    if len(good) < MIN_OVERLAP:
        return None
    # Repeated nearest neighbours must not inflate overlap support.
    order = good[np.argsort(distance[good], kind='stable')]
    _, first = np.unique(indices[order], return_index=True)
    good = np.sort(order[first])
    if len(good) < MIN_OVERLAP:
        return None
    available = len(good)
    if available > cap:
        good = good[np.linspace(0, available - 1, cap, dtype=int)]
    return dict(edge=edge, p=aa['points'][indices[good]], q=bb['points'][good],
                n=aa['normals'][indices[good]], support=available,
                overlap_fraction=float(available / min(len(aa['points']), len(bb['points']))))


def plane_residual(match, transforms, centres):
    a, b = match['edge']['a'], match['edge']['b']
    p = transformed(match['p'], a, transforms, centres)
    q = transformed(match['q'], b, transforms, centres)
    normal = match['n'] @ transforms[a][0].T
    return np.einsum('ij,ij->i', q - p, normal)


def optimise(nodes, edges, landmarks, layout, initial=None, tolerance_mm=5.,
             capture_mm=250., max_iterations=20, cloud_rows=2000,
             progress=lambda message: None, cancel=lambda: False):
    """Return an unapplied candidate; all points use one nearby metric frame.

`landmarks` is a list of lists of (scan_id, measured_xyz). There is one unknown
XYZ per physical target, so an observation in several links is counted once.
`layout` supplies constrained scan motions (including existing vertical links).
Cloud link weights balance their mean squared plane error against target
observations. These engineering weights are not instrument uncertainties.
"""
    check(cancel)
    for value, label in ((tolerance_mm, 'tolerance'), (capture_mm, 'capture distance')):
        if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
            raise ValueError('Enter a finite positive ' + label + '.')
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 2 <= max_iterations <= 50:
        raise ValueError('Choose 2 to 50 bundle iterations.')
    if layout.size < 1:
        raise ValueError('All scan motions are fixed; release a reference before adjusting.')
    centres = layout.centres
    ids = sorted(nodes)
    if set(ids) != set(centres):
        raise ValueError('The bundle nodes and motion layout do not match.')
    parameters = np.zeros(layout.size + 3 * len(landmarks))
    if initial is not None:
        parameters[:layout.size] = layout.seed(initial)
    tr = layout.transforms(parameters[:layout.size])
    measurements, owners, groups = [], [], []
    for group, observations in enumerate(landmarks):
        if len(observations) < 2 or len({i for i, _ in observations}) != len(observations):
            raise ValueError('A common target needs one observation per scan, in at least two scans.')
        p = np.asarray([v for _, v in observations], float)
        if p.shape != (len(observations), 3) or not np.isfinite(p).all():
            raise ValueError('A bundle target has invalid coordinates.')
        parameters[layout.size+3*group:layout.size+3*group+3] = np.mean([
            transformed(v[None], i, tr, centres)[0] for (i, _), v in zip(observations, p)], axis=0)
        for ident, xyz in observations:
            measurements.append(xyz);owners.append(ident);groups.append(group)
    measurements = np.asarray(measurements, float).reshape(-1, 3)
    groups = np.asarray(groups, int)
    node_indices = {ident: np.flatnonzero(np.asarray(owners) == ident) for ident in ids}
    target_rows = 3 * len(measurements)
    cloud_edges = [e for e in edges if e.get('use_cloud')]
    cap = min(cloud_rows, (MAX_RESIDUALS - target_rows) // max(1, len(cloud_edges)))
    if target_rows > MAX_RESIDUALS or cloud_edges and cap < MIN_OVERLAP:
        raise ValueError('This bundle exceeds the bounded residual budget. Use fewer redundant links or a smaller connected group.')
    warnings = []
    original = {ident: (np.eye(3), np.zeros(3)) for ident in ids}
    first_matches = None
    stable = 0
    evaluations = 0
    converged = False
    result = None
    matches = []
    for iteration in range(1, max_iterations + 1):
        check(cancel)
        progress(f'Bundle iteration {iteration}/{max_iterations}: matching {len(cloud_edges)} surface links…')
        tr = layout.transforms(parameters[:layout.size])
        matches = []
        for edge in cloud_edges:
            match = cloud_matches(edge, nodes, tr, centres, capture_mm / 1000, cap, cancel,
                                  edge.get('initial_guide') if iteration == 1 else None)
            if match is None:
                if edge.get('has_targets'):
                    warning = 'Insufficient distinct cloud overlap on ' + edge['id'] + '; its recorded target observations are still used.'
                    if warning not in warnings:warnings.append(warning)
                    continue
                raise ValueError('Insufficient overlap on ' + edge['id'] + ': fewer than 500 distinct surface correspondences. Review this link.')
            matches.append(match)
        if first_matches is None:first_matches = matches
        count = target_rows + sum(len(m['p']) for m in matches)
        if count < len(parameters):
            raise ValueError('Too few observations to constrain every scan and common target.')
        # Each row touches only one scan and one target, or two linked scans.
        rr, cc = [], []
        for row, (ident, group) in enumerate(zip(owners, groups)):
            deps = layout.dependencies(ident) + list(range(layout.size+3*group, layout.size+3*group+3))
            rr.extend(np.repeat(np.arange(3*row, 3*row+3), len(deps)))
            cc.extend(np.tile(deps, 3))
        offset = target_rows
        for match in matches:
            edge = match['edge'];length = len(match['p'])
            deps = sorted(set(layout.dependencies(edge['a']) + layout.dependencies(edge['b'])))
            rr.extend(np.repeat(np.arange(offset, offset+length), len(deps)))
            cc.extend(np.tile(deps, length));offset += length
        sparsity = coo_matrix((np.ones(len(rr), np.int8), (rr, cc)), shape=(count, len(parameters))).tocsr()

        def residual(values):
            nonlocal evaluations
            check(cancel)
            poses = layout.transforms(values[:layout.size])
            result_rows = []
            if target_rows:
                moved = np.empty_like(measurements)
                for ident, rows in node_indices.items():
                    if len(rows):moved[rows] = transformed(measurements[rows], ident, poses, centres)
                xyz = values[layout.size:].reshape(-1, 3)
                raw = moved - xyz[groups]
                norm = np.linalg.norm(raw, axis=1) / (tolerance_mm / 1000)
                robust = raw * np.sqrt(2 / (np.sqrt(1 + norm**2) + 1))[:, None]
                result_rows.append(robust.ravel())
            for match in matches:
                # A surface link has the weight of 20 scalar observations,
                # regardless of its sample density; target rows have unit weight.
                weight = np.sqrt(20. / len(match['p']))
                raw = plane_residual(match, poses, centres)
                # Robustify in physical metres BEFORE balancing link density.
                scale = tolerance_mm / 1000
                robust = raw * np.sqrt(2 / (np.sqrt(1 + (raw/scale)**2) + 1))
                result_rows.append(weight * robust)
            evaluations += 1
            if evaluations % 50 == 0:
                progress(f'Jointly adjusting {len(ids)} setups and {len(landmarks)} common targets · iteration {iteration}')
            return np.concatenate(result_rows)

        before = parameters.copy()
        result = least_squares(residual, parameters, jac_sparsity=sparsity, method='trf',
                               tr_solver='lsmr', x_scale='jac', max_nfev=500,
                               ftol=1e-8, xtol=1e-8, gtol=1e-8,
                               tr_options={'atol': 1e-11, 'btol': 1e-11})
        check(cancel)
        if not np.isfinite(result.x).all() or not np.isfinite(result.fun).all():
            raise ValueError('Bundle adjustment produced non-finite values. No adjustment was applied.')
        if not result.success:
            if result.status == 0:
                condition = observability(result.jac)
                raise NonConvergence('Full bundle adjustment reached its evaluation limit.',
                                     iteration, evaluations, condition)
            raise ValueError('Bundle adjustment failed: ' + str(result.message))
        parameters = result.x
        after = layout.transforms(parameters[:layout.size])
        change = max(float(np.linalg.norm(after[i][1]-tr[i][1]) +
                           np.linalg.norm(after[i][0]-tr[i][0])) for i in ids)
        stable = stable + 1 if change < max(1e-7, tolerance_mm * 1e-5) else 0
        if not cloud_edges or iteration >= 2 and stable >= 1:
            converged = True
            break
    if not converged:
        check(cancel)
        condition = observability(result.jac)
        raise NonConvergence('Full bundle correspondence refinement reached its iteration limit.',
                             iteration, evaluations, condition)
    check(cancel)
    progress('Checking whether the observations constrain the whole bundle…')
    condition = observability(result.jac)
    poses = layout.transforms(parameters[:layout.size])
    cloud_reports = {}
    for match in matches:
        edge = match['edge'];values = plane_residual(match, poses, centres)
        report = dict(before=statistics(plane_residual(match, original, centres)), after=statistics(values),
                      inlier_count=match['support'], sampled_correspondences=len(values),
                      overlap_fraction=match['overlap_fraction'], residual_type='point-to-plane')
        if edge.get('loop_closure') and report['after']['rms_mm'] > max(5*tolerance_mm, capture_mm*.1):
            raise ValueError('The proposed loop conflicts with the bundle: ' + edge['id'] + '. Review or remove that loop candidate.')
        cloud_reports[edge['id']] = report
    warnings.extend([
        'One datum is held fixed. Original scan coordinates and scale are preserved.',
        'Surface links use bounded samples and engineering weights, not instrument covariance. Residuals are not accuracy or probability.',
        'Local optimisation can settle on a wrong match in repetitive tunnels. Review loop links and independent check targets.',
    ])
    return dict(method=METHOD, converged=True, full_bundle_converged=True,
                fallback_used=False, applied=False, transforms=poses,
                landmarks=parameters[layout.size:].reshape(-1, 3), iterations=iteration,
                scan_count=len(ids), common_targets=len(landmarks), target_observations=len(measurements),
                cloud_reports=cloud_reports, warnings=warnings, condition_estimate=condition,
                cloud_fit=statistics(np.concatenate([plane_residual(m, poses, centres) for m in matches]) if matches else []),
                cloud_before=statistics(np.concatenate([plane_residual(m, original, centres) for m in matches]) if matches else []),
                parameters=len(parameters), residual_count=len(result.fun), evaluations=evaluations,
                scale=1., centres=centres)
