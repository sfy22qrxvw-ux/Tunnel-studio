"""Editable alignment traces preserve the surveyed source and work with CAD tools."""
import unittest

import numpy as np

from alignment_model import trace_spec
import cad_api
import cad_geometry as geometry
from core import Alignment


class AlignmentTraceTest(unittest.TestCase):
    def setUp(self):
        self.axis = Alignment([dict(chainage=s, e=s, n=20, h=30+s/10, radius=4)
                               for s in (0, 5, 10, 15)])
        self.original = self.axis.a.copy()

    def trace(self, projection):
        return trace_spec(self.axis, dict(start=2, end=10, projection=projection))

    def test_copies_interior_nodes_and_interpolates_ends(self):
        self.assertEqual(self.trace('3d')['points'], [[2, 20, 30.2], [5, 20, 30.5], [10, 20, 31]])
        self.assertEqual(self.trace('plan')['points'], [[2, 20, 0], [5, 20, 0], [10, 20, 0]])
        np.testing.assert_array_equal(self.axis.a, self.original)
        with self.assertRaisesRegex(ValueError, 'outside the alignment'):
            trace_spec(self.axis, dict(start=-1, end=10, projection='plan'))

    def test_plan_trace_offsets_and_trims_with_straight_boundary(self):
        data = cad_api.empty()
        cad_api.execute(data, dict(action='create', spec=self.trace('plan')))
        trace = data['objects'][-1]
        cad_api.execute(data, dict(action='offset', ids=[trace['id']], distance=1, frame=geometry.frame({})))
        np.testing.assert_allclose(np.asarray(data['objects'][-1]['vertices'])[:, 1], 21)
        cad_api.execute(data, dict(action='create', spec=dict(kind='line', points=[[7, 18, 0], [7, 22, 0]])))
        boundary = data['objects'][-1]
        cad_api.execute(data, dict(action='trim', ids=[trace['id'], boundary['id']], keep=[9, 20, 0],
                                   frame=geometry.frame({})))
        np.testing.assert_allclose(data['objects'][-1]['vertices'], [[7, 20, 0], [10, 20, 0]])
        self.assertFalse(trace['visible'])
        np.testing.assert_array_equal(self.axis.a, self.original)

    def test_design_circle_centre_is_available_for_snapping(self):
        spec = dict(kind='alignment_profile', alignment=self.original.tolist(), start=5,
                    profile='design_circle', horizontal=2, vertical=-1)
        section = geometry.build(spec)
        centre, _, right, up, radius = self.axis.at(5)
        expected = centre + 2*right - up
        np.testing.assert_allclose(section['centre'], expected)
        np.testing.assert_allclose(np.linalg.norm(np.asarray(section['vertices'])-expected,axis=1), radius)
