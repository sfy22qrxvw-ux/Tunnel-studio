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
    def test_independent_runs_fit_at_a_bend_without_moving_corner_heights(self):
        design = designer.default()
        bearing = np.deg2rad(40)
        tangent = np.array([np.cos(bearing), np.sin(bearing)])
        design.update(plan=[[0, 0, 0], [20, 0, 0], [20 + 100*tangent[0], 100*tangent[1], 0]],
                      vertical=[[0, 0], [121, 0]])
        design['profiles'] = [
            dict(id='rectangle', kind='rectangle', width=1, height=1),
            dict(id='circle', kind='circle', radius=1.5),
        ]
        design['sections'] = [
            dict(start=0, end=20, first='rectangle'),
            dict(start=20, end=120, first='circle'),
        ]
        with self.assertRaisesRegex(ValueError, 'Profiles change abruptly'):
            designer.prepare(design)

        entry, _ = designer.build(designer.run_design(design, 0))
        main, _ = designer.build(designer.run_design(design, 1))
        baseline = np.asarray(entry['vertices'])
        entry_width = max(entry['faces'][0]) - 1
        width = max(main['faces'][0]) - 1
        np.testing.assert_allclose(baseline[-entry_width:, 0], 20, atol=1e-12)
        np.testing.assert_allclose((np.array(main['vertices'][:width])[:, :2] - [20, 0]) @ tangent,
                                   0, atol=1e-12)

        fitted = designer.fit_run_end(entry, end='end', target=main)
        fitted_vertices = np.asarray(fitted['vertices'])
        np.testing.assert_allclose((fitted_vertices[-entry_width:, :2] - [20, 0]) @ tangent, 0, atol=1e-12)
        np.testing.assert_array_equal(fitted_vertices[:, 2], baseline[:, 2])
        self.assertLess(fitted_vertices[-entry_width:, 0].min(), 20)
        self.assertGreater(fitted_vertices[-entry_width:, 0].max(), 20)
        self.assertEqual(fitted['faces'], entry['faces'])

        reversed_run = designer.run_design(design, 1)
        reversed_run.update(plan=[design['plan'][2], design['plan'][1]], vertical=[[0, 0], [101, 0]],
                            sections=[dict(start=0, end=100, first='circle')])
        reversed_main, _ = designer.build(reversed_run)
        fitted_to_end = designer.fit_run_end(entry, end='end', target=reversed_main, target_end='end')
        np.testing.assert_allclose(fitted_to_end['vertices'], fitted_vertices, atol=1e-12)

        adjusted = designer.fit_run_end(fitted, end='end', corner=1, distance=.15)
        delta = np.asarray(adjusted['vertices']) - fitted_vertices
        moved = len(delta) - entry_width + 1
        np.testing.assert_allclose(delta[moved], [.15, 0, 0], atol=1e-12)
        np.testing.assert_array_equal(np.delete(delta, moved, axis=0), 0)
        np.testing.assert_allclose(g.update_parameters(adjusted, {})['vertices'], adjusted['vertices'],
                                   atol=1e-12)
        with self.assertRaisesRegex(ValueError, 'pass the next cross-section'):
            designer.fit_run_end(entry, end='end', distance=-2)
        with self.assertRaisesRegex(ValueError, 'folds or collapses a face'):
            designer.build(entry['definition']['design'], end_offsets={'end': {'1': [0, -2, 0]}})
        with self.assertRaisesRegex(ValueError, 'reverses solid winding'):
            g.geometry(fitted_vertices, np.asarray(fitted['faces'])[:, ::-1],
                       solid=True, normalize_winding=False)

        design['sections'][1]['start'] = 0
        replies = []
        server = SimpleNamespace(STATE={}, JOB={}, save=lambda: None)
        for index in (0, 1):
            designer.post(server, SimpleNamespace(send=replies.append), '/api/tunnel-design-action',
                          dict(action='build_run', design=design, section_index=index,
                               context=cad_api.context(server), revision=cad_api.current(server)['revision']))
        data = cad_api.current(server)
        short, long = [cad_api.get_object(data, reply['run_id']) for reply in replies]
        self.assertEqual(short['definition']['design']['sections'][0]['end'], 20)
        self.assertEqual(long['definition']['design']['sections'][0]['start'], 0)
        self.assertEqual(long['definition']['design']['sections'][0]['end'], 120)
        self.assertEqual(len(server.STATE['tunnel_design']['design']['sections']), 2)

        # Push one corner beyond the circular run's start while retaining real overlap.
        data = cad_api.execute(data, dict(action='fit_run_end', ids=[short['id']],
                                         end='start', corner=1, distance=.25))
        edited = cad_api.get_object(data, short['id'])
        common = g.boolean([edited, long], 'intersect')
        union = g.boolean([edited, long], 'union')
        self.assertGreater(common['volume_m3'], 0)
        self.assertGreater(union['volume_m3'], long['volume_m3'])
        self.assertAlmostEqual(union['volume_m3'],
                               edited['volume_m3'] + long['volume_m3'] - common['volume_m3'])
        parts = g.cut(edited, [10, 0, 0], [1, 0, 0])
        self.assertEqual(len(parts), 2)
        self.assertAlmostEqual(sum(part['volume_m3'] for part in parts), edited['volume_m3'])

        # The rectangular cap just beyond the bend retains its collinear crown node.
        design['sections'][0]['end'] = 21
        through_bend, _ = designer.build(designer.run_design(design, 0))
        self.assertTrue(through_bend['solid'])
        self.assertEqual(len(through_bend['faces']), 2 * len(through_bend['vertices']) - 4)

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
