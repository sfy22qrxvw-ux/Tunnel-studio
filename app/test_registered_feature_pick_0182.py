"""Recover exact original picks from the registered, float32 preview (0.18.2)."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import cad_api
import jobdata
import preview_index
import registration_pose
import workflows


class RegisteredFeaturePickTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        n=preview_index.BLOCK*3+17;ids=np.arange(n);row=ids%preview_index.BLOCK
        self.points=np.c_[100*(ids//preview_index.BLOCK)+.01*(row%128),.01*(row//128),.001*np.sin(row*.03)]
        self.target=preview_index.BLOCK+2123
        # An excluded duplicate in a different block must never steal the pick.
        self.duplicate=7;self.points[self.duplicate]=self.points[self.target]
        self.mask=np.zeros(n,'u1');self.mask[self.duplicate]=1
        self.xyz=self.root/'measured.xyz64';self.maskpath=self.root/'excluded.mask'
        self.points.astype('<f8').tofile(self.xyz);self.mask.tofile(self.maskpath)
        a=.73;b=.41
        rz=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1.]])
        rx=np.array([[1.,0,0],[0,np.cos(b),-np.sin(b)],[0,np.sin(b),np.cos(b)]])
        self.rotation=rz@rx;self.translation=np.array([456789.1234567,321987.7654321,-24.9876543])
        self.meta=dict(name='Registered setup',file=str(self.xyz),count=n,minimum=self.points.min(0).tolist(),maximum=self.points.max(0).tolist(),mask_file=str(self.maskpath))
        registration_pose.set_pose(self.meta,self.rotation,self.translation)
        self.world=registration_pose.points(self.meta,self.points)
        self.item=dict(id='scan',kind='scan',name=self.meta['name'],meta=self.meta,visible=True)
        state=workflows.empty();state['job_items']=[self.item]
        self.server=SimpleNamespace(STATE=state,WORK=self.root,JOB={'busy':False},progress=lambda _:None)
        self.original_hash=hashlib.sha256(self.xyz.read_bytes()).hexdigest()
        # As in WebGL, the displayed position is float32 relative to a local
        # preview origin, then promoted back to a world-coordinate pick.
        lo,hi=registration_pose.world_bounds(self.meta);self.origin=(lo+hi)/2
        self.displayed=(self.world[self.target]-self.origin).astype(np.float32).astype(float)+self.origin
        self.body=dict(source_id=self.item['id'],source_revision=registration_pose.revision(self.meta),point=self.displayed.tolist())
        self.bounds=preview_index.bounds(self.meta)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.xyz.read_bytes()).hexdigest(),self.original_hash)
        self.tmp.cleanup()

    def pick(self,body=None,progress=lambda _:None):
        return cad_api.snap_cloud(self.server,self.body if body is None else body,progress)

    def box(self,centre,half=(.04,.04,.04)):
        return jobdata.selection_spec(dict(mode='oriented_box',centre=np.asarray(centre).tolist(),axes=self.rotation.T.tolist(),half_size=list(half)))

    def test_rotated_translated_float32_preview_recovers_exact_original_id_and_world_xyz(self):
        error=float(np.linalg.norm(self.displayed-self.world[self.target]))
        self.assertGreater(error,0)
        self.assertLess(error,.0001)
        value=self.pick()
        self.assertEqual(value['index'],self.target)
        self.assertEqual(value['source_id'],self.item['id'])
        self.assertEqual(value['source_revision'],registration_pose.revision(self.meta))
        np.testing.assert_array_equal(value['point'],self.world[self.target])
        # A world pick near460 km must still use local100 m block bounds.
        self.assertGreater(np.linalg.norm(self.displayed),400000)
        self.assertLess(np.linalg.norm(registration_pose.inverse_point(self.meta,self.displayed)),110)

    def test_masked_original_points_are_never_recovered(self):
        self.mask[self.target]=1;self.mask.tofile(self.maskpath)
        with self.assertRaisesRegex(ValueError,'retained measured point'):
            self.pick()

    def test_equal_distance_duplicate_ids_have_a_stable_lowest_id_tie_break(self):
        self.mask[self.duplicate]=0;self.mask.tofile(self.maskpath)
        value=self.pick();self.assertEqual(value['index'],self.duplicate)
        np.testing.assert_array_equal(value['point'],self.world[self.target])

    def test_limit_isolate_and_hidden_boxes_are_evaluated_in_registered_world_space(self):
        centre=self.world[self.target]
        inside=self.box(centre)
        outside=self.box(centre+self.rotation@np.array([.2,0.,0.]))
        self.item.update(limit_box=inside,isolate_box=inside,hidden_boxes=[outside])
        self.assertEqual(self.pick()['index'],self.target)
        self.item['hidden_boxes']=[inside]
        with self.assertRaisesRegex(ValueError,'retained measured point'):self.pick()
        self.item['hidden_boxes']=[];self.item['isolate_box']=outside
        with self.assertRaisesRegex(ValueError,'retained measured point'):self.pick()
        self.item['isolate_box']=None;self.item['limit_box']=outside
        with self.assertRaisesRegex(ValueError,'retained measured point'):self.pick()
        self.item['limit_hide_outside']=False
        self.assertEqual(self.pick()['index'],self.target)

    def test_pose_change_before_pick_rejects_stale_revision_without_opening_xyz(self):
        registration_pose.compose(self.meta,np.eye(3),[1.,0.,0.])
        with patch('cad_api.open',side_effect=AssertionError('A stale pick must fail before file reads.'),create=True):
            with self.assertRaisesRegex(ValueError,'scan changed'):self.pick()

    def test_pose_change_during_pick_prevents_publication(self):
        # The first block contains an unmasked duplicate, so there is a possible
        # answer before the simulated concurrent movement. It must be withheld.
        self.mask[self.duplicate]=0;self.mask.tofile(self.maskpath)
        changed=[]
        def move_during_read(message):
            if not changed and message.startswith('Snapping'):
                changed.append(True);registration_pose.compose(self.meta,np.eye(3),[1.,0.,0.])
        with self.assertRaisesRegex(ValueError,'changed while picking'):
            self.pick(progress=move_during_read)
        self.assertEqual(changed,[True])

    def test_cancellation_closes_owned_xyz_and_mask_handles(self):
        opened=[];real_open=open
        def tracked_open(*args,**kwargs):
            handle=real_open(*args,**kwargs);opened.append(handle);return handle
        def cancel(message):
            raise RuntimeError('cancelled at an exact-point read checkpoint')
        with patch('cad_api.open',tracked_open,create=True):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                self.pick(progress=cancel)
        self.assertEqual({Path(handle.name) for handle in opened},{self.xyz,self.maskpath})
        self.assertTrue(all(handle.closed for handle in opened))

    def test_exact_reads_are_bounded_and_prune_unrelated_original_blocks(self):
        calls=[];read=np.fromfile
        def record(file,*args,**kwargs):
            calls.append((Path(file.name),int(kwargs.get('count',args[1] if len(args)>1 else -1))))
            return read(file,*args,**kwargs)
        with patch.object(cad_api.np,'fromfile',record):value=self.pick()
        self.assertEqual(value['index'],self.target)
        xyz=[count for path,count in calls if path==self.xyz]
        masks=[count for path,count in calls if path==self.maskpath]
        self.assertEqual(len(xyz),2)  # duplicate's block and target's block only
        self.assertEqual(len(masks),2)
        self.assertTrue(all(0<count<=3*preview_index.BLOCK for count in xyz))
        self.assertTrue(all(0<count<=preview_index.BLOCK for count in masks))
        self.assertLess(sum(xyz),self.meta['count']*3)

    def test_stale_merge_and_hidden_sources_are_rejected(self):
        self.item['merge_stale']='Registration changed'
        with self.assertRaisesRegex(ValueError,'predates the current registration'):
            self.pick()
        self.item.pop('merge_stale');self.item['visible']=False
        with self.assertRaisesRegex(ValueError,'visible scan'):self.pick()


if __name__=='__main__':unittest.main()
