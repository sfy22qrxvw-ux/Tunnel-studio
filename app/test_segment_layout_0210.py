"""Known ring geometry for observed, partially observed and layout-only panels."""
import tempfile
import unittest
from unittest import mock
import numpy as np

import lining_features as lf
import lining_tiles
from surface_extraction import Settings
from test_lining_features_0182 import model


class SegmentLayoutTests(unittest.TestCase):
    def detector(self, count=6, offset=15):
        d=lf.Detector(model(),(0.,8.),dict(lining_task='segments',lining_sector_count=count,
                                          lining_sector_offset_deg=offset),lambda m:None)
        d.rings=[d.add('ring_seam',label,station_m=c,coefficients=[c,.08,-.03])
                 for label,c in [('Left',2.),('Right',4.)]]
        return d

    def test_known_ring_only_labels_wrap_all_measured_panels_and_fit_lining(self):
        d=self.detector()
        d.segment()
        a=np.deg2rad(np.arange(15.5,375.5))
        p=lf.world(np.full_like(a,3.),a,d.m)
        labels=d.classify(p)
        self.assertTrue(np.all(labels.reshape(6,60)==np.array([r[0]['id'] for r in d.segments])[:,None]))
        for row,*_ in d.segments:
            self.assertEqual(row['confidence_score'],1)
            self.assertTrue(row['inferred'])
            self.assertEqual(row['boundary_sources'],['measured','inferred','measured','inferred'])
            self.assertEqual(len(row['boundaries']),2)
            self.assertAlmostEqual(np.linalg.norm(row['label_centre'][1:]),1.,places=5)
            for i,edge in enumerate(row['panel_outline']):
                np.testing.assert_allclose(edge[-1],row['panel_outline'][(i+1)%4][0],atol=2e-6)
        self.assertEqual(lf.detection_summary(d,'segments')['segment_confidence_counts'],dict(low=6,medium=0,high=0))
        self.assertFalse(d.longitudinal)
        occupied=np.ones(d.next_id,int)
        occupied[d.segments[-1][0]['id']]=0
        report=lf.detection_summary(d,'segments',occupied)
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['segment_confidence_counts']['low'],5)
        self.assertEqual(report['unoccupied_panel_sectors'],1)

    def test_real_joints_are_preserved_only_missing_sectors_are_split(self):
        d=self.detector(count=6)
        for a in (0.,60.,180.):
            d.longitudinal.append(d.add('longitudinal_seam','Observed',ring_interval=1,station_m=3.,
                                         arc_intercept_m=np.deg2rad(a),arc_slope=.02))
        d.segment()
        bands=[row['segment_confidence'] for row,*_ in d.segments]
        self.assertEqual(bands.count('high'),1)
        self.assertEqual(bands.count('medium'),4)
        self.assertEqual(bands.count('low'),1)
        self.assertEqual(len(d.longitudinal),3)
        for row,*_ in d.segments:
            self.assertEqual(row['confidence_score'],len(row['boundaries'])-1)

    def test_overlapping_or_unpaired_ring_planes_and_measured_only_mode(self):
        for condition in ('missing','overlapping','off'):
            d=self.detector(count=0 if condition=='off' else 6)
            if condition=='missing':d.rings.pop()
            if condition=='overlapping':d.rings[1]['coefficients']=[2.1,-.3,.3]
            d.segment();self.assertFalse(d.segments)
        for bad in (1,2.5,25,-3):
            with self.assertRaises(ValueError):Settings.read(dict(lining_sector_count=bad))

    def test_disk_backed_low_evidence_and_single_window_agree(self):
        m=model();a=np.arange(240)*2*np.pi/240;t=np.arange(.01,7.,.02)
        ts,angles=np.meshgrid(t,a,indexing='ij')
        p=lf.world(ts.ravel(),angles.ravel(),m)
        intensity=np.where((abs(ts-2.)<.022)|(abs(ts-4.)<.022),20.,100.).ravel()
        s=dict(lining_task='segments',lining_detail='detailed',lining_sector_count=5)
        d=lf.Detector(m,(0.,7.),s,lambda _:None)
        d.accumulate(p,intensity);d.analyse()
        self.assertEqual(len(d.segments),5)
        # Force the streaming interval path, including no longitudinal candidates.
        with tempfile.TemporaryDirectory() as work, mock.patch.object(lining_tiles,'INTERVAL_CELLS',1):
            tiled=lining_tiles.TiledDetector(m,(0.,7.),s,lambda _:None,work)
            try:
                tiled.accumulate(p,intensity);tiled.analyse()
                self.assertEqual(len(tiled.segments),5)
                check=lf.world(np.full(239,3.),a[1:]+.0005,m)
                for detector in (d,tiled):
                    self.assertEqual(len(np.unique(detector.classify(check))),5)
                    self.assertEqual(lf.detection_summary(detector,'segments')['segment_confidence_counts']['low'],5)
            finally:tiled.close()

if __name__=='__main__':unittest.main()
