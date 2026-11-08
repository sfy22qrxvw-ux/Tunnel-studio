"""Bounded noise screening: original-ID coverage, exact support and rollback."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

import clean_tunnel as clean
import compute_runtime


SETTINGS = dict(noise_radius=.16, noise_neighbours=8,
                noise_minimum=2, noise_sigma=3.)


def reference_labels(points):
    """Independent whole-area formulation of the original neighbour/MAD rule."""
    unique, inverse = np.unique(points, axis=0, return_inverse=True)
    n = len(unique)
    k = min(SETTINGS['noise_neighbours'], max(1, n - 1))
    distance, indices = cKDTree(unique).query(
        unique, k=k + 1, distance_upper_bound=SETTINGS['noise_radius'] * (1 + 1e-12))
    distance, indices = distance[:, 1:], indices[:, 1:]
    valid = np.isfinite(distance)
    support = valid.sum(1)
    mean = np.divide(np.where(valid, distance, 0).sum(1), support,
                     out=np.full(n, np.nan), where=support > 0)
    labels = np.zeros(n, np.int32)
    labels[support < SETTINGS['noise_minimum']] = 1
    safe = np.minimum(indices, n - 1)
    local = mean[safe]
    usable = valid & (support[safe] >= SETTINGS['noise_minimum']) & np.isfinite(local)
    local = np.where(usable, local, np.nan)
    eligible = (support >= SETTINGS['noise_minimum']) & (usable.sum(1) >= SETTINGS['noise_minimum'])
    if eligible.any():
        values = local[eligible]
        median = np.nanmedian(values, axis=1)
        mad = np.nanmedian(np.abs(values - median[:, None]), axis=1)
        threshold = np.maximum(median + SETTINGS['noise_sigma'] * 1.4826 * mad, 1.8 * median)
        labels[np.flatnonzero(eligible)[mean[eligible] > threshold + 1e-12]] = 2
    return labels[inverse]


class NoiseLargeCountTests(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(829)
        p = rng.uniform(-.7, .7, (800, 3))
        p = np.vstack((p, p[:37], [[3., 3., 3.], [4., 4., 4.]]))
        p += [480000., 380000., 120.]
        ids = np.arange(len(p), dtype=np.int64) + 5_000_000_000
        return ids, p

    def execute(self, folder, ids, points, workers=1, selected_total=None, cancel=lambda: False,
                progress=lambda _: None):
        def batches():
            for start in range(0, len(ids), 103):
                yield ids[start:start + 103], points[start:start + 103], {}
            yield np.empty(0, np.int64), np.empty((0, 3)), {}
        ip, lp = Path(folder) / 'ids', Path(folder) / 'labels'
        with patch.object(compute_runtime, 'workers', return_value=workers), \
                patch.object(compute_runtime, 'memory_budget', return_value=1024 ** 3), \
                patch.object(clean, 'CHUNK', 47), patch.object(clean, 'QUERY_CHUNK', 23):
            result = clean.run_batches(batches(), folder, ip, lp, SETTINGS, progress, cancel,
                                       selected_total=selected_total)
        return result, np.fromfile(ip, '<i8'), np.fromfile(lp, '<i4')

    def test_core_windows_and_tile_halos_match_whole_area_classification(self):
        ids, p = self.fixture()
        expected = reference_labels(p)
        before = p.copy()
        with tempfile.TemporaryDirectory() as directory:
            result, output_ids, labels = self.execute(directory, ids, p, selected_total=len(ids))
            np.testing.assert_array_equal(np.sort(output_ids), ids)
            np.testing.assert_array_equal(labels[np.argsort(output_ids)], expected)
            np.testing.assert_array_equal(p, before)
            self.assertEqual(result['statistics']['input_points'], len(ids))
            self.assertEqual(result['statistics']['max_selected_points'], 2_000_000_000)
            self.assertFalse(list(Path(directory).glob('tunnel_noise_*')))

    def test_parallel_results_are_identical_to_serial(self):
        ids, p = self.fixture()
        outputs = []
        for workers in (1, 3):
            with tempfile.TemporaryDirectory() as directory:
                result, ii, ll = self.execute(directory, ids, p, workers=workers)
                outputs.append((ii.tobytes(), ll.tobytes()))
                self.assertEqual(result['statistics']['cpu_workers'], workers)
        self.assertEqual(outputs[0], outputs[1])

    def test_declared_oversize_is_rejected_before_consuming_input(self):
        def batches():
            self.fail('An oversized operation must not start reading the cloud.')
            yield
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, '2,000,000,000'):
                clean.run_batches(batches(), folder, Path(folder) / 'ids', Path(folder) / 'labels',
                                  SETTINGS, lambda _: None, lambda: False,
                                  selected_total=2_000_000_001)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_untrusted_small_count_does_not_collect_the_entire_stream(self):
        ids, p = self.fixture()
        for count in (1, len(ids) + 1):
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, 'count changed'):
                    self.execute(folder, ids, p, selected_total=count)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_cancel_during_spatial_work_removes_only_new_files(self):
        ids, p = self.fixture()
        stopped = threading.Event()
        def progress(message):
            if 'CPU workers' in message:
                stopped.set()
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / 'previous.result'
            old.write_bytes(b'unchanged')
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                self.execute(folder, ids, p, workers=3, cancel=stopped.is_set, progress=progress)
            self.assertEqual(old.read_bytes(), b'unchanged')
            self.assertEqual(list(Path(folder).iterdir()), [old])

    def test_dense_halo_fails_without_truncating_support(self):
        points = np.arange(60, dtype=float).reshape(20, 3) * .0001
        ids = np.arange(len(points), dtype=np.int64)
        with tempfile.TemporaryDirectory() as folder:
            ip, lp = Path(folder) / 'ids', Path(folder) / 'labels'
            with patch.object(compute_runtime, 'workers', return_value=1), \
                    patch.object(compute_runtime, 'memory_budget', return_value=128 * 1024 ** 2), \
                    patch.object(clean, 'BYTES_PER_SUPPORT_RECORD', 10 * 1024 ** 2):
                with self.assertRaisesRegex(ValueError, 'No neighbours were discarded'):
                    clean.run_batches(iter([(ids, points, {})]), folder, ip, lp, SETTINGS,
                                      lambda _: None, lambda: False)
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
