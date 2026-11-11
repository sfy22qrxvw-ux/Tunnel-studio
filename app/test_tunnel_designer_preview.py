"""A valid section outline remains available even when its loft is too large."""
from types import SimpleNamespace
import unittest

import tunnel_designer as designer


class TunnelDesignerPreviewTest(unittest.TestCase):
    def test_profile_preview_survives_full_range_geometry_limit(self):
        design = designer.default()
        design['plan'] = [[station, 0, 0] for station in range(501)]
        design['vertical'] = [[0, 2], [500, 2]]
        design['sections'][0]['end'] = 500
        responses = []
        designer.post(SimpleNamespace(JOB={}), SimpleNamespace(send=responses.append),
                      '/api/tunnel-design-preview', {'design': design})
        result = responses[0]
        self.assertTrue(result['range_limited'])
        self.assertIn('501 cross-sections', result['model_error'])
        self.assertEqual(result['profiles'][0]['id'], 'p1')
        self.assertEqual(len(result['profiles'][0]['points']), 72)
        self.assertEqual(result['plan_chainages'][0], 0)
        self.assertEqual(result['plan_chainages'][-1], 500)

        design['sections'][0]['end'] = 20
        _, loops, info = designer.prepare(design)
        self.assertEqual(info['profile_count'], 21)
        self.assertEqual(len(loops), 21)
