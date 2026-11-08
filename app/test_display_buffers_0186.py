# Copyright (c) 2026 Damian Gulczynski.
"""Production comparison previews retain numerical results through binary display."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import design_compare
import display_buffers
import inspection


def decode(blob):
    magic, version, count, length, flags, reserved = display_buffers.HEADER.unpack_from(blob)
    assert (magic, version, reserved) == (b'TSDP', 1, 0)
    offset = 24 + (length + 7) // 8 * 8
    metadata = json.loads(blob[24:24 + length])
    points = np.frombuffer(blob, '<f4', count=count * 6, offset=offset).reshape(count, 6)
    end = offset + count * 24
    deviations = np.frombuffer(blob, '<f8', count=count, offset=end) if flags & 1 else None
    assert len(blob) == end + (count * 8 if flags & 1 else 0)
    return metadata, points, deviations


class DisplayBuffers(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        with display_buffers._lock:
            display_buffers._cache.clear()

    def tearDown(self):
        self.directory.cleanup()

    def fixture(self, module, count=10007):
        rows = np.zeros(count, module.RECORD)
        t = np.arange(count, dtype=np.float64)
        rows['xyz'] = np.column_stack((450000.123456 + t * .001,
                                      310000.654321 + np.sin(t * .01), 75.1 + np.cos(t * .02)))
        rows['deviation_m'] = np.sin(t * .001) * .055
        rows['status'][::19] = 1
        path = self.root / (module.__name__ + '.result')
        rows.tofile(path)
        return dict(results_file=str(path), record_count=count, used=int((rows['status'] == 0).sum()),
                    signed=True, tolerance_mm=25., id='result-1', source_id='scan-1',
                    evaluated=count, source_kind='scan', mode_label='Cloud vs Mesh',
                    reference_label='Design mesh', reference_name='Tunnel design',
                    sampling_note='Every retained input point evaluated.')

    def check_roundtrip(self, module):
        result = self.fixture(module)
        path = Path(result['results_file'])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        legacy = module.preview(result)
        typed = module.preview(result, packed=True)
        self.assertIsInstance(typed['points'], np.ndarray)
        self.assertIsInstance(typed['deviations_mm'], np.ndarray)
        metadata, points, deviations = decode(display_buffers.encode(typed))
        np.testing.assert_array_equal(points, np.asarray(legacy['points'], dtype='<f4'))
        np.testing.assert_array_equal(deviations, np.asarray(legacy['deviations_mm'], dtype='<f8'))
        for key, value in legacy.items():
            if key not in ('points', 'deviations_mm'):
                self.assertEqual(metadata[key], value)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_design_result_roundtrip_exact_display_and_errors(self):
        self.check_roundtrip(design_compare)

    def test_inspection_result_roundtrip_exact_display_and_errors(self):
        self.check_roundtrip(inspection)

    def test_cache_reuses_only_same_result_and_replaced_data_invalidates(self):
        result = self.fixture(design_compare)
        calls = []
        def build():
            calls.append(1)
            return design_compare.preview(result, packed=True)
        first = display_buffers.cached_result(result, build)
        self.assertIs(display_buffers.cached_result(result, build), first)
        self.assertEqual(len(calls), 1)
        replacement = self.root / 'replacement'
        rows = np.fromfile(result['results_file'], design_compare.RECORD)
        rows['deviation_m'] += .123456789
        rows.tofile(replacement)
        replacement.replace(result['results_file'])
        second = display_buffers.cached_result(result, build)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first, second)

    def test_changed_display_tolerance_invalidates_cache(self):
        result = self.fixture(design_compare)
        first = display_buffers.cached_result(result, lambda: design_compare.preview(result, packed=True))
        result['tolerance_mm'] = 1000.
        second = display_buffers.cached_result(result, lambda: design_compare.preview(result, packed=True))
        self.assertNotEqual(first, second)
        self.assertEqual(decode(second)[0]['tolerance_mm'], 1000.)

    def test_result_changed_during_read_is_not_cached(self):
        result = self.fixture(design_compare)
        def build():
            value = design_compare.preview(result, packed=True)
            with Path(result['results_file']).open('ab') as stream:
                stream.write(b'x')
            return value
        with self.assertRaisesRegex(ValueError, 'changed while loading'):
            display_buffers.cached_result(result, build)
        self.assertFalse(display_buffers._cache)

    def test_invalid_points_counts_and_deviations_rejected(self):
        valid = dict(origin=[1., 2., 3.], points=np.zeros((4, 6)), deviations_mm=np.zeros(4))
        invalid = [dict(valid, points=np.zeros((4, 3))), dict(valid, sample_count=7),
                   dict(valid, points=np.full((4, 6), np.nan)), dict(valid, deviations_mm=np.zeros(3)),
                   dict(valid, deviations_mm=np.full(4, np.inf))]
        for value in invalid:
            with self.subTest(value=list(value)):
                with self.assertRaises(ValueError):
                    display_buffers.encode(value)

    def test_optional_deviation_array_and_float64_origin(self):
        origin = [450000.123456789, 310000.987654321, 75.123456789]
        metadata, points, deviations = decode(display_buffers.encode(dict(origin=origin, points=np.ones((3, 6)))))
        self.assertEqual(metadata['origin'], origin)
        self.assertIsNone(deviations)
        self.assertEqual(points.dtype, np.dtype('<f4'))

    def test_binary_is_smaller_than_same_100k_json_preview(self):
        result = self.fixture(design_compare, 100000)
        legacy = design_compare.preview(result)
        binary = display_buffers.encode(design_compare.preview(result, packed=True))
        plain = json.dumps(legacy, separators=(',', ':'), allow_nan=False).encode()
        self.assertLess(len(binary), len(plain) * .5)


if __name__ == '__main__':
    unittest.main()
