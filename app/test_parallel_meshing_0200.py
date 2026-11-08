"""Numerical, seam, cancellation and original-row checks for CPU stages."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from scipy.ndimage import binary_closing, binary_dilation, gaussian_filter
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes

import compute_runtime
import core
import parallel_meshing as parallel
import registration_pose


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        count = 400_003
        x = np.linspace(-2, 18, count)
        self.points = np.c_[x, np.sin(x), np.cos(x)]
        self.source = self.root / 'points.xyz64'
        self.points.astype('<f8').tofile(self.source)
        flags = np.zeros(count, 'u1')
        flags[::11] = 1
        self.mask = self.root / 'points.mask'
        flags.tofile(self.mask)
        self.meta = dict(file=str(self.source), mask_file=str(self.mask), count=count)
        registration_pose.set_pose(self.meta, np.eye(3), np.array([.25, .5, .1]))
        self.axis = core.Alignment([
            dict(chainage=0, e=0, n=0, h=0, radius=3),
            dict(chainage=10, e=10, n=0, h=1, radius=3),
            dict(chainage=20, e=20, n=2, h=1, radius=3),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def test_parallel_projection_preserves_pose_mask_rows_and_serial_values(self):
        with patch.object(compute_runtime, 'workers', return_value=1):
            serial, ns = core.project_cloud(self.meta, self.axis, self.root / 'serial.bin')
        with patch.object(compute_runtime, 'workers', return_value=3):
            parallel_path, np_ = core.project_cloud(self.meta, self.axis, self.root / 'parallel.bin')
        self.assertEqual(ns, np_)
        a = np.fromfile(serial, '<f8').reshape(-1, 4)
        b = np.fromfile(parallel_path, '<f8').reshape(-1, 4)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(len(a), self.meta['count'])
        self.assertTrue(np.isfinite(a[110000, 0]))
        np.testing.assert_array_equal(np.fromfile(self.source, '<f8').reshape(-1, 3), self.points)

    def test_cancel_preserves_previous_destination_and_removes_temporary(self):
        destination = self.root / 'previous.bin'
        destination.write_bytes(b'previous-analysis')
        stop = threading.Event()
        def progress(_):
            stop.set()
        with patch.object(compute_runtime, 'workers', return_value=3):
            with self.assertRaises(compute_runtime.ProcessingCancelled):
                core.project_cloud(self.meta, self.axis, destination, progress, stop.is_set)
        self.assertEqual(destination.read_bytes(), b'previous-analysis')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_changed_pose_refuses_publication(self):
        destination = self.root / 'previous.bin'
        destination.write_bytes(b'previous-analysis')
        changed = False
        def progress(_):
            nonlocal changed
            if not changed:
                changed = True
                registration_pose.set_pose(self.meta, np.eye(3), np.array([2., 0., 0.]))
        with patch.object(compute_runtime, 'workers', return_value=3):
            with self.assertRaisesRegex(ValueError, 'pose or source changed'):
                core.project_cloud(self.meta, self.axis, destination, progress)
        self.assertEqual(destination.read_bytes(), b'previous-analysis')
        self.assertFalse(list(self.root.glob('*.tmp')))


class VoxelTests(unittest.TestCase):
    def test_parallel_noise_filter_matches_global_neighbours(self):
        points = np.random.default_rng(39).random((400_003, 3))
        points[-3:] += np.array([[20., 0., 0.], [30., 0., 0.], [40., 0., 0.]])
        distances = cKDTree(points).query(points, k=2, workers=1)[0][:, 1]
        expected = points[np.isfinite(distances) & (distances <= .08)]
        with patch.object(compute_runtime, 'workers', return_value=3):
            actual = parallel.noise_filter_parallel(points, .08)
        np.testing.assert_array_equal(actual, expected)

    def test_triangle_filter_preserves_global_face_order_and_rules(self):
        points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [10., 0., 0.]])
        faces = np.tile(np.array([[0, 1, 2], [0, 1, 3], [0, 0, 1]], np.uint32), (133_335, 1))
        with patch.object(compute_runtime, 'workers', return_value=3):
            actual, removed = parallel.triangle_filter_parallel(points, faces, 2.)
        np.testing.assert_array_equal(actual, np.tile([[0, 1, 2]], (133_335, 1)))
        self.assertEqual(removed, 133_335)

    def test_small_work_keeps_serial_fallback(self):
        points = np.random.default_rng(40).random((100, 3))
        with patch.object(compute_runtime, 'workers', return_value=8):
            with patch.object(compute_runtime, 'ThreadPoolExecutor', side_effect=AssertionError('unexpected pool')):
                parallel.noise_filter_parallel(points, 1.)

    def test_voxelisation_matches_serial_and_uses_original_chunks(self):
        points = np.random.default_rng(17).uniform(0, 3, size=(400_003, 3))
        dims = np.array([33, 33, 33])
        expected = np.zeros(tuple(dims), np.uint8)
        ix = np.floor(points / .1).astype(np.int64)
        expected[ix[:, 0], ix[:, 1], ix[:, 2]] = 1
        messages = []
        with patch.object(compute_runtime, 'workers', return_value=3):
            actual = parallel.voxel_grid_parallel(points, np.zeros(3), .1, dims, messages.append)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(len(messages), 5)
        self.assertIn('5/5', messages[-1])

    def test_smoothing_halos_match_complete_field_all_axes_and_edges(self):
        rng = np.random.default_rng(22)
        grid = (rng.random((132, 13, 17)) > .88).astype(np.uint8)
        for axis in range(3):
            values = np.moveaxis(grid, 0, axis)
            for sigma in (.6, 1.7, 3.):
                expected = gaussian_filter(binary_closing(binary_dilation(values, iterations=1),
                                                         iterations=2).astype(np.float32), sigma)
                with patch.object(compute_runtime, 'workers', return_value=3):
                    actual = parallel.smoothed_voxel_field_parallel(values, sigma)
                np.testing.assert_array_equal(actual, expected)

    def test_marching_slabs_have_no_seam_boundary_or_duplicate_faces(self):
        x, y, z = np.ogrid[:132, :27, :29]
        field = ((x - 65.3) ** 2 / 48 ** 2 + (y - 13.2) ** 2 / 10 ** 2
                 + (z - 14.1) ** 2 / 11 ** 2).astype(np.float32)
        for axis in range(3):
            values = np.moveaxis(field, 0, axis)
            v0, f0, _, _ = marching_cubes(values, level=1., allow_degenerate=False)
            with patch.object(compute_runtime, 'workers', return_value=3):
                v, f, workers = parallel.marching_cubes_field_parallel(values, 1.)
                again_v, again_f, _ = parallel.marching_cubes_field_parallel(values, 1.)
            self.assertEqual(workers, 3)
            self.assertEqual(len(f), len(f0))
            self.assertEqual(len(v), len(v0))
            self.assertLess(float(cKDTree(v0).query(v)[0].max()), 1e-5)
            edges = np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
            _, counts = np.unique(edges, axis=0, return_counts=True)
            self.assertTrue(np.all(counts == 2))
            self.assertEqual(len(np.unique(np.sort(f, axis=1), axis=0)), len(f))
            np.testing.assert_array_equal(v, again_v)
            np.testing.assert_array_equal(f, again_f)

    def test_level_exactly_on_shared_plane(self):
        values = np.broadcast_to(np.arange(132, dtype=np.float32)[:, None, None] - 64,
                                 (132, 9, 11)).copy()
        v0, f0, _, _ = marching_cubes(values, level=0., allow_degenerate=False)
        with patch.object(compute_runtime, 'workers', return_value=3):
            v, f, _ = parallel.marching_cubes_field_parallel(values, 0.)
        self.assertEqual(len(f), len(f0))
        self.assertEqual(len(v), len(v0))
        self.assertTrue(np.all(v[:, 0] == 64))

    def test_field_cancellation_stops_before_merge(self):
        stop = threading.Event()
        values = np.broadcast_to(np.arange(132, dtype=np.float32)[:, None, None], (132, 9, 11)).copy()
        with patch.object(compute_runtime, 'workers', return_value=3):
            with self.assertRaises(compute_runtime.ProcessingCancelled):
                parallel.marching_cubes_field_parallel(values, 64., lambda _: stop.set(), stop.is_set)


if __name__ == '__main__':
    unittest.main()
