"""Regression scenes for cropped rings and false circumferential evidence."""
import unittest
import numpy as np
import lining_features as lf
from test_lining_features_0182 import model


class DirectionalRingTests(unittest.TestCase):
    def test_unique_measured_sequence_keeps_extra_traces_unresolved(self):
        from ring_evidence import supported_sequence
        rows=[dict(station_m=x) for x in (0.,2.,3.,4.,5.)]
        used,unknown=supported_sequence(rows,dict(lining_ring_width_m=2.))
        self.assertEqual([r['station_m'] for r in used],[0.,2.,4.])
        self.assertEqual([r['station_m'] for r in unknown],[3.,5.])
        rows=[dict(station_m=x) for x in (0.,1.,2.,3.,4.,5.)]
        used,unknown=supported_sequence(rows,dict(lining_ring_width_m=2.))
        self.assertFalse(used)
        self.assertEqual(len(unknown),6)
        rows=[dict(station_m=x) for x in (0.,2.,3.,4.,5.,10.,12.)]
        used,unknown=supported_sequence(rows,dict(lining_ring_width_m=2.))
        self.assertEqual([r['station_m'] for r in used],[0.,2.,4.,10.,12.])

    def detector(self):
        d = lf.Detector(model(length=7., radius=3.), (0., 7.),
                        dict(lining_task='seams', lining_ring_width_m=2.), lambda _: None)
        d.count[:] = 1
        return d

    def test_cropped_tilted_joint_uses_visible_wall_without_inventing_spacing(self):
        d = self.detector()
        t = d.t0+(np.arange(d.nt)+.5)*d.cell
        a = (np.arange(d.na)+.5)*2*np.pi/d.na
        # Two disconnected measured wall arcs total less than 40% of a circle.
        visible = (a < 1.1) | ((a > 3.) & (a < 4.1))
        d.count[:, ~visible] = 0
        for station in (2., 4.3):
            seam = abs(t[:, None]-(station+.39*np.cos(a))[None, :]) < .015
            d.depth[seam] = .012
        d.analyse()
        self.assertEqual(len(d.rings), 0)
        unknown=d.unresolved_ring_candidates
        self.assertEqual(len(unknown),2)
        np.testing.assert_allclose([r['station_m'] for r in unknown], [2., 4.3], atol=.025)
        self.assertTrue(all(r['coverage_fraction'] < .4 for r in unknown))
        self.assertTrue(all(r['observed_coverage_fraction'] >= .8 for r in unknown))
        self.assertFalse(lf.ring_width_matches(d.s, 2.3))

    def test_bright_shallow_joints_win_over_deep_periodic_bolt_rows(self):
        d=self.detector()
        t=d.t0+(np.arange(d.nt)+.5)*d.cell
        a=(np.arange(d.na)+.5)*2*np.pi/d.na
        d.icount[:]=1;d.isum[:]=50.
        for station in (1.,3.,5.):
            seam=abs(t[:,None]-(station+.10*np.cos(a))[None,:])<.015
            d.isum[seam]=80.
            d.depth[seam]=.002
        for station in (2.,4.,6.):
            for angle in np.arange(0,2*np.pi,1.):
                pocket=(abs(t[:,None]-station)<.05)&(abs(lf.angular_delta(a,angle))[None,:]<.04)
                d.depth[pocket]=.08;d.isum[pocket]=10.
        d.analyse()
        np.testing.assert_allclose([r['station_m'] for r in d.rings],[1.,3.,5.],atol=.025)

    def test_isolated_end_trace_is_explicitly_unresolved(self):
        from ring_evidence import supported_sequence
        used,unknown=supported_sequence([dict(station_m=x) for x in (1.,3.,5.,10.9)],dict(lining_ring_width_m=2.))
        self.assertEqual([r['station_m'] for r in used],[1.,3.,5.])
        self.assertEqual([r['station_m'] for r in unknown],[10.9])

    def test_longitudinal_stripes_and_clipped_sloping_wall_are_not_rings(self):
        d = self.detector()
        t = d.t0+(np.arange(d.nt)+.5)*d.cell
        a = (np.arange(d.na)+.5)*2*np.pi/d.na
        d.depth[:] = .03+.008*t[:, None]
        for angle in (.4, 1.2, 2.1, 3.5, 4.3, 5.2):
            d.depth[:, abs(a-angle) < .02] += .02
        d.count[t[:, None] < (1.+.6*np.cos(a))[None, :]] = 0
        d.analyse()
        self.assertFalse(d.rings)

    def test_missing_band_has_no_measured_joint_support(self):
        d = self.detector()
        t = d.t0+(np.arange(d.nt)+.5)*d.cell
        d.count[abs(t-3.) < .05] = 0
        d.analyse()
        self.assertFalse(d.rings)


if __name__ == '__main__':
    unittest.main()
