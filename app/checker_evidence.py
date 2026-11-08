# Copyright (c) 2026 Damian Gulczynski.
"""Streaming evidence for four-quadrant checker suggestions, not accuracy estimates."""
import numpy as np


class CheckerEvidence:
    """Equal spatial-cell weighting prevents dense quadrants dominating a score.

    A fixed fitted frame is checked against every available finite source point
    in a disc of radius 0.46 times the entered printed size. Thin strips at the
    two transition axes are excluded. Values are normalized using the fit's
    robust intensity range; no per-quadrant contrast normalization is performed.
    """
    def __init__(self, frame):
        self.frame = frame
        self.count = np.zeros(64, dtype=np.int64)
        self.sums = np.zeros(64)
        self.matches = np.zeros(64, dtype=np.int64)
        self.plane_sq = 0.
        self.plane_count = 0
        self.disc_count = 0
        self.quadrant_min = np.full((4, 2), np.inf)
        self.quadrant_max = np.full((4, 2), -np.inf)

    def add(self, points, values):
        p, v = np.asarray(points), np.asarray(values)
        valid = np.isfinite(p).all(axis=1) & np.isfinite(v)
        p, v = p[valid], v[valid]
        if not len(p):
            return
        f = self.frame
        delta = p - f['centre']
        d = f['diameter']
        u = delta @ np.asarray(f['u']) / d
        w = delta @ np.asarray(f['w']) / d
        residual = delta @ np.asarray(f['normal'])
        disc = (u*u + w*w) <= .46**2
        self.disc_count += int(disc.sum())
        keep = disc & (np.abs(residual) <= f['plane_limit'])
        self.plane_sq += float(np.sum(residual[keep]**2))
        self.plane_count += int(keep.sum())
        keep &= (np.abs(u) >= .025) & (np.abs(w) >= .025)
        if not keep.any():
            return
        u, w, v = u[keep], w[keep], v[keep]
        z = 2*np.clip((v-f['low'])/(f['high']-f['low']), 0, 1)-1
        ids = np.clip(((u+.5)*8).astype(int), 0, 7)*8 + np.clip(((w+.5)*8).astype(int), 0, 7)
        expected = np.sign(u*w)*f['polarity']
        self.count += np.bincount(ids, minlength=64)
        self.sums += np.bincount(ids, weights=z, minlength=64)
        self.matches += np.bincount(ids[z*expected > .1], minlength=64)
        quadrant = (u >= 0).astype(int)*2 + (w >= 0).astype(int)
        for k in range(4):
            xy = np.column_stack([u[quadrant == k], w[quadrant == k]])
            if len(xy):
                self.quadrant_min[k] = np.minimum(self.quadrant_min[k], xy.min(0))
                self.quadrant_max[k] = np.maximum(self.quadrant_max[k], xy.max(0))

    def finish(self):
        ix, iy = np.indices((8, 8)).reshape(2, -1)
        quad = (ix >= 4).astype(int)*2 + (iy >= 4).astype(int)
        sign = np.where((ix >= 4) == (iy >= 4), 1., -1.) * self.frame['polarity']
        occupied = self.count > 0
        means = self.sums / np.maximum(self.count, 1)
        fractions = self.matches / np.maximum(self.count, 1)
        points, cells, signed, agreement = [], [], [], []
        for k in range(4):
            use = occupied & (quad == k)
            points.append(int(self.count[quad == k].sum()))
            cells.append(int(use.sum()))
            signed.append(float(np.mean(means[use]*sign[use])) if use.any() else -1.)
            agreement.append(float(np.mean(fractions[use])) if use.any() else 0.)
        score = float(np.mean(signed))
        # Equal quadrant weighting for both the checker and competing edge models.
        stripes = []
        for edge_sign in (np.where(ix >= 4, 1., -1.), np.where(iy >= 4, 1., -1.), np.ones(64)):
            stripes.append(abs(float(np.mean([
                np.mean(means[occupied & (quad == k)]*edge_sign[occupied & (quad == k)])
                if np.any(occupied & (quad == k)) else 0. for k in range(4)]))))
        # Edges may have any orientation and need not pass through the fitted
        # crossing. Testing only the two fitted axes let diagonal seams mimic
        # a checker when only small portions of the quadrants were observed.
        weight=np.zeros(64)
        for k in range(4):
            use=occupied & (quad==k)
            if use.any():weight[use]=1/(4*use.sum())
        for angle in np.linspace(0,np.pi,24,endpoint=False):
            position=(ix-3.5)*np.cos(angle)+(iy-3.5)*np.sin(angle)
            for offset in (-2,-1,0,1,2):
                stripes.append(abs(float(np.sum(means*weight*np.where(position>=offset,1.,-1.)))))
        advantage = score - max(stripes)
        spread = self.quadrant_max - self.quadrant_min
        supported = (min(points) >= 5 and min(cells) >= 3 and np.all(spread >= .14))
        planar_fraction=self.plane_count/max(1,self.disc_count)
        accepted = bool(supported and planar_fraction>=.65 and min(signed) >= .25 and min(agreement) >= .68
                        and score >= .55 and advantage >= .20)
        result = {'score': score, 'quadrant_points': points, 'quadrant_cells': cells,
                  'quadrant_contrast': signed, 'quadrant_agreement': agreement,
                  'edge_advantage': advantage, 'validation_points': int(self.count.sum()),
                  'planar_fraction':float(planar_fraction),
                  'plane_rms_mm': float(np.sqrt(self.plane_sq/max(1, self.plane_count))*1000),
                  'four_quadrants_supported': bool(supported),
                  'validation_passed': accepted}
        return result


def require_checker(evidence):
    result = evidence.finish()
    if not result['validation_passed']:
        raise ValueError('The patch does not show a supported alternating pattern in all four quadrants. '
                         'Stripes, shadows and partial targets are not accepted automatically. '
                         'Check printed size and intensity/RGB, or enter an independently measured centre.')
    return result


def priority_keys(indices):
    """Deterministic uniform priorities: later chunks can replace earlier records."""
    x = np.asarray(indices, dtype=np.uint64) + np.uint64(0x9E3779B97F4A7C15)
    x = (x ^ (x >> 30)) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> 27)) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> 31)


class PatchSample:
    def __init__(self, limit):
        self.limit = int(limit)
        self.available = 0
        self.points = np.empty((0, 3))
        self.attributes = np.empty((0, 4))
        self.keys = np.empty(0, dtype=np.uint64)

    def add(self, points, attributes, indices):
        self.available += len(points)
        keys = priority_keys(indices)
        if len(keys) > self.limit:
            take = np.argpartition(keys, self.limit-1)[:self.limit]
            points, attributes, keys = points[take], attributes[take], keys[take]
        p = np.concatenate([self.points, points])
        a = np.concatenate([self.attributes, attributes])
        k = np.concatenate([self.keys, keys])
        if len(k) > self.limit:
            take = np.argpartition(k, self.limit-1)[:self.limit]
            p, a, k = p[take], a[take], k[take]
        self.points, self.attributes, self.keys = p, a, k
