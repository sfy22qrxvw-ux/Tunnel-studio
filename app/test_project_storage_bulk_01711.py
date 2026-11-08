import tempfile,time,unittest
from pathlib import Path
from unittest import mock
import projects

class ProjectStorageBulk01711Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);(self.root/'projects').mkdir()
    def tearDown(self):
        # Let any tiny background purge thread finish before TemporaryDirectory cleanup.
        for _ in range(50):
            if not projects.storage_purge_status(self.root)['running']:break
            time.sleep(.01)
        self.t.cleanup()
    def asset(self,ch,size):
        p=self.root/(ch*32+'.xyz64');p.write_bytes(bytes([ord(ch)%251])*size);return str(p)
    def save(self,title,assets):
        return projects.save_named(self.root,{'project_title':title,'assets':[{'file':x} for x in assets]},title,True)
    def test_overview_shows_total_exclusive_and_shared(self):
        shared=self.asset('a',31);one=self.asset('b',47);two=self.asset('c',53)
        p1=self.save('One',[shared,one]);p2=self.save('Two',[shared,two])
        rows={r['project_id']:r for r in projects.projects_storage_overview(self.root,{})}
        self.assertEqual(rows[p1['project_id']]['total_bytes'],78)
        self.assertEqual(rows[p1['project_id']]['exclusive_bytes'],47)
        self.assertEqual(rows[p1['project_id']]['shared_bytes'],31)
        self.assertEqual(rows[p2['project_id']]['exclusive_bytes'],53)
    def test_batch_makes_selected_only_shared_asset_reclaimable(self):
        shared=self.asset('d',61);a=self.asset('e',17);b=self.asset('f',19)
        p1=self.save('One',[shared,a]);p2=self.save('Two',[shared,b])
        r=projects.projects_storage_review(self.root,[p1['project_id'],p2['project_id']],{})
        self.assertEqual(r['exclusive_bytes'],97)
        self.assertEqual(r['shared_bytes'],0)
    def test_batch_protects_asset_used_by_unselected_project(self):
        shared=self.asset('1',71);a=self.asset('2',23);b=self.asset('3',29)
        p1=self.save('One',[shared,a]);p2=self.save('Two',[shared,b]);p3=self.save('Three',[shared])
        r=projects.projects_storage_review(self.root,[p1['project_id'],p2['project_id']],{})
        self.assertEqual(r['exclusive_bytes'],52)
        self.assertEqual(r['shared_bytes'],71)
        self.assertTrue((self.root/'projects'/(p3['project_id']+'.json')).exists())
    def test_delete_returns_after_quarantine_without_waiting_for_physical_purge(self):
        a=self.asset('4',101);p=self.save('Slow',[a])
        real=projects._schedule_purge
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            out=projects.delete_projects_permanently(self.root,[p['project_id']],{},101)
            schedule.assert_called_once()
            self.assertFalse(Path(a).exists())
            self.assertFalse((self.root/'projects'/(p['project_id']+'.json')).exists())
            self.assertTrue(out['purge_pending'])
            stage=Path(schedule.call_args.args[0]);self.assertTrue(stage.exists())
            projects._purge_stage(stage)
        self.assertFalse(stage.exists())
    def test_resume_purge_finds_pending_queue(self):
        queue=self.root/projects._PURGE_ROOT/('delete_20261104T010000Z_'+'a'*32);queue.mkdir(parents=True)
        (queue/'x.bin').write_bytes(b'x'*7);projects._write_purge_meta(queue,{'bytes':7,'label':'Old'})
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            projects.resume_storage_purges(self.root)
            schedule.assert_called_once_with(queue)
    def test_unmarked_legacy_directory_is_never_adopted_for_deletion(self):
        legacy=self.root/'.delete_project_old';legacy.mkdir();(legacy/'x.bin').write_bytes(b'x')
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            projects.resume_storage_purges(self.root)
            self.assertTrue((legacy/'x.bin').is_file())
            schedule.assert_not_called()
    def test_active_project_does_not_self_protect_in_batch(self):
        a=self.asset('5',41);p=self.save('Current',[a])
        r=projects.projects_storage_review(self.root,[p['project_id']],p)
        self.assertTrue(r['active']);self.assertEqual(r['exclusive_bytes'],41)
    def test_active_project_includes_new_working_assets_not_in_last_named_save(self):
        saved=self.asset('6',43);new=self.asset('7',47);p=self.save('Current',[saved])
        current={'project_id':p['project_id'],'project_title':'Current','cloud':{'file':saved},'analysis':{'results_file':new}}
        r=projects.projects_storage_review(self.root,[p['project_id']],current)
        self.assertEqual(r['exclusive_bytes'],90)
        row={x['project_id']:x for x in projects.projects_storage_overview(self.root,current)}[p['project_id']]
        self.assertEqual(row['total_bytes'],90)

if __name__=='__main__':unittest.main()
