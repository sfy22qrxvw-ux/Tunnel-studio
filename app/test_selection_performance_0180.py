"""Exact registered selections with bounded work on multi-million-point scans."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import jobdata
import pointio
import preview_index
import registration_pose
import scan_edits


class SelectionPerformance0180Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def cache(self,n=200000,index=True):
        path=self.root/'scan.xyz64';builder=preview_index.StreamingBoundsBuilder()
        with path.open('wb') as stream:
            for start in range(0,n,100000):
                x=np.arange(start,min(n,start+100000),dtype=float)
                xyz=np.column_stack([x,x*.01,x*.001]);xyz.astype('<f8').tofile(stream)
                if index:builder.add(start,xyz)
        meta={'name':'Tunnel','file':str(path),'count':n,'minimum':[0.,0.,0.],
              'maximum':[n-1,(n-1)*.01,(n-1)*.001],'attribute_counts':[0]*4}
        if index:builder.finish(meta)
        return meta

    def counted_reads(self,meta):
        counts=[];original=np.fromfile
        def read(source,*args,**kwargs):
            result=original(source,*args,**kwargs)
            if str(getattr(source,'name',source))==meta['file']:counts.append(result.size//3)
            return result
        return counts,mock.patch.object(jobdata.np,'fromfile',side_effect=read)

    def test_first_selection_after_streamed_import_reads_only_candidate_block(self):
        n=2_100_000;source=self.root/'input.xyz';source.write_text('streamed source fixture')
        def records(*args,**kwargs):
            for start in range(0,n,100000):
                x=np.arange(start,min(n,start+100000),dtype=float)
                yield np.column_stack([x,x*.01,x*.001]),np.full((len(x),4),np.nan,dtype='<f4')
        with mock.patch.object(pointio,'records',side_effect=records):
            meta=pointio.import_cloud(source,self.root/'imported.xyz64')
        self.assertTrue(Path(meta['file']+'.viewindex.npz').is_file())
        # Exercise persisted index loading, as when import ran in another process.
        with preview_index._lock:preview_index._cache.clear()
        counts,reads=self.counted_reads(meta)
        spec={'mode':'box','minimum':[1_000_000.,-1e9,-1e9],'maximum':[1_000_099.,1e9,1e9]}
        with reads:selected=np.concatenate(list(jobdata.selection_ids(meta,spec)))
        np.testing.assert_array_equal(selected,np.arange(1_000_000,1_000_100))
        self.assertLessEqual(sum(counts),preview_index.BLOCK*2)
        self.assertLess(sum(counts),n/50)

    def test_disjoint_scan_reads_no_coordinates_and_creates_no_edit_mask(self):
        meta=self.cache(index=False)
        spec={'mode':'box','minimum':[-100.,-100.,-100.],'maximum':[-1.,-1.,-1.]}
        item={'id':'scan','kind':'scan','meta':meta,'name':'Tunnel'}
        s=SimpleNamespace(STATE={'job_items':[item]},WORK=self.root,save=lambda:None,progress=lambda message:None)
        with (mock.patch.object(preview_index,'bounds',side_effect=AssertionError('must not index disjoint scan')),
              mock.patch.object(pointio,'arrays',side_effect=AssertionError('must not map disjoint scan'))):
            self.assertEqual(list(jobdata.selection_ids(meta,spec)),[])
            with self.assertRaisesRegex(ValueError,'No retained points'):
                scan_edits.apply(s,item,'delete_selection',spec)
        self.assertFalse(list(self.root.glob('*.mask')))
        self.assertFalse(Path(meta['file']+'.viewindex.npz').exists())

    def test_virtual_pose_selection_matches_visible_world_points_and_masks(self):
        meta=self.cache();R=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
        registration_pose.set_pose(meta,R,[200000.,375000.,100.])
        mask=self.root/'existing.mask'
        with mask.open('wb') as stream:stream.truncate(meta['count'])
        with mask.open('r+b') as stream:stream.seek(123450);stream.write(b'\1')
        meta['mask_file']=str(mask)
        lo,hi=123450,123549
        spec={'mode':'box','minimum':[190000.,375000.+lo,0.],
              'maximum':[210000.,375000.+hi,10000.]}
        counts,reads=self.counted_reads(meta)
        with reads:ids=np.concatenate(list(jobdata.selection_ids(meta,spec)))
        np.testing.assert_array_equal(ids,np.arange(lo+1,hi+1))
        self.assertLess(sum(counts),meta['count']/4)
        # An inverted selection plus a clip must still exclude the masked point.
        inverse={**spec,'invert':True,'clip_box':{'mode':'box','minimum':[190000.,375000.+lo-10,0.],
                                               'maximum':[210000.,375000.+hi+10,10000.]}}
        ids=np.concatenate(list(jobdata.selection_ids(meta,inverse)))
        np.testing.assert_array_equal(ids,np.r_[np.arange(lo-10,lo),np.arange(hi+1,hi+11)])

    def test_delete_undo_redo_restore_keep_exact_original_rows_on_3m_points(self):
        meta=self.cache(3_200_000);source=Path(meta['file'])
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        item={'id':'scan','kind':'scan','meta':meta,'name':'Tunnel','edit_history':[],'redo_history':[]}
        s=SimpleNamespace(STATE={'job_items':[item]},WORK=self.root,save=lambda:None,progress=lambda message:None)
        spec={'mode':'box','minimum':[1_500_000.,-1e9,-1e9],'maximum':[1_500_099.,1e9,1e9]}
        counts,reads=self.counted_reads(meta)
        with reads:scan_edits.apply(s,item,'delete_selection',spec)
        self.assertLessEqual(sum(counts),preview_index.BLOCK*2)
        self.assertEqual(scan_edits.properties(meta)['count'],3_199_900)
        actual=np.fromfile(meta['mask_file'],'u1')
        np.testing.assert_array_equal(np.flatnonzero(actual),np.arange(1_500_000,1_500_100))
        group=item['deleted_groups'][0]['id']
        scan_edits.apply(s,item,'undo_edit');self.assertEqual(scan_edits.properties(meta)['count'],3_200_000)
        scan_edits.apply(s,item,'redo_edit');self.assertEqual(scan_edits.properties(meta)['count'],3_199_900)
        scan_edits.apply(s,item,'restore_deleted',group_id=group)
        self.assertEqual(scan_edits.properties(meta)['count'],3_200_000)
        self.assertFalse(np.fromfile(meta['mask_file'],'u1').any())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
