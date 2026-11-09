"""Prepared-analysis totals count retained rows while preserving Undo geometry."""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from core import Alignment,project_cloud
import job_api
import projects
import scan_edits


class AnalysisCounts0187Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.xyz=np.column_stack((np.arange(12),np.zeros((12,2)))).astype('<f8')
        self.xyz.tofile(self.root/'source.xyz64')
        flags=np.zeros(12,'u1');flags[:4]=1;flags.tofile(self.root/'source.mask')
        self.meta=dict(name='Scan',file=str(self.root/'source.xyz64'),count=12,
            mask_file=str(self.root/'source.mask'),minimum=self.xyz.min(0).tolist(),
            maximum=self.xyz.max(0).tolist(),attribute_counts=[0]*4)
        self.axis=Alignment([dict(chainage=0.,e=0.,n=0.,h=0.,radius=1.),
                             dict(chainage=5.,e=5.,n=0.,h=0.,radius=1.)])

    def test_projection_counts_retained_rows_and_keeps_deleted_rows_for_undo(self):
        derived,count=project_cloud(self.meta,self.axis,self.root/'derived.analysis64')
        self.assertEqual(count,2)
        rows=np.fromfile(derived,'<f8').reshape(-1,4)
        self.assertEqual(rows.shape,(12,4))
        self.assertEqual(np.flatnonzero(np.isfinite(rows[:,0])).tolist(),list(range(6)))
        self.assertTrue(np.isfinite(rows[:4]).all())
        self.assertNotIn('retained_summary',self.meta,
                         'Temporary mesh projections must not attach a discarded derived path')

    def test_fresh_analysis_keeps_exact_total_after_deleting_outside_alignment(self):
        derived,count=project_cloud(self.meta,self.axis,self.root/'derived.analysis64')
        scan_edits.record_projection(self.meta,derived,count)
        summary=scan_edits.cached_summary(self.meta)
        self.assertEqual(summary['count'],8);self.assertEqual(summary['valid_points'],2)
        item=dict(id='scan',name='Scan',kind='scan',visible=True,meta=self.meta,
                  edit_history=[],redo_history=[],deleted_groups=[])
        s=SimpleNamespace(WORK=self.root,STATE=dict(job_items=[item],active_job_scan='scan',
            cloud=copy.deepcopy(self.meta),derived=derived,valid_points=count,edits=[],redo=[]),
            SELECTION=None,progress=lambda _:None)
        s.save=lambda:projects.atomic_json(self.root/'saved.json',s.STATE)
        original=Path(derived).read_bytes()
        result=scan_edits.delete_many(s,['scan'],dict(mode='box',
            minimum=[10.9,-.1,-.1],maximum=[11.1,.1,.1]))
        self.assertEqual(result['count'],1);self.assertEqual(s.STATE['valid_points'],2)
        scan_edits.apply(s,item,'undo_edit')
        self.assertEqual(s.STATE['valid_points'],2)
        self.assertEqual(Path(derived).read_bytes(),original)

    def test_incomplete_deletion_mask_cannot_produce_exact_count(self):
        Path(self.meta['mask_file']).write_bytes(b'\0'*11)
        destination=self.root/'bad.analysis64'
        with self.assertRaisesRegex(ValueError,'incomplete'):
            project_cloud(self.meta,self.axis,destination)
        self.assertFalse(destination.exists())

    def test_source_changed_during_projection_cannot_publish_a_count(self):
        path=Path(self.meta['mask_file']);before=path.stat()
        def mutate(_message):
            os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000))
        with self.assertRaisesRegex(ValueError,'changed'):
            project_cloud(self.meta,self.axis,self.root/'changed.analysis64',mutate)

    def test_repeated_use_for_analysis_skips_projection_and_new_disk_allocation(self):
        derived,count=project_cloud(self.meta,self.axis,self.root/(uuid.uuid4().hex+'.analysis64'))
        scan_edits.record_projection(self.meta,derived,count)
        item=dict(id='scan',name='Scan',kind='scan',meta=self.meta)
        s=SimpleNamespace(WORK=self.root,STATE=dict(axis=self.axis,job_items=[item]),
            JOB={'busy':False},SELECTION=None,progress=lambda _:None,save=lambda:None,
            start_job=lambda fn,*args,**kwargs:fn())
        request=dict(action='analyse',id='scan',position_confirmed=True)
        with patch.object(job_api,'project_cloud',side_effect=AssertionError('reprojected')), \
             patch.object(job_api.jobdata,'room',side_effect=AssertionError('reserved new disk space')):
            self.assertTrue(job_api.post(s,SimpleNamespace(send=lambda _:None),'/api/job-action',request))
        self.assertEqual((s.STATE['derived'],s.STATE['valid_points']),(derived,count))

    def test_projection_reuse_validates_alignment_pose_file_and_edited_count(self):
        derived,count=project_cloud(self.meta,self.axis,self.root/(uuid.uuid4().hex+'.analysis64'))
        scan_edits.record_projection(self.meta,derived,count)
        self.assertEqual(scan_edits.reusable_projection(self.meta,self.axis,self.root),(derived,2))
        changed=Alignment([dict(chainage=0.,e=0.,n=0.,h=0.,radius=2.),
                           dict(chainage=5.,e=5.,n=0.,h=0.,radius=2.)])
        self.assertIsNone(scan_edits.reusable_projection(self.meta,changed,self.root))
        moved={**self.meta,'registration_translation':[1.,0.,0.]}
        self.assertIsNone(scan_edits.reusable_projection(moved,self.axis,self.root))

        old_mask=self.meta['mask_file']
        after,changed_count=scan_edits.update_mask(self.meta,[np.array([4])],self.root,1,lambda _:None,[],derived,count)
        self.meta['mask_file']=after
        self.assertEqual(changed_count,1)
        self.assertNotEqual(after,old_mask)
        self.assertEqual(scan_edits.reusable_projection(self.meta,self.axis,self.root),(derived,1))
        path=Path(derived);data=path.read_bytes();path.write_bytes(data)
        self.assertIsNone(scan_edits.reusable_projection(self.meta,self.axis,self.root))


if __name__=='__main__':unittest.main()
