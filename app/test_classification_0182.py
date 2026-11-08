# Copyright (c) 2026 Damian Gulczynski.
"""TMatch data-integrity and locally supported ring regression checks.

Generated geometry tests clustering and failure behavior. They do not measure
physical segment recognition or accuracy on scanner data.
"""
from pathlib import Path
import hashlib
import tempfile
import unittest

import numpy as np
from scipy.spatial import cKDTree

import tmatch


def _band(index, start, width=1., bits=(1 << 36) - 1):
    return dict(id=index, cleaning_class=0, station_m=start + width / 2,
                station_min_m=start, station_max_m=start + width,
                points=100, angular_support_bits=bits, ring_id=0)


def _stream(points, ids=None, block=211):
    ids = np.arange(len(points), dtype=np.int64) + 10000000 if ids is None else np.asarray(ids)
    def batches():
        for start in range(0, len(points), block):
            yield ids[start:start + block], points[start:start + block], None
    return batches


def _coordinates(points, model):
    local = points - model.get('origin', np.zeros(3))
    return {'s': local[:, 0], 'angle': np.mod(np.arctan2(local[:, 2], local[:, 1]), 2 * np.pi)}


def _classify(points, folder, settings=None, ids=None, classifier=None,
              progress=lambda _: None, cancel=lambda: False, model=None):
    folder = Path(folder)
    ip, lp = folder / 'output.ids', folder / 'output.labels'
    result = tmatch._cluster_batches(
        _stream(points, ids), folder, ip, lp, settings or {}, progress, cancel,
        len(points), model or {'seed': {'axis': [1., 0., 0.]}},
        classifier or (lambda p, m: np.zeros(len(p), dtype=np.int32)), _coordinates)
    return result, np.fromfile(ip, '<i8'), np.fromfile(lp, '<i4')


class Classification0182Tests(unittest.TestCase):
    def test_partial_band_does_not_suppress_other_supported_rings(self):
        rows = [_band(1, 0), _band(2, 1.04, bits=3), _band(3, 2.08)]
        rings, note = tmatch.group_rings(rows, .015)
        self.assertEqual([r['id'] for r in rings], [1, 3])
        self.assertEqual([r['ring_id'] for r in rows], [1, 0, 3])
        self.assertIn('circumferential', rows[1]['ring_withheld_reason'])
        self.assertIn('2 independently supported', note)

    def test_width_mismatch_is_local_and_does_not_move_boundaries(self):
        rows = [_band(1, 0), _band(2, 1.04), _band(3, 2.08, width=.45)]
        rings, _ = tmatch.group_rings(rows, .015, 1.)
        self.assertEqual([r['ring_id'] for r in rows], [1, 2, 0])
        self.assertEqual([(r['station_min_m'], r['station_max_m']) for r in rings],
                         [(0., 1.), (1.04, 2.04)])
        self.assertIn('width', rows[2]['ring_withheld_reason'])

    def test_overlapping_bands_withheld_locally(self):
        rows = [_band(1, 0), _band(2, 1.04), _band(3, 3), _band(4, 3.9)]
        rings, _ = tmatch.group_rings(rows, .015)
        self.assertEqual([r['ring_id'] for r in rows], [1, 2, 0, 0])
        self.assertEqual(len(rings), 2)
        self.assertIn('overlap', rows[2]['ring_withheld_reason'])
        self.assertIn('overlap', rows[3]['ring_withheld_reason'])

    def test_one_supported_band_is_not_a_ring_layout_and_stale_confidence_clears(self):
        rows = [_band(1, 0), _band(2, 1.04, bits=3)]
        for row in rows:
            row['ring_id'] = 9
            row['ring_confidence'] = 1.
        rings, _ = tmatch.group_rings(rows, .015)
        self.assertEqual(rings, [])
        self.assertEqual([r['ring_id'] for r in rows], [0, 0])
        self.assertTrue(all('ring_confidence' not in r for r in rows))

    def test_full_point_labels_preserve_ids_and_supported_rings_across_partial_band(self):
        # Two actual separated half-shell components per complete band. The
        # middle band contains only a small patch; it must remain unassigned.
        angular = np.r_[np.arange(.08, np.pi - .08, .04),
                        np.arange(np.pi + .08, 2 * np.pi - .08, .04)]
        patches = []
        truth = []
        for band in range(3):
            angles = angular if band != 1 else np.arange(.08, .55, .04)
            s, a = np.meshgrid(np.linspace(0, 1, 11) + band * 1.25, angles, indexing='ij')
            patches.append(np.c_[s.ravel(), 3 * np.cos(a.ravel()), 3 * np.sin(a.ravel())])
            truth.extend([band] * s.size)
        points = np.concatenate(patches)
        # Distinct measurements at duplicate XYZ must keep distinct identities.
        points = np.r_[points, points[:7]]
        truth = np.r_[truth, np.zeros(7, dtype=int)]
        order = np.random.default_rng(182).permutation(len(points))
        points = points[order] + [4400000., 500000., 280000.]
        truth = truth[order]
        ids = np.arange(len(points), dtype=np.int64)[order] + 20000000000
        before = hashlib.sha256(points.tobytes()).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            result, written_ids, labels = _classify(
                points, folder, {'tmatch_radius_mm': 200., 'tmatch_min_points': 30}, ids,
                model={'seed': {'axis': [1., 0., 0.]},
                       'origin': np.array([4400000., 500000., 280000.])})
        np.testing.assert_array_equal(written_ids, ids)
        self.assertEqual(len(labels), len(points))
        by_id = {row['id']: row for row in result['clusters']}
        ring_ids = np.array([by_id[int(label)]['ring_id'] for label in labels])
        np.testing.assert_array_equal(ring_ids, np.where(truth == 0, 1, np.where(truth == 2, 3, 0)))
        self.assertEqual([r['id'] for r in result['rings']], [1, 3])
        self.assertTrue(all(r['plane_normal_supported'] for r in result['rings']))
        self.assertEqual(sum(r['points'] for r in result['clusters']), len(points))
        self.assertEqual(result['statistics']['input_points'], len(points))
        self.assertEqual(hashlib.sha256(points.tobytes()).hexdigest(), before)

    def test_exact_radius_components_match_bruteforce_across_batches(self):
        rng = np.random.default_rng(17)
        points = np.r_[rng.normal(0, .013, (80, 3)),
                       rng.normal(0, .013, (80, 3)) + [.2, 0, 0],
                       [[1., 2., 3.]] * 4]
        radius = .015
        parent = np.arange(len(points))
        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a, b in cKDTree(points).query_pairs(radius + 1e-9):
            parent[root(b)] = root(a)
        expected = np.array([root(i) for i in range(len(points))])
        with tempfile.TemporaryDirectory() as folder:
            result, _, labels = _classify(points, folder, {'tmatch_min_points': 3})
        for i in range(len(points)):
            same = np.flatnonzero(expected == expected[i])
            if len(same) < 3:
                self.assertEqual(labels[i], 0)
            else:
                self.assertGreater(labels[i], 0)
                np.testing.assert_array_equal(np.flatnonzero(labels == labels[i]), same)
        self.assertEqual(result['statistics']['kept_points'], len(points))

    def test_no_components_and_connected_shell_have_explicit_status(self):
        points = np.c_[np.arange(80) / 128, np.zeros((80, 2))]
        with tempfile.TemporaryDirectory() as folder:
            result, _, _ = _classify(points, folder)
            self.assertEqual(result['classification']['status'], 'single_connected_component')
            self.assertIn('physical segments', result['classification']['summary'])
        with tempfile.TemporaryDirectory() as folder:
            result, _, labels = _classify(points, folder,
                classifier=lambda p, m: np.full(len(p), 3, np.int32))
            self.assertEqual(result['classification']['status'], 'no_supported_components')
            self.assertEqual(result['classification']['unresolved_lining_points'], len(points))
            self.assertEqual(result['statistics']['kept_points'], len(points))
            self.assertTrue(all(row['automatic_keep'] for row in result['clusters']))
            self.assertTrue(np.all(labels > 0))

    def test_cancel_and_invalid_ids_preserve_previous_files(self):
        points = np.c_[np.arange(80) / 128, np.zeros((80, 2))]
        for invalid_ids in (np.arange(len(points), dtype=float) + .5,
                            np.arange(len(points), dtype=np.int64) - 1):
            with tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                ip, lp = folder / 'output.ids', folder / 'output.labels'
                ip.write_bytes(b'previous point identities')
                lp.write_bytes(b'previous labels')
                with self.assertRaisesRegex(ValueError, 'identifiers'):
                    _classify(points, folder, ids=invalid_ids)
                self.assertEqual(ip.read_bytes(), b'previous point identities')
                self.assertEqual(lp.read_bytes(), b'previous labels')
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            ip, lp = folder / 'output.ids', folder / 'output.labels'
            ip.write_bytes(b'previous point identities')
            lp.write_bytes(b'previous labels')
            state = {'cancel': False}
            def progress(message):
                if message.startswith('Connecting lining cells'):
                    state['cancel'] = True
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                _classify(points, folder, progress=progress, cancel=lambda: state['cancel'])
            self.assertEqual(ip.read_bytes(), b'previous point identities')
            self.assertEqual(lp.read_bytes(), b'previous labels')
            self.assertFalse(list(folder.glob('tmatch_*')))


if __name__ == '__main__':
    unittest.main()
