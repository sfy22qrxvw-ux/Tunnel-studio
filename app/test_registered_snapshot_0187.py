# Copyright (c) 2026 Damian Gulczynski.
"""Original setup edits cannot invalidate an independent saved merged survey."""
import copy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import analysis_sources
from core import Alignment,project_cloud
import explorer_deletion
import inspection_api
import job_api
import jobdata
import projects
import registered_cloud
import registration_pose
import scan_edits
import workflows
from test_registration_virtual_0176 import add_scan


class RegisteredSnapshot0187Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.s=SimpleNamespace(WORK=self.root,STATE=workflows.empty(),JOB={'busy':False},
            JOB_CANCEL_FILE=self.root/'cancel',SELECTION=None,progress=lambda _:None,
            WORKSPACE_HISTORY={'undo':[],'redo':[]})
        self.s.STATE.update(job_items=[],scan_links=[],survey_controls={})
        self.s.save=lambda:projects.atomic_json(self.root/'project.json',workflows.serial(self.s))
        self.s.start_job=lambda action,*args,**kwargs:action()
        self.sources=[]
        for i in range(11):
            shift=np.array([i*10.,0.,0.])
            source=add_scan(self.s,'source_'+str(i),shift)
            registration_pose.set_pose(source['meta'],np.eye(3),-shift)
            self.sources.append(source)
        self.merged=registered_cloud.build(self.s,self.sources,'registration_fixture')
        registered_cloud.install(self.s,self.merged)
        self.s.STATE['axis']=Alignment([
            dict(chainage=0.,e=-1.,n=0.,h=0.,radius=1.),
            dict(chainage=3.,e=2.,n=0.,h=0.,radius=1.)])
        derived,count=project_cloud(self.merged['meta'],self.s.STATE['axis'],self.root/'saved.analysis64')
        self.s.STATE.update(active_job_scan=self.merged['id'],cloud=copy.deepcopy(self.merged['meta']),
                            derived=derived,valid_points=count)
        obj=self.root/'saved_mesh.obj'
        obj.write_text('v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3\nf 1 3 4\n')
        meta=jobdata.import_obj(obj,self.root)
        meta.update(source_item_id=self.merged['id'],source_name=self.merged['name'])
        self.mesh=dict(id='saved_mesh',name='Saved mesh',kind='mesh',meta=meta,visible=True,source_bytes=0)
        self.s.STATE['job_items'].append(self.mesh)
        inspection_api.post(self.s,SimpleNamespace(send=lambda _:None),'/api/inspect',
                            dict(mode='cloud_mesh',source_id=self.merged['id'],reference_id=self.mesh['id'],
                                 tolerance_mm=25,spacing_m=.4))
        self.result=copy.deepcopy(self.s.STATE['inspection_result'])
        self.saved=projects.save_named(self.root,workflows.serial(self.s),'Eleven setups and saved results',True)
        self.s.STATE.update({key:self.saved[key] for key in ('project_id','project_title','saved_at')})
        self.s.save()
        assets=[self.merged['meta']['file'],self.merged['meta']['attributes_file'],derived,
                self.mesh['meta']['vertices_file'],self.mesh['meta']['faces_file'],self.result['results_file']]
        self.asset_hashes={path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in assets}

    def current_merge(self):
        return next(x for x in self.s.STATE['job_items'] if x['id']==self.merged['id'])

    def assert_saved_results(self,state):
        self.assertEqual(state['active_job_scan'],self.merged['id'])
        self.assertEqual(state['cloud']['file'],self.merged['meta']['file'])
        self.assertEqual(state['derived'],str(self.root/'saved.analysis64'))
        current=next(x for x in state['job_items'] if x['id']==self.merged['id'])
        self.assertFalse(current.get('merge_stale'));self.assertFalse(current.get('merge_archived'))
        registered_cloud.require_current(current)
        inspected=inspection_api.current(SimpleNamespace(STATE=state))
        self.assertFalse(inspected.get('stale'),inspected.get('stale_reason'))
        self.assertEqual(inspected['id'],self.result['id'])
        self.assertEqual(inspected['used'],44)
        self.assertIn(self.mesh['id'],{x['id'] for x in state['job_items']})
        for path,digest in self.asset_hashes.items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),digest,path)

    def delete_original_point(self):
        source=self.sources[0]
        registered_cloud.set_visibility(self.s.STATE['job_items'],source,True)
        result=scan_edits.delete_many(self.s,[source['id']],
                                     dict(mode='box',minimum=[-.01,-.01,-.01],maximum=[.01,.01,.01]))
        self.assertEqual(result['count'],1)
        # Automatic selection must see the new mask before inventory polling
        # has had an opportunity to add the saved-snapshot annotation.
        self.assertEqual({x['id'] for x in analysis_sources._members(self.s)},
                         {x['id'] for x in self.sources})
        job_api.snapshot(self.s)
        return source

    def save_and_reopen(self):
        saved=projects.save_named(self.root,workflows.serial(self.s))
        self.s.STATE=workflows.prepare_data(self.s,projects.read_named(self.root,saved['project_id']))
        job_api.snapshot(self.s)
        return self.s.STATE

    def test_original_point_delete_preserves_saved_analysis_mesh_and_reopen(self):
        source=self.delete_original_point()
        self.assert_saved_results(self.s.STATE)
        self.assertEqual(self.current_merge()['merge_source_changes']['point_edits'],[source['id']])
        self.assertIn('saved',self.current_merge()['position_status'].lower())
        # Current automatic scopes use all original setups with their current
        # masks; explicitly selecting the saved merged object still uses 44 rows.
        members=analysis_sources._members(self.s)
        self.assertEqual({x['id'] for x in members},{x['id'] for x in self.sources})
        collection=analysis_sources.collection('current','Current sources',members)
        count=sum(len(points) for _,points in analysis_sources.blocks(collection['meta']))
        self.assertEqual(count,43)
        self.assertEqual(analysis_sources.resolve(self.s,self.merged['id'])['meta']['count'],44)
        self.assert_saved_results(self.save_and_reopen())

    def test_undo_original_mask_edit_restores_automatic_merge_choice(self):
        source=self.delete_original_point()
        scan_edits.apply(self.s,source,'undo_edit')
        job_api.snapshot(self.s)
        self.assert_saved_results(self.s.STATE)
        self.assertFalse(self.current_merge().get('merge_source_changes'))
        self.assertEqual([x['id'] for x in analysis_sources._members(self.s)],[self.merged['id']])
        self.assert_saved_results(self.save_and_reopen())

    def test_deleting_original_item_preserves_independent_saved_results(self):
        source=self.sources[0];body={'ids':[source['id']]}
        review=explorer_deletion.review(self.s,body)
        with patch.object(projects,'_schedule_purge') as schedule:
            explorer_deletion.delete(self.s,{**body,'confirmed':True,'revision':review['revision']})
            if schedule.called:projects._purge_stage(schedule.call_args.args[0])
        job_api.snapshot(self.s)
        self.assert_saved_results(self.s.STATE)
        self.assertNotIn(source['id'],{x['id'] for x in self.s.STATE['job_items']})
        self.assertFalse(Path(source['meta']['file']).exists())
        self.assertEqual(self.current_merge()['merge_source_changes']['removed'],[source['id']])
        self.assertEqual({x['id'] for x in analysis_sources._members(self.s)},
                         {x['id'] for x in self.sources[1:]})
        self.assert_saved_results(self.save_and_reopen())

    def test_real_pose_change_still_retires_previous_registration(self):
        registration_pose.compose(self.sources[0]['meta'],np.eye(3),[1.,0.,0.])
        job_api.snapshot(self.s)
        merged=self.current_merge()
        self.assertTrue(merged['merge_stale']);self.assertTrue(merged['merge_archived'])
        self.assertFalse(merged['visible']);self.assertIsNone(self.s.STATE['active_job_scan'])
        self.assertIsNone(self.s.STATE['derived'])
        with self.assertRaisesRegex(ValueError,'predates'):registered_cloud.require_current(merged)
        self.assertTrue(inspection_api.current(self.s)['stale'])
        for path,digest in self.asset_hashes.items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),digest,path)

    def test_coordinate_cache_replacement_remains_conservative(self):
        source=self.sources[0]
        replacement=self.root/'replacement.xyz64'
        replacement.write_bytes(Path(source['meta']['file']).read_bytes())
        source['meta']['file']=str(replacement)
        job_api.snapshot(self.s)
        self.assertTrue(self.current_merge()['merge_stale'])
        self.assertTrue(self.current_merge()['merge_archived'])
        self.assertIsNone(self.s.STATE['derived'])


if __name__=='__main__':unittest.main()
