"""Measured-evidence regressions for lining seams, panels and recess candidates.

These constructed scenes check implementation behaviour, not field precision.
"""
from pathlib import Path
import tempfile, unittest
from unittest import mock
import numpy as np
import lining_features as lf
import lining_tiles


def model(length=12., radius=1.):
    stations=np.arange(-1.,length+2.)
    nodes=np.c_[stations,np.zeros((len(stations),2)),np.full(len(stations),radius)]
    return dict(reference=np.zeros(3),axis=np.array([1.,0.,0.]),u=np.array([0.,1.,0.]),
                v=np.array([0.,0.,1.]),nodes=nodes,pitch=1.,radius=radius,nangle=240,t0=-1.,
                profile=np.zeros(240),target=np.zeros((len(nodes),240)),
                supported=np.ones((len(nodes),240),bool))


def scene(*, intensity=True, detail='detailed', tilted=False, bolts=False, empty=False,depth_m=.04):
    m=model();cell=lf.PRESETS[detail]['cell'];na=int(np.ceil(2*np.pi/cell))
    tt,aa=np.meshgrid(np.arange(.5*cell,12.,cell),(np.arange(na)+.5)*2*np.pi/na,indexing='ij')
    left=2.+(.16*np.cos(aa)+.08*np.sin(aa) if tilted else 0.)
    right=4.+(.12*np.cos(aa)-.10*np.sin(aa) if tilted else 0.)
    recess=(abs(tt-left)<cell*.75)|(abs(tt-right)<cell*.75)
    for angle in (.5,2.6,4.8):
        joint=angle+.08*(tt-3.)
        recess|=(tt>left)&(tt<right)&(abs(lf.angular_delta(aa,joint))<cell*.75)
    if empty:recess[:]=False
    depth=np.zeros(tt.shape) if intensity else np.where(recess,depth_m,0.)
    if bolts:
        for station,angle in ((7.,.01),(9.,3.)):
            hole=(tt-station)**2+lf.angular_delta(aa,angle)**2<.08**2
            depth[hole]=.04
    rr=1.+depth
    points=np.c_[tt.ravel(),(rr*np.cos(aa)).ravel(),(rr*np.sin(aa)).ravel()]
    values=np.where(recess,25.,100.).ravel() if intensity else None
    return m,points,values


def analyse(m,p,values,**settings):
    d=lf.Detector(m,(0.,12.),dict(lining_task='segments',**settings),lambda _:None)
    for start in range(0,len(p),25000):
        d.accumulate(p[start:start+25000],values[start:start+25000] if values is not None else None)
    d.analyse()
    return d


class LiningFeatureRegressionTests(unittest.TestCase):
    def test_zero_mad_quantized_intensity_still_finds_measured_seams(self):
        m,p,values=scene()
        d=analyse(m,p,values)
        self.assertTrue(d.intensity_used)
        self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))
        self.assertFalse(d.geom.any())
        np.testing.assert_allclose([r['station_m'] for r in d.rings],[2.,4.],atol=.02)
        np.testing.assert_allclose([r['arc_slope'] for r in d.longitudinal],.08,atol=.01)

    def test_intensity_units_and_offset_do_not_determine_segmentation(self):
        m,p,values=scene()
        d=analyse(m,p,values*1e-6+1000.)
        self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))

    def test_constant_wall_and_missing_bands_do_not_become_seams(self):
        m,p,values=scene(empty=True)
        keep=(abs(p[:,0]-2.)>.05)&(abs(p[:,0]-4.)>.05)
        d=analyse(m,p[keep],values[keep])
        self.assertFalse(d.intensity_used)
        self.assertFalse(d.dark.any())
        self.assertFalse(d.geom.any())
        self.assertFalse(d.features)

    def test_geometry_only_and_tilted_ring_planes_are_supported(self):
        m,p,values=scene(intensity=False,tilted=True)
        d=analyse(m,p,values)
        self.assertFalse(d.intensity_used)
        self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))
        np.testing.assert_allclose(d.rings[0]['coefficients'],[2.,.16,.08],atol=.02)
        np.testing.assert_allclose(d.rings[1]['coefficients'],[4.,.12,-.1],atol=.02)

    def test_shallow_seams_have_a_separate_threshold_from_bolt_pockets(self):
        m,p,values=scene(intensity=False,depth_m=.006)
        d=analyse(m,p,values)
        self.assertFalse(d.geom.any())
        self.assertTrue(d.seam_geom.any())
        self.assertEqual(d.threshold,.012)
        self.assertEqual(d.seam_threshold,.004)
        self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))

    def test_uniform_broad_recess_is_not_split_into_many_joint_lines(self):
        d=lf.Detector(model(),(0.,12.),dict(lining_task='segments'),lambda _:None)
        d.count[:]=1;d.depth[:]=.04
        d.analyse(features=False)
        d.rings=[dict(id=4,station_m=2.,coefficients=[2.,0.,0.]),dict(id=5,station_m=4.,coefficients=[4.,0.,0.])]
        d.next_id=6;d.between_seams()
        self.assertFalse(d.longitudinal)

    def test_one_ambiguous_boundary_does_not_erase_independent_panels(self):
        d=lf.Detector(model(),(0.,12.),dict(lining_task='segments'),lambda _:None)
        d.rings=[d.add('ring_seam','Left',station_m=2.,coefficients=[2.,0.,0.]),
                 d.add('ring_seam','Right',station_m=4.,coefficients=[4.,0.,0.])]
        for intercept,slope in ((.3,0.),(.32,.2),(1.6,0.),(2.9,0.),(4.2,0.),(5.4,0.)):
            d.longitudinal.append(d.add('longitudinal_seam','Joint',ring_interval=1,station_m=3.,arc_intercept_m=intercept,arc_slope=slope))
        d.segment()
        self.assertEqual(len(d.segments),3)
        ambiguous={r['id'] for r in d.longitudinal if r['segment_boundary_status']=='ambiguous'}
        self.assertEqual(len(ambiguous),2)
        self.assertTrue(all(not ambiguous.intersection(row['boundaries']) for row,*_ in d.segments))
        status=lf.detection_summary(d,'segments')
        self.assertEqual(status['status'],'partial')
        self.assertEqual(status['ambiguous_segment_boundaries'],2)

    def test_millimetre_noise_does_not_produce_a_segment_layout(self):
        m,p,values=scene(empty=True)
        rng=np.random.default_rng(182)
        p[:,1:]*=(1+rng.normal(0,.001,len(p)))[:,None]
        values+=rng.normal(0,.2,len(values))
        d=analyse(m,p,values)
        self.assertFalse(d.rings)
        self.assertFalse(d.longitudinal)
        self.assertFalse(d.segments)

    def test_wrap_recess_and_seams_remain_separate_candidates(self):
        m,p,values=scene(intensity=False,bolts=True)
        d=lf.Detector(m,(0.,12.),dict(lining_task='all'),lambda _:None)
        d.accumulate(p);d.analyse()
        holes=[row for row in d.features if row['kind']=='bolt_hole']
        self.assertEqual(len(holes),2)
        np.testing.assert_allclose(sorted(r['station_m'] for r in holes),[7.,9.],atol=.02)
        self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))
        labels=d.classify(p)
        self.assertTrue(all(np.count_nonzero(labels==r['id'])>=3 for r in holes))

    def test_tiled_intervals_keep_ring_and_panel_identities(self):
        m,p,values=scene()
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(lining_tiles,'WINDOW_CELLS',80000):
                d=lining_tiles.TiledDetector(m,(0.,12.),dict(lining_task='segments'),lambda _:None,folder)
            try:
                for start in range(0,len(p),25000):d.accumulate(p[start:start+25000],values[start:start+25000])
                d.analyse()
                self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))
                ids=[row['id'] for row in d.features]
                self.assertEqual(len(ids),len(set(ids)))
                labels=d.classify(p)
                segment_ids=[row['id'] for row,*_ in d.segments]
                self.assertGreater(np.isin(labels,segment_ids).sum(),20000)
                self.assertTrue(all(set(row['boundaries']).issubset(ids) for row,*_ in d.segments))
                self.assertLessEqual(len(d.evidence.maps),2)
                self.assertLessEqual(len(d.labels.maps),2)
            finally:d.close()
            self.assertFalse(list(Path(folder).iterdir()))

    def test_streamed_wide_interval_uses_measured_seams(self):
        m,p,values=scene()
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(lining_tiles,'WINDOW_CELLS',80000),mock.patch.object(lining_tiles,'INTERVAL_CELLS',20000):
                d=lining_tiles.TiledDetector(m,(0.,12.),dict(lining_task='segments'),lambda _:None,folder)
                try:
                    d.accumulate(p,values);d.analyse()
                    self.assertEqual((len(d.rings),len(d.longitudinal),len(d.segments)),(2,3,3))
                    labels=d.classify(p)
                    self.assertGreater(np.isin(labels,[row['id'] for row,*_ in d.segments]).sum(),20000)
                finally:d.close()

    def test_full_point_labelling_preserves_nonconsecutive_original_ids(self):
        m,p,values=scene()
        original=p.copy();ids=np.arange(len(p),dtype=np.int64)*7+3
        def batches():
            for start in range(0,len(p),13001):yield ids[start:start+13001],p[start:start+13001],dict(mode='full')
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder);id_path=target/'ids';label_path=target/'labels'
            # Calibration is fixed here to isolate the full-density output
            # contract; the real-file evidence exercises actual calibration.
            with mock.patch.object(lf.tunnel_lining,'fit_model',return_value=m),mock.patch.object(lf.tunnel_lining,'longitudinal_reference',return_value=m):
                result=lf.run_batches(batches,id_path,label_path,dict(lining_task='segments'),lambda _:None,lambda:False,len(p),lambda ix:values[(ix-3)//7])
            np.testing.assert_array_equal(np.fromfile(id_path,'<i8'),ids)
            self.assertEqual(label_path.stat().st_size,len(p)*4)
            self.assertEqual(sum(row['points'] for row in result['clusters']),len(p))
            self.assertEqual(result['statistics']['kept_points'],len(p))
            self.assertEqual(result['statistics']['seam_depth_threshold_mm'],4.)
            self.assertEqual(result['detection']['segment_candidates'],3)
        np.testing.assert_array_equal(p,original)


if __name__=='__main__':unittest.main()
