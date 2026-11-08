"""Full requested heatmap extent and mesh registration/revision boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import heatmap
import mesh_workflows
import registration_pose
from core import Alignment, project_cloud


class WholeCloudWorkflows0183Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.axis=Alignment([dict(chainage=x,e=0.,n=x,h=0.,radius=3.5) for x in (0.,1000.)])
        stations=np.array([0.,.1,200.,400.,600.,800.,999.9,1000.])
        points=np.c_[np.full(len(stations),3.51),stations,np.zeros(len(stations))]
        path=self.root/'scan.xyz64';points.astype('<f8').tofile(path)
        self.meta=dict(file=str(path),name='Complete tunnel',count=len(points),minimum=points.min(0).tolist(),maximum=points.max(0).tolist())
        self.source=dict(id='scan',kind='scan',name='Complete tunnel',visible=True,meta=self.meta)
        self.state=dict(job_items=[self.source],axis=self.axis,structures=[])
        self.s=SimpleNamespace(STATE=self.state,WORK=self.root,progress=lambda _:None)

    def tearDown(self):self.temp.cleanup()

    def test_heatmap_exceeding_old_grid_limit_keeps_both_ends_and_all_retained_points(self):
        derived,_=project_cloud(self.meta,self.axis,self.root/'scan.analysis64')
        original=Path(self.meta['file']).read_bytes()
        mask=np.zeros(self.meta['count'],'u1');mask[2]=1
        path=self.root/'scan.mask';mask.tofile(path);self.meta['mask_file']=str(path)
        messages=[]
        result=heatmap.calculate(self.meta,derived,self.axis,0.,1000.,.01,1.,.3,'mean',progress=messages.append)
        self.assertTrue(result['resolution_adjusted'])
        self.assertEqual(result['requested_step'],.01)
        self.assertEqual(result['requested_cells'],36000000)
        self.assertLessEqual(result['nx']*result['ny'],300000)
        self.assertEqual((result['start'],result['end']),(0.,1000.))
        self.assertEqual(result['points'],7)
        counts=np.asarray(result['counts']).reshape(result['ny'],result['nx'])
        self.assertEqual(int(counts[:,0].sum()),2)
        self.assertEqual(int(counts[:,-1].sum()),2)
        np.testing.assert_allclose([x for x in result['values'] if x is not None],10.,atol=1e-8)
        self.assertTrue(any('all retained points' in m for m in messages))
        self.assertIn('requested',result['resolution_note'])
        self.assertEqual(Path(self.meta['file']).read_bytes(),original)
        np.testing.assert_array_equal(np.fromfile(path,'u1'),mask)

    def test_small_heatmap_preserves_requested_resolution(self):
        derived,_=project_cloud(self.meta,self.axis,self.root/'scan.analysis64')
        result=heatmap.calculate(self.meta,derived,self.axis,0.,1000.,10.,5.,.3)
        self.assertFalse(result['resolution_adjusted'])
        self.assertEqual(result['step'],10.)
        self.assertEqual(result['points'],self.meta['count'])

    def test_stale_merged_scan_is_rejected_before_mesh_cache_creation(self):
        self.source.update(registered_merge=True,merge_stale='Registration changed')
        before=set(self.root.iterdir())
        with patch.object(mesh_workflows.meshing,'create',side_effect=AssertionError('Must reject before reconstruction')):
            with self.assertRaisesRegex(ValueError,'predates'):
                mesh_workflows.create_from_scan(self.s,{'source_id':'scan'})
        self.assertEqual(set(self.root.iterdir()),before)

    def test_pose_changed_during_meshing_discards_result_and_does_not_publish(self):
        original=Path(self.meta['file']).read_bytes();before=set(self.root.iterdir())
        def build(*args,**kwargs):
            (Path(args[1])/'temporary.vertices64').write_bytes(b'partial output')
            registration_pose.set_pose(self.meta,np.eye(3),[20.,0.,0.])
            return {'mesh_summary':{}}
        with patch.object(mesh_workflows.meshing,'create',side_effect=build):
            with self.assertRaisesRegex(ValueError,'source changed'):
                mesh_workflows.create_from_scan(self.s,{'source_id':'scan'})
        self.assertEqual(len(self.state['job_items']),1)
        self.assertEqual(set(self.root.iterdir()),before)
        self.assertEqual(Path(self.meta['file']).read_bytes(),original)

    def test_alignment_mesh_does_not_reuse_projection_from_an_old_pose(self):
        old=copy.deepcopy(self.meta)
        self.state.update(cloud=old,derived=str(self.root/'old.analysis64'))
        registration_pose.set_pose(self.meta,np.eye(3),[20.,0.,0.])
        body=dict(start=0.,end=1000.,step=1.,degrees=5.,band=.3,min_points=1,max_edge=2.)
        with patch.object(mesh_workflows,'project_cloud',wraps=project_cloud) as project:
            with patch.object(mesh_workflows.structures,'scan_mesh',side_effect=ValueError('stop after projection')):
                with self.assertRaisesRegex(ValueError,'stop after projection'):
                    mesh_workflows._alignment_mesh(self.s,self.source,self.root,'mesh',body,None)
        self.assertEqual(project.call_count,1)
        self.assertFalse((self.root/'mesh-analysis.analysis64').exists())


if __name__=='__main__':unittest.main()
