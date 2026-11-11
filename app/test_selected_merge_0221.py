"""Only the checked registered setups enter a selected merge; publication is atomic."""
import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np
import jobdata
import preview_index
import projects
import registration_pose
import registration_workspace as rw
from test_registration_storage_0175 import FakeServer,add_scan


class SelectedMerge0221Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=FakeServer(self.root);self.s.JOB={'busy':False}
        self.s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
        self.q=np.array([[0,0,0],[1,0,0],[0,1,0]],float)
        for i,ident in enumerate(('a','b','c')):
            add_scan(self.s,ident,self.q+[i*10,0,0],{})
            registration_pose.set_pose(rw.scan(self.s,ident)['meta'],np.eye(3),[-i*10,0,0])
        self.s.STATE['scan_links']=[dict(id='ab',a='a',b='b'),dict(id='bc',a='b',b='c')]

    def test_exact_pair_through_intermediate_scan(self):
        before={x['id']:Path(x['meta']['file']).read_bytes() for x in rw.scans(self.s)}
        merged=rw.build_merge(self.s,'a',lambda _:None,source_ids=['a','c'],name='Selected pair')
        self.assertEqual(set(merged['merge_source_ids']),{'a','c'})
        self.assertEqual(merged['name'],'Selected pair');self.assertEqual(merged['meta']['count'],6)
        np.testing.assert_array_equal(np.fromfile(merged['meta']['file'],'<f8').reshape(-1,3),np.vstack([self.q,self.q]))
        self.assertNotIn('merge_parent',rw.scan(self.s,'b'))
        for x in rw.scans(self.s):self.assertEqual(before[x['id']],Path(x['meta']['file']).read_bytes())

    def test_no_connection_cannot_be_merged_by_bundle_label_alone(self):
        self.s.STATE['scan_links']=[]
        for x in rw.scans(self.s):x['bundle_id']='same-label'
        with self.assertRaisesRegex(ValueError,'Register them first'):rw.selected_merge_ids(self.s,['a','b'])
        for invalid in ([],['a'],['a','a'],['a','missing'],'ab'):
            with self.assertRaises(ValueError):rw.selected_merge_ids(self.s,invalid)

    def test_deleted_points_excluded_and_temporary_clipping_does_not_trim_merge(self):
        a=rw.scan(self.s,'a');mask=self.root/'excluded.mask';np.array([0,1,0],'u1').tofile(mask);a['meta']['mask_file']=str(mask)
        a['limit_box']={'minimum':[100,100,100],'maximum':[101,101,101]}
        merged=rw.build_merge(self.s,'a',lambda _:None,source_ids=['a','b'])
        self.assertEqual(merged['meta']['count'],5)
        np.testing.assert_array_equal(np.fromfile(merged['meta']['file'],'<f8').reshape(-1,3)[:2],self.q[[0,2]])
        meta=merged['meta']
        self.assertTrue(Path(meta['file']+'.viewindex.npz').is_file())
        np.testing.assert_array_equal(preview_index.cached_bounds(meta),[[self.q.min(0),self.q.max(0)]])

    def test_save_failure_restores_previous_scene(self):
        before=copy.deepcopy(self.s.STATE)
        self.s.save=lambda:(_ for _ in ()).throw(OSError('save failed'))
        with self.assertRaisesRegex(OSError,'save failed'):rw.build_merge(self.s,'a',lambda _:None,source_ids=['a','b'])
        self.assertEqual(self.s.STATE,before)
        self.assertEqual(len(list(self.root.glob('*.xyz64'))),3)

    def test_cancel_leaves_no_published_cloud(self):
        before=copy.deepcopy(self.s.STATE)
        def progress(message):
            if message.startswith('Merging setup 2'):raise jobdata.Cancelled('stop')
        with self.assertRaises(jobdata.Cancelled):rw.build_merge(self.s,'a',progress,source_ids=['a','b'])
        self.assertEqual(self.s.STATE,before)

    def test_merge_survives_source_removal_in_saved_project(self):
        merged=rw.build_merge(self.s,'a',lambda _:None,source_ids=['a','c'])
        expected=Path(merged['meta']['file']).read_bytes()
        self.s.STATE['job_items']=[x for x in self.s.STATE['job_items'] if x['id'] not in ('a','c')]
        import registered_cloud
        registered_cloud.reconcile_state(self.s.STATE)
        saved=projects.save_named(self.root,self.s.STATE,'Independent result',True)
        again=projects.read_named(self.root,saved['project_id'])
        result=next(x for x in again['job_items'] if x['id']==merged['id'])
        self.assertEqual(Path(result['meta']['file']).read_bytes(),expected)
        self.assertFalse(result.get('merge_stale'))

    def test_stale_review_does_not_start_a_job(self):
        from types import SimpleNamespace
        response={};h=SimpleNamespace(send=lambda value:response.update(value))
        rw.api(self.s,h,'/api/scan-registration',dict(action='selection_merge_estimate',ids=['a','c']))
        registration_pose.compose(rw.scan(self.s,'a')['meta'],np.eye(3),[1,0,0])
        self.s.start_job=lambda *a,**k:self.fail('A stale merge must not start')
        with self.assertRaisesRegex(ValueError,'selected setups changed'):
            rw.api(self.s,h,'/api/scan-registration',dict(action='build_selection_merge',ids=['a','c'],expected_token=response['selection_token']))

    def test_source_revision_change_during_merge_refuses_publication(self):
        def progress(message):
            if message.startswith('Merging setup 2'):
                registration_pose.compose(rw.scan(self.s,'a')['meta'],np.eye(3),[1,0,0])
        with self.assertRaisesRegex(ValueError,'changed while building'):
            rw.build_merge(self.s,'a',progress,source_ids=['a','c'])
        self.assertFalse(any(x.get('registered_merge') for x in self.s.STATE['job_items']))


if __name__=='__main__':unittest.main()
