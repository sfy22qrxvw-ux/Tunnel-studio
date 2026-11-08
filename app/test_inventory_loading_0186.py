# Copyright (c) 2026 Damian Gulczynski.
"""Legacy inventory stays responsive without weakening exact scan properties."""
import copy
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import job_api
import jobdata
import pointio
import registration_pose
import scan_edits


class InventoryLoading0186Tests(unittest.TestCase):
    def setUp(self):
        self.wait_worker()
        with scan_edits._DISPLAY_LOCK:
            scan_edits._DISPLAY_COUNTS.clear();scan_edits._DISPLAY_QUEUE.clear()
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.wait_worker)
        n=4099;ids=np.arange(n,dtype=float)
        self.xyz=np.column_stack([ids,ids*.25,ids*.5])
        self.attrs=np.column_stack([ids%256,ids%256,(ids*3)%256,(ids*7)%256]).astype('<f4')
        self.attrs[::7,0]=np.nan
        self.flags=np.zeros(n,'u1');self.flags[:71]=1;self.flags[-53:]=1;self.flags[200::5]=1
        self.xyz.astype('<f8').tofile(self.root/'scan.xyz64')
        self.attrs.tofile(self.root/'scan.attr32');self.flags.tofile(self.root/'old.mask')
        self.meta=dict(name='Legacy setup',file=str(self.root/'scan.xyz64'),
                       attributes_file=str(self.root/'scan.attr32'),mask_file=str(self.root/'old.mask'),
                       count=n,minimum=self.xyz.min(0).tolist(),maximum=self.xyz.max(0).tolist(),
                       attribute_counts=np.isfinite(self.attrs).sum(0).tolist())
        self.item=dict(id='scan',kind='scan',name='Legacy setup',meta=self.meta,visible=True,
                       source_bytes=163960,hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[])
        self.server=SimpleNamespace(WORK=self.root,STATE={'job_items':[self.item]},JOB={'busy':False},
                                    save=lambda:None,progress=lambda _:None)
        self.originals={k:hashlib.sha256(Path(self.meta[k]).read_bytes()).hexdigest()
                        for k in ('file','attributes_file')}

    def wait_worker(self):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with scan_edits._DISPLAY_LOCK:worker=scan_edits._DISPLAY_THREAD
            if worker is None:return
            worker.join(.05)
        self.fail('The display count worker did not finish.')

    def assert_sources_unchanged(self):
        for key,digest in self.originals.items():
            self.assertEqual(hashlib.sha256(Path(self.meta[key]).read_bytes()).hexdigest(),digest)

    def test_legacy_job_inventory_returns_while_count_is_pending_and_coalesces_reads(self):
        started=threading.Event();release=threading.Event();calls=[]
        original=scan_edits._read_mask_count
        def held(*args,**kwargs):
            calls.append(args[1]);started.set()
            if not release.wait(3):raise RuntimeError('Test count was not released.')
            return original(*args,**kwargs)
        registration_pose.set_pose(self.meta,np.eye(3),[100.,200.,300.])
        try:
            with (patch.object(scan_edits,'_read_mask_count',side_effect=held),
                  patch.object(scan_edits,'properties',side_effect=AssertionError('Inventory requested exact XYZ statistics')),
                  patch.object(pointio,'arrays',side_effect=AssertionError('Inventory mapped full coordinates'))):
                start=time.monotonic();first=job_api.snapshot(self.server)['items'][0]
                self.assertLess(time.monotonic()-start,1.)
                self.assertTrue(started.wait(1));self.assertEqual(first['count'],len(self.flags))
                self.assertEqual(first['original_count'],len(self.flags));self.assertIsNone(first['excluded_count'])
                self.assertFalse(first['count_exact']);self.assertFalse(first['bounds_exact']);self.assertTrue(first['metadata_pending'])
                np.testing.assert_allclose(first['minimum'],np.array(self.meta['minimum'])+[100.,200.,300.])
                for _ in range(8):self.assertTrue(job_api.snapshot(self.server)['items'][0]['metadata_pending'])
                self.assertEqual(len(calls),1);release.set();self.wait_worker()
                final=job_api.snapshot(self.server)['items'][0]
        finally:release.set();self.wait_worker()
        self.assertEqual(final['count'],int((self.flags==0).sum()));self.assertTrue(final['count_exact'])
        self.assertFalse(final['bounds_exact']);self.assertFalse(final['metadata_pending'])
        self.assertEqual(final['excluded_count'],int(np.count_nonzero(self.flags)))
        self.assertNotIn('retained_properties',self.meta);self.assert_sources_unchanged()

    def test_count_worker_reads_only_bounded_mask_bytes_and_no_analytical_cache(self):
        reads=[];original=np.fromfile
        def read(source,*args,**kwargs):
            self.assertEqual(str(getattr(source,'name',source)),self.meta['mask_file'])
            values=original(source,*args,**kwargs);reads.append(len(values));return values
        with (patch.object(scan_edits,'_DISPLAY_MASK_CHUNK',127),patch.object(np,'fromfile',side_effect=read),
              patch.object(pointio,'arrays',side_effect=AssertionError('Unexpected XYZ/attribute mapping'))):
            scan_edits.display_properties(self.meta);self.wait_worker()
        self.assertEqual(sum(reads),len(self.flags));self.assertLessEqual(max(reads),127)
        self.assertEqual(scan_edits.display_properties(self.meta)['count'],int((self.flags==0).sum()))
        self.assertNotIn('retained_properties',self.meta);self.assert_sources_unchanged()

    def test_failed_count_is_reported_without_repeated_worker_attempts(self):
        with patch.object(scan_edits,'_read_mask_count',side_effect=OSError('mask temporarily unavailable')) as read:
            scan_edits.display_properties(self.meta);self.wait_worker()
            for _ in range(8):
                values=scan_edits.display_properties(self.meta)
                self.assertFalse(values['metadata_pending']);self.assertFalse(values['count_exact'])
                self.assertIn('temporarily unavailable',values['metadata_error'])
            self.assertEqual(read.call_count,1)
        self.assertNotIn('retained_properties',self.meta)

    def test_queue_capacity_deferral_remains_pending_and_later_retries(self):
        with patch.object(scan_edits,'_DISPLAY_MAX_QUEUED',0):
            values=scan_edits.display_properties(self.meta)
            self.assertTrue(values['metadata_pending']);self.assertFalse(values['count_exact'])
            self.assertEqual(len(scan_edits._DISPLAY_COUNTS),0)
        scan_edits.display_properties(self.meta);self.wait_worker()
        self.assertTrue(scan_edits.display_properties(self.meta)['count_exact'])

    def test_changed_mask_cannot_publish_the_previous_revision_count(self):
        started=threading.Event();release=threading.Event();original=scan_edits._read_mask_count;first=[True]
        def held(*args,**kwargs):
            if first[0]:
                first[0]=False;started.set()
                if not release.wait(3):raise RuntimeError('Test count was not released.')
            return original(*args,**kwargs)
        try:
            with patch.object(scan_edits,'_read_mask_count',side_effect=held):
                scan_edits.display_properties(self.meta);self.assertTrue(started.wait(1))
                old=scan_edits._display_key(scan_edits._properties_signature(self.meta))
                changed=np.ones(len(self.flags),'u1');changed[99:107]=0;changed.tofile(self.meta['mask_file'])
                new=scan_edits._display_key(scan_edits._properties_signature(self.meta));self.assertNotEqual(old,new)
                self.assertTrue(scan_edits.display_properties(self.meta)['metadata_pending'])
                release.set();self.wait_worker()
        finally:release.set();self.wait_worker()
        values=scan_edits.display_properties(self.meta)
        self.assertEqual(values['count'],8);self.assertTrue(values['count_exact']);self.assertIsNone(values['metadata_error'])
        self.assertEqual(scan_edits._DISPLAY_COUNTS[old]['state'],'error');self.assert_sources_unchanged()

    def test_sync_active_preserves_valid_local_properties_against_stale_or_missing_alias(self):
        expected=copy.deepcopy(scan_edits.properties(self.meta))
        for stale in (None,dict(expected,count=0,minimum=None,maximum=None,attribute_counts=[0]*4)):
            cloud=copy.deepcopy(self.meta)
            if stale is None:cloud.pop('retained_properties',None)
            else:cloud['retained_properties']=stale
            self.server.STATE.update(active_job_scan='scan',cloud=cloud)
            with (patch.object(scan_edits,'properties',side_effect=AssertionError('Sync requested an exact pass')),
                  patch.object(pointio,'arrays',side_effect=AssertionError('Sync read XYZ'))):
                job_api.sync_active(self.server)
            self.assertEqual(self.meta['retained_properties'],expected)
            self.assertEqual(cloud['retained_properties'],expected)

    def test_sync_active_mask_change_invalidates_and_mask_removal_restores_original_count(self):
        scan_edits.properties(self.meta);cloud=copy.deepcopy(self.meta)
        changed=np.zeros(len(self.flags),'u1');changed[7]=1;changed.tofile(self.root/'new.mask')
        cloud['mask_file']=str(self.root/'new.mask');self.server.STATE.update(active_job_scan='scan',cloud=cloud)
        with patch.object(pointio,'arrays',side_effect=AssertionError('Sync read XYZ')):
            job_api.sync_active(self.server)
            self.assertNotIn('retained_properties',self.meta)
            self.assertEqual(self.meta['mask_file'],cloud['mask_file'])
            cloud.pop('mask_file');job_api.sync_active(self.server)
            values=scan_edits.display_properties(self.meta)
        self.assertFalse(self.meta.get('mask_file'));self.assertTrue(values['count_exact'])
        self.assertEqual(values['count'],len(self.flags));self.assertFalse(values['metadata_pending'])

    def test_exact_analytics_still_calculate_retained_bounds_and_channel_counts(self):
        scan_edits.display_properties(self.meta);self.wait_worker()
        self.assertNotIn('retained_properties',self.meta)
        exact=scan_edits.properties(self.meta);use=self.flags==0
        self.assertEqual(exact['count'],int(use.sum()))
        np.testing.assert_array_equal(exact['minimum'],self.xyz[use].min(0))
        np.testing.assert_array_equal(exact['maximum'],self.xyz[use].max(0))
        self.assertEqual(exact['attribute_counts'],np.isfinite(self.attrs[use]).sum(0).tolist())
        with patch.object(pointio,'arrays',side_effect=AssertionError('Exact cache unnecessarily rescanned')):
            self.assertEqual(scan_edits.properties(self.meta),exact)
            self.assertTrue(scan_edits.display_properties(self.meta)['bounds_exact'])
        self.assert_sources_unchanged()

    def test_exact_cache_invalidates_on_source_attribute_and_same_path_mask_revision(self):
        for key in ('file','attributes_file','mask_file'):
            cached=copy.deepcopy(scan_edits.properties(self.meta));path=Path(self.meta[key]);stat=path.stat()
            os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
            self.assertIsNone(scan_edits.cached_properties(self.meta),key)
            fresh=scan_edits.properties(self.meta)
            self.assertNotEqual(fresh['source_signature'],cached['source_signature'])
            self.assertEqual(fresh['count'],cached['count'])
        self.assert_sources_unchanged()

    def test_foreground_count_uses_exact_result_that_finishes_during_the_read(self):
        original=scan_edits._read_mask_count
        def finish_exact(meta,key,entry,**kwargs):
            scan_edits.properties(self.meta)
            return original(meta,key,entry,**kwargs)
        with patch.object(scan_edits,'_read_mask_count',side_effect=finish_exact):
            count=scan_edits.retained_count(self.meta)
        self.assertEqual(count,int((self.flags==0).sum()));self.assert_sources_unchanged()

    def test_actual_request_cancellation_is_not_hidden_by_exact_count_completion(self):
        original=scan_edits._read_mask_count
        def finish_exact(meta,key,entry,**kwargs):
            scan_edits.properties(self.meta)
            return original(meta,key,entry,**kwargs)
        with patch.object(scan_edits,'_read_mask_count',side_effect=finish_exact):
            with self.assertRaises(InterruptedError):scan_edits.retained_count(self.meta,cancel=lambda:True)
        self.assertEqual(scan_edits.retained_count(self.meta),int((self.flags==0).sum()))

    def test_cancelled_foreground_count_continues_once_for_inventory(self):
        with self.assertRaises(InterruptedError):scan_edits.retained_count(self.meta,cancel=lambda:True)
        self.wait_worker();values=scan_edits.display_properties(self.meta)
        self.assertTrue(values['count_exact']);self.assertFalse(values['metadata_pending'])
        self.assertEqual(values['count'],int((self.flags==0).sum()))

    def test_binary_and_legacy_preview_counts_are_retained_without_exact_xyz_statistics(self):
        other=copy.deepcopy(self.item);other['id']='unmasked';other['meta'].pop('mask_file');other['name']='Other setup'
        hidden=copy.deepcopy(other);hidden['id']='hidden';hidden['visible']=False
        items=[self.item,other,hidden];expected=int((self.flags==0).sum())+len(self.flags)
        with (patch.object(scan_edits,'properties',side_effect=AssertionError('Display requested full XYZ statistics')),
              patch.object(pointio,'arrays',side_effect=AssertionError('Display mapped full coordinates'))):
            blob,metadata=jobdata.preview_binary(items,detail=1)
            legacy=jobdata.preview(items,budget=63)
        self.assertEqual(metadata['source_points'],expected);self.assertTrue(metadata['count_exact'])
        self.assertEqual(struct.unpack_from('<4sI3d3d',blob)[-2],expected)
        self.assertEqual(legacy['source_points'],expected);self.assertLessEqual(legacy['sample_count'],63)
        self.assertNotIn('retained_properties',self.meta);self.assert_sources_unchanged()

    def test_zero_retained_scan_needs_no_point_sample_and_reports_empty_bounds(self):
        np.ones(len(self.flags),'u1').tofile(self.meta['mask_file'])
        self.assertEqual(scan_edits.retained_count(self.meta),0)
        with patch.object(jobdata,'_item_sample',side_effect=AssertionError('An empty scan requested XYZ')):
            _,metadata=jobdata.preview_binary([self.item],detail=100)
        self.assertEqual(metadata['source_points'],0);self.assertEqual(metadata['sample_count'],0)
        values=job_api.snapshot(self.server)['items'][0]
        self.assertTrue(values['count_exact']);self.assertTrue(values['bounds_exact'])
        self.assertIsNone(values['minimum']);self.assertIsNone(values['maximum'])


if __name__=='__main__':unittest.main()
