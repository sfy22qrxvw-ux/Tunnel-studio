"""Exact atomic multi-scan edits and sparse legacy masks on large row counts."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import editing
import jobdata
import preview_index
import projects
import registration_pose
import scan_edits
import workspace_history


class PointDeleteBatch0183Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.saves=0
        self.server=SimpleNamespace(WORK=self.root,STATE={'job_items':[]},JOB={'busy':False},SELECTION={'old':'selection'},progress=lambda _:None)
        def save():
            self.saves+=1;projects.atomic_json(self.root/'project.json',self.server.STATE)
        self.server.save=save

    def tearDown(self):self.tmp.cleanup()

    def scan(self,name,n=100003,shift=0.):
        ids=np.arange(n,dtype=float);p=np.c_[ids,np.sin(ids),np.cos(ids)]
        xyz=self.root/(name+'.xyz64');p.astype('<f8').tofile(xyz)
        attr=self.root/(name+'.attr32');np.ones((n,4),dtype='<f4').tofile(attr)
        meta=dict(name=name,file=str(xyz),attributes_file=str(attr),count=n,minimum=p.min(0).tolist(),maximum=p.max(0).tolist(),attribute_counts=[n]*4)
        registration_pose.set_pose(meta,np.eye(3),[shift,0.,0.])
        builder=preview_index.StreamingBoundsBuilder();builder.add(0,p);builder.finish(meta)
        item=dict(id=name,name=name,kind='scan',visible=True,meta=meta,edit_history=[],redo_history=[],hidden_boxes=[])
        self.server.STATE['job_items'].append(item)
        return item,p

    @staticmethod
    def box(lo,hi):return dict(mode='box',minimum=[lo,-100.,-100.],maximum=[hi,100.,100.])

    def test_two_registered_scans_save_once_with_visibility_masks_exact_ids_and_undo(self):
        a,p=self.scan('a');b,_=self.scan('b',shift=1000.)
        old=self.root/'shared.mask';mask=np.zeros(len(p),'u1');mask[40310]=1;mask.tofile(old)
        for item in (a,b):
            item['meta']['mask_file']=str(old)
            item['meta']['retained_properties']=dict(revision=str(old),count=len(p)-1,minimum=p.min(0).tolist(),maximum=p.max(0).tolist(),attribute_counts=[len(p)-1]*4)
            item.update(limit_box=self.box(40300.,40700.),isolate_box=self.box(40200.,40800.),hidden_boxes=[self.box(40550.,40560.)])
        originals={x['id']:hashlib.sha256(Path(x['meta']['file']).read_bytes()).hexdigest() for x in (a,b)}
        before=copy.deepcopy(self.server.STATE)
        with workspace_history.request(self.server,'/api/job-action'):
            workspace_history.configure('/api/job-action',{'action':'delete_selection_many'})
            result=scan_edits.delete_many(self.server,['a','b'],self.box(40000.,41000.))
        self.assertEqual(self.saves,1)
        self.assertEqual(result['changed_scans'],2)
        self.assertEqual(workspace_history.status(self.server)['undo_count'],1)
        actual_total=0
        for item,shift in ((a,0.),(b,1000.)):
            x=p[:,0]+shift
            expected=(x>=40300.)&(x<=40700.)&~((x>=40550.)&(x<=40560.))
            expected[40310]=True
            actual=np.fromfile(item['meta']['mask_file'],'u1')!=0
            np.testing.assert_array_equal(actual,expected)
            actual_total+=int(expected.sum())-1
            self.assertEqual(scan_edits.properties(item['meta'])['count'],len(p)-int(expected.sum()))
            self.assertEqual(hashlib.sha256(Path(item['meta']['file']).read_bytes()).hexdigest(),originals[item['id']])
        self.assertEqual(result['count'],actual_total)
        np.testing.assert_array_equal(np.fromfile(old,'u1'),mask)
        workspace_history.apply(self.server)
        self.assertEqual(self.server.STATE['job_items'],before['job_items'])
        workspace_history.apply(self.server,redo=True)
        self.assertEqual(sum(x['meta']['count']-scan_edits.properties(x['meta'])['count'] for x in self.server.STATE['job_items']),actual_total+2)

    def test_cancel_on_second_scan_rolls_back_first_mask_and_all_metadata(self):
        self.scan('a');self.scan('b');before=copy.deepcopy(self.server.STATE);files=set(self.root.iterdir())
        def cancel(message):
            if 'scan 2/2' in message:raise RuntimeError('cancelled before second scan')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            scan_edits.delete_many(self.server,['a','b'],self.box(30000.,30010.),progress=cancel)
        self.assertEqual(self.server.STATE,before)
        self.assertEqual(self.server.SELECTION,{'old':'selection'})
        self.assertEqual(set(self.root.iterdir()),files)
        self.assertEqual(self.saves,0)

    def test_captured_selection_clip_intersects_scan_limit_without_expanding_it(self):
        item,_=self.scan('a')
        item['limit_box']=self.box(30000.,30100.)
        selection={**self.box(20000.,40000.),'clip_box':self.box(30040.,30050.)}
        result=scan_edits.delete_many(self.server,['a'],selection)
        self.assertEqual(result['count'],11)
        np.testing.assert_array_equal(np.flatnonzero(np.fromfile(item['meta']['mask_file'],'u1')),np.arange(30040,30051))

    def test_failed_final_save_restores_all_scans_and_discards_new_masks(self):
        self.scan('a');self.scan('b');before=copy.deepcopy(self.server.STATE);files=set(self.root.iterdir())
        self.server.save=mock.Mock(side_effect=OSError('disk write failed'))
        with self.assertRaisesRegex(OSError,'disk write failed'):
            scan_edits.delete_many(self.server,['a','b'],self.box(30000.,30010.))
        self.assertEqual(self.server.STATE,before)
        self.assertEqual(set(self.root.iterdir()),files)

    def test_locked_or_stale_scan_rejected_before_any_other_scan_is_edited(self):
        self.scan('a');b,_=self.scan('b');before=copy.deepcopy(self.server.STATE)
        b['locked']=True
        with self.assertRaisesRegex(ValueError,'locked'):scan_edits.delete_many(self.server,['a','b'],self.box(30.,40.))
        b.pop('locked');b['merge_stale']=True
        with self.assertRaisesRegex(ValueError,'predates'):scan_edits.delete_many(self.server,['a','b'],self.box(30.,40.))
        b.pop('merge_stale')
        self.assertEqual(self.server.STATE,before)
        self.assertFalse(list(self.root.glob('*.mask')))
        self.assertEqual(self.saves,0)

    def test_local_edit_prunes_both_million_point_clouds_and_disjoint_scan(self):
        a,_=self.scan('a',1000003);b,_=self.scan('b',1000003,shift=2000000.)
        read=np.fromfile;counts=[]
        def counted(source,*args,**kwargs):
            values=read(source,*args,**kwargs)
            if str(getattr(source,'name',source)) in (a['meta']['file'],b['meta']['file']):counts.append((str(source.name),values.size//3))
            return values
        with mock.patch.object(jobdata.np,'fromfile',side_effect=counted):
            result=scan_edits.delete_many(self.server,['a','b'],self.box(500000.,500099.))
        self.assertEqual(result['count'],100)
        self.assertEqual(result['changed_scans'],1)
        self.assertTrue(all(path==a['meta']['file'] for path,_ in counts))
        self.assertLessEqual(sum(n for _,n in counts),2*preview_index.BLOCK)
        self.assertEqual(self.saves,1)

    def test_empty_batch_does_not_save_or_create_undo(self):
        self.scan('a');self.scan('b');before=copy.deepcopy(self.server.STATE)
        with workspace_history.request(self.server,'/api/job-action'):
            workspace_history.configure('/api/job-action',{'action':'delete_selection_many'})
            result=scan_edits.delete_many(self.server,['a','b'],self.box(-100.,-1.))
        self.assertEqual(result['count'],0);self.assertEqual(self.saves,0)
        self.assertEqual(self.server.STATE,before)
        self.assertEqual(workspace_history.status(self.server)['undo_count'],0)

    def test_legacy_half_billion_row_mask_is_sparse_and_duplicate_ids_count_once(self):
        n=500000003;ids=self.root/'selection.ids';np.array([7,n-1,7],'<i8').tofile(ids)
        meta=dict(count=n,file=str(self.root/'unchanged.xyz64'));history=[]
        entry=editing.apply_edit(meta,dict(file=str(ids),count=3,spec={}),self.root,history,'small local selection')
        self.assertEqual(entry['count'],2)
        path=Path(entry['after_mask']);self.assertEqual(path.stat().st_size,n)
        # This fixture deliberately needs no coordinate data: applying known
        # original IDs writes a sparse byte mask, never N zero bytes or XYZ.
        allocated=getattr(path.stat(),'st_blocks',None)
        if allocated is not None:self.assertLess(allocated*512,1024*1024)
        with path.open('rb') as stream:
            stream.seek(7);self.assertEqual(stream.read(1),b'\1')
            stream.seek(n-1);self.assertEqual(stream.read(1),b'\1')
            stream.seek(n//2);self.assertEqual(stream.read(1),b'\0')
        self.assertFalse(Path(meta['file']).exists())

    def test_legacy_invalid_selection_or_cancel_keeps_old_mask_and_history(self):
        n=100003;old=self.root/'old.mask';mask=np.zeros(n,'u1');mask[5]=1;mask.tofile(old)
        meta=dict(count=n,mask_file=str(old));before=copy.deepcopy(meta);history=[];ids=self.root/'selection.ids'
        np.array([6,n],'<i8').tofile(ids)
        with self.assertRaisesRegex(ValueError,'no longer valid'):
            editing.apply_edit(meta,dict(file=str(ids),count=2,spec={}),self.root,history,'invalid')
        np.array([6,7],'<i8').tofile(ids)
        def cancel(message):
            if message.startswith('Excluding'):raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            editing.apply_edit(meta,dict(file=str(ids),count=2,spec={}),self.root,history,'cancel',cancel)
        self.assertEqual(meta,before);self.assertEqual(history,[])
        np.testing.assert_array_equal(np.fromfile(old,'u1'),mask)
        self.assertEqual(list(self.root.glob('*.mask')),[old])


if __name__=='__main__':unittest.main()
