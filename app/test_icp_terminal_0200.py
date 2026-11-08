"""Convergence/regression checks for terminal-first cloud registration QA."""
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

import cloud_registration as icp


class TerminalValidationTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(2200)
        # Three normal families distributed in 3D constrain all rigid motions.
        self.reference = rng.uniform(-2, 2, (1200, 3))
        self.normals = np.eye(3)[np.arange(len(self.reference)) % 3]
        self.tree = cKDTree(self.reference)

    def run_icp(self, query=None, iterations=20):
        return icp._run_icp(
            self.reference.copy() if query is None else query,
            self.reference, self.normals, self.tree, iterations, .005,
            np.random.default_rng(0), lambda _: None, lambda: False)

    def worsening_broad_score(self):
        original = icp._matches
        calls = 0

        def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            rows, ids, distances, score = original(*args, **kwargs)
            return rows, ids, distances, score + calls * .001

        return changed

    def test_valid_terminal_pose_is_not_discarded_for_broad_score(self):
        offset = np.array([.007, -.004, .003])
        with patch.object(icp, '_matches', side_effect=self.worsening_broad_score()):
            result = self.run_icp(self.reference + offset)
        self.assertTrue(result['converged'], result.get('reason'))
        self.assertTrue(result['terminal_validation_passed'])
        self.assertFalse(result['best_effort_iterate_retained'])
        self.assertGreater(result['terminal_fixed_domain_score_m2'], result['best_fixed_domain_score_m2'])
        np.testing.assert_allclose(result['translation'], -offset, atol=1e-7)
        self.assertLessEqual(result['terminal_step_mm'], .25)
        self.assertEqual(result['normal_matrix_rank'], 6)
        self.assertGreaterEqual(result['inlier_count'], 500)

    def test_iteration_exhaustion_is_not_promoted_by_good_residuals(self):
        result = self.run_icp(iterations=2)
        self.assertFalse(result['converged'])
        self.assertTrue(result['terminal_validation_passed'])
        self.assertEqual(result['iteration_stop_reason'], 'maximum iterations reached')
        self.assertIn('maximum iterations reached', result['reason'])

    def test_earlier_best_effort_preserves_original_failure_reason(self):
        with patch.object(icp, '_matches', side_effect=self.worsening_broad_score()):
            result = self.run_icp(iterations=2)
        self.assertFalse(result['converged'])
        self.assertTrue(result['best_effort_iterate_retained'])
        self.assertIn('maximum iterations reached', result['reason'])
        self.assertIn('best-effort earlier iterate retained', result['reason'])

    def test_raw_terminal_increment_must_be_small(self):
        original = icp._robust_step
        baseline = self.run_icp()
        self.assertTrue(baseline['converged'])
        calls = 0

        def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            # Exact initial alignment stops once the capture gate has settled.
            # Fresh terminal evidence nevertheless demands a substantial move.
            if calls > baseline['iterations']:
                step = result[0].copy()
                step[3] = .002
                result = (step, *result[1:])
            return result

        with patch.object(icp, '_robust_step', side_effect=changed):
            result = self.run_icp()
        self.assertEqual(result['iteration_stop_reason'], 'converged')
        self.assertFalse(result['converged'])
        self.assertFalse(result['terminal_validation_passed'])
        self.assertIn('not stationary', result['reason'])

    def test_unobservable_plane_is_refused(self):
        self.normals[:] = [0., 0., 1.]
        result = self.run_icp()
        self.assertFalse(result['converged'])
        self.assertLess(result['normal_matrix_rank'], 6)
        self.assertIn('degenerate or weakly constrained', result['reason'])


if __name__ == '__main__':
    unittest.main()
