import tempfile, unittest
from pathlib import Path
import projects

class ProjectDelete0179Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);(self.root/'projects').mkdir()
    def tearDown(self):self.t.cleanup()
    def asset(self,name,size=32):
        p=self.root/name;p.write_bytes(b'x'*size);return str(p)
    def save(self,title,file,ident=None):
        d={'project_title':title,'cloud':{'file':file}}
        if ident:d['project_id']=ident
        return projects.save_named(self.root,d,title,as_new=not bool(ident))
    def test_delete_removes_exclusive_keeps_shared(self):
        shared=self.asset('a'*32+'.xyz64',31);exclusive=self.asset('b'*32+'.analysis64',47)
        p1=projects.save_named(self.root,{'project_title':'One','cloud':{'file':shared},'surface_result':{'results_file':exclusive}},'One',True)
        p2=projects.save_named(self.root,{'project_title':'Two','cloud':{'file':shared}},'Two',True)
        r=projects.project_storage_review(self.root,p1['project_id'],p2)
        self.assertEqual(r['exclusive_bytes'],47);self.assertEqual(r['shared_bytes'],31)
        out=projects.delete_project_permanently(self.root,p1['project_id'],p2,47)
        self.assertFalse(Path(exclusive).exists());self.assertTrue(Path(shared).exists())
        self.assertFalse((self.root/'projects'/(p1['project_id']+'.json')).exists())
        self.assertTrue((self.root/'projects'/(p2['project_id']+'.json')).exists())
        self.assertEqual(out['exclusive_bytes'],47)
    def test_current_same_project_does_not_self_protect(self):
        a=self.asset('c'*32+'.xyz64',55)
        p=projects.save_named(self.root,{'project_title':'Current','cloud':{'file':a}},'Current',True)
        r=projects.project_storage_review(self.root,p['project_id'],p)
        self.assertTrue(r['active']);self.assertEqual(r['exclusive_bytes'],55)
    def test_recovery_protects_shared_asset(self):
        a=self.asset('d'*32+'.xyz64',21)
        p=projects.save_named(self.root,{'project_title':'Old','cloud':{'file':a}},'Old',True)
        rec=self.root/'startup_recovery';rec.mkdir();projects.atomic_json(rec/'keep.json',{'cloud':{'file':a}})
        r=projects.project_storage_review(self.root,p['project_id'],{})
        self.assertEqual(r['exclusive_bytes'],0);self.assertEqual(r['shared_bytes'],21)
    def test_orphan_cleanup_only_unreferenced_generated_data(self):
        keep=self.asset('e'*32+'.xyz64',19);orphan=self.asset('f'*32+'.xyz64',23)
        foreign=self.root/'notes.txt';foreign.write_text('keep me')
        p=projects.save_named(self.root,{'project_title':'Keep','cloud':{'file':keep}},'Keep',True)
        r=projects.orphan_storage_review(self.root,p)
        self.assertEqual(r['bytes'],23);self.assertIn(orphan,r['paths']);self.assertNotIn(str(foreign),r['paths'])
        projects.clean_orphan_storage(self.root,p,23)
        self.assertFalse(Path(orphan).exists());self.assertTrue(Path(keep).exists());self.assertTrue(foreign.exists())
    def test_orphan_cleanup_never_auto_deletes_neutral_exchange_files(self):
        e57=self.root/('2'*32+'.e57');e57.write_bytes(b'e'*29)
        xyz=self.root/('3'*32+'.xyz');xyz.write_bytes(b'x'*31)
        cache=self.root/('4'*32+'.xyz64');cache.write_bytes(b'c'*37)
        r=projects.orphan_storage_review(self.root,{})
        self.assertIn(str(cache),r['paths'])
        self.assertNotIn(str(e57),r['paths'])
        self.assertNotIn(str(xyz),r['paths'])

    def test_stale_review_rejected(self):
        a=self.asset('1'*32+'.xyz64',11);p=projects.save_named(self.root,{'project_title':'P','cloud':{'file':a}},'P',True)
        with self.assertRaisesRegex(ValueError,'changed'):
            projects.delete_project_permanently(self.root,p['project_id'],{},99)
        self.assertTrue(Path(a).exists())

if __name__=='__main__':unittest.main()
