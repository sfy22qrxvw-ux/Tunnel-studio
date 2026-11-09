"""Whole-cloud example matching, exact identities, cancellation and Undo.

Synthetic surfaces establish implementation behaviour, not field recognition
accuracy. The repeated-observation fixture streams over one million distinct
IDs with owned blocks, while geometric calibration remains bounded.
"""
from pathlib import Path
import hashlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import numpy as np
import shape_matching as sm
import shape_selection as ss
import scan_edits
import workspace_history
import test_lining_features_0182 as lining_fixture
import test_deleted_points_0213 as restore_fixture


def streamed(points, repeats=1, block=100000, offset=0):
    def batches():
        for copy in range(repeats):
            for start in range(0,len(points),block):
                p=points[start:start+block]
                yield np.arange(len(p),dtype='<i8')+start+copy*len(points)+offset,p,{'mode':'full'}
    return batches


class ShapeMatchingTests(unittest.TestCase):
    def test_depth_score_keeps_millimetre_variations_at_large_service_offsets(self):
        mask=np.zeros((25,25),np.float32);mask[4:21,4:21]=1
        height=np.full(mask.shape,5.,np.float64);guard=1-mask
        exact=sm.scores(mask,height,mask,height,guard,.0005)[0,0]
        different=sm.scores(mask,height+.004,mask,height,guard,.0005)[0,0]
        self.assertGreater(exact,.999);self.assertLess(different,.001)

    def test_repeated_compact_recesses_match_across_angular_seam_but_broad_recess_does_not(self):
        m,p,_=lining_fixture.scene(empty=True,bolts=True)
        t=p[:,0];a=np.arctan2(p[:,2],p[:,1]);wide=(t-10.5)**2+(a+1.5)**2<.18**2
        p[wide,1:]*=1.04
        expected=np.linalg.norm(p[:,1:],axis=1)>1.02
        example={'mode':'box','minimum':[6.8,.8,-.2],'maximum':[7.2,1.2,.2]}
        with tempfile.TemporaryDirectory() as tmp:
            r=sm.run(streamed(p),example,tmp,{'side':'recess'},model=m)
            ids=np.fromfile(r['point_ids_file'],'<i8')
            self.assertGreater(len(ids),60);self.assertTrue(expected[ids].all());self.assertFalse(wide[ids].any())
            self.assertTrue((p[ids,0]<8).any());self.assertTrue((p[ids,0]>8).any())
            self.assertFalse((Path(tmp)/'evidence.sqlite').exists())

    def test_continuing_service_extends_beyond_example_and_keeps_wall_behind_it(self):
        m,wall,_=lining_fixture.scene(empty=True)
        t,a=np.meshgrid(np.arange(0,12,.02),np.linspace(0,2*np.pi,48,endpoint=False),indexing='ij')
        pipe=np.c_[t.ravel(),(.8+.06*np.cos(a)).ravel(),(.06*np.sin(a)).ravel()]
        p=np.vstack([wall,pipe]);example={'mode':'box','minimum':[1,.7,-.1],'maximum':[2,.9,.1]}
        with tempfile.TemporaryDirectory() as tmp:
            r=sm.run(streamed(p),example,tmp,{'side':'raised','min_offset_mm':30,'max_offset_mm':400,'continuation':'long'},model=m)
            ids=np.fromfile(r['point_ids_file'],'<i8')
            self.assertTrue(r['statistics']['continuing_service']);self.assertGreater(len(ids),len(pipe)*.95)
            self.assertTrue((ids>=len(wall)).all());self.assertLess(p[ids,0].min(),.1);self.assertGreater(p[ids,0].max(),11.9)

    def test_more_than_a_million_ids_have_no_admission_cap_and_preserve_64_bit_ids(self):
        m,p,_=lining_fixture.scene(empty=True,bolts=True);repeats=6
        total=len(p)*repeats;self.assertGreater(total,1000000)
        example={'mode':'box','minimum':[6.8,.8,-.2],'maximum':[7.2,1.2,.2]}
        with tempfile.TemporaryDirectory() as tmp:
            r=sm.run(streamed(p,repeats,offset=2**31),example,tmp,{'side':'recess'},model=m)
            ids=np.fromfile(r['point_ids_file'],'<i8')
            self.assertEqual(r['statistics']['input_points'],total);self.assertGreater(ids.min(),2**31)
            self.assertEqual(len(ids)%repeats,0)
            for copy in range(1,repeats):
                np.testing.assert_array_equal(ids[:len(ids)//repeats]+copy*len(p),ids[copy*len(ids)//repeats:(copy+1)*len(ids)//repeats])
            self.assertLessEqual(r['statistics']['calibration_points'],160000)

    def test_invalid_settings_and_empty_example_fail_clearly(self):
        for cfg in [{'max_offset_mm':4,'min_offset_mm':5},{'grid_mm':float('nan')},{'respect_view':'no'},{'side':'unknown'}]:
            with self.assertRaises(ValueError):sm.settings(cfg)
        m,p,_=lining_fixture.scene(empty=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'no supported points'):
                sm.run(streamed(p),{'mode':'box','minimum':[1,.8,-.2],'maximum':[2,1.2,.2]},tmp,{},model=m)
            self.assertFalse((Path(tmp)/'evidence.sqlite').exists())

    def test_cancellation_during_matching_never_rewrites_source_coordinates(self):
        m,p,_=lining_fixture.scene(empty=True,bolts=True);cancelled=False
        def progress(message):
            nonlocal cancelled
            if 'Example learned' in message:cancelled=True
        digest=hashlib.sha256(p.tobytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'stopped'):
                sm.run(streamed(p),{'mode':'box','minimum':[6.8,.8,-.2],'maximum':[7.2,1.2,.2]},tmp,{},progress,lambda:cancelled,model=m)
        self.assertEqual(hashlib.sha256(p.tobytes()).hexdigest(),digest)


class ShapeReviewTests(unittest.TestCase):
    def setUp(self):
        self.fx=restore_fixture.DeletedPointAreaTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.s=self.fx.s;self.item,self.p=self.fx.scan('posed',shift=1000)
        folder=self.fx.root/'shape';folder.mkdir();self.ids=np.array([4,9,25,29],'<i8')
        self.ids.tofile(folder/'ids');np.array([1,1,2,2],'<i4').tofile(folder/'labels');np.ones(4,'u1').tofile(folder/'flags')
        db=sqlite3.connect(folder/'review.sqlite');db.execute(sm.MATCH_SCHEMA)
        db.executemany('INSERT INTO matches VALUES(?,?,?,?,?,?,?)',[(1,1,0,.95,1,2,2),(2,2,1,.9,1,2,2)]);db.commit();db.close()
        np.savez(folder/'preview.npz',points=self.p[self.ids]+[1000,0,0],context=self.p+[1000,0,0],labels=np.array([1,1,2,2]),indices=np.arange(4,dtype='<i8'))
        self.row=dict(id='preview',revision='v1',source_id=self.item['id'],source_name='posed',source_fingerprint=ss.feature_selection.fingerprint(self.item),
                      folder=str(folder),point_ids_file=str(folder/'ids'),labels_file=str(folder/'labels'),selected_file=str(folder/'flags'),review_file=str(folder/'review.sqlite'),preview_file=str(folder/'preview.npz'),
                      mode='shape',settings=sm.settings(),count=4,statistics=dict(input_points=len(self.p),selected_points=4,candidate_points=4),warnings=[],method='fixture',research_dois=[])
        self.s.SHAPE_SELECTION=self.row

    def body(self,action,**extra):return dict(action=action,preview_id=self.row['id'],revision=self.row['revision'],**extra)

    def test_exact_group_review_refinement_and_reversible_separation(self):
        ss.choose(self.s,self.body('select',operation='toggle',group_ids=[1],selected=False))
        self.assertEqual(self.row['statistics']['selected_points'],2)
        ss.choose(self.s,self.body('select',operation='all'))
        q=self.p[self.ids]+[1000,0,0];spec={'mode':'box','minimum':(q[0]-.00001).tolist(),'maximum':(q[0]+.00001).tolist()}
        ss.choose(self.s,self.body('refine',operation='exclude',selection=spec))
        self.assertEqual(self.row['statistics']['selected_points'],3)
        original=Path(self.item['meta']['file']).read_bytes()
        with workspace_history.request(self.s,'/api/shape-selection'):
            body=self.body('separate',confirmed=True);workspace_history.configure('/api/shape-selection',body);ss.edit(self.s,body)
        self.assertEqual(len(self.s.STATE['job_items']),2)
        child=self.s.STATE['job_items'][-1];np.testing.assert_array_equal(np.fromfile(child['meta']['file'],'<f8').reshape(-1,3),self.p[self.ids[1:]])
        self.assertEqual(child['meta']['registration_translation'],[1000,0,0]);self.assertEqual(Path(self.item['meta']['file']).read_bytes(),original)
        workspace_history.apply(self.s);self.assertEqual(len(self.s.STATE['job_items']),1)
        self.assertEqual(ss.current(self.s,self.body('select'))['statistics']['selected_points'],3)

    def test_cancelled_review_preserves_old_flags_and_group_counts(self):
        old=self.row['selected_file'];data=Path(old).read_bytes();revision=self.row['revision']
        def stop(_):raise ValueError('cancelled')
        with mock.patch.object(self.s,'progress',stop):
            with self.assertRaisesRegex(ValueError,'cancelled'):ss.choose(self.s,self.body('select',operation='none'))
        self.assertEqual(self.row['selected_file'],old);self.assertEqual(Path(old).read_bytes(),data);self.assertEqual(self.row['revision'],revision)
        self.assertEqual(ss.public(self.s)['groups'][0]['selected_points'],2)

    def test_failed_save_rolls_back_separation_and_retains_review(self):
        old=Path(self.item['meta']['file']).read_bytes();before=ss.feature_selection.fingerprint(self.item)
        with mock.patch.object(self.s,'save',side_effect=OSError('save failed')):
            with self.assertRaisesRegex(OSError,'save failed'):ss.edit(self.s,self.body('separate',confirmed=True))
        self.assertEqual(len(self.s.STATE['job_items']),1);self.assertEqual(Path(self.item['meta']['file']).read_bytes(),old)
        self.assertEqual(ss.feature_selection.fingerprint(self.s.STATE['job_items'][0]),before)
        self.assertEqual(self.row['statistics']['selected_points'],4)

    def test_active_limit_box_is_default_and_can_be_ignored(self):
        self.item['limit_box']={'minimum':[-1,-1,-1],'maximum':[1,1,1]}
        self.assertEqual(list(ss.batches(self.item,sm.settings(),lambda _:None)),[])
        all_rows=list(ss.batches(self.item,sm.settings({'search_scope':'whole'}),lambda _:None))
        self.assertEqual(sum(len(ids) for ids,_,_ in all_rows),len(self.p))
        self.item['limit_hide_outside']=False
        all_rows=list(ss.batches(self.item,sm.settings(),lambda _:None))
        self.assertEqual(sum(len(ids) for ids,_,_ in all_rows),len(self.p))

    def test_box_reads_only_intersecting_original_blocks_for_both_pocket_tools(self):
        import preview_index, surface_streaming
        step=preview_index.BLOCK
        item,p=self.fx.scan('index',n=3*step,shift=1000)
        item['limit_box']=dict(minimum=[1000+step+3,-1,-1],maximum=[1000+step+6,1,1])
        meta=item['meta'];original=Path(meta['file']).read_bytes()
        mask=self.fx.root/'index.mask';excluded=np.zeros(len(p),'u1');excluded[step+4]=1
        excluded.tofile(mask);meta['mask_file']=str(mask)
        preview_index.bounds(meta)
        with mock.patch.object(preview_index,'indexed_selection_blocks',wraps=preview_index.indexed_selection_blocks) as indexed:
            for rows in (ss.batches(item,sm.settings(),lambda _:None),
                         surface_streaming.batches(self.s,meta,'separate',{'mode':'box',**item['limit_box']},100000)):
                result=list(rows)
                self.assertEqual(len(result),1)
                ids,points,_=result[0]
                np.testing.assert_array_equal(ids,[step+3,step+5,step+6])
                np.testing.assert_array_equal(points,p[ids]+[1000,0,0])
            self.assertEqual(indexed.call_count,2)
        reads=[]
        read=ss.RowFile.read
        def observe(reader,start,count):
            if reader.path==Path(meta['file']):reads.append((start,count))
            return read(reader,start,count)
        with mock.patch.object(ss.RowFile,'read',observe):
            list(ss.batches(item,sm.settings(),lambda _:None))
        self.assertEqual(reads,[(step,step)])
        self.assertEqual(Path(meta['file']).read_bytes(),original)

    def test_active_shape_caches_are_protected_and_abandoned_generated_caches_are_recognised(self):
        import storage_inventory,projects
        protected=projects._local_asset_paths(self.fx.root,workspace_history.storage_state(self.s))
        self.assertIn(Path(self.row['point_ids_file']).resolve(),protected)
        folder=self.fx.root/'Analysis'/('shape-'+'a'*32);folder.mkdir(parents=True)
        self.assertTrue(storage_inventory.owned_name(self.fx.root,folder/'candidate_ids.bin'))
        self.assertFalse(storage_inventory.owned_name(self.fx.root,folder/'user-export.e57'))


if __name__=='__main__':unittest.main()
