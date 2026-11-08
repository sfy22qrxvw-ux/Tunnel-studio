"""Exact deletion commits never require whole-cloud analytical metadata."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import jobdata
import mask_storage
import pointio
import preview_index
import projects
import scan_edits
import workspace_history


class PointDeleteFast0187Tests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.n=200003;self.saves=0
        ids=np.arange(self.n);theta=ids*.017
        self.points=np.c_[ids.astype(float),np.sin(theta)*3,np.cos(theta)*3]
        self.attrs=np.ones((self.n,4),'<f4')
        self.attrs[::3,0]=np.nan;self.attrs[::5,2]=np.nan;self.attrs[:,3]=np.nan
        self.points.astype('<f8').tofile(self.root/'scan.xyz64');self.attrs.tofile(self.root/'scan.attr32')
        self.flags=np.zeros(self.n,'u1');self.flags[::17]=2;self.flags.tofile(self.root/'legacy.mask')
        self.meta=dict(name='Legacy tunnel',file=str(self.root/'scan.xyz64'),attributes_file=str(self.root/'scan.attr32'),
                       mask_file=str(self.root/'legacy.mask'),count=self.n,minimum=self.points.min(0).tolist(),
                       maximum=self.points.max(0).tolist(),attribute_counts=np.isfinite(self.attrs).sum(0).tolist())
        builder=preview_index.StreamingBoundsBuilder();builder.add(0,self.points);builder.finish(self.meta)
        self.item=dict(id='scan',kind='scan',name='Legacy tunnel',visible=True,meta=self.meta,edit_history=[],redo_history=[],deleted_groups=[])
        self.s=SimpleNamespace(STATE=dict(job_items=[self.item]),WORK=self.root,JOB={'busy':False},SELECTION={'keep':'selection'},progress=lambda _:None)
        def save():
            self.saves+=1;projects.atomic_json(self.root/'project.json',self.s.STATE)
        self.s.save=save

    @staticmethod
    def box(lo,hi):return dict(mode='box',minimum=[lo,-4.,-4.],maximum=[hi,4.,4.])

    def commit(self,selection):
        with workspace_history.request(self.s,'/api/job-action'):
            workspace_history.configure('/api/job-action',{'action':'delete_selection_many'})
            return scan_edits.delete_many(self.s,['scan'],selection)

    def assert_exact(self,expected):
        np.testing.assert_array_equal(np.fromfile(self.meta['mask_file'],'u1')!=0,expected)
        retained=~expected;props=scan_edits.properties(self.meta)
        self.assertEqual(props['count'],int(retained.sum()))
        np.testing.assert_array_equal(props['minimum'],self.points[retained].min(0))
        np.testing.assert_array_equal(props['maximum'],self.points[retained].max(0))
        np.testing.assert_array_equal(props['attribute_counts'],np.isfinite(self.attrs[retained]).sum(0))
        chunks=list(pointio.chunks(self.meta))
        np.testing.assert_array_equal(np.vstack([p for p,_ in chunks]),self.points[retained])
        np.testing.assert_array_equal(np.vstack([a for _,a in chunks]),self.attrs[retained])

    def no_full_statistics(self):
        return mock.patch.multiple(scan_edits,properties=mock.Mock(side_effect=AssertionError('eager full statistics')))

    def test_uncached_legacy_delete_next_delete_undo_redo_restore_and_export(self):
        originals={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (self.meta['file'],self.meta['attributes_file'],self.meta['mask_file'])}
        calls=[];read=np.fromfile
        def tracked(source,*args,**kwargs):
            result=read(source,*args,**kwargs);calls.append((str(getattr(source,'name',source)),result.size));return result
        with self.no_full_statistics(), mock.patch.object(pointio,'arrays',side_effect=AssertionError('whole-source map')), mock.patch.object(np,'fromfile',side_effect=tracked):
            first=self.commit(self.box(100000,100099))
            first_mask=self.meta['mask_file'];first_summary=copy.deepcopy(scan_edits.cached_summary(self.meta))
            second=self.commit(self.box(100050,100149))
        expected=self.flags!=0;expected[100000:100150]=True
        self.assertEqual(first['count'],int(np.count_nonzero(self.flags[100000:100100]==0)))
        self.assertEqual(first['count']+second['count'],int(expected.sum()-np.count_nonzero(self.flags)))
        self.assertEqual(scan_edits.cached_summary(self.meta)['count'],self.n-int(expected.sum()))
        self.assertIsNone(scan_edits.cached_properties(self.meta))
        self.assertFalse(scan_edits.display_properties(self.meta)['bounds_exact'])
        self.assertEqual(sum(size//3 for path,size in calls if path==self.meta['file']),2*preview_index.BLOCK)
        self.assertFalse([v for v in calls if v[0]==self.meta['attributes_file']])
        with self.no_full_statistics(),mock.patch.object(pointio,'arrays',side_effect=AssertionError('whole-source map')):
            scan_edits.apply(self.s,self.item,'undo_edit')
            self.assertEqual(self.meta['mask_file'],first_mask)
            self.assertEqual(scan_edits.cached_summary(self.meta),first_summary)
            scan_edits.apply(self.s,self.item,'redo_edit')
        self.assert_exact(expected)
        group=self.item['deleted_groups'][0]['id']
        with self.no_full_statistics():scan_edits.apply(self.s,self.item,'restore_deleted',group_id=group)
        expected[100000:100100]=self.flags[100000:100100]!=0
        self.assert_exact(expected)
        for path,digest in originals.items():self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),digest)
        self.assertEqual(workspace_history.status(self.s)['undo_count'],2)
        self.assertEqual(self.saves,5)

    def test_empty_in_source_bounds_creates_no_mask_and_reads_no_attributes(self):
        before=copy.deepcopy(self.s.STATE);files=set(self.root.iterdir())
        empty=dict(mode='box',minimum=[100000,-.001,-.001],maximum=[100100,.001,.001])
        with self.no_full_statistics(),mock.patch.object(mask_storage,'copy_mask',side_effect=AssertionError('empty mask copy')),mock.patch.object(jobdata,'room',side_effect=AssertionError('empty allocation')),mock.patch.object(pointio,'arrays',side_effect=AssertionError('full source read')):
            result=self.commit(empty)
        self.assertEqual(result['count'],0);self.assertEqual(self.saves,0)
        self.assertEqual(self.s.STATE,before);self.assertEqual(set(self.root.iterdir()),files)
        self.assertEqual(workspace_history.status(self.s)['undo_count'],0)

    def test_duplicate_already_deleted_ids_are_noop_before_mask_copy(self):
        selected=self.root/'selected.ids';np.array([0,17,17,34],'<i8').tofile(selected)
        before=copy.deepcopy(self.s.STATE);files=set(self.root.iterdir())
        with self.no_full_statistics(),mock.patch.object(mask_storage,'copy_mask',side_effect=AssertionError('no-op mask copy')):
            with self.assertRaisesRegex(ValueError,'No retained points'):
                scan_edits.apply(self.s,self.item,'delete_selection',selected_file=dict(file=str(selected),count=4))
        self.assertEqual(self.s.STATE,before);self.assertEqual(set(self.root.iterdir()),files)

    def test_boundary_deletion_defers_exact_bounds_and_mixed_channels_until_requested(self):
        scan_edits.properties(self.meta)
        with self.no_full_statistics(),mock.patch.object(preview_index,'retained_bounds',side_effect=AssertionError('eager retained bounds')):
            result=self.commit(self.box(0,500))
        expected=self.flags!=0;expected[:501]=True
        self.assertGreater(result['count'],0)
        self.assertIsNone(scan_edits.cached_properties(self.meta))
        display=scan_edits.display_properties(self.meta)
        self.assertTrue(display['count_exact']);self.assertFalse(display['bounds_exact'])
        self.assert_exact(expected)

    def derived(self,count_known=True):
        self.analysis=np.zeros((self.n,4),'<f8');self.analysis[:,0]=self.points[:,0]
        self.analysis[::7,0]=np.nan;self.analysis.tofile(self.root/'scan.analysis64')
        self.s.STATE.update(active_job_scan='scan',cloud=copy.deepcopy(self.meta),derived=str(self.root/'scan.analysis64'),
                            valid_points=int((np.isfinite(self.analysis[:,0])&(self.flags==0)).sum()) if count_known else None)
        if count_known:
            self.meta['retained_summary']=scan_edits._summary(self.meta,int((self.flags==0).sum()),self.s.STATE['derived'],self.s.STATE['valid_points'])
            self.s.STATE['cloud']=copy.deepcopy(self.meta)

    def test_active_derived_count_uses_changed_rows_and_survives_undo_redo_restore(self):
        self.derived();original=self.s.STATE['valid_points'];path=self.s.STATE['derived'];read=np.fromfile;rows=[]
        def tracked(source,*args,**kwargs):
            result=read(source,*args,**kwargs)
            if str(getattr(source,'name',source))==path:rows.append(result.size//4)
            return result
        with self.no_full_statistics(),mock.patch.object(pointio,'arrays',side_effect=AssertionError('full source map')),mock.patch.object(np,'fromfile',side_effect=tracked):
            self.commit(self.box(100000,100099))
            expected=self.flags!=0;expected[100000:100100]=True
            after=int((np.isfinite(self.analysis[:,0])&~expected).sum())
            self.assertEqual(self.s.STATE['valid_points'],after)
            scan_edits.apply(self.s,self.item,'undo_edit');self.assertEqual(self.s.STATE['valid_points'],original)
            scan_edits.apply(self.s,self.item,'redo_edit');self.assertEqual(self.s.STATE['valid_points'],after)
            scan_edits.apply(self.s,self.item,'restore_deleted',group_id=self.item['deleted_groups'][0]['id'])
            self.assertEqual(self.s.STATE['valid_points'],original)
        self.assertLessEqual(sum(rows),200)
        self.assertEqual(self.s.STATE['derived'],path)
        self.assertEqual(self.s.STATE['cloud']['mask_file'],self.meta['mask_file'])

    def test_unknown_derived_count_keeps_analysis_and_defers_only_informational_total(self):
        self.derived(count_known=False);path=self.s.STATE['derived'];read=np.fromfile
        def tracked(source,*args,**kwargs):
            if str(getattr(source,'name',source))==path:raise AssertionError('unexpected analysis rescan')
            return read(source,*args,**kwargs)
        with self.no_full_statistics(),mock.patch.object(np,'fromfile',side_effect=tracked):self.commit(self.box(100000,100099))
        self.assertIsNone(self.s.STATE['valid_points']);self.assertEqual(self.s.STATE['derived'],path)
        self.assertEqual(self.s.STATE['active_job_scan'],'scan')
        self.assertEqual(self.s.STATE['cloud']['mask_file'],self.meta['mask_file'])
        self.assertNotIn('valid_points',scan_edits.cached_summary(self.meta))

    def test_replaced_analysis_file_cannot_reuse_a_stale_scalar_count(self):
        self.derived();self.commit(self.box(100000,100099))
        path=Path(self.s.STATE['derived']);replacement=self.root/'replacement.analysis64'
        changed=self.analysis.copy();changed[:,0]=np.nan;changed.tofile(replacement);replacement.replace(path)
        self.assertIsNotNone(scan_edits.cached_summary(self.meta))
        with self.no_full_statistics():self.commit(self.box(100100,100199))
        self.assertIsNone(self.s.STATE['valid_points'])
        self.assertEqual(self.s.STATE['derived'],str(path))
        self.assertNotIn('valid_points',scan_edits.cached_summary(self.meta))

    def test_import_without_channel_counts_keeps_unknown_statistics_separate(self):
        self.meta.pop('mask_file');self.meta.pop('attribute_counts')
        with self.no_full_statistics(),mock.patch.object(pointio,'arrays',side_effect=AssertionError('full source map')):
            result=self.commit(self.box(100000,100099))
        self.assertEqual(result['count'],100)
        self.assertIsNone(scan_edits.cached_properties(self.meta))
        expected=np.zeros(self.n,dtype=bool);expected[100000:100100]=True
        self.assert_exact(expected)

    def tiny_scan(self):
        points=np.c_[np.arange(8,dtype=float),np.zeros((8,2))]
        path=self.root/'tiny.xyz64';points.astype('<f8').tofile(path)
        meta=dict(name='Tiny',file=str(path),count=8,minimum=[0.,0.,0.],maximum=[7.,0.,0.],attribute_counts=[0]*4)
        item=dict(id='tiny',kind='scan',name='Tiny',visible=True,meta=meta,edit_history=[],redo_history=[],deleted_groups=[])
        self.s.STATE=dict(job_items=[item]);return item,meta

    def test_analysis_replacement_during_copy_does_not_reanchor_old_count_for_undo(self):
        item,meta=self.tiny_scan();path=self.root/'tiny.analysis64'
        np.zeros((8,4),'<f8').tofile(path)
        meta['retained_summary']=scan_edits._summary(meta,8,str(path),8)
        self.s.STATE.update(active_job_scan='tiny',cloud=copy.deepcopy(meta),derived=str(path),valid_points=8)
        changed=[]
        def replace_during_copy(message):
            if message.startswith('Preparing reversible edit') and not changed:
                replacement=self.root/'replacement.analysis64'
                np.full((8,4),np.nan,dtype='<f8').tofile(replacement);replacement.replace(path);changed.append(True)
        with self.no_full_statistics():
            scan_edits.apply(self.s,item,'delete_selection',self.box(2.9,3.1),progress=replace_during_copy)
            entry=item['edit_history'][-1]
            self.assertNotIn('valid_points',entry['before_summary'])
            self.assertNotIn('valid_points',entry['after_summary'])
            self.assertIsNone(self.s.STATE['valid_points'])
            scan_edits.apply(self.s,item,'undo_edit')
        self.assertTrue(changed);self.assertIsNone(self.s.STATE['valid_points'])
        self.assertEqual(self.s.STATE['derived'],str(path))
        self.assertEqual(self.s.STATE['active_job_scan'],'tiny')
        self.assertEqual(scan_edits.retained_count(meta),8)
        self.assertEqual(int(np.isfinite(np.fromfile(path,'<f8').reshape(8,4)[:,0]).sum()),0)

    def test_inconsistent_legacy_properties_are_not_resigned_for_undo(self):
        item,meta=self.tiny_scan();path=self.root/'tiny.mask'
        np.array([1,1,1,0,0,0,0,0],dtype='u1').tofile(path);meta['mask_file']=str(path)
        meta['retained_properties']=dict(revision=str(path),count=7,minimum=[1.,0.,0.],maximum=[7.,0.,0.],attribute_counts=[0]*4)
        with self.no_full_statistics():
            scan_edits.apply(self.s,item,'delete_selection',self.box(5.9,6.1))
            entry=item['edit_history'][-1]
            self.assertEqual(entry['before_summary']['count'],5)
            self.assertIsNone(entry['before_properties'])
            scan_edits.apply(self.s,item,'undo_edit')
            self.assertIsNone(scan_edits.cached_properties(meta))
        exact=scan_edits.properties(meta)
        self.assertEqual(exact['count'],5)
        self.assertEqual(exact['minimum'],[3.,0.,0.])
        self.assertEqual(exact['maximum'],[7.,0.,0.])

    def test_legacy_masked_projection_total_is_not_proof_of_retained_finite_count(self):
        item,meta=self.tiny_scan();mask=self.root/'tiny.mask'
        np.array([1,1,0,0,0,0,0,0],dtype='u1').tofile(mask);meta['mask_file']=str(mask)
        path=self.root/'tiny.analysis64';analysis=np.zeros((8,4),'<f8');analysis[3:,0]=np.nan;analysis.tofile(path)
        # Legacy projection returned three finite rows without excluding IDs
        # zero and one. Three looks plausible (<= six retained), but is wrong.
        self.s.STATE.update(active_job_scan='tiny',cloud=copy.deepcopy(meta),derived=str(path),valid_points=3)
        scan_edits.properties(meta)
        with self.no_full_statistics():scan_edits.apply(self.s,item,'delete_selection',self.box(6.9,7.1))
        self.assertIsNone(self.s.STATE['valid_points'])
        self.assertNotIn('valid_points',scan_edits.cached_summary(meta))
        self.assertEqual(scan_edits.cached_summary(meta)['count'],5)
        self.assertEqual(self.s.STATE['derived'],str(path))
        actual=(np.fromfile(meta['mask_file'],'u1')==0)&np.isfinite(analysis[:,0])
        self.assertEqual(int(actual.sum()),1)
        # A permanent producer can attach its proved retained total without
        # replacing exact bounds/channel statistics or scanning XYZ again.
        exact_before=copy.deepcopy(scan_edits.properties(meta))
        with self.no_full_statistics(),mock.patch.object(pointio,'arrays',side_effect=AssertionError('full source map')):
            summary=scan_edits.record_projection(meta,str(path),1)
            scan_edits.refresh_aliases(self.s,item)
        self.assertEqual(summary['valid_points'],1);self.assertEqual(self.s.STATE['valid_points'],1)
        for name in ('count','minimum','maximum','attribute_counts'):
            self.assertEqual(scan_edits.cached_properties(meta)[name],exact_before[name])

    def test_cancellation_and_final_save_failure_keep_source_and_selection(self):
        before=copy.deepcopy(self.s.STATE);files=set(self.root.iterdir())
        def cancelled(message):
            if message.startswith('Excluding'):raise RuntimeError('cancelled after changed IDs')
        with self.no_full_statistics():
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                scan_edits.apply(self.s,self.item,'delete_selection',self.box(100000,100099),progress=cancelled)
        self.assertEqual(self.s.STATE,before);self.assertEqual(set(self.root.iterdir()),files)
        self.item=self.s.STATE['job_items'][0];self.meta=self.item['meta'];self.derived()
        before=copy.deepcopy(self.s.STATE);selection=copy.deepcopy(self.s.SELECTION)
        self.s.save=mock.Mock(side_effect=OSError('save failed'))
        with self.no_full_statistics():
            with self.assertRaisesRegex(OSError,'save failed'):
                scan_edits.apply(self.s,self.item,'delete_selection',self.box(100000,100099))
        self.assertEqual(self.s.STATE,before);self.assertEqual(self.s.SELECTION,selection)
        self.assertEqual(set(self.root.glob('*.mask')),{self.root/'legacy.mask'})


if __name__=='__main__':unittest.main()
