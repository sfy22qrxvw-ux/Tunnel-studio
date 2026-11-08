# Copyright (c) 2026 Damian Gulczynski.
from pathlib import Path
import tempfile,unittest
from types import SimpleNamespace
from unittest import mock
import numpy as np
import ray_box_extraction


def scene(translation=(0,0,0)):
    rng=np.random.default_rng(24);stations=np.arange(0,8,.08);angles=np.linspace(0,2*np.pi,180,endpoint=False)
    t,a=np.meshgrid(stations,angles,indexing='ij');radius=3+.015*np.sin(3*a)+rng.normal(0,.003,a.shape)
    wall=np.c_[t.ravel(),(radius*np.cos(a)).ravel(),(radius*np.sin(a)).ravel()]
    # Dense services in front of the right wall. Wall returns remain present in
    # the same station/angle ray boxes and must not be replaced by the service.
    service_a=np.linspace(-.24,.24,70);service_t=np.arange(1,7,.04);tt,aa=np.meshgrid(service_t,service_a,indexing='ij')
    rr=2.25+rng.normal(0,.004,tt.shape);service=np.c_[tt.ravel(),(rr*np.cos(aa)).ravel(),(rr*np.sin(aa)).ravel()]
    points=np.vstack([wall,service])+np.asarray(translation,float)
    truth=np.r_[np.ones(len(wall),bool),np.zeros(len(service),bool)]
    return points,truth


class RayBoxTests(unittest.TestCase):
    def run_method(self,points):
        with tempfile.TemporaryDirectory() as folder:
            ids=Path(folder)/'ids';labels=Path(folder)/'labels'
            def batches():
                for start in range(0,len(points),5000):
                    stop=min(len(points),start+5000);yield np.arange(start,stop),points[start:stop],{'mode':'full'}
            result=ray_box_extraction.run_batches(batches,ids,labels,dict(resolution=.2,envelope_thickness=.08,envelope_angle_deg=3,envelope_quantile=.7,ray_station_m=.25,ray_angle_deg=3,ray_depth_mm=20,ray_keep_mm=60,ray_min_points=4,ray_inward_mm=1200,ray_outward_mm=300),lambda _:None,lambda:False)
            return np.fromfile(labels,'<i4'),result

    def test_wall_behind_service_is_retained_and_service_rejected(self):
        points,truth=scene();labels,result=self.run_method(points);pred=np.isin(labels,result['kept'])
        self.assertGreater(pred[truth].mean(),.98);self.assertGreater((~pred[~truth]).mean(),.95)
        self.assertEqual(len(labels),len(points));self.assertEqual(sum(c['points'] for c in result['clusters']),len(points))
        self.assertGreater(result['statistics']['supported_ray_boxes'],0)

    def test_translation_does_not_change_labels(self):
        points,_=scene();a,_=self.run_method(points);b,_=self.run_method(points+[200000,375000,100])
        # Trigonometric synthetic coordinates gain sub-nanometre rounding when
        # translated to large eastings. Only boundary-bin ties may differ.
        self.assertLess(np.mean(a!=b),.0002)

    def test_cancel_preserves_output_contract(self):
        points,_=scene()
        with tempfile.TemporaryDirectory() as folder:
            ids=Path(folder)/'ids';labels=Path(folder)/'labels';ids.write_bytes(b'old');labels.write_bytes(b'old')
            def batches():yield np.arange(len(points)),points,{'mode':'full'}
            with self.assertRaisesRegex(ValueError,'cancelled'):
                ray_box_extraction.run_batches(batches,ids,labels,{},lambda _:None,lambda:True)
            self.assertEqual(ids.read_bytes(),b'old');self.assertEqual(labels.read_bytes(),b'old')

    def test_histogram_tiles_match_single_window_including_boundaries(self):
        rng=np.random.default_rng(9);counts=rng.integers(0,5,size=(17,12,8),dtype=np.uint32)
        centres=np.linspace(-.3,.1,8,dtype=np.float32)
        expected=ray_box_extraction._surface_from_histogram(counts,centres,4,lambda _:None,lambda:False)
        with tempfile.TemporaryDirectory() as folder:
            histogram=ray_box_extraction.BoxHistogram(Path(folder)/'counts',*counts.shape)
            try:
                station,angular,radial=np.indices(counts.shape)
                repeated=counts.ravel().astype(int)
                histogram.add(np.repeat(station.ravel(),repeated),np.repeat(angular.ravel(),repeated),np.repeat(radial.ravel(),repeated))
                # One core station per window forces a boundary at every row.
                with mock.patch.object(ray_box_extraction,'HISTOGRAM_WINDOW_BYTES',3*12*8*4):
                    actual=histogram.surface(centres,4,lambda _:None,lambda:False)
                np.testing.assert_array_equal(actual[0],expected[0])
                np.testing.assert_array_equal(actual[1],expected[1])
                self.assertLessEqual(len(histogram.rows.maps),2)
            finally:histogram.close()
            self.assertFalse(histogram.rows.maps)

    def test_non_divisible_radial_range_uses_the_reported_effective_depth(self):
        points=np.tile([1.,2.99,0.],(80,1))
        model=dict(reference=np.zeros(3),axis=np.array([1.,0.,0.]),u=np.array([0.,1.,0.]),v=np.array([0.,0.,1.]),nodes=np.array([[0.,0.,0.,3.],[2.,0.,0.,3.]]),pitch=1.,radius=3.)
        def batches():yield np.arange(len(points)),points,{'mode':'full'}
        with tempfile.TemporaryDirectory() as folder:
            ids=Path(folder)/'ids';labels=Path(folder)/'labels'
            with mock.patch.object(ray_box_extraction.tunnel_lining,'fit_model',return_value=model),mock.patch.object(ray_box_extraction.tunnel_lining,'spatial_sample',return_value=(points,len(points))):
                result=ray_box_extraction.run_batches(batches,ids,labels,dict(ray_inward_mm=100,ray_outward_mm=20,ray_depth_mm=100,ray_keep_mm=5),lambda _:None,lambda:False)
            np.testing.assert_array_equal(np.fromfile(labels,'<i4'),0)
            self.assertAlmostEqual(result['statistics']['ray_depth_mm'],60)

    def test_cancel_after_indexing_closes_mappings_and_removes_temporary_index(self):
        points,_=scene();cancelled=False;closed=[]
        original_close=ray_box_extraction.BoxHistogram.close
        def close(histogram):
            original_close(histogram);closed.append(not histogram.rows.maps)
        def progress(message):
            nonlocal cancelled
            if message.startswith('2/4 Indexing'):cancelled=True
        def batches():
            for start in range(0,len(points),5000):yield np.arange(start,min(start+5000,len(points))),points[start:start+5000],{'mode':'full'}
        with tempfile.TemporaryDirectory() as folder:
            ids=Path(folder)/'ids';labels=Path(folder)/'labels';ids.write_bytes(b'old');labels.write_bytes(b'old')
            with mock.patch.object(ray_box_extraction.BoxHistogram,'close',close),self.assertRaisesRegex(ValueError,'cancelled'):
                ray_box_extraction.run_batches(batches,ids,labels,{},progress,lambda:cancelled)
            self.assertEqual(closed,[True]);self.assertFalse(list(Path(folder).glob('ray_box_*')))
            self.assertEqual(ids.read_bytes(),b'old');self.assertEqual(labels.read_bytes(),b'old')

    def test_api_routing_preserves_gu_sac_default_and_all_retained_original_ids(self):
        import pointio,surface_api,workflows
        from surface_extraction import Settings
        self.assertEqual(Settings.read().method,'gusac')
        self.assertEqual(Settings.read({'method':'ray_box'}).method,'ray_box')
        with self.assertRaisesRegex(ValueError,'whole number'):Settings.read({'method':'ray_box','ray_min_points':3.5})
        points,_=scene((200000.,375000.,100.))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.xyz';np.savetxt(source,points,fmt='%.17g')
            meta=pointio.import_cloud(source,root/'source.xyz64')
            excluded=np.zeros(len(points),np.uint8);excluded[::101]=1
            mask=root/'mask';excluded.tofile(mask);meta['mask_file']=str(mask)
            before=Path(meta['file']).read_bytes();state=workflows.empty();state['surface_source']=meta
            server=SimpleNamespace(STATE=state,WORK=root,SELECTION=None,JOB={'busy':False},progress=lambda _:None,save=lambda:None)
            surface_api.CANCEL.clear()
            surface_api.run(server,{'source':'separate','settings':{'method':'ray_box'},'selection':{'mode':'full'}})
            result=state['surface_result']
            self.assertEqual(result['settings']['method'],'ray_box')
            self.assertEqual(result['doi'],ray_box_extraction.DOI)
            np.testing.assert_array_equal(np.fromfile(result['point_ids_file'],'<i8'),np.flatnonzero(excluded==0))
            self.assertEqual(sum(c['points'] for c in result['clusters']),int((excluded==0).sum()))
            self.assertEqual(Path(meta['file']).read_bytes(),before)


if __name__=='__main__':unittest.main()
