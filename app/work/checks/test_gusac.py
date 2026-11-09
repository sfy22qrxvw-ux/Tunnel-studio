"""Synthetic correctness checks, NOT validation on scanned tunnels.

Run from the app directory: python work/checks/test_gusac.py
Writes an explicitly synthetic report beside this test file.
"""
from pathlib import Path
import json
import sys
import tempfile
import time
import unittest

import numpy as np

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
import gusac

RESULTS = []


def tunnel(curvature=0., sparse=False, objects=80, joints=False):
    rng = np.random.default_rng(12)
    s = np.linspace(-5, 5, 101)
    angle = np.linspace(0, 2*np.pi, 360, endpoint=False)
    ss, aa = np.meshgrid(s, angle, indexing='ij')
    ss = ss.ravel(); aa = aa.ravel()
    if sparse:
        # Sparse original lining returns remain behind the foreground service.
        affected = np.abs(np.arctan2(np.sin(aa), np.cos(aa))) < .24
        use = ~affected | ((np.arange(len(ss)) % 11) == 0)
        ss = ss[use]; aa = aa[use]
    radius = np.full(len(ss), 3.25)
    joint = (np.abs(ss-1) < .06) if joints else np.zeros(len(ss), bool)
    radius[joint] += .045
    wall = np.c_[ss, curvature*ss**2+radius*np.cos(aa), radius*np.sin(aa)]
    wall += rng.normal(0, .001, wall.shape)
    st, at = np.meshgrid(np.linspace(-4.9, 4.9, 101),
                         np.linspace(0, 2*np.pi, objects, endpoint=False), indexing='ij')
    st = st.ravel(); at = at.ravel()
    pipe = np.c_[st, curvature*st**2+2.25+.30*np.cos(at), .30*np.sin(at)]
    return np.r_[wall, pipe], len(wall), joint


def batches(points, order=None, block=9000, id_offset=0):
    order = np.arange(len(points)) if order is None else np.asarray(order)
    def stream():
        for start in range(0, len(order), block):
            ix = order[start:start+block]
            yield np.asarray(ix+id_offset, dtype=np.int64), points[ix], None
    return stream


def measured(name, points, wall_count, model=None):
    started = time.monotonic()
    if model is None:
        model = gusac.fit_batches(batches(points), {})
    labels = gusac.classify(points, model)
    wall_recall = float(np.isin(labels[:wall_count], gusac.KEEP_CLASSES).mean())
    obstruction_removal = float((labels[wall_count:] == 1).mean())
    RESULTS.append(dict(case=name, source='synthetic_generated_geometry',
                        points=len(points), wall_points=wall_count,
                        lining_retained_fraction=wall_recall,
                        obstruction_candidate_fraction=obstruction_removal,
                        class_counts=np.bincount(labels, minlength=5).tolist(),
                        regional_planes=len(model['patches']),
                        seconds=round(time.monotonic()-started, 3)))
    return labels, model


class GuSACTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.points, cls.wall, _ = tunnel()
        cls.labels, cls.model = measured('straight_cylinder_and_pipe', cls.points, cls.wall)

    def test_01_original_lining_and_pipe(self):
        self.assertTrue(np.isin(self.labels[:self.wall], gusac.KEEP_CLASSES).all())
        self.assertGreater(float((self.labels[self.wall:] == 1).mean()), .97)
        self.assertGreater(len(self.model['patches']), 100)

    def test_02_sparse_wall_and_overrepresented_service(self):
        points, wall, _ = tunnel(sparse=True, objects=640)
        labels, _ = measured('sparse_lining_behind_dense_service', points, wall)
        self.assertGreater(float(np.isin(labels[:wall], gusac.KEEP_CLASSES).mean()), .995)
        self.assertGreater(float((labels[wall:] == 1).mean()), .95)

    def test_03_curved_branch(self):
        points, wall, _ = tunnel(curvature=.012)
        labels, _ = measured('gently_curved_branch', points, wall)
        self.assertGreater(float(np.isin(labels[:wall], gusac.KEEP_CLASSES).mean()), .995)
        self.assertGreater(float((labels[wall:] == 1).mean()), .95)

    def test_04_joints_remain_original_points(self):
        points, wall, joint = tunnel(joints=True)
        labels, _ = measured('outward_joint_recesses', points, wall)
        self.assertTrue(np.isin(labels[:wall][joint], gusac.KEEP_CLASSES).all())
        self.assertGreater(int((labels[:wall][joint] == 2).sum()), 100)

    def test_05_shuffle_and_chunk_boundaries(self):
        order = np.random.default_rng(33).permutation(len(self.points))
        model = gusac.fit_batches(batches(self.points, order, block=7333), {})
        labels = gusac.classify(self.points, model)
        np.testing.assert_array_equal(labels, self.labels)
        # Classifying arbitrary chunks must not change patch ownership.
        joined = np.concatenate([gusac.classify(self.points[i:i+1133], self.model)
                                 for i in range(0, len(self.points), 1133)])
        np.testing.assert_array_equal(joined, self.labels)

    def test_06_duplicate_positions_keep_distinct_ids(self):
        points = np.r_[self.points, self.points[:200], self.points[self.wall:self.wall+200]]
        with tempfile.TemporaryDirectory() as tmp:
            before = points.copy(); ids_path = Path(tmp)/'result.ids'; labels_path = Path(tmp)/'result.labels'
            result = gusac.run_batches(batches(points, block=7222, id_offset=1000000), tmp,
                                       ids_path, labels_path, {}, lambda _: None, lambda: False, len(points))
            ids = np.fromfile(ids_path, '<i8'); labels = np.fromfile(labels_path, '<i4')
            np.testing.assert_array_equal(ids, np.arange(len(points))+1000000)
            self.assertEqual(len(labels), len(points))
            self.assertEqual(len(np.unique(ids)), len(points))
            np.testing.assert_array_equal(points, before)
            np.testing.assert_array_equal(labels[-400:-200], labels[:200])
            np.testing.assert_array_equal(labels[-200:], labels[self.wall:self.wall+200])
            self.assertEqual(result['statistics']['input_points'], len(points))

    def test_07_large_survey_coordinate_offset(self):
        shifted = self.points+np.array([4400000., 500000., 280000.])
        labels, _ = measured('large_coordinate_offset', shifted, self.wall)
        self.assertTrue(np.isin(labels[:self.wall], gusac.KEEP_CLASSES).all())
        self.assertGreater(float((labels[self.wall:] == 1).mean()), .97)
        self.assertGreater(float((labels == self.labels).mean()), .995)

    def test_08_cancel_preserves_previous_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = Path(tmp)/'previous.ids'; labels = Path(tmp)/'previous.labels'
            ids.write_bytes(b'previous ids'); labels.write_bytes(b'previous labels')
            state = {'cancelled': False}
            def progress(message):
                if message.startswith('GuSAC · checking original points'):
                    state['cancelled'] = True
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                gusac.run_batches(batches(self.points), tmp, ids, labels, {}, progress,
                                   lambda: state['cancelled'], len(self.points))
            self.assertEqual(ids.read_bytes(), b'previous ids')
            self.assertEqual(labels.read_bytes(), b'previous labels')
            self.assertFalse(list(Path(tmp).glob('gusac_*')))

    def test_09_invalid_inputs_do_not_produce_outputs(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            gusac.classify(np.array([[np.nan, 0., 1.]]), self.model)
        with self.assertRaisesRegex(ValueError, 'between'):
            gusac.fit_batches(batches(self.points), {'gusac_sector_deg': 0})
        with self.assertRaisesRegex(ValueError, 'Too little'):
            gusac.fit_batches(batches(self.points[:10]), {})


if __name__ == '__main__':
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuSACTests)
    run = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(kind='synthetic_only', paper_accuracy_reproduced=False,
                  real_scan_validation=False,
                  limitation='Generated circular/gently curved geometry does not validate all services, irregular profiles, heavy occlusion, runtime on billion-point clouds or real scanner data.',
                  tests_run=run.testsRun, failures=len(run.failures), errors=len(run.errors),
                  numpy_version=np.__version__, seconds=round(time.monotonic()-started, 3),
                  cases=RESULTS)
    target = Path(__file__).with_name('gusac_synthetic_report.json')
    target.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('Synthetic report:', target)
    sys.exit(0 if run.wasSuccessful() else 1)
