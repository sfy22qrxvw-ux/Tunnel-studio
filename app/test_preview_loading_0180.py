# Copyright (c) 2026 Damian Gulczynski.
"""Measured overview rendering avoids repeated full-cache and mask reads."""
from pathlib import Path
from unittest.mock import patch
import copy
import tempfile
import unittest
import numpy as np
import jobdata
import preview_store


class PreviewLoading0180Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name);n=12000
        i=np.arange(n,dtype=np.float64)
        self.points=np.column_stack((180000+i*.002,399000+np.sin(i*.01),50+np.cos(i*.01)))
        self.attrs=np.column_stack((i%256,i%256,(i*3)%256,(i*7)%256)).astype('<f4')
        xyz=self.work/'scan.xyz64';attr=self.work/'scan.attr32'
        self.points.astype('<f8').tofile(xyz);self.attrs.tofile(attr)
        self.meta=dict(file=str(xyz),attributes_file=str(attr),count=n,
                       minimum=self.points.min(0).tolist(),maximum=self.points.max(0).tolist(),
                       attribute_counts=[n]*4)
        self.item=dict(id='scan',kind='scan',name='Scan',meta=self.meta,visible=True,
                       hidden_boxes=[],isolate_box=None)
        self.origin=np.array([180000.,399000.,50.])

    def sample(self,quick=False,mode='rgb'):
        return jobdata._item_sample_uncached(self.item,1000,self.origin,[.22,.73,.81],mode,quick=quick)

    def test_cached_quick_and_normal_views_read_no_full_xyz_or_attributes(self):
        jobdata._source_overview(self.meta,build=True)
        with patch.object(jobdata,'_display_rows',side_effect=AssertionError('Unexpected full-cache read')):
            quick=self.sample(quick=True);normal=self.sample()
        np.testing.assert_array_equal(quick,normal)
        self.assertEqual(len(quick),1000)
        self.assertGreater(float(np.ptp(quick[:,3])),.8)
        self.assertGreater(float(np.ptp(quick[:,4])),.8)

    def test_quick_intensity_only_scan_is_greyscale_not_solid_bundle_blue(self):
        attrs=self.attrs.copy();attrs[:,1:]=np.nan;attrs.tofile(self.meta['attributes_file'])
        self.meta['attribute_counts']=[len(attrs),0,0,0]
        jobdata._source_overview(self.meta,build=True)
        quick=self.sample(quick=True)
        np.testing.assert_array_equal(quick[:,3],quick[:,4])
        np.testing.assert_array_equal(quick[:,4],quick[:,5])
        self.assertGreater(float(np.ptp(quick[:,3])),.85)

    def test_pose_and_ordinary_deletion_reuse_source_overview_and_keep_sources_exact(self):
        originals={key:Path(self.meta[key]).read_bytes() for key in ('file','attributes_file')}
        jobdata._source_overview(self.meta,build=True)
        path=preview_store.overview_path(self.meta)
        self.meta['registration_rotation']=np.eye(3).tolist()
        self.meta['registration_translation']=[4.,5.,6.]
        flags=np.zeros(self.meta['count'],'u1');flags[::10]=1
        mask=self.work/'edited.mask';flags.tofile(mask);self.meta['mask_file']=str(mask)
        self.meta['retained_properties']=dict(revision=str(mask),count=int((flags==0).sum()),
            minimum=self.meta['minimum'],maximum=self.meta['maximum'],attribute_counts=[int((flags==0).sum())]*4)
        self.assertEqual(path,preview_store.overview_path(self.meta))
        with patch.object(jobdata,'_display_rows',side_effect=AssertionError('Unexpected full scan read')):
            shown=self.sample()
        world=shown[:,:3].astype(float)+self.origin-np.array([4.,5.,6.])
        ids=np.rint((world[:,0]-180000)/.002).astype(int)
        self.assertTrue((flags[ids]==0).all())
        np.testing.assert_allclose(world,self.points[ids],atol=2e-6)
        for key,value in originals.items():self.assertEqual(Path(self.meta[key]).read_bytes(),value)

    def test_standard_import_prepares_coloured_overview_before_first_display(self):
        jobdata.preview_cache(self.meta,self.work)
        self.assertIsNotNone(preview_store.read_overview(self.meta))
        with patch.object(jobdata,'_display_rows',side_effect=AssertionError('Unexpected source read')):
            shown=self.sample(quick=True)
        self.assertEqual(len(shown),1000)

    def test_changed_attribute_file_invalidates_disposable_overview(self):
        jobdata._source_overview(self.meta,build=True)
        path=preview_store.overview_path(self.meta)
        import os
        stat=Path(self.meta['attributes_file']).stat()
        os.utime(self.meta['attributes_file'],ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
        self.assertNotEqual(path,preview_store.overview_path(self.meta))
        self.assertIsNone(preview_store.read_overview(self.meta))

    def test_old_preview_uses_measured_colours_even_in_quick_mode(self):
        preview=self.work/'old.preview64';self.points[::12].astype('<f8').tofile(preview)
        self.meta.update(preview_file=str(preview),preview_stride=12,preview_count=1000)
        shown=self.sample(quick=True)
        self.assertEqual(len(shown),1000)
        self.assertGreater(float(np.ptp(shown[:,3])),.8)

    def test_high_detail_still_reads_original_points_above_overview_budget(self):
        # A deliberately small cache substitutes for a huge scan in this test:
        # requesting more points must not make the overview an analysis/LOD cap.
        ids=np.arange(1000,dtype='<i8')
        preview_store.write_overview(self.meta,ids,self.points[:1000],self.attrs[:1000])
        shown=jobdata._item_sample_uncached(self.item,3000,self.origin,[.22,.73,.81])
        self.assertEqual(len(shown),3000)
        self.assertGreater(float(shown[:,0].max()),23.)


if __name__=='__main__':unittest.main()
