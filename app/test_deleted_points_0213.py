"""Full-density partial restoration, bounded I/O, Undo and atomic rollback."""
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import json
import tempfile
import unittest
from unittest import mock
import numpy as np
import deleted_points
import jobdata
import pointio
import projects
import registration_pose
import scan_edits
import workspace_history


class DeletedPointAreaTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.s=SimpleNamespace(WORK=self.root,STATE={'job_items':[]},JOB={'busy':False},SELECTION={'old':True},progress=lambda _:None)
        self.saves=0
        def save():
            self.saves+=1;projects.atomic_json(self.root/'saved.json',self.s.STATE)
        self.s.save=save

    def scan(self,name,n=31,shift=0):
        xyz=np.column_stack((np.arange(n,dtype=float),np.zeros(n),np.zeros(n)))
        attrs=np.column_stack((np.arange(n,dtype=float),np.ones((n,3))))
        f=self.root/(name+'.xyz64');a=f.with_suffix('.attr32');xyz.astype('<f8').tofile(f);attrs.astype('<f4').tofile(a)
        meta=dict(name=name,file=str(f),attributes_file=str(a),count=n,minimum=xyz.min(0).tolist(),maximum=xyz.max(0).tolist(),attribute_counts=[n]*4)
        registration_pose.set_pose(meta,np.eye(3),[shift,0,0])
        item=dict(id=name,name=name,kind='scan',meta=meta,visible=True,edit_history=[],redo_history=[],deleted_groups=[])
        self.s.STATE['job_items'].append(item);return item,xyz

    @staticmethod
    def box(a,b):return dict(mode='box',minimum=[a,-1,-1],maximum=[b,1,1])

    def deleted(self,item,lo=0,hi=1e9):
        scan_edits.apply(self.s,item,'delete_selection',self.box(lo,hi),commit=False)
        return item['deleted_groups'][-1]

    def archived(self,name='cleaned',n=31,shift=0):
        source,xyz=self.scan(name+'-source',n,shift);self.s.STATE['job_items'].remove(source)
        ids=np.arange(n,dtype='<i8');labels=np.where(ids%2,1,0).astype('<i4')
        ip=self.root/(name+'.surface_ids');lp=self.root/(name+'.surface_labels');ids.tofile(ip);labels.tofile(lp)
        clean,_=self.scan(name,3,shift)
        group=dict(id=name+'-removed',kind='delete',name='Cleaning exclusions',count=int((labels==0).sum()),active=True,
            archive=dict(source_meta=source['meta'],point_ids_file=str(ip),labels_file=str(lp),input_count=n,kept=[1]))
        clean['deleted_groups'].append(group);return clean,group,xyz

    def restore(self,pairs,selection,**kwargs):return deleted_points.restore_selection(self.s,pairs,selection,**kwargs)

    def test_live_area_uses_full_density_world_pose_and_bounded_reads(self):
        item,xyz=self.scan('posed',200003,shift=1000);group=self.deleted(item,1000,201002)
        original={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (item['meta']['file'],item['meta']['attributes_file'],item['meta']['mask_file'])}
        p=scan_edits.deleted_preview(item,group['id'],budget=3)
        self.assertNotIn(13345,{round(row[0]+p['origin'][0]) for row in p['points']})
        sizes=[];real=deleted_points.RowFile.take
        def take(reader,ids):sizes.append(len(ids));return real(reader,ids)
        with mock.patch.object(pointio,'arrays',side_effect=AssertionError('whole cloud access')),mock.patch.object(deleted_points.RowFile,'take',take):
            result=self.restore([[item['id'],group['id']]],self.box(13344.9,13345.1))
        self.assertEqual(result['restored_points'],1);self.assertLessEqual(max(sizes),pointio.CHUNK)
        flags=np.fromfile(item['meta']['mask_file'],'u1');self.assertEqual(int(flags.sum()),200002);self.assertEqual(flags[12345],0)
        ids=np.concatenate(list(scan_edits.group_ids(item['meta'],group)));self.assertNotIn(12345,ids);self.assertEqual(len(ids),200002)
        for path,digest in original.items():self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),digest)
        self.assertEqual(self.saves,1)

    def test_repeat_restore_then_whole_group_only_restores_remainder(self):
        item,_=self.scan('live');g=self.deleted(item)
        self.restore([['live',g['id']]],self.box(4,8));old=g['remaining']['ids_file'];old_bytes=Path(old).read_bytes()
        before=copy.deepcopy(self.s.STATE)
        with self.assertRaisesRegex(ValueError,'No deleted full-density'):self.restore([['live',g['id']]],self.box(4,8))
        self.assertEqual(self.s.STATE,before);item=self.s.STATE['job_items'][0];g=item['deleted_groups'][0]
        self.restore([['live',g['id']]],self.box(10,14));self.assertEqual(Path(old).read_bytes(),old_bytes);self.assertEqual(g['count'],21)
        scan_edits.apply(self.s,item,'restore_deleted',group_id=g['id'])
        self.assertFalse(g['active']);self.assertFalse(np.fromfile(item['meta']['mask_file'],'u1').any())

    def test_archive_creates_only_selected_scan_and_persists_remainder(self):
        clean,g,xyz=self.archived(n=200003,shift=1000);before=copy.deepcopy(clean['meta'])
        result=self.restore([[clean['id'],g['id']]],self.box(1010,1020))
        self.assertEqual(result,dict(restored_points=6,changed_groups=1,restored_scans=1))
        restored=self.s.STATE['job_items'][-1];self.assertEqual(clean['meta'],before)
        np.testing.assert_array_equal(np.fromfile(restored['meta']['file'],'<f8').reshape(-1,3),xyz[np.arange(10,21,2)])
        np.testing.assert_array_equal(registration_pose.points(restored['meta'],np.array([[10,0,0]])),[[1010,0,0]])
        self.assertEqual(g['count'],99996)
        bounds=scan_edits.deleted_preview_many([clean],[[clean['id'],g['id']]])['bounds'][0]
        self.assertEqual(bounds['maximum'][0],201002)
        preview=scan_edits.deleted_preview(clean,g['id'],budget=200003)
        world=np.asarray(preview['points'])[:,:3]+preview['origin'];self.assertFalse(((world[:,0]>=1010)&(world[:,0]<=1020)).any())
        saved=json.loads((self.root/'saved.json').read_text());self.assertEqual(saved['job_items'][0]['deleted_groups'][0]['remaining'],g['remaining'])
        self.assertIn(Path(g['remaining']['ids_file']).resolve(),projects._local_asset_paths(self.root,self.s.STATE,owned_only=True))
        scan_edits.restore_archive(self.s,clean,g,self.s.progress)
        self.assertFalse(g['active']);self.assertEqual(self.s.STATE['job_items'][-1]['meta']['count'],99996)

    def test_mixed_groups_are_one_workspace_undo_and_redo(self):
        live,_=self.scan('live');a=self.deleted(live);clean,b,_=self.archived();before=copy.deepcopy(self.s.STATE)
        with workspace_history.request(self.s,'/api/job-action'):
            workspace_history.configure('/api/job-action',{'action':'restore_deleted_selection'})
            result=self.restore([['live',a['id']],['cleaned',b['id']]],self.box(4,8))
        self.assertEqual(result['restored_points'],8);self.assertEqual(self.saves,1)
        after=copy.deepcopy(self.s.STATE);self.assertEqual(workspace_history.status(self.s)['undo_count'],1)
        workspace_history.apply(self.s);self.assertEqual(self.s.STATE['job_items'],before['job_items'])
        workspace_history.apply(self.s,redo=True);self.assertEqual(self.s.STATE['job_items'],after['job_items'])

    def test_cancel_after_first_group_rolls_back_all_new_files(self):
        clean,a,_=self.archived();live,_=self.scan('live');b=self.deleted(live)
        previous=copy.deepcopy(self.s.STATE);paths=set(self.root.iterdir())
        def progress(message):
            if 'group 2/2' in message:raise jobdata.Cancelled('test cancellation')
        with self.assertRaises(jobdata.Cancelled):self.restore([['cleaned',a['id']],['live',b['id']]],self.box(4,8),progress=progress)
        self.assertEqual(self.s.STATE,previous);self.assertEqual(set(self.root.iterdir()),paths);self.assertEqual(self.saves,0)

    def test_failed_save_rolls_back_masks_and_restored_scan(self):
        live,_=self.scan('live');a=self.deleted(live);clean,b,_=self.archived()
        previous=copy.deepcopy(self.s.STATE);paths=set(self.root.iterdir())
        with mock.patch.object(self.s,'save',side_effect=OSError('test failed save')):
            with self.assertRaisesRegex(OSError,'test failed save'):self.restore([['live',a['id']],['cleaned',b['id']]],self.box(4,8))
        self.assertEqual(self.s.STATE,previous);self.assertEqual(set(self.root.iterdir()),paths)

    def test_unselected_group_untouched_and_invalid_input_makes_no_files(self):
        item,_=self.scan('live');a=self.deleted(item,0,10);b=self.deleted(item,20,30)
        self.restore([['live',a['id']],['live',a['id']]],self.box(5,25))
        self.assertEqual(a['count'],5);self.assertEqual(b['count'],11)
        flags=np.fromfile(item['meta']['mask_file'],'u1');self.assertTrue(flags[20:31].all())
        before=copy.deepcopy(self.s.STATE);paths=set(self.root.iterdir())
        for pairs,selection in (([],self.box(0,1)),([['missing','missing']],self.box(0,1)),([['live',a['id']]],None)):
            with self.assertRaises(ValueError):self.restore(pairs,selection)
        self.assertEqual(self.s.STATE,before);self.assertEqual(set(self.root.iterdir()),paths)

    def test_deleted_export_combines_exact_posed_live_and_archived_points_without_restoring(self):
        import workspace_tools
        live,_=self.scan('live',shift=1000);a=self.deleted(live);clean,b,_=self.archived(shift=1000)
        before=copy.deepcopy(self.s.STATE)
        request=dict(ids=['live','cleaned'],deleted_groups=[['live',a['id']],['cleaned',b['id']]],selection=self.box(1004,1008))
        with workspace_tools.highlighted(self.s,request,self.s.progress) as (meta,revision):
            xyz=np.fromfile(meta['file'],'<f8').reshape(-1,3);attrs=np.fromfile(meta['attributes_file'],'<f4').reshape(-1,4)
            np.testing.assert_array_equal(xyz[:,0],[1004,1005,1006,1007,1008,1004,1006,1008]);np.testing.assert_array_equal(attrs[:,0],xyz[:,0]-1000)
            self.assertEqual(meta['count'],8);self.assertEqual(revision,workspace_tools.revision(self.s,request))
        self.assertEqual(self.s.STATE,before);self.assertEqual(self.saves,0)
        self.restore([['live',a['id']]],self.box(1004,1005))
        self.assertNotEqual(revision,workspace_tools.revision(self.s,request))

    def test_cancelled_deleted_export_removes_temporary_files_and_keeps_groups(self):
        import workspace_tools
        item,_=self.scan('live');g=self.deleted(item);before=copy.deepcopy(self.s.STATE);paths=set(self.root.iterdir())
        def cancel(message):raise jobdata.Cancelled('test export stopped')
        with self.assertRaises(jobdata.Cancelled):
            with workspace_tools.highlighted(self.s,dict(ids=['live'],deleted_groups=[['live',g['id']]],selection=self.box(4,8)),cancel):pass
        self.assertEqual(self.s.STATE,before);self.assertEqual(set(self.root.iterdir()),paths)

    def test_archive_export_can_include_measured_wall_context_in_the_same_area(self):
        import workspace_tools
        item,g,_=self.archived(shift=1000);before=copy.deepcopy(self.s.STATE)
        request=dict(ids=[item['id']],deleted_groups=[[item['id'],g['id']]],selection=self.box(1004,1008),include_deleted_context=True)
        with workspace_tools.highlighted(self.s,request,self.s.progress) as (meta,revision):
            p=np.fromfile(meta['file'],'<f8').reshape(-1,3)
            np.testing.assert_array_equal(p[:,0],[1004,1005,1006,1007,1008])
        self.assertEqual(self.s.STATE,before)


if __name__=='__main__':unittest.main()
