"""A derived merge may share cache ownership with projects, copies or Undo."""
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import projects
import registration_workspace as rw
import workspace_history
from test_registration_storage_0175 import FakeServer,add_scan


class MergeRelease0180Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.s=FakeServer(self.root);self.s.JOB={'busy':False}
        self.s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
        points=np.array([[0,0,0],[1,0,0],[0,1,0]],float)
        add_scan(self.s,'a',points,{});add_scan(self.s,'b',points+[2,0,0],{})
        self.s.STATE['scan_links']=[{'id':'link','a':'a','b':'b'}]
        self.merge=rw.build_merge(self.s,'a',lambda message:None)

    def tearDown(self):self.temp.cleanup()

    def files(self):
        return [Path(self.merge['meta'][key]) for key in ('file','attributes_file','preview_file')]

    def test_saved_project_keeps_shared_cache_after_scene_release(self):
        saved=projects.save_named(self.root,self.s.STATE,'Saved registration',True)
        before={p:p.read_bytes() for p in self.files()}
        result=rw.release_merge(self.s,self.merge['id'])
        self.assertEqual(result['reclaimed_bytes'],0)
        self.assertGreater(result['protected_bytes'],0)
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertTrue(projects.read_named(self.root,saved['project_id'])['job_items'])

    def test_other_scene_copy_keeps_shared_cache(self):
        duplicate=copy.deepcopy(self.merge);duplicate['id']='copy'
        self.s.STATE['job_items'].append(duplicate)
        result=rw.release_merge(self.s,self.merge['id'])
        self.assertEqual(result['reclaimed_bytes'],0)
        self.assertTrue(all(p.is_file() for p in self.files()))

    def test_existing_undo_keeps_shared_cache(self):
        self.s.WORKSPACE_HISTORY['undo']=[{'before':copy.deepcopy(self.s.STATE),'after':{}}]
        result=rw.release_merge(self.s,self.merge['id'])
        self.assertEqual(result['reclaimed_bytes'],0)
        self.assertTrue(all(p.is_file() for p in self.files()))

    def test_explicit_release_does_not_add_invalid_undo_and_clears_active_alias(self):
        self.s.STATE.update(active_job_scan=self.merge['id'],cloud=copy.deepcopy(self.merge['meta']),valid_points=6)
        with workspace_history.request(self.s,'/api/scan-registration'):
            workspace_history.configure('/api/scan-registration',{'action':'release_merge'})
            result=rw.release_merge(self.s,self.merge['id'])
        self.assertGreater(result['reclaimed_bytes'],0)
        self.assertFalse(any(p.exists() for p in self.files()))
        self.assertEqual(self.s.WORKSPACE_HISTORY,{'undo':[],'redo':[]})
        self.assertIsNone(self.s.STATE['cloud'])
        self.assertIsNone(self.s.STATE['active_job_scan'])


if __name__=='__main__':unittest.main()
