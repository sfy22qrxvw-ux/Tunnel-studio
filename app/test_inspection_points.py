# Copyright (c) 2026 Damian Gulczynski.
"""Independent fixtures for exact full-reference cloud distance evaluation.

These checks establish implementation behaviour, not field survey accuracy or
throughput/memory measurements for a 550-million-point survey.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

import inspection_points as ip
import jobdata


def brute(reference, queries, mask=None):
    """Independent all-points oracle; small fixtures only, no spatial index."""
    keep = np.isfinite(reference).all(axis=1)
    if mask is not None:
        keep &= mask == 0
    retained = reference[keep]
    result = np.full(len(queries), np.nan, dtype=np.float64)
    for index, query in enumerate(queries):
        if np.isfinite(query).all():
            with np.errstate(over='ignore', under='ignore', invalid='ignore'):
                delta = retained - query
                distance = np.hypot(np.hypot(delta[:, 0], delta[:, 1]), delta[:, 2])
            value = distance.min()
            if np.isfinite(value):
                result[index] = value
    return result


@contextmanager
def tiny_limits():
    with mock.patch.multiple(ip, READ_POINTS=19, LEAF_POINTS=23, QUERY_POINTS=11,
                             TREE_CACHE=2, NODE_CACHE=5, SPLIT_SAMPLE=32):
        yield


class CloudReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.number = 0
        self.rng = np.random.default_rng(3871)

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, points, mask=None):
        self.number += 1
        path = self.root / f'reference-{self.number}.xyz64'
        np.asarray(points, dtype='<f8').tofile(path)
        meta = dict(file=str(path), count=len(points), name='Reference fixture')
        if mask is not None:
            mask_path = self.root / f'reference-{self.number}.mask'
            np.asarray(mask, dtype='u1').tofile(mask_path)
            meta['mask_file'] = str(mask_path)
        return meta

    def compare(self, evaluator, points, reference, mask=None, atol=1e-12):
        before = np.asarray(points).copy()
        distance, valid = evaluator.evaluate(points)
        expected = brute(reference, points, mask)
        self.assertEqual(distance.dtype, np.dtype('float64'))
        self.assertEqual(valid.dtype, np.dtype('bool'))
        np.testing.assert_array_equal(valid, np.isfinite(expected))
        np.testing.assert_allclose(distance, expected, rtol=2e-15, atol=atol, equal_nan=True)
        np.testing.assert_array_equal(points, before)
        return distance

    def test_all_points_random_oracle_and_nonfinite_queries(self):
        reference = self.rng.normal(size=(743, 3))
        queries = np.vstack([self.rng.normal(size=(83, 3)), reference[::37],
                             [np.nan, 0, 0], [0, np.inf, 0], [0, 0, -np.inf]])
        meta = self.source(reference)
        original = Path(meta['file']).read_bytes()
        with tiny_limits(), ip.CloudReference(meta, self.work) as evaluator:
            self.assertFalse(evaluator.signed)
            self.assertEqual(evaluator.reference_count, len(reference))
            self.assertIn('without sampling', evaluator.note)
            self.compare(evaluator, queries, reference)
        self.assertEqual(Path(meta['file']).read_bytes(), original)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_mask_and_finite_filter_do_not_read_attributes(self):
        reference = np.asarray([[0., 0, 0], [1, 0, 0], [2, 0, 0],
                                [np.nan, 0, 0], [0, np.inf, 0], [3, 0, 0], [3, 0, 0]])
        mask = np.asarray([1, 0, 255, 0, 1, 0, 0], dtype='u1')
        meta = self.source(reference, mask)
        meta['attributes_file'] = str(self.root / 'deliberately-missing.attr32')
        with tiny_limits(), ip.CloudReference(meta, self.work) as evaluator:
            self.compare(evaluator, np.asarray([[0., 0, 0], [2, 0, 0], [4, 0, 0]]), reference, mask)
            self.assertEqual(evaluator.reference_count, 3)
            self.assertEqual(evaluator.index_stats['excluded_points'], 3)
            self.assertEqual(evaluator.index_stats['nonfinite_retained_points'], 1)

    def test_full_reference_beyond_two_hundred_thousand_points(self):
        # The final point is the unique nearest neighbour of the final query;
        # dropping a reference tail or a 200k sample cannot pass this check.
        n = 220_003
        x = np.arange(n, dtype=np.float64) * .01
        reference = np.column_stack((x, np.sin(x), np.cos(x)))
        meta = self.source(reference)
        queries = np.vstack([reference[-1], reference[200_017] + [.001, .002, -.003],
                             [5000., .2, .1]])
        with ip.CloudReference(meta, self.work) as evaluator:
            self.compare(evaluator, queries, reference)
            self.assertEqual(evaluator.reference_count, n)
            self.assertEqual(evaluator.index_stats['retained_finite_points'], n)
            self.assertGreater(evaluator.index_stats['leaves'], 1)
            self.assertLessEqual(evaluator.index_stats['largest_leaf'], 32_768)
            self.assertLessEqual(evaluator.index_stats['maximum_owned_read_points'], 100_000)

    def test_cross_partition_boundaries_far_queries_and_shuffled_source(self):
        grid = np.stack(np.meshgrid(np.arange(-6, 7), np.arange(-4, 5), [-1, 0, 1],
                                    indexing='ij'), axis=-1).reshape(-1, 3).astype(float)
        queries = np.asarray([[.4999999999999, .5, .5], [.5000000000001, -.5, -.5],
                              [6., 4, 1], [6.001, 4, 1], [1000, -2000, 3000],
                              [-4000, -1000, 0], [0, 0, 0]])
        expected = None
        with tiny_limits():
            for reference in (grid, grid[self.rng.permutation(len(grid))]):
                with ip.CloudReference(self.source(reference), self.work) as evaluator:
                    distance = self.compare(evaluator, queries, reference)
                    if expected is None:
                        expected = distance
                    else:
                        np.testing.assert_array_equal(distance, expected)

    def test_large_global_offsets_and_mixed_coordinate_scales(self):
        base = np.asarray([1e12, -4e11, 2e10])
        reference = base + self.rng.normal(size=(403, 3)) * .02
        queries = np.vstack([reference[::17], base + self.rng.normal(size=(29, 3)) * .02])
        with tiny_limits(), ip.CloudReference(self.source(reference), self.work) as evaluator:
            self.compare(evaluator, queries, reference, atol=0.)
        # Subtracting a block origin at 1e16 would round the small points together.
        mixed = np.asarray([[1e16, 1e16, 1e16], [0., 0, 0], [.001, .002, .003],
                            [.002, .001, .004]])
        query = np.asarray([[.001, .002, .003], [.0011, .0022, .0031]])
        with ip.CloudReference(self.source(mixed), self.work) as evaluator:
            self.compare(evaluator, query, mixed, atol=0.)

    def test_original_block_overlap_is_repartitioned_and_caches_are_bounded(self):
        # Every source read block spans both distant clusters. An input-block
        # index would keep overlapping boxes, whereas this index partitions XYZ.
        left = self.rng.normal(size=(191, 3)) + [-1000., 0, 0]
        right = self.rng.normal(size=(191, 3)) + [1000., 0, 0]
        reference = np.stack((left, right), axis=1).reshape(-1, 3)
        queries = np.vstack([left[::3] + .01, right[::3] - .01])
        with tiny_limits(), mock.patch.object(ip.np, 'memmap', side_effect=AssertionError('Whole-file mappings are forbidden')):
            with ip.CloudReference(self.source(reference), self.work) as evaluator:
                self.compare(evaluator, queries, reference)
                stats = evaluator.index_stats
                self.assertLessEqual(stats['maximum_owned_read_points'], 19)
                self.assertLessEqual(stats['largest_leaf'], 23)
                self.assertLessEqual(stats['peak_cached_trees'], 2)
                self.assertLessEqual(stats['peak_cached_nodes'], 5)
                rows = evaluator._connection.execute('SELECT low,high FROM nodes WHERE stored IS NOT NULL').fetchall()
                widths = [np.frombuffer(high, '<f8')[0] - np.frombuffer(low, '<f8')[0] for low, high in rows]
                self.assertLess(max(widths), 20.)

    def test_coincident_duplicates_are_lossless_and_keep_original_count(self):
        reference = np.tile([1234567.890123, -456789.012345, 101.234567], (10_003, 1))
        meta = self.source(reference)
        with tiny_limits(), ip.CloudReference(meta, self.work) as evaluator:
            self.compare(evaluator, np.vstack([reference[0], reference[0] + [3, 4, 0]]), reference)
            self.assertEqual(evaluator.reference_count, 10_003)
            self.assertEqual(evaluator.index_stats['largest_leaf'], 1)
            self.assertEqual(evaluator.index_stats['duplicate_coordinates_collapsed'], 10_002)
            self.assertEqual((evaluator._directory / 'points.xyz64').stat().st_size, 24)

    def test_byte_budget_eviction_preserves_exact_distances_and_row_order(self):
        # A deliberately tiny cache cannot retain all spatial leaves. Revisit
        # shuffled query regions with exact and offset points, checking a
        # separate brute-force oracle rather than any cache implementation.
        reference = self.rng.normal(size=(971, 3))
        queries = np.vstack([reference[::17], self.rng.normal(size=(137, 3)),
                             reference[::-31], [[np.nan, 0, 0]]])
        queries = queries[self.rng.permutation(len(queries))]
        with tiny_limits(), mock.patch.object(ip, 'TREE_CACHE_BYTES', 9000):
            with ip.CloudReference(self.source(reference), self.work) as evaluator:
                first = self.compare(evaluator, queries, reference)
                again = self.compare(evaluator, queries[::-1], reference)
                np.testing.assert_allclose(first, again[::-1], rtol=0, atol=0, equal_nan=True)
                self.assertLessEqual(evaluator.index_stats['peak_cached_component_bytes'], 9000)
                self.assertGreater(evaluator.index_stats['tree_builds'], 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_exact_radix_fallback_handles_skew_negative_and_equal_coordinates(self):
        reference = self.rng.normal(size=(233, 3))
        reference[::7, 0] = 0.
        reference[::11, 1] = -0.
        queries = self.rng.normal(size=(33, 3))
        with tiny_limits(), mock.patch.object(ip.CloudReference, '_pivot', return_value=-1e300):
            with ip.CloudReference(self.source(reference), self.work) as evaluator:
                self.compare(evaluator, queries, reference)
                self.assertGreater(evaluator.index_stats['exact_median_fallbacks'], 0)
                self.assertLessEqual(evaluator.index_stats['largest_leaf'], 23)

    def test_no_valid_reference_and_invalid_file_lengths_clean_up(self):
        cases = [(np.zeros((2, 3)), [1, 1]),
                 (np.asarray([[np.nan, 0, 0], [0, np.inf, 0]]), None)]
        for points, mask in cases:
            with self.assertRaisesRegex(ValueError, 'no retained finite points'):
                ip.CloudReference(self.source(points, mask), self.work)
            self.assertEqual(list(self.work.iterdir()), [])
        for key in ('file', 'mask_file'):
            meta = self.source(np.zeros((3, 3)), [0, 0, 0])
            path = Path(meta[key])
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'invalid length'):
                ip.CloudReference(meta, self.work)
            self.assertEqual(list(self.work.iterdir()), [])
        with self.assertRaises(ValueError):
            ip.CloudReference(self.source(np.empty((0, 3))), self.work)

    def test_build_cancellation_and_failure_remove_all_transient_files(self):
        reference = self.rng.normal(size=(301, 3))
        meta = self.source(reference)
        digest = hashlib.sha256(Path(meta['file']).read_bytes()).hexdigest()
        cancelled = {'value': False}
        def progress(message):
            if message.startswith('Indexing reference cloud'):
                cancelled['value'] = True
        with tiny_limits(), self.assertRaises(jobdata.Cancelled):
            ip.CloudReference(meta, self.work, progress, lambda: cancelled['value'])
        self.assertEqual(list(self.work.iterdir()), [])
        with tiny_limits(), mock.patch.object(ip.CloudReference, '_partition', side_effect=OSError('Injected disk failure')):
            with self.assertRaisesRegex(OSError, 'Injected disk failure'):
                ip.CloudReference(meta, self.work)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(hashlib.sha256(Path(meta['file']).read_bytes()).hexdigest(), digest)

    def test_mid_query_cancellation_closes_index(self):
        state = {'armed': False, 'calls': 0}
        def cancel():
            if state['armed']:
                state['calls'] += 1
                return state['calls'] >= 60
            return False
        reference = self.rng.normal(size=(811, 3))
        with tiny_limits():
            evaluator = ip.CloudReference(self.source(reference), self.work, cancel=cancel)
            state['armed'] = True
            with self.assertRaises(jobdata.Cancelled):
                evaluator.evaluate(self.rng.normal(size=(89, 3)))
            self.assertGreater(evaluator.index_stats['leaf_queries'], 0)
            self.assertTrue(evaluator._closed)
            self.assertEqual(list(self.work.iterdir()), [])
            evaluator.close()

    def test_source_and_mask_change_invalidate_including_restored_mtime(self):
        reference = np.asarray([[0., 0, 0], [1, 0, 0], [2, 0, 0]])
        meta = self.source(reference, [0, 0, 0])
        evaluator = ip.CloudReference(meta, self.work)
        mask_path = Path(meta['mask_file'])
        before = mask_path.stat()
        mask_path.write_bytes(bytes([1, 0, 0]))
        os.utime(mask_path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'changed'):
            evaluator.evaluate(np.asarray([[0., 0, 0]]))
        self.assertEqual(list(self.work.iterdir()), [])
        with ip.CloudReference(meta, self.work) as fresh:
            self.assertEqual(fresh.reference_count, 2)
            self.assertEqual(fresh.evaluate(np.asarray([[0., 0, 0]]))[0][0], 1.)
        # Changing a metadata path also invalidates an already-copied reference.
        evaluator = ip.CloudReference(meta, self.work)
        meta['file'] = self.source(reference + 10)['file']
        with self.assertRaisesRegex(ValueError, 'changed'):
            evaluator.evaluate(np.asarray([[0., 0, 0]]))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_mutation_during_build_is_not_published(self):
        reference = self.rng.normal(size=(121, 3))
        meta = self.source(reference, np.zeros(len(reference), dtype='u1'))
        changed = {'value': False}
        def progress(message):
            if not changed['value'] and message.startswith('Indexing reference cloud'):
                with Path(meta['mask_file']).open('r+b') as output:
                    output.write(b'\x01')
                changed['value'] = True
        with tiny_limits(), self.assertRaisesRegex(ValueError, 'changed'):
            ip.CloudReference(meta, self.work, progress)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_empty_queries_close_and_malformed_queries(self):
        meta = self.source(np.asarray([[1., 2, 3]]))
        evaluator = ip.CloudReference(meta, self.work)
        distance, valid = evaluator.evaluate(np.empty((0, 3)))
        self.assertEqual(distance.shape, (0,))
        self.assertEqual(valid.shape, (0,))
        evaluator.close()
        evaluator.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            evaluator.evaluate(np.zeros((1, 3)))
        evaluator = ip.CloudReference(meta, self.work)
        with self.assertRaisesRegex(ValueError, 'N by 3'):
            evaluator.evaluate(np.zeros((4, 2)))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_noncontiguous_float32_queries_convert_only_bounded_blocks(self):
        reference = self.rng.normal(size=(97, 3))
        source = self.rng.normal(size=(83, 6)).astype(np.float32)
        queries = source[:, ::2]
        self.assertFalse(queries.flags.c_contiguous)
        with tiny_limits(), ip.CloudReference(self.source(reference), self.work) as evaluator:
            original = evaluator._evaluate_block
            seen = []
            def bounded(block):
                seen.append((len(block), block.dtype))
                self.assertLessEqual(len(block), 11)
                self.assertEqual(block.dtype, np.dtype('float64'))
                return original(block)
            with mock.patch.object(evaluator, '_evaluate_block', side_effect=bounded):
                self.compare(evaluator, queries, reference)
            self.assertGreater(len(seen), 1)

    def test_extreme_finite_distance_norms_do_not_silently_become_zero(self):
        reference = np.asarray([[0., 0, 0], [1e-200, 0, 0], [2e-200, 1e-200, 0]])
        queries = np.asarray([[.9e-200, 0, 0], [1e-200, 0, 0], [1e200, 1e200, 0]])
        with ip.CloudReference(self.source(reference), self.work) as evaluator:
            self.compare(evaluator, queries, reference, atol=0.)
            self.assertGreater(evaluator.index_stats['robust_distance_fallback_queries'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
