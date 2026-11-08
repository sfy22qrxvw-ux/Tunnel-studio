# Copyright (c) 2026 Damian Gulczynski.
"""Exact, bounded and cancellable display loading regressions."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import copy
import hashlib
import json
import os
import socket
import tempfile
import unittest

import numpy as np
import detail_preview
import jobdata
import preview_index
import preview_store
import registration_pose
from checker_evidence import priority_keys


class RenderLoading0186Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name); n = 280013
        ids = np.arange(n, dtype=np.float64)
        self.xyz = np.column_stack([420000 + ids*.0002, 310000 + 3*np.sin(ids*.00017),
                                    110 + 2*np.cos(ids*.00011)])
        self.attrs = np.column_stack([ids % 256, ids % 256, (ids*3) % 256, (ids*7) % 256]).astype('<f4')
        self.xyz.astype('<f8').tofile(self.work/'cloud.xyz64')
        self.attrs.tofile(self.work/'cloud.attr32')
        self.meta = dict(file=str(self.work/'cloud.xyz64'), attributes_file=str(self.work/'cloud.attr32'),
                         count=n, minimum=self.xyz.min(0).tolist(), maximum=self.xyz.max(0).tolist(),
                         attribute_counts=[n]*4)
        self.item = dict(id='scan', kind='scan', name='Scan', meta=self.meta, visible=True,
                         hidden_boxes=[], isolate_box=None)
        self.origin = (self.xyz.min(0)+self.xyz.max(0))/2
        self.view = dict(target=self.origin.tolist(), right=[1.,0.,0.], up=[0.,1.,0.],
                         forward=[0.,0.,1.], scale=100., aspect=1.)
        self.fallback = [.22,.73,.81]
        self.before = self.hashes()

    def hashes(self):
        return {k:hashlib.sha256(Path(self.meta[k]).read_bytes()).hexdigest()
                for k in ('file','attributes_file')}

    def sample(self, budget=17000, quick=False, cancel=None):
        return detail_preview.sample(self.item,budget,self.origin,self.fallback,'rgb',
                                     (100.,120.),self.view,quick=quick,cancel=cancel)

    def oracle(self, budget):
        q = self.xyz @ registration_pose.get(self.meta)[0].T + registration_pose.get(self.meta)[1]
        axes = np.array([self.view[k] for k in ('right','up','forward')])
        local = (q-np.array(self.view['target'])) @ axes.T
        if self.view.get('projection') == 'perspective':
            z=local[:,2]
            use=(z>=self.view['near'])&(z<=self.view['far'])
            use&=(np.abs(local[:,0])<=z*self.view['scale']*self.view['aspect']*1.15)
            use&=(np.abs(local[:,1])<=z*self.view['scale']*1.15)
        else:
            use=(np.abs(local[:,:2])<=[self.view['scale']*1.15,
                                     self.view['scale']/self.view['aspect']*1.15]).all(1)
        if self.meta.get('mask_file'):
            use &= np.fromfile(self.meta['mask_file'],'u1')==0
        if self.item.get('limit_box') and self.item.get('limit_hide_outside',True):
            b=self.item['limit_box'];use&=((q>=b['minimum'])&(q<=b['maximum'])).all(1)
        if self.item.get('isolate_box'):
            b=self.item['isolate_box'];use&=((q>=b['minimum'])&(q<=b['maximum'])).all(1)
        for b in self.item['hidden_boxes']:
            use&=~((q>=b['minimum'])&(q<=b['maximum'])).all(1)
        ids=np.flatnonzero(use)
        if len(ids)>budget:ids=ids[np.argpartition(priority_keys(ids),budget-1)[:budget]]
        ids.sort()
        expected=np.empty((len(ids),6),'<f4')
        expected[:,:3]=q[ids]-self.origin;expected[:,3:]=self.attrs[ids,1:]/255.
        return expected,ids

    def test_exact_original_point_ids_with_amortised_priority_selection(self):
        values=np.arange(2100000,dtype=np.int64)
        sampler=detail_preview._PrioritySample(180000)
        for start in range(0,len(values),16384):sampler.add(values[start:start+16384])
        expected=np.sort(values[np.argpartition(priority_keys(values),179999)[:180000]])
        np.testing.assert_array_equal(sampler.finish(),expected)
        self.assertLess(sampler.partitions,12)
        self.assertLessEqual(sampler.ids.nbytes+sampler.keys.nbytes,6*1024*1024)

    def test_first_detail_indexes_during_one_bounded_scan_pass(self):
        original=np.fromfile;reads=[]
        def read(source,dtype=float,count=-1,*args,**kwargs):
            if getattr(source,'name',None)==self.meta['file']:
                reads.append(count)
                self.assertEqual(np.dtype(dtype),np.dtype('<f8'))
                self.assertGreater(count,0)
                self.assertLessEqual(count,detail_preview.READ_POINTS*3)
            return original(source,dtype,count,*args,**kwargs)
        with patch.object(np,'fromfile',side_effect=read),patch.object(preview_index,'bounds',side_effect=AssertionError('Second index scan')):
            shown=self.sample()
        self.assertEqual(sum(reads),len(self.xyz)*3)
        np.testing.assert_array_equal(shown,self.oracle(17000)[0])
        self.assertTrue(Path(self.meta['file']+'.viewindex.npz').is_file())
        self.assertEqual(self.hashes(),self.before)

    def test_detail_cache_avoids_source_reads_and_has_owned_provenance(self):
        shown=self.sample()
        original=np.fromfile
        def read(source,*args,**kwargs):
            self.assertNotIn(getattr(source,'name',None),(self.meta['file'],self.meta['attributes_file']))
            return original(source,*args,**kwargs)
        with patch.object(np,'fromfile',side_effect=read),patch.object(jobdata,'_display_rows',side_effect=AssertionError('Unexpected sample read')):
            np.testing.assert_array_equal(shown,self.sample())
        path=preview_store.cache_path(self.item,17000,self.origin,self.fallback,'rgb',(100.,120.),False,view=self.view)
        self.assertEqual(json.loads(Path(str(path)+'.owner.json').read_text())['source_file'],self.meta['file'])

    def test_pose_mask_clip_hidden_region_and_changed_view_match_exact_oracle(self):
        angle=.08;R=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1.]])
        self.meta.update(registration_rotation=R.tolist(),registration_translation=[2.,3.,4.])
        q=self.xyz@R.T+np.array([2.,3.,4.]);self.origin=(q.min(0)+q.max(0))/2
        self.view.update(target=self.origin.tolist(),scale=18.,aspect=1.8)
        flags=np.zeros(len(self.xyz),'u1');flags[::7]=1;flags[80000:95000]=1
        flags.tofile(self.work/'edited.mask');self.meta['mask_file']=str(self.work/'edited.mask')
        self.item['limit_box']=dict(minimum=(q.min(0)+[3.,0.,0.]).tolist(),maximum=q.max(0).tolist())
        self.item['isolate_box']=dict(mode='box',minimum=q.min(0).tolist(),maximum=(q.max(0)-[2.,0.,0.]).tolist())
        self.item['hidden_boxes']=[dict(mode='box',minimum=(self.origin-[3.,3.,3.]).tolist(),
                                       maximum=(self.origin+[3.,3.,3.]).tolist())]
        np.testing.assert_array_equal(self.sample(),self.oracle(17000)[0])
        self.view['target'][0]+=8.
        np.testing.assert_array_equal(self.sample(),self.oracle(17000)[0])
        self.assertEqual(self.hashes(),self.before)

    def test_mask_edit_invalidates_the_cached_detail(self):
        flags=np.zeros(len(self.xyz),'u1');flags.tofile(self.work/'edited.mask')
        self.meta['mask_file']=str(self.work/'edited.mask')
        shown=self.sample();_,ids=self.oracle(17000)
        flags[ids[:200]]=1;flags.tofile(self.meta['mask_file'])
        stamp=Path(self.meta['mask_file']).stat();os.utime(self.meta['mask_file'],ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1000000))
        updated=self.sample()
        self.assertFalse(np.array_equal(updated,shown))
        np.testing.assert_array_equal(updated,self.oracle(17000)[0])

    def test_full_detail_can_exceed_overview_budget(self):
        jobdata._source_overview(self.meta,build=True)
        shown=self.sample(270000)
        self.assertEqual(len(shown),270000)
        np.testing.assert_array_equal(shown,self.oracle(270000)[0])

    def test_high_detail_reuses_source_rows_across_colour_and_pose_changes(self):
        first=jobdata._item_sample_uncached(self.item,270000,self.origin,self.fallback,'rgb')
        self.meta['registration_translation']=[2.,3.,4.]
        with patch.object(jobdata,'_display_rows',side_effect=AssertionError('High detail reread source')):
            updated=jobdata._item_sample_uncached(self.item,270000,self.origin,self.fallback,'intensity-gray')
        self.assertEqual(len(updated),270000)
        np.testing.assert_allclose(updated[:,:3],first[:,:3]+[2.,3.,4.],atol=2e-6)
        np.testing.assert_array_equal(updated[:,3],updated[:,4])
        np.testing.assert_array_equal(updated[:,4],updated[:,5])
        self.assertGreater(float(np.ptp(updated[:,3])),.85)
        self.assertIsNotNone(preview_store.read_overview(self.meta))
        self.assertEqual(self.hashes(),self.before)

    def test_quick_view_reuses_measured_colours_without_source_or_index_scan(self):
        jobdata._source_overview(self.meta,build=True)
        self.view.update(scale=8.,aspect=1.7)
        with patch.object(jobdata,'_display_rows',side_effect=AssertionError('Unexpected source read')),patch.object(preview_index,'bounds',side_effect=AssertionError('Unexpected full index scan')):
            shown=self.sample(800000,quick=True)
        self.assertGreater(len(shown),1000)
        self.assertLessEqual(len(shown),250000)
        self.assertGreater(float(np.ptp(shown[:,3])),.9)
        self.assertFalse(Path(self.meta['file']+'.viewindex.npz').exists())

    def test_quick_intensity_overview_has_measured_greyscale(self):
        self.attrs[:,1:]=np.nan;self.attrs.tofile(self.meta['attributes_file'])
        self.meta['attribute_counts']=[len(self.xyz),0,0,0]
        jobdata._source_overview(self.meta,build=True)
        shown=self.sample(100000,quick=True)
        np.testing.assert_array_equal(shown[:,3],shown[:,4])
        np.testing.assert_array_equal(shown[:,4],shown[:,5])
        self.assertGreater(float(np.ptp(shown[:,3])),.85)

    def test_legacy_quick_view_does_not_scan_whole_mask_or_request_full_budget(self):
        flags=np.zeros(len(self.xyz),'u1');flags[::3]=1;flags.tofile(self.work/'edited.mask')
        self.meta['mask_file']=str(self.work/'edited.mask')
        original=jobdata._display_rows;requests=[]
        def read(path,count,ids,*args,**kwargs):
            requests.append((str(path),len(ids)))
            self.assertLessEqual(len(ids),20000)
            return original(path,count,ids,*args,**kwargs)
        with patch.object(jobdata,'_display_rows',side_effect=read),patch.object(np,'fromfile',side_effect=AssertionError('Unexpected full mask pass')):
            shown=jobdata._item_sample_uncached(self.item,1000000,self.origin,self.fallback,quick=True)
        self.assertGreater(len(shown),1000)
        self.assertLessEqual(len(shown),20000)
        self.assertGreater(float(np.ptp(shown[:,3])),.9)
        self.assertEqual({p for p,_ in requests},{self.meta['file'],self.meta['attributes_file']})

    def test_no_mapping_exceeds_64_mib(self):
        original=np.memmap;sizes=[]
        def mapped(path,dtype=float,*args,**kwargs):
            shape=kwargs.get('shape')
            if shape is not None:
                size=int(np.prod(shape))*np.dtype(dtype).itemsize;sizes.append(size)
                self.assertLessEqual(size,64*1024*1024)
            return original(path,dtype,*args,**kwargs)
        with patch.object(np,'memmap',side_effect=mapped):
            shown=self.sample()
        self.assertTrue(sizes);self.assertEqual(len(shown),17000)

    def test_cancelled_detail_stops_before_full_read_and_publishes_no_index_or_cache(self):
        checks=0
        def cancel():
            nonlocal checks
            checks+=1
            return checks>=3
        with self.assertRaises(InterruptedError):self.sample(cancel=cancel)
        self.assertFalse(Path(self.meta['file']+'.viewindex.npz').exists())
        self.assertFalse(any(self.work.glob('display_cache/*.npy')))
        self.assertEqual(self.hashes(),self.before)

    def test_perspective_camera_matches_exact_original_points(self):
        self.view.update(projection='perspective',target=(self.origin-[30.,0.,0.]).tolist(),
                         right=[0.,1.,0.],up=[0.,0.,1.],forward=[1.,0.,0.],
                         scale=.2,aspect=1.6,near=.03,far=100.)
        np.testing.assert_array_equal(self.sample(),self.oracle(17000)[0])
        # Repeat after moving the camera: cached block bounds must not impose a stale view.
        self.view['scale']=.1
        np.testing.assert_array_equal(self.sample(),self.oracle(17000)[0])

    def test_aborted_http_connection_is_detected_without_consuming_next_request(self):
        left,right=socket.socketpair()
        self.addCleanup(left.close);self.addCleanup(right.close)
        handler=SimpleNamespace(connection=left)
        self.assertFalse(detail_preview.request_cancelled(handler))
        right.sendall(b'GET /next')
        self.assertFalse(detail_preview.request_cancelled(handler))
        self.assertEqual(left.recv(9),b'GET /next')
        right.close()
        self.assertTrue(detail_preview.request_cancelled(handler))

    def test_binary_route_stops_a_cancelled_view_without_a_spurious_response(self):
        import job_api
        state=SimpleNamespace(JOB={'busy':False},STATE={'job_items':[self.item]})
        handler=SimpleNamespace(send=lambda *a,**k:self.fail('Cancelled response sent'))
        with patch.object(jobdata,'preview_binary',side_effect=InterruptedError('Stopped')):
            self.assertTrue(job_api.get(state,handler,'/api/job-preview-bin',{}))
        self.assertTrue(handler.close_connection)


if __name__=='__main__':
    unittest.main()
