"""Designer previews, required stations and editable long solids."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import cad_api
import cad_geometry as g
import tunnel_designer as designer


class TunnelDesignerPreviewTest(unittest.TestCase):
    def test_profile_preview_survives_full_range_geometry_limit(self):
        design = designer.default()
        design['plan'] = [[station, 0, 0] for station in range(501)]
        design['vertical'] = [[0, 2], [500, 2]]
        design['sections'][0]['end'] = 500
        responses = []
        with patch.object(g, 'MAX_PROJECT_VERTICES', 20_000):
            designer.post(SimpleNamespace(JOB={}, STATE={}), SimpleNamespace(send=responses.append),
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

    def test_long_fine_model_keeps_required_stations_and_exact_profile_dimensions(self):
        design = designer.default()
        stations = np.r_[np.arange(1371), 1500.]
        design.update(plan=[[float(station), 0, 0] for station in stations], detail='fine',
                      vertical=[[0, 0], [1500, 0]])
        design['sections'][0]['end'] = 1500
        design['profiles'][0]['radius'] = 3.775
        design['profiles'].append(dict(id='draft', name='Unused draft', kind='circle', radius=0))
        value, info = designer.build(design, vertex_budget=250_000)
        self.assertEqual(info['required_sections'], len(stations))
        self.assertGreater(info['maximum_spacing'], info['requested_spacing'])
        self.assertTrue(value['solid'])
        rings = np.asarray(value['vertices']).reshape(-1, 180, 3)
        self.assertEqual(len(rings), info['profile_count'])
        np.testing.assert_array_equal(np.intersect1d(stations, rings[:, 0, 0]), stations)
        np.testing.assert_allclose(np.linalg.norm(rings[:, :, 1:], axis=2), 3.775, atol=1e-10, rtol=0)
        self.assertEqual(len(value['faces']), 2*len(value['vertices'])-4)

    def test_two_fine_circles_share_the_same_perimeter_grid(self):
        design = designer.default()
        design.update(plan=[[float(x), 0, 0] for x in range(1372)], detail='fine',
                      vertical=[[0, 0], [1371, 0]])
        design['profiles'].append(dict(id='p2', name='Second profile', kind='circle', radius=3.775))
        design['sections'][0].update(end=1371, last='p2', type='interpolate')
        _, _, info = designer.prepare(design, preview=True)
        self.assertEqual(info['required_sections'], 1372)
        self.assertEqual(info['vertices'], 1372*180)

    def test_invalid_assigned_profile_named_in_preview_and_drawing_plane_follows_grade(self):
        design = designer.default()
        design['plan'] = [[100,200,0], [106,208,0]]
        design['vertical'] = [[0,2], [10,3]]
        design['sections'][0]['end'] = 10
        design['profiles'][0]['radius'] = None
        responses = []
        server = SimpleNamespace(STATE={}, JOB={})
        designer.post(server, SimpleNamespace(send=responses.append), '/api/tunnel-design-preview', {'design': design})
        self.assertEqual(responses[0]['profile_error_id'], 'p1')
        self.assertIn('“Section A”', responses[0]['model_error'])
        self.assertIn('radius must be positive', responses[0]['model_error'])
        frame = designer.profile_operation(server, dict(context=cad_api.context(server), revision='empty',
            operation='drawing_frame', design=design, chainage=5))['frame']
        np.testing.assert_allclose(frame['origin'], [103,204,2.5])
        tangent = [.6,.8,.1]
        self.assertAlmostEqual(np.dot(frame['x'], tangent), 0.)
        self.assertAlmostEqual(np.dot(frame['y'], tangent), 0.)

    def test_legacy_and_current_model_json_round_trip(self):
        design = designer.default()
        design['profiles'].append(dict(id='unused', name='Rectangle', kind='rectangle', width=7, height=7))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'tunnel.json'
            for legacy in (True, False):
                value, _ = designer.build(design, legacy_sampling=legacy)
                obj = cad_api.object_record(value, dict(name='Tunnel'))
                path.write_bytes(cad_api.export_bytes([obj], 'json'))
                restored, _, _ = cad_api.import_model_json(path)
                self.assertEqual(restored[0]['vertices'], value['vertices'])
                self.assertEqual(restored[0]['faces'], value['faces'])
