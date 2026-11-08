"""File-level verification and exclusive publication on a disposable workspace."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import project_folder_copy as copier
import projects
import workflows
import workspace_folders
import startup_support


class OrganisedCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.work=root/'shared'
        self.drive=root/'drive'
        self.work.mkdir();self.drive.mkdir()
        self.addCleanup(patch.stopall)
        patch('workspace_folders.os.environ',dict(TUNNEL_STUDIO_DATA=str(self.work))).start()
        self.state=workflows.empty()
        p=self.work/'bore.xyz64';np.zeros((3,3),dtype='<f8').tofile(p)
        meta=dict(name='Registered cloud',count=3,file=str(p),minimum=[0,0,0],maximum=[0,0,0],source_format='registered_merge')
        self.state['job_items']=[dict(id='scan1',name='Registered cloud',kind='scan',registered_merge=True,meta=meta)]
        self.state=projects.save_named(self.work,self.state,'Live project')
        self.source=p
        self.s=SimpleNamespace(WORK=self.work,WORKSPACE_CATALOGUE=self.work,STATE=self.state,JOB={'busy':False},progress=lambda _:None)
        self.s.start_job=lambda fn,*a,**k:fn()
        self.input=dict(name='North drive',parent=str(self.drive))
    def run_copy(self):
        review=copier.request(self.s,dict(action='review',**self.input))
        copier.request(self.s,dict(action='copy',token=review['token'],**self.input))
        return review

    def test_verified_registered_cloud_copied_and_new_snapshot_opens_independently(self):
        original=self.source.read_bytes()
        review=self.run_copy()
        root=Path(review['destination']);receipt=self.s.PROJECT_FOLDER_COPY
        data=projects.read_named(root,receipt['project_id'])
        path=Path(data['job_items'][0]['meta']['file'])
        self.assertEqual(path.relative_to(root).parts,('Clouds','Registered','bore.xyz64'))
        self.assertEqual(original,path.read_bytes());self.assertEqual(original,self.source.read_bytes())
        self.assertNotEqual(receipt['project_id'],self.s.STATE['project_id'])
        self.assertTrue(workspace_folders.managed_root(root))
        self.source.unlink()
        restored=workflows.prepare_data(SimpleNamespace(WORK=root,progress=lambda _:None),data)
        self.assertEqual(restored['job_items'][0]['meta']['count'],3)

    def test_stale_review_cancel_and_destination_collision_preserve_original(self):
        review=copier.request(self.s,dict(action='review',**self.input))
        self.state['project_title']='Other'
        with self.assertRaisesRegex(ValueError,'changed'):
            copier.request(self.s,dict(action='copy',token=review['token'],**self.input))
        def cancel(m):
            if 'Verifying' in m:raise RuntimeError('Cancelled')
        self.s.progress=cancel
        with self.assertRaisesRegex(RuntimeError,'Cancelled'):self.run_copy()
        self.assertFalse(list(self.drive.iterdir()))
        target=self.drive/self.input['name']
        def race(m):
            if 'Publishing' in m:target.mkdir()
        self.s.progress=race
        with self.assertRaises(OSError):self.run_copy()
        self.assertTrue(target.is_dir());self.assertFalse(list(target.iterdir()))
        self.assertTrue(self.source.exists())

    def test_surviving_held_assets_are_copied(self):
        heldfile=self.work/'prior.attr32';heldfile.write_bytes(b'attributes')
        missing=self.work/'unavailable.xyz64'
        held=dict(kind='scan',name='held',reason='missing XYZ',
                  original_data_json=json.dumps(dict(file=str(missing),attributes_file=str(heldfile))))
        self.state['project_recovery']=dict(warnings=['previous notice'],unavailable=[held])
        r=self.run_copy()
        root=Path(r['destination']);data=projects.read_named(root,self.s.PROJECT_FOLDER_COPY['project_id'])
        record=json.loads(data['project_recovery']['unavailable'][0]['original_data_json'])
        self.assertTrue(Path(record['attributes_file']).is_relative_to(root))
        self.assertEqual(Path(record['attributes_file']).read_bytes(),heldfile.read_bytes())

    def test_folder_command_accepts_only_the_server_root(self):
        response=[]
        handler=SimpleNamespace(send=response.append)
        with patch.object(startup_support.os,'startfile',create=True) as explorer:
            self.assertTrue(workflows.post(self.s,handler,'/api/open-data-folder',{}))
            explorer.assert_called_once_with(str(self.work),'explore')
            self.assertEqual(response,[{'opened':True}])
            with self.assertRaises(ValueError):
                workflows.post(self.s,handler,'/api/open-data-folder',{'path':str(self.drive)})
            explorer.assert_called_once()

if __name__=='__main__':unittest.main()
