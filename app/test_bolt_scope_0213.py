"""Raised/recessed geometry and full-density bolt windows above one million IDs."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import numpy as np
import feature_selection as local
import lining_features as lf
import lining_tiles
from surface_extraction import Settings
from test_bolts_0182 import patch
from test_lining_features_0182 import scene


class BoltScopeTests(unittest.TestCase):
    def scene(self):
        m,p,_=scene(empty=True,bolts=True)
        raised=(p[:,0]<8)&(np.linalg.norm(p[:,1:],axis=1)>1.02)
        p[raised,1:]*=.96/1.04
        return m,p,raised

    def cfg(self,side):return dict(lining_task='bolts',lining_bolt_kind=side,lining_bolt_height_mm=100,lining_depth_mm=12,lining_bolt_min_points=8)

    def test_local_pocket_selects_negative_dip_and_raised_mode_leaves_it(self):
        p,truth=patch();p[truth,2]-=.05
        recess=local.settings({'feature_kind':'recess'})
        labels,groups,stats,warnings=local.bolt_labels(p,np.array([0,0,-.025]),[0,0,1],recess,lambda _:None)
        self.assertEqual(len(groups),1);np.testing.assert_array_equal(labels>0,truth)
        self.assertIn('Recessed bolt pocket',groups[0]['name']);self.assertAlmostEqual(groups[0]['height_mm'],25,delta=.2)
        labels,groups,*_=local.bolt_labels(p,np.array([0,0,-.025]),[0,0,1],local.settings(),lambda _:None)
        self.assertFalse(labels.any());self.assertEqual(groups,[])

    def test_whole_cloud_sides_are_distinct_and_window_results_agree(self):
        m,p,truth=self.scene()
        for side,kind,station in [('raised','bolt_fitting',7),('recess','bolt_hole',9)]:
            cfg=self.cfg(side);d=lf.Detector(m,(0,12),cfg,lambda _:None)
            for start in range(0,len(p),100000):d.accumulate(p[start:start+100000])
            d.analyse();self.assertEqual(len(d.features),1);self.assertEqual(d.features[0]['kind'],kind)
            self.assertAlmostEqual(d.features[0]['station_m'],station,delta=.1)
            direct=d.classify(p)>0;self.assertTrue(direct.any())
            self.assertTrue(np.all((p[direct,0]<8) if side=='raised' else (p[direct,0]>8)))
            with tempfile.TemporaryDirectory() as tmp,mock.patch.object(lining_tiles,'WINDOW_CELLS',70000):
                tiled=lining_tiles.TiledDetector(m,(0,12),cfg,lambda _:None,tmp)
                try:
                    for start in range(0,len(p),100000):tiled.accumulate(p[start:start+100000])
                    tiled.analyse();self.assertGreater(tiled.windows,1)
                    np.testing.assert_array_equal(tiled.classify(p)>0,direct)
                finally:tiled.close()

    def test_more_than_one_million_source_ids_are_streamed_and_labelled(self):
        m,p,_=self.scene();repeat=6;total=len(p)*repeat;self.assertGreater(total,1000000)
        largest=0
        def batches():
            nonlocal largest
            for copy in range(repeat):
                for start in range(0,len(p),100000):
                    q=p[start:start+100000];largest=max(largest,len(q))
                    ids=np.arange(copy*len(p)+start,copy*len(p)+start+len(q),dtype='<i8')
                    yield ids,q,{'mode':'full'}
        with tempfile.TemporaryDirectory() as tmp:
            ids=Path(tmp)/'source.ids';labels=Path(tmp)/'source.labels'
            with mock.patch.object(lf.tunnel_lining,'fit_model',return_value=m),mock.patch.object(lf.tunnel_lining,'longitudinal_reference',return_value=m):
                result=lf.run_batches(batches,ids,labels,self.cfg('recess'),lambda _:None,lambda:False,total)
            self.assertEqual(result['statistics']['input_points'],total);self.assertLessEqual(largest,100000)
            self.assertEqual(ids.stat().st_size,total*8);self.assertEqual(labels.stat().st_size,total*4)
            all_ids=np.fromfile(ids,'<i8');self.assertEqual(all_ids[-1],total-1)
            lab=np.fromfile(labels,'<i4');candidate={f['id'] for f in result['features'] if f['kind']=='bolt_hole'}
            self.assertTrue(candidate);self.assertGreater(int(np.isin(lab,list(candidate)).sum()),repeat)
            for k in range(1,repeat):np.testing.assert_array_equal(lab[:len(p)],lab[k*len(p):(k+1)*len(p)])

    def test_new_settings_reject_bad_geometry_or_depth(self):
        for cfg in [{'lining_bolt_kind':'guess'},{'lining_bolt_height_mm':8,'lining_depth_mm':12},{'lining_bolt_min_points':1.5}]:
            with self.assertRaises(ValueError):Settings.read(cfg)
        self.assertEqual(Settings.read({'lining_bolt_kind':'recess','lining_bolt_height_mm':150}).lining_bolt_height_mm,150)
        with self.assertRaises(ValueError):local.settings({'feature_kind':'guess'})


if __name__=='__main__':unittest.main()
