"""Finite-budget fallback, datum preservation and atomic acceptance checks."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from scipy.sparse import eye, csr_matrix
from scipy.spatial import cKDTree

import bundle_adjustment as bundle
import bundle_workflow as workflow
import registration_network as network
import registration_workspace as rw
import registration_pose as pose
import registered_cloud
from test_registration_transaction_0186 import Server, add, P


class BundleBudgetTests(unittest.TestCase):
    def fixture(self):
        points = {'a': P, 'b': P + [.02, -.01, .03]}
        nodes = {ident: dict(points=p, normals=None, tree=None) for ident, p in points.items()}
        edges = [dict(id='ab', a='a', b='b', mode='rigid', has_targets=True, use_cloud=False)]
        centres = {ident: p.mean(axis=0) for ident, p in points.items()}
        layout = network._MixedMotions(set(points), edges, 'a', centres)
        landmarks = [[(ident, p[index]) for ident, p in points.items()] for index in range(len(P))]
        return nodes, edges, landmarks, layout

    def test_finite_observable_budget_exhaustion_has_distinct_failure(self):
        def exhausted(fun, values, **kwargs):
            self.assertEqual(kwargs['max_nfev'], 500)
            for name in ('ftol', 'xtol', 'gtol'):
                self.assertEqual(kwargs[name], 1e-8)
            residual = fun(values)
            return SimpleNamespace(success=False, status=0, message='budget',
                                   x=values, fun=residual, jac=eye(len(residual), len(values), format='csr'))
        with mock.patch.object(bundle, 'least_squares', side_effect=exhausted):
            with self.assertRaises(bundle.NonConvergence) as failure:
                bundle.optimise(*self.fixture())
        self.assertEqual(failure.exception.iterations, 1)
        self.assertGreater(failure.exception.evaluations, 0)
        self.assertTrue(np.isfinite(failure.exception.condition_estimate))

    def test_singular_budget_exhaustion_does_not_enable_fallback(self):
        def exhausted(fun, values, **kwargs):
            residual = fun(values)
            return SimpleNamespace(success=False, status=0, message='budget',
                                   x=values, fun=residual,
                                   jac=csr_matrix((len(residual), len(values))))
        with mock.patch.object(bundle, 'least_squares', side_effect=exhausted):
            with self.assertRaises(ValueError) as failure:
                bundle.optimise(*self.fixture())
        self.assertNotIsInstance(failure.exception, bundle.NonConvergence)
        self.assertIn('unconstrained', str(failure.exception))

    def test_nonfinite_failure_does_not_enable_fallback(self):
        def invalid(fun, values, **kwargs):
            return SimpleNamespace(success=False, status=0, x=values * np.nan,
                                   fun=np.array([np.nan]), message='invalid')
        with mock.patch.object(bundle, 'least_squares', side_effect=invalid):
            with self.assertRaises(ValueError) as failure:
                bundle.optimise(*self.fixture())
        self.assertNotIsInstance(failure.exception, bundle.NonConvergence)
        self.assertIn('non-finite', str(failure.exception))


class PairwiseFallbackTests(unittest.TestCase):
    def fixture(self, conflicting_loop=False):
        self.points = {'a': P.copy(), 'b': P + [.02, -.01, .03], 'c': P + [.04, .01, -.02]}
        self.nodes = {ident: dict(points=p, normals=None, tree=None) for ident, p in self.points.items()}
        self.edges = [dict(id='01-ab', a='a', b='b', mode='rigid', use_cloud=False, has_targets=True),
                      dict(id='02-bc', a='b', b='c', mode='rigid', use_cloud=False, has_targets=True)]
        if conflicting_loop:
            self.edges.append(dict(id='03-ac', a='a', b='c', mode='rigid', use_cloud=False,
                                   has_targets=True, loop_closure=True))
        self.observations = []
        for edge in self.edges:
            for index in range(len(P)):
                control = self.points[edge['a']][index].copy()
                if edge['id'] == '03-ac':
                    control += [.1, 0., 0.]
                self.observations.append(dict(id=str(index), edge_id=edge['id'], a=edge['a'], b=edge['b'],
                                              role='fit', control=control, source=self.points[edge['b']][index]))
        centres = {ident: p.mean(axis=0) for ident, p in self.points.items()}
        self.layout = network._MixedMotions(set(self.points), self.edges, 'a', centres)
        self.failure = bundle.NonConvergence('evaluation limit', 2, 500, 3.)
        self.options = dict(tolerance_mm=5., capture_mm=250.)

    def run_fallback(self, cancel=lambda: False):
        return workflow._pairwise_fallback(self.nodes, self.edges, [], self.observations,
            self.layout, self.options, self.failure, 2000, lambda _: None, cancel)

    def test_fresh_pairwise_forest_preserves_anchor_and_raw_points(self):
        self.fixture()
        original = {ident: points.copy() for ident, points in self.points.items()}
        out = self.run_fallback()
        self.assertTrue(out['fallback_used'])
        self.assertTrue(out['pairwise_converged'])
        self.assertFalse(out['full_bundle_converged'])
        self.assertFalse(out['converged'])
        self.assertIn(workflow.FALLBACK_REVIEW_WARNING, out['warnings'])
        for ident, points in self.points.items():
            np.testing.assert_array_equal(points, original[ident])
            np.testing.assert_allclose(bundle.transformed(points, ident, out['transforms'], self.layout.centres), P, atol=1e-12)
        np.testing.assert_array_equal(out['transforms']['a'][0], np.eye(3))
        np.testing.assert_array_equal(out['transforms']['a'][1], np.zeros(3))

    def test_inconsistent_loop_is_refused(self):
        self.fixture(conflicting_loop=True)
        with self.assertRaisesRegex(ValueError, 'conflicts with target observations'):
            self.run_fallback()

    def test_cancel_is_never_fallback_success(self):
        self.fixture()
        with self.assertRaises(bundle.Cancelled):
            self.run_fallback(cancel=lambda: True)

    def test_unconverged_cloud_link_is_refused(self):
        self.fixture()
        self.edges = self.edges[:1]
        self.edges[0].update(use_cloud=True, has_targets=False)
        self.observations = []
        for node in self.nodes.values():
            node.update(tree=object(), normals=np.zeros((len(P), 3)))
        failure = dict(converged=False, inlier_count=700, normal_matrix_rank=6,
                       reason='maximum iterations reached; best-effort earlier iterate retained')
        with mock.patch.object(workflow.icp, '_run_icp', return_value=failure):
            with self.assertRaisesRegex(ValueError, 'maximum iterations reached'):
                self.run_fallback()

    def test_real_cloud_pair_uses_converged_current_coordinate_fit(self):
        rng = np.random.default_rng(200)
        reference = rng.uniform(-2., 2., (1200, 3))
        offset = np.array([.007, -.004, .003])
        normals = np.eye(3)[np.arange(len(reference)) % 3]
        self.points = {'a': reference, 'b': reference + offset}
        self.nodes = {ident: dict(points=p, normals=normals, tree=cKDTree(p))
                      for ident, p in self.points.items()}
        self.edges = [dict(id='cloud', a='a', b='b', mode='rigid', use_cloud=True, has_targets=False)]
        self.observations = []
        self.layout = network._MixedMotions(set(self.nodes), self.edges, 'a',
            {ident: p.mean(axis=0) for ident, p in self.points.items()})
        self.failure = bundle.NonConvergence('evaluation limit', 2, 500, 3.)
        self.options = dict(tolerance_mm=5., capture_mm=250.)
        out = self.run_fallback()
        self.assertTrue(out['pairwise_converged'])
        self.assertFalse(out['converged'])
        self.assertGreaterEqual(out['cloud_reports']['cloud']['inlier_count'], 500)
        np.testing.assert_allclose(bundle.transformed(self.points['b'], 'b', out['transforms'],
                                                     self.layout.centres), reference, atol=1e-7)


class FallbackTransactionTests(unittest.TestCase):
    def prepare(self, folder):
        s = Server(folder)
        add(s, 'a', P);add(s, 'b', P + [.02, -.01, .03])
        s.STATE['scan_links'] = [dict(id='ab', a='a', b='b', mode='rigid', kind='target_match',
            matches=[dict(a='T'+str(i), b='T'+str(i), role='fit') for i in range(len(P))])]
        failure = bundle.NonConvergence('evaluation limit', 2, 500, 3.)
        with mock.patch.object(bundle, 'optimise', side_effect=failure):
            draft, _ = workflow.build(s, dict(anchor='a', use_cloud=False))
        s.STATE['scan_link_draft'] = draft
        return s, draft

    def test_warning_is_saved_inside_accept_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            s, draft = self.prepare(folder)
            seen = []
            def save():
                seen.append(copy.deepcopy(s.STATE['last_registration_result']))
            s.save = save
            rw.accept(s, draft['id'])
            self.assertEqual(len(seen), 1)
            result = seen[0]['solution']
            self.assertIn(workflow.FALLBACK_APPLIED_WARNING, result['warnings'])
            self.assertNotIn(workflow.FALLBACK_REVIEW_WARNING, result['warnings'])
            self.assertFalse(result['converged'])
            self.assertFalse(result['full_bundle_converged'])
            self.assertTrue(result['pairwise_converged'])
            self.assertEqual(s.STATE['scan_links'][0]['status'], 'pairwise fallback adjusted')
            np.testing.assert_allclose(pose.points(rw.scan(s, 'b')['meta'], P + [.02, -.01, .03]), P, atol=1e-12)

    def test_failed_save_restores_draft_and_all_poses(self):
        with tempfile.TemporaryDirectory() as folder:
            s, draft = self.prepare(folder)
            previous = copy.deepcopy(s.STATE)
            s.save = mock.Mock(side_effect=OSError('save failed'))
            with self.assertRaisesRegex(OSError, 'save failed'):
                rw.accept(s, draft['id'])
            self.assertEqual(s.STATE, previous)

    def test_merge_save_failure_removes_only_new_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            s, _ = self.prepare(folder)
            previous = copy.deepcopy(s.STATE)
            new = Path(folder) / 'new.xyz64'
            new.write_bytes(b'new')
            merged = dict(id='new', meta=dict(file=str(new)))
            s.save = mock.Mock(side_effect=OSError('save failed'))
            def install(server, item):
                server.STATE['job_items'].append(item)
            with mock.patch.object(registered_cloud, 'build', return_value=merged), \
                    mock.patch.object(registered_cloud, 'install', side_effect=install):
                with self.assertRaisesRegex(OSError, 'save failed'):
                    rw.build_merge(s, 'a', lambda _: None, registration_id='request-id')
            self.assertEqual(s.STATE, previous)
            self.assertFalse(new.exists())
            self.assertTrue((Path(folder) / 'a.xyz64').exists())


if __name__ == '__main__':
    unittest.main()
