# Copyright (c) 2026 Damian Gulczynski.
"""Counting copied masks never reads coordinates or changes the old revision."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import mask_storage


class MaskStorage0187Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.mask'
        self.destination = self.root / 'next.mask'

    def digest(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_first_mask_is_zero_and_returns_zero_exclusions(self):
        count = 20_000_003
        self.assertEqual(mask_storage.copy_mask(None, self.destination, count, lambda _: None), 0)
        self.assertEqual(self.destination.stat().st_size, count)
        self.assertEqual(int(np.count_nonzero(np.memmap(self.destination, 'u1', 'r'))), 0)

    def test_dense_mask_count_and_bytes_match_including_nonbinary_flags(self):
        count = 8 * 1024 * 1024 + 19
        expected = (np.arange(count, dtype='u4') % 4).astype('u1')
        expected.tofile(self.source)
        digest = self.digest()
        # Exercise the ordinary complete-file fallback used when sparse range
        # queries are unavailable. No Linux-specific sparse-copy assumption.
        with patch.object(mask_storage, '_ranges', return_value=iter([(0, count)])):
            excluded = mask_storage.copy_mask(self.source, self.destination, count, lambda _: None)
        self.assertEqual(excluded, int(np.count_nonzero(expected)))
        self.assertEqual(self.destination.read_bytes(), expected.tobytes())
        self.assertEqual(self.digest(), digest)

    def test_sparse_holes_and_allocated_zero_blocks_are_counted_exactly(self):
        count = 20_000_003
        with self.source.open('wb') as source:
            source.truncate(count)
            source.seek(17);source.write(bytes([1, 0, 255]))
            source.seek(count - 1);source.write(b'\x01')
        digest = self.digest()
        excluded = mask_storage.copy_mask(self.source, self.destination, count, lambda _: None)
        self.assertEqual(excluded, 3)
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.digest(), digest)
        self.source.write_bytes(bytes(4099))
        self.assertEqual(mask_storage.copy_mask(self.source, self.destination, 4099, lambda _: None), 0)
        self.assertEqual(self.destination.read_bytes(), bytes(4099))

    def test_short_sparse_source_is_rejected_without_changing_source(self):
        with self.source.open('wb') as source:source.truncate(100_000)
        digest = self.digest()
        with self.assertRaisesRegex(ValueError, 'Incomplete exclusion mask'):
            mask_storage.copy_mask(self.source, self.destination, 200_000, lambda _: None)
        self.assertEqual(self.digest(), digest)

    def test_source_truncated_during_copy_is_rejected(self):
        self.source.write_bytes(bytes([1]) * 4096)
        def truncated_ranges(source, count):
            self.source.write_bytes(bytes([1]) * 12)
            yield 0, count
        with patch.object(mask_storage, '_ranges', side_effect=truncated_ranges):
            with self.assertRaisesRegex(ValueError, 'Incomplete exclusion mask'):
                mask_storage.copy_mask(self.source, self.destination, 4096, lambda _: None)

    def test_cancel_during_copy_preserves_source_and_closes_destination(self):
        self.source.write_bytes(bytes([1]) * (8 * 1024 * 1024 + 17))
        digest = self.digest()
        def cancel(message):
            if 'mask bytes' in message:raise InterruptedError('cancel copying')
        with self.assertRaisesRegex(InterruptedError, 'cancel copying'):
            mask_storage.copy_mask(self.source, self.destination, self.source.stat().st_size, cancel)
        self.assertEqual(self.digest(), digest)
        # The transactional editing caller removes an unfinished destination;
        # copy_mask must close it so that also works under Windows file locking.
        self.destination.unlink()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':unittest.main()
