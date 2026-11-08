# Copyright (c) 2026 Damian Gulczynski.
"""Geometry / sampling contracts for the six inspection workflows.

Open3D tests use its actual triangle engine when the dependency is installed.
All other tests need only the application's existing NumPy dependency.
"""
from pathlib import Path
from unittest import mock
import copy
import importlib.util
import math
import tempfile
import unittest

import numpy as np

import inspection_geometry as geometry
from jobdata import Cancelled


def mesh(vertices=None, faces=None):
    return dict(vertices=np.asarray(vertices if vertices is not None else
                [[0., 0., 0.], [2., 0., 0.], [0., 1., 0.]], dtype=np.float64),
                faces=np.asarray(faces if faces is not None else [[0, 1, 2]], dtype=np.int64))


def file_mesh(root, source):
    vp, fp = root/'vertices64', root/'faces32'
    np.asarray(source['vertices'], dtype='<f8').tofile(vp)
    np.asarray(source['faces'], dtype='<u4').tofile(fp)
    return dict(kind='mesh', meta=dict(vertices_file=str(vp), faces_file=str(fp),
                vertices=len(source['vertices']), triangles=len(source['faces'])))


def gathered(source, spacing, chunk=17, **kwargs):
    batches = list(geometry.mesh_surface_batches(source, spacing, chunk_points=chunk, **kwargs))
    return tuple(np.concatenate([batch[i] for batch in batches]) for i in range(3))


class AnalyticReferenceTests(unittest.TestCase):
    def test_sphere_known_signed_distances_and_survey_offset(self):
        centre = np.array([6500000., 10500000., 123.456])
        item = dict(kind='sphere', origin=centre.tolist(), radius=2.)
        points = centre+np.array([[3., 0., 0.], [1., 0., 0.], [0., 0., 0.], [0., 2., 0.]])
        signed = geometry.make_reference(item, signed=True)
        values, valid = signed.evaluate(points)
        np.testing.assert_allclose(values, [1., -1., -2., 0.], atol=1e-12)
        self.assertTrue(valid.all()); self.assertEqual(values.dtype, np.dtype('float64'))
        unsigned, _ = geometry.make_reference(item).evaluate(points)
        np.testing.assert_array_equal(unsigned, abs(values))
        self.assertIn('Analytic', signed.representation)
        self.assertIsNone(signed.coordinate_step_mm)

    def test_capped_cylinder_side_cap_rim_and_inside(self):
        ref = geometry.make_reference(dict(kind='cylinder', radius=2., height=6.), signed=True)
        q = np.array([[3., 0., 3.], [0., 0., 7.], [3., 0., 7.],
                      [0., 0., 3.], [1.5, 0., .25], [2., 0., 6.]])
        values, valid = ref.evaluate(q)
        np.testing.assert_allclose(values, [1., 1., math.sqrt(2), -2., -.25, 0.], atol=1e-12)
        self.assertTrue(valid.all())
        self.assertIn('capped cylinder', ref.representation)

    def test_composed_rotation_scale_mirror_and_origin_for_box(self):
        definition = dict(kind='box', size=[2., 3., 4.], origin=[120., 250., 90.],
                          frame=dict(x=[0., 1., 0.], y=[0., 0., 1.]))
        base_basis = np.array([[0., 0., 1.], [1., 0., 0.], [0., 1., 0.]])
        local = np.array([[3., 1., 1.], [1., 1., 1.], [3., 4., 5.], [0., 0., 0.]])
        r = np.array([[0., -1., 0.], [-1., 0., 0.], [0., 0., 1.]])
        transform = np.eye(4); transform[:3, :3] = r*2.5
        transform[:3, 3] = [500000., 700000., 12.]
        world = ((local@base_basis.T+definition['origin'])@transform[:3, :3].T+transform[:3, 3])
        ref = geometry.make_reference(dict(definition=definition, transform=transform.tolist()), signed=True)
        values, valid = ref.evaluate(world)
        np.testing.assert_allclose(values, np.array([1., -1., math.sqrt(3), 0.])*2.5, atol=1e-9)
        self.assertTrue(valid.all())

    def test_axis_defined_cylinder_and_legacy_finite_plane(self):
        ref = geometry.make_reference(dict(kind='cylinder', origin=[7., 8., 9.],
                                          axis=[1., 0., 0.], radius=1., height=4.), signed=True)
        values, _ = ref.evaluate(np.array([[9., 10., 9.], [12., 8., 9.], [9., 8., 9.]]))
        np.testing.assert_allclose(values, [1., 1., -1.], atol=1e-12)
        plane = dict(kind='plane', e=500000., n=200000., h=99., yaw=90., length=4., width=2.)
        ref = geometry.make_reference(plane)
        values, valid = ref.evaluate(np.array([[500000., 200000., 101.],
                                              [500002., 200000., 99.],
                                              [500002., 200003., 100.]]))
        np.testing.assert_allclose(values, [2., 1., math.sqrt(3)], atol=1e-10)
        self.assertTrue(valid.all()); self.assertIn('finite rectangular', ref.representation)

    def test_infinite_plane_signed_normal_and_finite_edge_ambiguity(self):
        ref = geometry.make_reference(dict(kind='plane', origin=[2., 0., 0.], normal=[1., 0., 0.]), signed=True)
        values, valid = ref.evaluate(np.array([[5., 100., 80.], [1., -5., 1.]]))
        np.testing.assert_allclose(values, [3., -1.]); self.assertTrue(valid.all())
        self.assertIn('infinite plane', ref.representation)
        ref = geometry.make_reference(dict(kind='plane', length=2., width=2.), signed=True)
        values, valid = ref.evaluate(np.array([[2., 0., 0.], [2., 0., -1.], [0., 0., 0.]]))
        self.assertFalse(valid[0]); self.assertTrue(np.isnan(values[0]))
        np.testing.assert_allclose(values[1:], [-math.sqrt(2), 0.]); self.assertTrue(valid[1:].all())

    def test_curves_are_exact_unsigned_and_not_labelled_surfaces(self):
        line = geometry.make_reference(dict(kind='line', points=[[0., 0., 0.], [2., 0., 0.]]))
        values, _ = line.evaluate(np.array([[1., 3., 0.], [4., 0., 0.], [-1., 1., 0.]]))
        np.testing.assert_allclose(values, [3., 2., math.sqrt(2)])
        circle = geometry.make_reference(dict(kind='circle', radius=2., centre=[1., 2., 3.]))
        values, _ = circle.evaluate(np.array([[4., 2., 3.], [3., 2., 4.], [1., 2., 3.]]))
        np.testing.assert_allclose(values, [1., 1., 2.])
        self.assertIn('curve', circle.representation); self.assertFalse(circle.info['signed_supported'])
        with self.assertRaisesRegex(ValueError, 'unsigned'):
            geometry.make_reference(dict(kind='circle', radius=2.), signed=True)

    def test_preserved_cad_definition_verified_and_stale_definition_falls_back(self):
        obj = dict(shape='sphere', solid=True, definition=dict(kind='sphere', radius=2.),
                   vertices=[[2., 0., 0.], [0., 2., 0.], [0., 0., 2.]], faces=[[0, 1, 2]])
        self.assertEqual(geometry.reference_info(obj)['kind'], 'sphere')
        stale = copy.deepcopy(obj); stale['vertices'][0][0] = 3.
        info = geometry.reference_info(stale)
        self.assertEqual(info['kind'], 'triangle_surface')
        self.assertIn('differ from', info['note'])
        opened = copy.deepcopy(obj); opened['solid'] = False
        self.assertEqual(geometry.reference_info(opened)['kind'], 'triangle_surface')
        shear = copy.deepcopy(obj); shear['transform'] = np.diag([2., 1., 1., 1.]).tolist()
        info = geometry.reference_info(shear)
        self.assertEqual(info['kind'], 'triangle_surface'); self.assertIn('Non-uniform', info['note'])

    def test_cad_default_dimensions_remain_analytic(self):
        for kind in ('sphere', 'cylinder', 'box'):
            with self.subTest(kind=kind):
                self.assertEqual(geometry.reference_info(dict(kind=kind))['kind'], kind)

    def test_invalid_definition_queries_and_cancellation(self):
        for spec in (dict(kind='sphere', radius=-1), dict(kind='plane', normal=[0, 0, 0]),
                     dict(kind='box', size=[1, 0, 1]), dict(kind='line', points=[[0, 0, 0], [0, 0, 0]]),
                     dict(kind='plane', length=2), dict(kind='plane', frame=dict(x_point=[1, 0, 0]))):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                geometry.make_reference(spec)
        ref = geometry.make_reference(dict(kind='sphere', radius=1))
        values, valid = ref.evaluate(np.array([[1., 0., 0.], [np.nan, 0., 0.], [np.inf, 0., 0.]]))
        np.testing.assert_array_equal(valid, [True, False, False]); self.assertEqual(values[0], 0.)
        with self.assertRaises(ValueError): ref.evaluate(np.zeros((3, 2)))
        with self.assertRaises(Cancelled): geometry.make_reference(dict(kind='sphere'), cancel=lambda: True)
        stopped = [False]; ref = geometry.make_reference(dict(kind='sphere'), cancel=lambda: stopped[0])
        stopped[0] = True
        with self.assertRaises(Cancelled): ref.evaluate(np.zeros((3, 3)))


class SurfaceSamplingTests(unittest.TestCase):
    def test_every_face_has_area_conserving_interior_samples_and_first_moment(self):
        source = mesh([[0, 0, 0], [2, 0, 0], [0, 1, 0], [7, 0, 0], [10, 0, 0], [7, 4, 0]],
                      [[0, 1, 2], [3, 4, 5]])
        ids, points, weights = gathered(source, .6, chunk=7)
        info = geometry.mesh_sample_info(source, .6)
        self.assertEqual(len(ids), math.ceil(math.sqrt(5)/.6)**2+math.ceil(5/.6)**2)
        self.assertEqual(info['sample_count'], len(ids)); self.assertEqual(info['area_m2'], 7.)
        self.assertEqual(ids.dtype, np.dtype('int64')); self.assertEqual(points.dtype, np.dtype('float64'))
        self.assertTrue(np.all(weights > 0)); self.assertAlmostEqual(weights.sum(), 7., places=12)
        for face, area in ((0, 1.), (1, 6.)):
            take = ids == face; triangle = source['vertices'][source['faces'][face]]
            self.assertAlmostEqual(weights[take].sum(), area, places=12)
            np.testing.assert_allclose(np.average(points[take], weights=weights[take], axis=0),
                                       triangle.mean(axis=0), atol=1e-12)
        self.assertTrue(np.all(points[ids == 0, 0] > 0)); self.assertTrue(np.all(points[ids == 0, 1] > 0))
        self.assertTrue(np.all(points[ids == 0, 0]/2+points[ids == 0, 1] < 1))
        self.assertIn('not exact', info['note'])

    def test_chunk_invariance_and_no_reused_output_buffers(self):
        source = mesh(); expected = gathered(source, .13, chunk=1)
        for size in (7, 41, 100000):
            actual = gathered(source, .13, chunk=size)
            for a, b in zip(actual, expected): np.testing.assert_array_equal(a, b)
        batches = list(geometry.mesh_surface_batches(source, .13, chunk_points=7))
        for index in range(1, len(batches)):
            self.assertFalse(np.shares_memory(batches[0][1], batches[index][1]))

    def test_survey_coordinates_remain_float64_and_source_is_unchanged(self):
        source = mesh(); source['vertices'] += np.array([6500000., 10500000., 123.456])
        before = {key: value.tobytes() for key, value in source.items()}
        ids, actual, weights = gathered(source, .5)
        _, expected, _ = gathered(mesh(), .5)
        np.testing.assert_allclose(actual-[6500000., 10500000., 123.456], expected, rtol=0, atol=2e-9)
        for key, value in source.items(): self.assertEqual(value.tobytes(), before[key])
        self.assertEqual(actual.dtype, np.dtype('float64'))

    def test_disk_mesh_reader_crosses_bounded_pages_with_equal_results(self):
        source = mesh([[0, 0, 0], [2, 0, 0], [7, 2, 1], [0, 1, 0],
                       [3, 0, 0], [5, 1, 0], [8, 3, 2]], [[0, 1, 3], [2, 5, 6]])
        expected = gathered(source, .7, chunk=13)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); item = file_mesh(root, source)
            saved = {p.name: p.read_bytes() for p in root.iterdir()}
            with mock.patch.object(geometry, 'VERTEX_PAGE', 2), mock.patch.object(geometry, 'VERTEX_CACHE_PAGES', 1), mock.patch.object(geometry, 'FACE_BLOCK', 1):
                actual = gathered(item, .7, chunk=13)
                info = geometry.mesh_sample_info(item, .7)
            for a, b in zip(actual, expected): np.testing.assert_array_equal(a, b)
            self.assertEqual(info['sample_count'], len(actual[0]))
            for p in root.iterdir(): self.assertEqual(p.read_bytes(), saved[p.name])

    def test_polygon_adapter_only_triangulates_valid_convex_planar_faces(self):
        source = dict(vertices=[[0., 0., 0.], [2., 0., 0.], [2., 1., 0.], [0., 1., 0.]], faces=[[0, 1, 2, 3]])
        info = geometry.mesh_sample_info(source, 1.)
        self.assertEqual(info['triangles'], 2); self.assertEqual(info['area_m2'], 2.)
        bad = copy.deepcopy(source); bad['vertices'][2][2] = 1.
        with self.assertRaisesRegex(ValueError, 'non-planar'): geometry.surface_meta(bad)
        bad = copy.deepcopy(source); bad['vertices'][2] = [.5, .2, 0.]
        with self.assertRaisesRegex(ValueError, 'non-convex'): geometry.surface_meta(bad)

    def test_invalid_geometry_and_spacing_are_reported_without_silent_drops(self):
        for source in (mesh([[0, 0, 0], [1, 0, 0], [2, 0, 0]]),
                       mesh([[0, 0, 0], [1, 0, 0], [np.nan, 1, 0]]),
                       mesh(faces=[[0, 1, 9]])):
            with self.subTest(source=source), self.assertRaises(ValueError):
                geometry.mesh_sample_info(source, .1)
        for spacing in (0, -1, np.inf, np.nan, 1e-100):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                geometry.mesh_sample_info(mesh(), spacing)
        with tempfile.TemporaryDirectory() as folder:
            source = file_mesh(Path(folder), mesh()); Path(source['meta']['faces_file']).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError, 'wrong size'): geometry.surface_meta(source)

    def test_cancellation_is_checked_inside_a_single_dense_face_and_closes_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = file_mesh(root, mesh())
            saved = {p.name: p.read_bytes() for p in root.iterdir()}; readers = []
            original_reader = geometry._VertexReader
            class TrackingReader(original_reader):
                def __init__(self, meta):
                    super().__init__(meta); readers.append(self)
            cancelled = [False]
            with mock.patch.object(geometry, '_VertexReader', TrackingReader):
                iterator = geometry.mesh_surface_batches(source, .00001, chunk_points=3, cancel=lambda: cancelled[0])
                first = next(iterator); self.assertEqual(len(first[0]), 3)
                cancelled[0] = True
                with self.assertRaises(Cancelled): next(iterator)
                iterator.close()
            self.assertTrue(readers); self.assertTrue(all(reader.file is None for reader in readers))
            for p in root.iterdir(): self.assertEqual(p.read_bytes(), saved[p.name])

    def test_reference_estimate_guard_precedes_engine_allocation(self):
        source = mesh(); info = geometry.reference_info(source)
        self.assertEqual(info['triangles'], 1); self.assertIn('not a measured whole-process', info['note'])
        with mock.patch.object(geometry, 'REFERENCE_WORKSPACE_LIMIT_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'component budget'):
                geometry.make_reference(source)


@unittest.skipUnless(importlib.util.find_spec('open3d') is not None, 'Open3D runtime is not installed in this test interpreter')
class TriangleReferenceTests(unittest.TestCase):
    def test_triangle_interior_distance_is_not_nearest_vertex_distance(self):
        source = mesh([[500000., 200000., 99.], [500010., 200000., 99.], [500000., 200010., 99.]])
        with geometry.make_reference(source, tolerance_mm=1) as ref:
            values, valid = ref.evaluate(np.array([[500002., 200002., 99.2], [500006., 200006., 99.], [np.nan, 0., 0.]]))
            np.testing.assert_allclose(values[:2], [.2, math.sqrt(2)], rtol=0, atol=2e-6)
            np.testing.assert_array_equal(valid, [True, True, False])
            self.assertEqual(ref.info['representation'], 'Triangle surface (tessellated)')
            self.assertLess(ref.coordinate_step_mm, .1)
            reference_step = ref.coordinate_step_mm
            distant, valid = ref.evaluate(np.array([[500100., 200000., 99.]]))
            np.testing.assert_allclose(distant, [90.], rtol=0, atol=1e-6)
            self.assertTrue(valid.all()); self.assertGreater(ref.coordinate_step_mm, reference_step)
        with self.assertRaisesRegex(ValueError, 'closed'): ref.evaluate(np.zeros((1, 3)))

    def test_mesh_sign_follows_winding_and_tangential_edge_is_unclassified(self):
        source = mesh([[0, 0, 0], [10, 0, 0], [0, 10, 0]])
        queries = np.array([[2., 2., .4], [2., 2., -.4], [6., 6., 0.]])
        with geometry.make_reference(source, signed=True) as ref:
            values, valid = ref.evaluate(queries)
            np.testing.assert_allclose(values[:2], [.4, -.4], atol=1e-7)
            self.assertFalse(valid[-1]); self.assertTrue(np.isnan(values[-1]))
        source['faces'] = source['faces'][:, ::-1]
        with geometry.make_reference(source, signed=True) as ref:
            values, _ = ref.evaluate(queries)
            np.testing.assert_allclose(values[:2], [-.4, .4], atol=1e-7)

    def test_precision_limits_degenerate_faces_and_cancellation(self):
        with self.assertRaisesRegex(ValueError, 'precision'):
            geometry.make_reference(mesh([[0, 0, 0], [1e6, 0, 0], [0, 1e6, 0]]), tolerance_mm=.1)
        with self.assertRaisesRegex(ValueError, 'degenerate'):
            geometry.make_reference(mesh([[0, 0, 0], [1, 0, 0], [2, 0, 0]]))
        with geometry.make_reference(mesh(), tolerance_mm=1) as ref:
            with self.assertRaisesRegex(ValueError, 'too far'):
                ref.evaluate(np.array([[1e10, 0., 0.]]))
        stop = [False]
        def progress(message):
            if 'Building' in message: stop[0] = True
        with self.assertRaises(Cancelled):
            geometry.make_reference(mesh(), progress=progress, cancel=lambda: stop[0])


if __name__ == '__main__':
    unittest.main()
