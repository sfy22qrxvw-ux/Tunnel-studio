# Copyright (c) 2026 Damian Gulczynski.
"""Fit circular and elliptical outlines to a bounded sample of a tunnel section.

Shen et al. (2021), doi:10.11947/j.JGGS.2021.0206, use plan PCA, outlier
screening, least-squares circles and section chords. Here a section's 3D
covariance supplies its plane if none is specified. Epochs use one alignment
plane and one common datum. The moment-seeded ellipse and coverage checks
are extensions. Diameter changes describe fitted geometry, not independent
point-pair observations or uncertainty in the control network.
"""
import math
import numpy as np
from scipy.optimize import least_squares

import jobdata


MIN_POINTS = 24
MAX_SAMPLE = 32000
ANGLES = (0, 30, 90, 150)


def _check(cancel):
    if cancel():
        raise jobdata.Cancelled('Section fitting cancelled.')


def _normalise(v):
    norm = np.linalg.norm(v)
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError('The section frame has no usable direction.')
    return v / norm


def _frame(points, frame, progress, cancel):
    _check(cancel)
    p = np.asarray(points)
    if p.ndim != 2 or p.shape[1] not in (2, 3) or len(p) < MIN_POINTS:
        raise ValueError('Fit a section using at least 24 planar XY or world XYZ points in metres.')
    if len(p) > MAX_SAMPLE:
        progress('Sampling up to 32,000 section points for the outline fit…')
        indices = np.random.default_rng(13271).choice(len(p), MAX_SAMPLE, replace=False)
        p = p[indices]
    p = np.asarray(p, dtype=float)
    if not np.isfinite(p).all():
        raise ValueError('Section points must all be finite.')
    progress('Estimating section orientation from the point moments…')
    if p.shape[1] == 2:
        p = np.column_stack((p, np.zeros(len(p))))
    # Trim gross isolated returns before estimating the plane. Use centred
    # arithmetic so large survey eastings/northings retain their precision.
    offset = np.median(p, axis=0)
    lengths = np.linalg.norm(p - offset, axis=1)
    scale = np.quantile(lengths, .97)
    selected = p[lengths <= 3 * scale + 1e-9] - offset
    if len(selected) < MIN_POINTS:
        raise ValueError('Too few points remain for a supported section plane.')
    for attempt in range(3):
        _check(cancel)
        centroid = selected.mean(axis=0)
        eigen, vectors = np.linalg.eigh(np.cov((selected - centroid).T))
        depth = (selected - centroid) @ vectors[:, 0]
        keep = np.abs(depth - np.mean(depth)) <= max(1e-6, 3 * np.std(depth))
        if attempt == 2 or np.all(keep):
            break
        selected = selected[keep]
        if len(selected) < MIN_POINTS:
            raise ValueError('Too few points support the fitted section plane.')
    if eigen[1] < 1e-10 or eigen[1] < .01 * eigen[2] or eigen[0] > .12 * eigen[1]:
        raise ValueError('Points do not define a sufficiently thin section with two supported dimensions.')
    normal = vectors[:, 0]
    if frame is None:
        if normal[np.argmax(np.abs(normal))] < 0:
            normal = -normal
        right = np.cross(normal, [0., 0., 1.])
        if np.linalg.norm(right) < 1e-6:
            right = np.array([1., 0., 0.]) - normal * normal[0]
        right = _normalise(right)
        up = _normalise(np.cross(right, normal))
        if abs(normal[2]) > .999999:
            up = _normalise(np.cross(normal, right))
        origin = offset + centroid
    else:
        origin, expected, right, up = (np.asarray(frame[k], float) for k in ('origin', 'normal', 'right', 'up'))
        axes = np.array([expected, right, up])
        if origin.shape != (3,) or axes.shape != (3,3) or not np.isfinite(origin).all() or not np.allclose(axes @ axes.T, np.eye(3), atol=1e-8):
            raise ValueError('Invalid common section plane.')
        if abs(np.dot(normal, expected)) < math.cos(math.radians(10)):
            raise ValueError('Measured plane departs from the requested alignment section by more than 10 degrees.')
        normal = expected
    uv = (selected + (offset - origin)) @ np.array([right, up]).T
    axes = dict(origin=origin.tolist(), normal=normal.tolist(), right=right.tolist(), up=up.tolist())
    return uv, axes


def _angular_weights(uv, centre):
    delta = uv - centre
    angle = np.arctan2(delta[:,1], delta[:,0]) % (2 * np.pi)
    bins = np.minimum((angle * (180 / np.pi)).astype(int), 359)
    counts = np.bincount(bins, minlength=360)
    # Each occupied angular sector contributes the same total squared weight.
    return np.sqrt(len(uv) / (np.count_nonzero(counts) * counts[bins]))


def _fit(points, ellipse, progress, cancel, frame):
    uv, axes = _frame(points, frame, progress, cancel)
    kind = 'ellipse' if ellipse else 'circle'
    _check(cancel)
    # Shift to scan-local coordinates before forming squared terms.
    offset = np.mean(np.quantile(uv, [.01, .99], axis=0), axis=0)
    q = uv - offset
    weights = _angular_weights(q, np.zeros(2))
    square_weights = weights**2
    centre = np.average(q, weights=square_weights, axis=0)
    delta = q - centre
    moment = (delta.T * square_weights) @ delta / square_weights.sum()
    if ellipse:
        eig, vec = np.linalg.eigh(moment)
        if eig[0] <= 1e-10:
            raise ValueError('Insufficient support for an ellipse.')
        # For perimeter points distributed uniformly in eccentric angle the
        # moment eigenvalues are half the squared semi-axes. Use them only
        # to seed a positive definite conic, then fit to actual point distances.
        shape = (vec * (1 / (2 * eig))) @ vec.T
        L = np.linalg.cholesky(shape)
        x = np.r_[centre, np.log(L[0,0]), L[1,0], np.log(L[1,1])]

        def matrix(v):
            root = np.array([[np.exp(v[2]), 0.], [v[3], np.exp(v[4])]])
            return root @ root.T

        def residual(v, arr):
            z = arr - v[:2]; grad = z @ matrix(v)
            return ((grad * z).sum(axis=1) - 1.) / np.maximum(2 * np.linalg.norm(grad, axis=1), 1e-10)
    else:
        b = np.sum(q*q, axis=1)
        design = np.column_stack([2*q, np.ones(len(q))]); init, _, rank, _ = np.linalg.lstsq(design*weights[:,None], b*weights, rcond=None)
        if rank != 3:
            raise ValueError('Circle fit needs points distributed around the lining.')
        x = np.r_[init[:2], math.sqrt(max(1e-10, init[2] + init[:2] @ init[:2]))]

        def residual(v, arr):
            return np.linalg.norm(arr - v[:2], axis=1) - v[2]

    active = np.ones(len(q), dtype=bool)
    for iteration in range(4):
        _check(cancel)
        progress(f'Fitting {kind} · residual screening pass {iteration + 1}')
        part = q[active]; weights = _angular_weights(part, x[:2])
        def error(v):
            _check(cancel)
            return residual(v, part) * weights
        # The first robust fit anchors the classical 3-sigma residual gate.
        # The accepted points are refit by weighted geometric least squares.
        fit = least_squares(error, x, loss='soft_l1' if iteration == 0 else 'linear',
                            f_scale=.005, max_nfev=120, x_scale='jac',
                            bounds=([-np.inf,-np.inf,-13.,-1e5,-13.],[np.inf,np.inf,13.,1e5,13.]) if ellipse else ([-np.inf,-np.inf,1e-7], np.inf))
        if not fit.success or not np.isfinite(fit.x).all():
            raise ValueError(f'Unable to fit a supported {kind} to this section.')
        x = fit.x
        if iteration == 3:
            break
        d = residual(x, q)
        inside = d[active]
        tol = max(1e-7, 3 * float(inside.std()))
        new = active & (np.abs(d - inside.mean()) <= tol)
        if np.count_nonzero(new) < MIN_POINTS:
            raise ValueError('Too few points survive the section residual check.')
        if iteration and np.array_equal(new, active):
            break
        active = new
    z = q[active] - x[:2]
    angles = np.sort(np.arctan2(z[:,1], z[:,0]) % (2*np.pi))
    gap = np.degrees(np.diff(np.r_[angles, angles[0]+2*np.pi])).max()
    support = len(np.unique((angles*180/np.pi).astype(int)))
    if gap > 60 or support < 24:
        raise ValueError(f'Fit withheld: largest unsupported arc {gap:.1f}°; require 24 occupied one-degree sectors and gaps at most 60°.')
    centroid = x[:2] + offset
    centre_xyz = np.asarray(axes['origin']) + centroid @ np.array([axes['right'], axes['up']])
    result = dict(centre_xyz=centre_xyz.tolist(), centre_uv_m=centroid.tolist(), plane=axes,
                  input_points=len(points), sample_points=min(len(points), MAX_SAMPLE), inliers=int(active.sum()), largest_gap_deg=float(gap),
                  rms_mm=float(1000*np.sqrt(np.mean(residual(x, q[active])**2))),
                  method='3D moment plane and ellipse seed, 3-sigma residual screening and weighted Sampson least squares' if ellipse else 'Section PCA, 3-sigma radial residual screening and weighted geometric circle least squares')
    if ellipse:
        shape=matrix(x); eig, vec = np.linalg.eigh(shape); radii=1/np.sqrt(eig)
        if radii[0]/radii[1] > 5:
            raise ValueError('The fitted ellipse is too elongated for a supported lining section.')
        result.update(major_radius_m=float(radii[0]), minor_radius_m=float(radii[1]),
                      diameters_m={str(a):float(2/np.sqrt(u @ shape @ u)) for a in ANGLES
                                   for u in [np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])]},
                      major_direction_xyz=(vec[:,0] @ np.array([axes['right'],axes['up']])).tolist() if abs(radii[0]-radii[1])>max(.0001,2*result['rms_mm']/1000) else None)
    else:
        result['radius_m'] = float(x[2])
    _check(cancel)
    return result


def fit_circle_section(points, progress=lambda _: None, cancel=lambda: False, *, frame=None):
    """Fit a circle to section points in metres; return centre, radius and inlier residual RMS."""
    return _fit(points, False, progress, cancel, frame)


def fit_ellipse_section(points, progress=lambda _: None, cancel=lambda: False, *, frame=None):
    """Fit an ellipse using 3D section moments and report central chord lengths in metres."""
    return _fit(points, True, progress, cancel, frame)


def convergence_analysis(sections_a, sections_b, progress=lambda _: None, cancel=lambda: False):
    """Compare station-keyed section records from a common datum, slab and filter.

    Each record carries chainage, datum, width_m, band_m, frame, circle and
    ellipse as produced by the station workflow. Positive closure = A minus B.
    Centre shifts are B minus A in the common horizontal-right, local-up frame.
    """
    _check(cancel)
    def index(rows):
        out = {}
        for rec in rows:
            station = float(rec['chainage'])
            if not np.isfinite(station):
                raise ValueError('Section chainages must be finite.')
            key=round(station,6)
            if key in out:
                raise ValueError('Duplicate section chainage within an epoch.')
            out[key]=rec
        return out
    a,b=index(sections_a),index(sections_b)
    if not a or not b:
        raise ValueError('Supply sections for both epochs.')
    rows=[]
    for ch in sorted(set(a) | set(b)):
        _check(cancel); progress(f'Comparing fitted outlines at chainage {ch:g} m')
        first,second=a.get(ch),b.get(ch)
        if first is None or second is None or first.get('error') or second.get('error'):
            rows.append(dict(chainage=ch,error='Missing or unsupported section in one or both epochs.')); continue
        if not first.get('datum') or first['datum'] != second.get('datum'):
            raise ValueError('The two epochs require a common independently checked datum.')
        if any(k not in first or k not in second or not np.isclose(first[k], second[k], atol=1e-9,rtol=0) for k in ('width_m','band_m')):
            raise ValueError('Use the same section width and wall band for both epochs.')
        for name in ('origin','right','up','normal'):
            if not np.allclose(first['frame'][name], second['frame'][name], atol=1e-7,rtol=0):
                raise ValueError('Matched chainages use different section planes.')
        ea,eb=first['ellipse'],second['ellipse']
        shift=1000*(np.array(eb['centre_uv_m'])-np.array(ea['centre_uv_m']))
        chord={str(t):1000*(ea['diameters_m'][str(t)]-eb['diameters_m'][str(t)]) for t in ANGLES}
        rows.append(dict(chainage=ch, chord_closure_mm=chord,
                         horizontal_closure_mm=chord['0'], vertical_closure_mm=chord['90'],
                         circle_diameter_closure_mm=2000*(first['circle']['radius_m']-second['circle']['radius_m']),
                         centre_right_mm=float(shift[0]), centre_up_mm=float(shift[1])))
    return dict(rows=rows, units='mm',method='Differences of fitted central diameters in common section planes; positive closure means shortening.')
