"""Verify that the cleaning report measures each regional plane call."""
from collections import Counter
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import gusac
import surface_api


class GusacProfileTests(unittest.TestCase):
    def test_report_counts_first_calls_and_all_extended_retries(self):
        rng = np.random.default_rng(2026)
        n = 15_000
        angles = rng.uniform(0, 2 * np.pi, n)
        radius = 2.5 + rng.normal(0, .002, n)
        xyz = np.c_[radius * np.cos(angles), radius * np.sin(angles), rng.uniform(0, 8, n)]
        settings = dict(method='gusac', gusac_distance_mm=50, gusac_sector_deg=15)
        calls = Counter()
        first, extended = [], []
        forced_failure = None
        fit_plane = gusac._regional_plane

        def observe(points, deviation, radial, normals, normal_ok, params, key):
            nonlocal forced_failure
            counts = extended if calls[key] else first
            counts.append(len(points))
            calls[key] += 1
            if forced_failure is None:
                forced_failure = key
            if key == forced_failure:
                return None  # This retry must be counted even when it fails.
            return fit_plane(points, deviation, radial, normals, normal_ok, params, key)

        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            path = work / 'tunnel.xyz64'
            xyz.astype('<f8').tofile(path)
            meta = dict(file=str(path), name='Synthetic tunnel', count=n,
                        minimum=xyz.min(0).tolist(), maximum=xyz.max(0).tolist())
            server = SimpleNamespace(WORK=work, STATE={'surface_source': meta},
                                     progress=lambda _: None, save=lambda: None)
            with patch.object(surface_api, 'CANCEL', Event()), patch.object(gusac, '_regional_plane', side_effect=observe):
                surface_api.run(server, dict(source='separate', settings=settings))
            report, content_type, _ = surface_api.report(server, 'json')
            stats = json.loads(json.dumps(report, allow_nan=False))['statistics']
            review = surface_api.public_result(server, server.STATE['surface_result'])
            self.assertNotIn('regional_plane_workload', review['statistics'])

        self.assertEqual(content_type, 'application/json')
        self.assertGreater(len(extended), 0)
        self.assertEqual(stats['attempted_regions'], len(first))
        self.assertEqual(stats['regional_plane_calls'], len(first) + len(extended))
        self.assertLess(stats['sparse_support_extended_regions'], len(extended))
        workload = stats['regional_plane_workload']
        self.assertEqual(workload['first']['points_per_call'], first)
        self.assertEqual(workload['extended']['points_per_call'], extended)
        self.assertAlmostEqual(workload['first']['seconds'] + workload['extended']['seconds'],
                               stats['regional_plane_seconds'], delta=.002)
