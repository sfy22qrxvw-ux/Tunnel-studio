"""Regression tests for theoretical profile edits and loft interpolation."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np

import cad_api
import cad_geometry as geometry
import tunnel_designer as designer


class ProfileToolsTest(unittest.TestCase):
    def test_circle_and_rectangle_remain_editable_after_combining(self):
        design = designer.default()
        design['detail'] = 'fine'
        circle = dict(id='circle', kind='circle', radius=3.775, x=0, y=0)
        rectangle = dict(id='rect', kind='rectangle', width=5, height=4, x=0, y=-2)
        design['drawing_objects'] = [circle, rectangle]
        design['profiles'][0].update(kind='composite', drawing_ids=['circle', 'rect'])
        model, _ = designer.build(design)
        self.assertTrue(model['solid'])
        self.assertAlmostEqual(model['minimum'][2], -4)
        self.assertAlmostEqual(model['maximum'][2], 3.775)
        saved = model['definition']['design']
        self.assertEqual(saved['drawing_objects'], [circle, rectangle])
        self.assertNotIn('parts', saved['profiles'][0])
        rectangle['height'] = 5
        rebuilt, _ = designer.build(design)
        self.assertAlmostEqual(rebuilt['minimum'][2], -4.5)
        self.assertGreater(rebuilt['volume_m3'], model['volume_m3'])
        rectangle['x'] = 20
        with self.assertRaisesRegex(ValueError, 'overlapping shapes'):
            designer.build(design)

    def test_join_uses_snapped_endpoints_and_signed_radii(self):
        parts = [dict(kind='polyline', nodes=[[-1,0,2], [1,0,0]]),
                 dict(kind='polyline', nodes=[[0,2,0], [1,0,0]]),
                 dict(kind='polyline', nodes=[[-1,0,0], [0,2,0]])]
        joined = designer.join_drawing_curves(parts)
        self.assertEqual(joined['nodes'], [[-1,0,2], [1,0,0], [0,2,0]])
        self.assertGreater(len(designer.outline(joined)), 3)
        with self.assertRaisesRegex(ValueError, 'one closed loop'):
            designer.join_drawing_curves(parts[:2])

    def test_circle_offset_preserves_analytic_geometry_and_source(self):
        profile = dict(kind='circle', radius=4, x=.2, y=.3)
        result, faceted = designer.profile_copy(profile, 'offset', 'balanced', .35)
        self.assertEqual(result, dict(kind='circle', radius=4.35, x=.2, y=.3))
        self.assertEqual(profile['radius'], 4)
        self.assertFalse(faceted)
        with self.assertRaisesRegex(ValueError, 'Profile radius must be positive'):
            designer.profile_copy(profile, 'offset', 'balanced', -4)

    def test_mirror_keeps_signed_arcs_and_reflects_the_offset(self):
        profile = dict(kind='custom', x=.5, y=1, nodes=[[-2,-1,0],[2,-1,0],[2,1,3],[-2,1,0]])
        result, faceted = designer.profile_copy(profile, 'mirror', 'balanced')
        self.assertEqual(result['nodes'][2], [-2, 1, -3])
        self.assertEqual(result['x'], -.5)
        self.assertEqual(result['y'], 1)
        self.assertFalse(faceted)
        np.testing.assert_allclose(np.sort(designer.outline(result)[:,0]),
                                   np.sort(-designer.outline(profile)[:,0]))

    def test_sampled_offset_reports_faceting(self):
        profile = dict(kind='custom', nodes=[[-2,-1,0],[2,-1,0],[2,1,3],[-2,1,0]])
        result, faceted = designer.profile_copy(profile, 'offset', 'balanced', .2)
        self.assertTrue(faceted)
        self.assertEqual(result['kind'], 'custom')
        self.assertEqual(np.asarray(result['nodes'])[:,2].max(), 0)
        self.assertEqual(profile['nodes'][2][2], 3)

    def test_smooth_chain_samples_both_ends_of_each_blend(self):
        design = designer.default()
        design['profiles'].extend([
            dict(id='arch', name='Tall arch', kind='arch', width=8, height=9, x=0, y=0),
            dict(id='last', name='Small circle', kind='circle', radius=2.6, x=0, y=0),
        ])
        design['sections'] = [
            dict(start=0,end=10,first='p1',last='arch',type='interpolate',interpolation='smoothstep'),
            dict(start=10,end=20,first='arch',last='last',type='interpolate',interpolation='smoothstep'),
        ]
        design['spacing'] = 100
        _, smooth, info = designer.prepare(design)
        self.assertEqual(info['profile_count'], 9)
        np.testing.assert_allclose(np.asarray(info['centres'])[:,0], np.arange(0, 20.1, 2.5))
        linear_design = deepcopy(design)
        linear_design['spacing'] = 2.5
        for section in linear_design['sections']:
            section.pop('interpolation')
        _, linear, _ = designer.prepare(linear_design)
        for index in (0,2,4,6,8):
            np.testing.assert_allclose(smooth[index]['vertices'], linear[index]['vertices'])
        self.assertFalse(np.allclose(smooth[1]['vertices'], linear[1]['vertices']))
        np.testing.assert_allclose(np.asarray(smooth[1]['vertices'])[:,1:],
                                   np.asarray(smooth[0]['vertices'])[:,1:] * .84375 +
                                   np.asarray(smooth[4]['vertices'])[:,1:] * .15625)
        model, _ = designer.build(design)
        self.assertTrue(model['solid'])

    def test_linked_cad_edit_preserves_origin_and_other_vertices(self):
        data = cad_api.empty()
        old = [[100, 200, 50], [104, 200, 50], [104, 204, 50], [100, 204, 50]]
        cad_api.execute(data, dict(action='create', spec=dict(kind='polyline', points=old, closed=True)))
        obj = data['objects'][0]
        state = SimpleNamespace(STATE={'cad':data})
        stamp = dict(context=cad_api.context(state),revision=data['revision'])
        linked = designer.profile_operation(state, dict(stamp, operation='link', object_id=obj['id']))['profile']
        self.assertEqual(linked['object_id'], obj['id'])
        initial = geometry.closed_profile_frame(obj)
        action = dict(action='vertex',ids=[obj['id']],index=1,profile_plane=True,point=[105, 200, 50.1])
        with self.assertRaisesRegex(ValueError, 'stay in the profile plane'):
            cad_api.execute(data, action)
        self.assertEqual(obj['vertices'], old)
        action['point'] = [105, 200, 50]
        cad_api.execute(data, action)
        expected = deepcopy(old);expected[1] = action['point']
        self.assertEqual(obj['vertices'], expected)
        self.assertEqual(geometry.closed_profile_frame(obj), initial)
        resolved = designer.resolve_profiles(state, {'profiles':[linked]})['profiles'][0]
        self.assertAlmostEqual(np.max(designer.outline(resolved)[:,0]), 3)
