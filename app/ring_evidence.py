"""Circumferential seams from directional, measured wall evidence.

Width is deliberately absent from detection: spacing is a subsequent check,
never a source of synthetic joints. All arrays are bounded by the lining tile.
"""
import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import find_peaks


def supported_sequence(rows, settings):
    """Choose only a unique longest measured sequence of compatible joints.

    Short fragments cannot displace a longer consistent sequence. Equally
    long alternatives remain unresolved; no coordinate or seam is invented.
    """
    from segment_panels import ring_width_matches
    if not settings.get('lining_ring_width_m', 0.):
        return rows, []
    if len(rows) < 2:
        return [], [dict(row, unresolved_reason='No adjacent measured joint supports the configured ring width') for row in rows]
    # Separate surveyed fragments must not compete merely because one has
    # more rings. A missing interval splits the sequence into independent runs.
    limit = settings['lining_ring_width_m'] + settings.get('lining_ring_tolerance_mm', 100.)/1000.
    breaks = [i for i in range(1, len(rows)) if rows[i]['station_m']-rows[i-1]['station_m'] > limit]
    if breaks:
        kept, unresolved = [], []
        for start, stop in zip([0]+breaks, breaks+[len(rows)]):
            accepted, unknown = supported_sequence(rows[start:stop], settings)
            kept.extend(accepted); unresolved.extend(unknown)
        return kept, unresolved
    lengths = [1]*len(rows)
    ways = [1]*len(rows)
    previous = [None]*len(rows)
    for end in range(len(rows)):
        for start in range(end):
            if not ring_width_matches(settings, rows[end]['station_m']-rows[start]['station_m']):
                continue
            length = lengths[start]+1
            if length > lengths[end]:
                lengths[end], ways[end], previous[end] = length, ways[start], start
            elif length == lengths[end]:
                ways[end] = min(2, ways[end]+ways[start])
    longest = max(lengths)
    if longest < 2:
        return [], [dict(row, unresolved_reason='No adjacent measured joint supports the configured ring width') for row in rows]
    ends = [i for i, length in enumerate(lengths) if length == longest]
    if sum(ways[i] for i in ends) != 1:
        return [], [dict(row, unresolved_reason='Competing equally long measured ring sequences') for row in rows]
    selected = set()
    end = ends[0]
    while end is not None:
        selected.add(end)
        end = previous[end]
    return ([row for i, row in enumerate(rows) if i in selected],
            [dict(row, unresolved_reason='Outside the unique longer width-consistent measured sequence')
             for i, row in enumerate(rows) if i not in selected])


def candidates(detector):
    d = detector
    valid = d.count > 0
    step = max(2, round(.10 / d.cell))
    axial = np.zeros(d.shape, bool)
    threshold = d.s.get('lining_seam_depth_mm', 4.) / 1000.
    if d.nt > 2 * step:
        middle = d.depth[step:-step]
        axial[step:-step] = (valid[:-2*step] & valid[2*step:]
                            & (middle > d.depth[:-2*step] + threshold)
                            & (middle > d.depth[2*step:] + threshold))
    return ((d.seam_geom & axial) | d.ring_dark | d.ring_bright) & (d.grid_labels == 0), step


def fit(detector):
    from lining_features import ridge_contrast
    d = detector
    d.unresolved_ring_candidates = []
    candidate, flank = candidates(d)
    ii, jj = np.where(candidate)
    if len(ii) < 12:
        return []
    t = d.t0 + (ii + .5) * d.cell
    a = (jj + .5) * 2 * np.pi / d.na
    # Angular averages retain continuity: one bolt must not vote as strongly
    # as a seam traversing every measured column in an angular bin.
    bins = min(180, d.na)
    votes = np.zeros((d.nt, bins))
    for j in range(bins):
        votes[:, j] = candidate[:, j*d.na//bins:(j+1)*d.na//bins].mean(1)
    band = max(.025, 1.5*d.cell)
    votes = maximum_filter1d(votes, max(3, 2*int(band/d.cell)+1), axis=0, mode='constant')
    angles = (np.arange(bins)+.5)*2*np.pi/bins
    rows = np.arange(d.nt)[:, None]
    columns = np.arange(bins)[None, :]
    best = np.zeros(d.nt)
    tilts = np.zeros((d.nt, 2))
    # A bounded tilted-plane accumulator sees the entire circumferential
    # trace; a narrow station seed can cut off most of a real tilted joint.
    for b in np.linspace(-.45, .45, 31):
        d.emit('4/5 Following measured circumferential joint traces')
        for c in np.linspace(-.45, .45, 31):
            if np.hypot(b, c) > .45:
                continue
            at = rows + np.rint((b*np.cos(angles)+c*np.sin(angles))/d.cell).astype(int)
            inside = (at >= 0) & (at < d.nt)
            score = (votes[np.clip(at, 0, d.nt-1), columns]*inside).mean(1)
            better = score > best
            best[better] = score[better]
            tilts[better] = [b, c]
    peaks, _ = find_peaks(best, height=.05, prominence=.02,
                         distance=max(1, round(.35/d.cell)))
    X = np.c_[np.ones(len(a)), np.cos(a), np.sin(a)]
    full_angles = (np.arange(d.na)+.5)*2*np.pi/d.na
    valid = d.count > 0
    found = []
    for ix in peaks[np.argsort(best[peaks])[::-1]]:
        coef = np.r_[d.t0+(ix+.5)*d.cell, tilts[ix]]
        for _ in range(3):
            hit = abs(t-X@coef) <= band
            if hit.sum() < 12:
                break
            coef = np.linalg.lstsq(X[hit], t[hit], rcond=None)[0]
        residual = t-X@coef
        hit = abs(residual) <= band
        if hit.sum() < 12 or np.linalg.norm(coef[1:]) > .45:
            continue
        covered = np.unique((a[hit]*36/(2*np.pi)).astype(int))
        at = np.rint((coef[0]+coef[1]*np.cos(full_angles)+coef[2]*np.sin(full_angles)-d.t0)/d.cell-.5).astype(int)
        columns = np.flatnonzero((at >= flank) & (at < d.nt-flank))
        columns = columns[valid[at[columns]-flank, columns] & valid[at[columns]+flank, columns]]
        observed = np.unique(columns*36//d.na)
        coverage = len(covered)/36.
        observed_fraction = len(np.intersect1d(covered, observed))/max(1, len(observed))
        column_fraction = len(np.intersect1d(np.unique(jj[hit]), columns))/max(1, len(columns))
        contrast = ridge_contrast(residual, band)
        if coverage < .25 or observed_fraction < .5 or column_fraction < .35 or contrast < 2.:
            continue
        if any(abs(coef[0]-row['station_m']) < .35 for row in found):
            continue
        found.append(dict(coefficients=coef.tolist(), station_m=float(coef[0]),
                          coverage_fraction=coverage,
                          observed_coverage_fraction=observed_fraction,
                          observed_column_support_fraction=column_fraction,
                          observed_circumference_fraction=len(observed)/36.,
                          fit_rms_m=float(np.sqrt(np.mean(residual[hit]**2))),
                          support_cells=int(hit.sum()), ridge_contrast=float(contrast)))
    ordered = sorted(found, key=lambda row: row['station_m'])
    if getattr(d, 'defer_ring_sequence', False):
        return ordered
    resolved, d.unresolved_ring_candidates = supported_sequence(ordered, d.s)
    return resolved
