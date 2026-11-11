# Copyright (c) 2026 Damian Gulczynski.
import unittest

import numpy as np

from combined_denoising import sample_originals


class CalibrationSamplingTests(unittest.TestCase):
    def sample(self, points, cap, block):
        points = np.asarray(points, dtype=float)

        def batches():
            for start in range(0, len(points), block):
                yield None, points[start:start+block], None

        actual, count = sample_originals(batches, 1, cap, lambda _: None, lambda: False)
        self.assertEqual(count, len(points))
        return actual

    def test_measured_voxel_minima_and_final_order(self):
        points = [[0.9, 0.1, 0.1], [-0.2, 0, 0], [0.1, 0.9, 0.9],
                  [2.5, 3, 1], [-0.8, 0.8, 0.9], [0.1, 0.8, 0.9]]
        expected = [[-0.8, 0.8, 0.9], [0.1, 0.8, 0.9], [2.5, 3, 1]]
        for block in (1, 2, 6):
            with self.subTest(block=block):
                np.testing.assert_array_equal(self.sample(points, 3, block), expected)
                np.testing.assert_array_equal(self.sample(points[::-1], 3, block), expected)

    def test_capped_sample_keeps_rank_tie_and_updates_representative(self):
        # The first two hash products cancel for this other rank-zero voxel.
        collision = [19349663.25, 73856093.25, 0.25]
        points = [collision, [0.9, 0.9, 0.9], [1.5, 2, 3],
                  [-7.5, 0, 0], [0.1, 0.9, 0.9]]
        for block in (1, 2, 5):
            with self.subTest(block=block):
                np.testing.assert_array_equal(self.sample(points, 1, block), [[0.1, 0.9, 0.9]])
                np.testing.assert_array_equal(self.sample(points[::-1], 1, block), [[0.1, 0.9, 0.9]])


if __name__ == '__main__':
    unittest.main()
