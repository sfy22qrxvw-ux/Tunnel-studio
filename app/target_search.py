# Copyright (c) 2026 Damian Gulczynski.
"""Automatic scale proposals from full-density local intensity statistics.

No printed dimensions are needed. A support scale is an analysis window, not a
measurement of the paper target. Nominations remain bounded and are not targets.
All accepted patterns must pass original-density checker evidence downstream.
"""
import numpy as np

SCALES = (.05, .08, .125, .20, .32, .50, .80, 1.25)


def nominate(p, a, channels, luminance, chunk, progress, mask=None, limit=900):
    """Visit every retained point; use cell moments, never a stride sample.

    Keep the strongest contrast cells per 2 m region and scale. Spatial quotas
    prevent nearby pipes / bright fixtures consuming the entire search budget.
    Chunk nominations deliberately overlap; patches are read independently.
    """
    raw = {}
    searched = 0
    for start in range(0, len(p), chunk):
        q = np.asarray(p[start:start+chunk])
        attrs = np.asarray(a[start:start+len(q)])
        keep = np.isfinite(q).all(1)
        if mask is not None:
            keep &= mask[start:start+len(q)] == 0
        q, attrs = q[keep], attrs[keep]
        searched += len(q)
        if not len(q):
            continue
        for ch in channels:
            v = luminance(attrs, ch)
            good = np.isfinite(v)
            qq, vv = q[good], v[good]
            if len(vv) < 20:
                continue
            # A tiny floor excludes quantisation-only cells but does not impose
            # the old 2% absolute intensity threshold on a low-contrast target.
            floor = max(1e-12, float(np.ptp(vv))*1e-5)
            channel_span=max(float(np.percentile(vv,99)-np.percentile(vv,1)),floor)
            for d in SCALES:
                size = d*.42
                cells = np.floor(qq/size).astype(np.int64)
                base = cells.min(0); dims = cells.max(0)-base+1
                if np.prod(dims.astype(float)) >= 2**62:
                    continue
                key = ((cells[:,0]-base[0])*dims[1]+cells[:,1]-base[1])*dims[2]+cells[:,2]-base[2]
                _, inv = np.unique(key, return_inverse=True)
                count = np.bincount(inv)
                mean = np.bincount(inv, weights=vv)/count
                var = np.maximum(0., np.bincount(inv, weights=vv*vv)/count-mean*mean)
                lower = np.full(len(count), np.inf); upper = -lower.copy()
                np.minimum.at(lower, inv, vv); np.maximum.at(upper, inv, vv)
                span = upper-lower
                # Both dark and bright populations must be present. Random
                # texture can nominate, but will not pass four-quadrant checks.
                eligible = np.flatnonzero((count >= 8) & (span > floor) & (var > .045*span*span))
                if not len(eligible):
                    continue
                centres = np.column_stack([np.bincount(inv, weights=qq[:,k])/count for k in range(3)])
                # Contrast is ranked locally, not against the whole-scan range.
                strength = np.sqrt(var[eligible])
                order = eligible[np.argsort(strength)[::-1]]
                quotas = {}
                for cell in order:
                    centre = centres[cell]
                    region = tuple(np.floor(centre/2).astype(int))
                    if quotas.get(region, 0) >= 2:
                        continue
                    quotas[region] = quotas.get(region, 0)+1
                    token = (ch, d, *np.floor(centre/size).astype(int))
                    proposal = (float(np.sqrt(var[cell])/channel_span), centre.copy(), ch, d, region)
                    if token not in raw or proposal[0] > raw[token][0]:
                        raw[token] = proposal
        progress(f'Searching local intensity / colour at automatic scales: {min(start+chunk,len(p)):,} / {len(p):,} source points')
    # Round-robin region / channel / scale groups. Never silently claim exhaustive
    # fitting when the bounded search has more nominations than can be tested.
    groups = {}
    for entry in raw.values():
        groups.setdefault((entry[4], entry[2], entry[3]), []).append(entry)
    for group in groups.values():
        group.sort(key=lambda x: x[0], reverse=True)
    ordered = sorted(groups.values(), key=lambda g: g[0][0], reverse=True)
    out = []
    for rank in range(max((len(g) for g in ordered), default=0)):
        for group in ordered:
            if rank >= len(group):
                continue
            entry = group[rank]
            if any(abs(np.log(entry[3]/old[3])) < .25 and entry[2] == old[2]
                   and np.linalg.norm(entry[1]-old[1]) < entry[3]*.35 for old in out):
                continue
            out.append(entry)
            if len(out) >= limit:
                return out, searched, len(raw) > len(out)
    return out, searched, False


def quick_pattern(points, values, seed, scale):
    """Cheap spatially balanced local checker screen before expensive refinement.

    Search crossing locations/orientations in the fitted plane. Adaptive local
    contrast normalisation preserves weak patterns without changing raw values.
    This stage cannot accept a target on its own.
    """
    q = np.asarray(points, float); v = np.asarray(values, float)
    keep = np.isfinite(v) & np.isfinite(q).all(1) & (np.linalg.norm(q-seed,axis=1) < scale*.95)
    q, v = q[keep], v[keep]
    if len(q) < 25:
        return None
    origin = np.median(q, axis=0)
    _, _, axes = np.linalg.svd(q-origin, full_matrices=False)
    keep = np.abs((q-origin)@axes[2]) < max(.004, scale*.05)
    q, v = q[keep], v[keep]
    if len(q) < 25:
        return None
    uv = (q-origin)@axes[:2].T
    # Equal occupied cell weighting, bounded to a few thousand observations.
    cells = np.floor(uv/(scale/30)).astype(int)
    _, inv = np.unique(cells, axis=0, return_inverse=True)
    counts = np.bincount(inv)
    xy = np.column_stack([np.bincount(inv, weights=uv[:,i])/counts for i in range(2)])
    z = np.bincount(inv, weights=v)/counts
    low, high = np.percentile(z, [8,92])
    if high-low <= max(1e-12, abs(high)*1e-8):
        return None
    z = np.clip(2*(z-low)/(high-low)-1, -1, 1)
    guess = (np.asarray(seed)-origin)@axes[:2].T
    best = None
    for dx in np.linspace(-.32,.32,5)*scale:
        for dy in np.linspace(-.32,.32,5)*scale:
            centre = guess+[dx,dy]
            delta = xy-centre
            use = np.sum(delta*delta,axis=1) < (scale*.43)**2
            if use.sum() < 16:
                continue
            x,y = delta[use].T; zz = z[use]
            for angle in np.linspace(0,np.pi/2,12,endpoint=False):
                u = x*np.cos(angle)+y*np.sin(angle)
                w = -x*np.sin(angle)+y*np.cos(angle)
                quad = (u>=0).astype(int)*2+(w>=0).astype(int)
                n = np.bincount(quad,minlength=4)
                if n.min() < 3:
                    continue
                means = np.bincount(quad,weights=zz,minlength=4)/n
                score = abs(float(means@np.array([1,-1,-1,1])/4))
                edge = max(abs(float(means@np.array([-1,-1,1,1])/4)),abs(float(means@np.array([-1,1,-1,1])/4)))
                quality = score-edge
                if quality > .18 and score > .40 and (best is None or quality>best[0]):
                    best = (quality, origin+centre@axes[:2], float(angle))
    return best
