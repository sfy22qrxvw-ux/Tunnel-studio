import tempfile,unittest,time
from pathlib import Path
import projects,session_start

class UniversalStorage01712Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);(self.root/'projects').mkdir()
    def tearDown(self):self.t.cleanup()
    def asset(self,name,size=11):
        p=self.root/name;p.write_bytes(b'x'*size);return str(p)
    def test_previous_session_named_projects_are_retired_and_not_listed_or_protecting(self):
        a=self.asset('a'*32+'.xyz64',31)
        p=projects.save_named(self.root,{'project_title':'Project1 — previous session 27 Oct 2026 20:59','cloud':{'file':a}},'Project1 — previous session 27 Oct 2026 20:59',True)
        self.assertEqual(projects.listing(self.root),[])
        self.assertFalse((self.root/'projects'/(p['project_id']+'.json')).exists())
        self.assertTrue(any((self.root/'diagnostics'/'retired_previous_sessions').glob(p['project_id']+'_*')))
        self.assertEqual(projects.orphan_storage_review(self.root,{})['bytes'],31)
    def test_nested_previous_session_label_is_retired(self):
        title='Project1 — previous session 27 Oct 2026 20:59 — previous session 27 Oct 2026 22:19'
        p=projects.save_named(self.root,{'project_title':title},title,True)
        self.assertEqual(projects.listing(self.root),[])
        self.assertFalse((self.root/'projects'/(p['project_id']+'.json')).exists())
    def test_only_newest_hidden_recovery_protects_assets(self):
        old=self.asset('b'*32+'.xyz64',17);new=self.asset('c'*32+'.xyz64',19)
        rec=self.root/'startup_recovery';rec.mkdir()
        projects.atomic_json(rec/'workspace_old.json',{'cloud':{'file':old}});time.sleep(.01)
        projects.atomic_json(rec/'workspace_new.json',{'cloud':{'file':new}})
        protected=projects._current_and_recovery_assets(self.root,{})
        self.assertNotIn(Path(old).resolve(),protected);self.assertIn(Path(new).resolve(),protected)
    def test_workspace_storage_covers_mixed_import_formats(self):
        rows=[]
        formats=['e57','las','laz','ply','pts','xyz','txt','csv','hspca','rtc360_raw_experimental','obj']
        state={'job_items':[]}
        for i,fmt in enumerate(formats):
            cache=self.asset(f'{i:032x}.xyz64',13+i)
            meta={'name':'scan.'+('obj' if fmt=='obj' else fmt),'file':cache,'count':1,'source_format':fmt,'source_name':'source.'+fmt}
            if fmt=='obj':meta={'name':'mesh.obj','vertices_file':cache,'faces_file':self.asset(f'{100+i:032x}.faces32',5),'vertices':1,'triangles':1,'source_format':'obj','source_name':'mesh.obj'}
            state['job_items'].append({'id':f'id{i}','kind':'mesh' if fmt=='obj' else 'scan','name':meta['name'],'meta':meta,'source_bytes':100+i})
        out=projects.workspace_storage_overview(self.root,state)
        self.assertEqual({r['source_format'] for r in out['items']},set(formats))
        self.assertEqual(len(out['items']),len(formats))
        self.assertGreater(out['cache_bytes'],0)
    def test_workspace_storage_does_not_treat_external_sources_as_deletable_assets(self):
        external=Path(self.t.name).parent/'external_source_test.e57';external.write_bytes(b'z'*7)
        try:
            cache=self.asset('d'*32+'.xyz64',23)
            state={'job_items':[{'id':'x','kind':'scan','name':'external.e57','source_bytes':7,'meta':{'name':'external.e57','source_name':external.name,'source_path':str(external),'source_format':'e57','file':cache,'count':1}}]}
            out=projects.workspace_storage_overview(self.root,state)
            self.assertEqual(out['items'][0]['cache_bytes'],23)
            self.assertNotIn(external.resolve(),projects._local_asset_paths(self.root,state))
        finally:external.unlink(missing_ok=True)

if __name__=='__main__':unittest.main()
