"""Regression checks for one registered representation and world coordinates."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import job_api,jobdata,registered_cloud,registration,registration_pose
import registration_workspace as rw
import scene_objects,workspace_tools,workflows
import projects
from test_registration_virtual_0176 import FakeServer,add_scan


class RegistrationCoherenceTests(unittest.TestCase):
    def pair(self,folder):
        s=FakeServer(folder);s.STATE.update(workflows.empty());s.STATE['job_items']=[]
        s.JOB={'busy':False};s.SELECTION=None
        s.start_job=lambda fn,*a,**kw:fn()
        a=add_scan(s,'a',np.array([0.,0.,0.]));b=add_scan(s,'b',np.array([10.,0.,0.]))
        draft=rw.solve_pair(s,'a','b');rw.accept(s,draft['id'],True)
        return s,a,b

    def test_new_link_retires_previous_merge_and_preserves_source_and_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            merged['edit_history']=[{'kind':'delete','note':'protected reviewed edit'}]
            c=add_scan(s,'c',np.array([20.,0.,0.]))
            s.STATE.update(active_job_scan=merged['id'],cloud=copy.deepcopy(merged['meta']),
                           derived='old.analysis64',valid_points=8,
                           surface_source_item=merged['id'],surface_source=copy.deepcopy(merged['meta']))
            original={x['id']:Path(x['meta']['file']).read_bytes() for x in (a,b,c)}
            before={p.name for p in Path(folder).glob('*.xyz64')}
            draft=rw.solve_pair(s,'a','c')
            with mock.patch.object(jobdata,'room',side_effect=AssertionError('Registration wrote a full copy')):
                rw.accept(s,draft['id'],True)
            self.assertEqual(before,{p.name for p in Path(folder).glob('*.xyz64')})
            self.assertTrue(merged['merge_stale']);self.assertTrue(merged['merge_archived'])
            self.assertFalse(merged['visible']);self.assertEqual(len(merged['edit_history']),1)
            self.assertTrue(Path(merged['meta']['file']).exists())
            self.assertIsNone(s.STATE['active_job_scan']);self.assertIsNone(s.STATE['surface_source_item'])
            for x in (a,b,c):
                self.assertTrue(x['visible']);self.assertNotIn('merge_parent',x)
                self.assertEqual(original[x['id']],Path(x['meta']['file']).read_bytes())
                world=registration_pose.points(x['meta'],np.fromfile(x['meta']['file'],'<f8').reshape(-1,3))
                np.testing.assert_allclose(world,np.fromfile(a['meta']['file'],'<f8').reshape(-1,3),atol=1e-10)
            # Polling must not repeatedly undo a user's Hide or clear a current export.
            a['visible']=False;s.STATE['export_ready']={'id':'current-export'}
            registered_cloud.reconcile_state(s.STATE)
            self.assertFalse(a['visible']);self.assertEqual(s.STATE['export_ready']['id'],'current-export')
            with self.assertRaisesRegex(ValueError,'predates'):
                registered_cloud.require_current(merged)
            with self.assertRaisesRegex(ValueError,'no longer current'):
                rw.accept(s,draft['id'],True)

    def test_legacy_duplicate_scene_repaired_on_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            merged.pop('merge_source_revisions');a['visible']=b['visible']=True
            data=workflows.serial(s);restored=workflows.prepare_data(s,data)
            byid={x['id']:x for x in restored['job_items']}
            self.assertTrue(byid[merged['id']]['merge_archived'])
            self.assertFalse(byid[merged['id']]['visible'])
            self.assertTrue(all(byid[i]['visible'] for i in ('a','b')))
            self.assertNotIn('merge_parent',byid['a'])
            self.assertTrue(Path(merged['meta']['file']).is_file())
            np.testing.assert_allclose(registration_pose.point(byid['b']['meta'],[10,0,0]),[0,0,0],atol=1e-10)

    def test_current_merged_result_roundtrip_remains_one_representation(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            restored=workflows.prepare_data(s,workflows.serial(s))
            current=next(x for x in restored['job_items'] if x['id']==merged['id'])
            self.assertFalse(current.get('merge_stale'));self.assertTrue(current['visible'])
            self.assertEqual(sum(x['visible'] for x in restored['job_items']),1)
            registered_cloud.set_visibility(restored['job_items'],restored['job_items'][0],True)
            self.assertFalse(current['visible'])
            registered_cloud.show_current(restored['job_items'])
            self.assertTrue(current['visible']);self.assertEqual(sum(x['visible'] for x in restored['job_items']),1)

    def test_portable_merge_provenance_follows_remapped_source_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            portable=Path(folder)/'scene.tstudio'
            projects.portable(s.WORK,workflows.serial(s),portable)
            data=projects.unpack(s.WORK,portable)
            restored=workflows.prepare_data(s,data)
            current=next(x for x in restored['job_items'] if x['id']==merged['id'])
            self.assertNotEqual(current['meta']['file'],merged['meta']['file'])
            self.assertFalse(current.get('merge_stale'));self.assertTrue(current['visible'])
            self.assertEqual(sum(x['visible'] for x in restored['job_items']),1)

    def test_highlighted_export_and_targets_use_world_pose_and_deletions(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder)
            R=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
            registration_pose.compose(b['meta'],R,[180000.,399000.,50.])
            mask=Path(folder)/'b.mask';np.array([0,1,0,0],dtype='u1').tofile(mask);b['meta']['mask_file']=str(mask)
            raw=np.fromfile(b['meta']['file'],'<f8').reshape(-1,3)
            expected=registration_pose.points(b['meta'],raw[[0,2,3]])
            request={'ids':['b'],'whole':True,'versions':{'b':scene_objects.source_revision(b)}}
            q=np.concatenate([q for q,_ in workspace_tools.batches(s,request,lambda _:None)])
            np.testing.assert_allclose(q,expected,atol=1e-9)
            selected={'mode':'box','minimum':(expected[0]-.001).tolist(),'maximum':(expected[0]+.001).tolist()}
            limited={**request,'whole':False,'selection':selected}
            q=np.concatenate([q for q,_ in workspace_tools.batches(s,limited,lambda _:None)])
            self.assertEqual(len(q),1);np.testing.assert_allclose(q[0],expected[0],atol=1e-9)
            markers=rw.scan_summary(s,b)['markers']
            np.testing.assert_allclose(markers[0]['source'],registration_pose.point(b['meta'],raw[0]),atol=1e-9)
            before=scene_objects.source_revision(b);registration_pose.compose(b['meta'],np.eye(3),[1,0,0])
            self.assertNotEqual(before,scene_objects.source_revision(b))
            with self.assertRaisesRegex(ValueError,'source changed'):workspace_tools.sources(s,request)

    def test_duplicate_merge_and_sources_cannot_be_exported_together(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            with self.assertRaisesRegex(ValueError,'both copies'):
                workspace_tools.sources(s,{'ids':['a',merged['id']],'whole':True})

    def test_control_apply_positions_component_once_without_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);merged=rw.build_merge(s,'a',lambda _:None)
            r=rw.activate(s,'a');rows=copy.deepcopy(r['rows'])
            R=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
            t=np.array([180000.,399000.,50.])
            for row in rows:row['control']=(R@np.asarray(row['source'])+t).tolist()
            r['rows']=rows;solution=registration.solve(rows);r['solution']=solution
            before={p.name:p.read_bytes() for p in Path(folder).glob('*.xyz64')}
            with mock.patch.object(registration,'transformed_cloud',side_effect=AssertionError('Unexpected duplicate cache')):
                body={'action':'apply','reviewed':True,'solution_id':solution['id'],'to_survey':False}
                workflows.post(s,SimpleNamespace(send=lambda _:None),'/api/registration-action',body)
                workflows.post(s,SimpleNamespace(send=lambda _:None),'/api/registration-action',body)
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(folder).glob('*.xyz64')})
            self.assertEqual(len(s.STATE['job_items']),3)
            self.assertTrue(merged['merge_stale']);self.assertFalse(merged['visible'])
            for x in (a,b):
                raw=np.fromfile(x['meta']['file'],'<f8').reshape(-1,3)
                local=raw-(np.array([10,0,0]) if x is b else 0)
                np.testing.assert_allclose(registration_pose.points(x['meta'],raw),local@R.T+t,atol=1e-8)
            self.assertEqual(r['rows'][0]['source'],[0.,0.,0.])
            self.assertEqual(s.STATE['registration']['transformed']['file'],a['meta']['file'])
            self.assertEqual(len(workspace_tools.inventory(s)),1)

    def test_network_adjusted_links_keep_one_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder)
            s.STATE['scan_links'][0]['status']='network adjusted'
            self.assertEqual(workspace_tools.inventory(s)[0]['scan_ids'],['a','b'])

    def test_main_view_target_pick_is_converted_to_stored_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            s,a,b=self.pair(folder);rw.activate(s,'b')
            with mock.patch.object(registration,'detect',return_value={'candidates':[]}) as detect:
                workflows.post(s,SimpleNamespace(send=lambda _:None),'/api/registration-action',
                               {'action':'detect','seed':[0.,0.,0.],'seed_frame':'world','target_type':'sphere','diameter':.1988})
            np.testing.assert_allclose(detect.call_args.args[3],[10.,0.,0.],atol=1e-10)


if __name__=='__main__':unittest.main()
