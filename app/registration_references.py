"""Rigid motions that preserve explicitly selected measured target coordinates.

One held point leaves rotation about that point; two leave rotation about
their line; three non-collinear points leave no rigid motion. These are exact
constraints, not large residual weights. Calculations use centred coordinates.
"""
import numpy as np
from scipy.spatial.transform import Rotation
import registration_workspace as rw


def references(s, ids, requested, edges, mode):
    if not isinstance(requested, list) or not requested or len(requested) > 200:
        raise ValueError('Choose the target coordinates to keep in the Keep coordinates column.')
    fit_targets = set()
    for edge in edges:
        for match in edge.get('matches', []):
            if match.get('role', 'fit') in ('fit', 'leica'):
                fit_targets.update(((edge['a'], match['a']), (edge['b'], match['b'])))
    out, seen = [], set()
    for item in requested:
        if not isinstance(item, dict):
            raise ValueError('Invalid coordinate reference.')
        ident, name = item.get('scan_id'), item.get('target')
        if not isinstance(ident, str) or not isinstance(name, str) or ident not in ids:
            raise ValueError('A coordinate reference must belong to one of these scans.')
        key = (ident, name)
        if key in seen:
            raise ValueError('Choose each coordinate reference only once.')
        seen.add(key)
        if key not in fit_targets:
            raise ValueError('Only Fit targets can be held fixed. Check targets must remain independent.')
        scan = rw.scan(s, ident)
        row = rw.targets(scan).get(name)
        if row is None:
            raise ValueError('A coordinate reference is missing. Save target edits and choose it again.')
        out.append(dict(scan_id=ident, scan_name=scan['name'], target=name, coordinates=list(row['source'])))
    p = np.array([r['coordinates'] for r in out], float)
    if mode == 'vertical':
        if len(p) < 2 or np.linalg.norm(p[:, :2] - p[:, :2].mean(0), axis=1).max() < 1e-4:
            raise ValueError('Keep at least two horizontally separated target coordinates for keep-vertical geometry.')
    else:
        singular = np.linalg.svd(p - p.mean(0), compute_uv=False)
        if len(p) < 3 or singular[0] < 1e-5 or singular[1] / singular[0] < 1e-3:
            raise ValueError('Keep at least three non-collinear target coordinates for full 3D geometry.')
    return out


class Motion:
    def __init__(self, points, mode):
        self.mode = mode
        self.pivot = np.asarray(points[0]) if len(points) else None
        self.axis = None
        if not len(points):
            self.dof = 4 if mode == 'vertical' else 6
        elif mode == 'vertical':
            # Vertical points share a yaw axis and do not fix heading.
            self.dof = 0 if np.linalg.norm(np.asarray(points)[:, :2] - self.pivot[:2], axis=1).max() > 1e-9 else 1
        else:
            delta = np.asarray(points) - self.pivot
            lengths = np.linalg.norm(delta, axis=1)
            longest = int(lengths.argmax())
            if lengths[longest] < 1e-9:
                self.dof = 3
            else:
                self.axis = delta[longest] / lengths[longest]
                self.dof = 0 if np.linalg.norm(np.cross(delta, self.axis), axis=1).max() > 1e-9 else 1

    def transform(self, values):
        if not self.dof:
            return np.eye(3), np.zeros(3)
        if self.mode == 'vertical':
            R = Rotation.from_rotvec([0, 0, values[0]]).as_matrix()
        elif self.axis is not None:
            R = Rotation.from_rotvec(self.axis * values[0]).as_matrix()
        else:
            R = Rotation.from_rotvec(values[:3]).as_matrix()
        t = self.pivot - R @ self.pivot if self.pivot is not None else values[-3:]
        return R, t
