"""Process-scoped workspace selection and protection checks using isolated folders."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import workspace_folders as wf


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.catalogue=self.home/'Shared';self.catalogue.mkdir()
        self.parent=self.home/'My surveys';self.parent.mkdir()
        self.s=SimpleNamespace(WORK=self.catalogue,WORKSPACE_CATALOGUE=self.catalogue,JOB={})
        self.app=Path(wf.__file__).parent

    def create(self,name):
        return wf.request(self.s,dict(action='create',parent=str(self.parent),name=name))

    def startup(self):
        with patch.dict(os.environ,dict(TUNNEL_STUDIO_DATA=str(self.catalogue),TUNNEL_STUDIO_SESSION_ROOT='')):
            return wf.startup()

    def test_select_requires_restart_and_never_changes_original_workspace(self):
        original=self.catalogue/'previous.xyz64';original.write_bytes(b'shared')
        info=self.create('Heading A')
        folder=self.parent/'Heading A'
        self.assertEqual(info['current_root'],str(self.catalogue))
        self.assertTrue(wf.managed_root(folder))
        cfg=Path(info['configuration_file'])
        self.assertTrue(cfg.is_file())
        self.assertFalse(cfg.is_relative_to(self.catalogue) or cfg.is_relative_to(folder))
        self.assertFalse((self.catalogue/wf.CATALOGUE).exists())
        self.assertTrue((folder/'Clouds'/'Imported').is_dir())
        self.assertEqual(self.startup(),(self.catalogue,folder,''))
        self.assertEqual(wf.request(self.s,dict(action='select',id='shared'))['next_id'],'shared')
        self.assertEqual(self.startup(),(self.catalogue,self.catalogue,''))
        self.assertEqual(original.read_bytes(),b'shared')

    def test_server_uses_same_root_for_inputs_helpers_and_outputs_on_fresh_process(self):
        self.create('Second tunnel')
        code="""import json,server,os,startup_support
server.start_empty()
print(json.dumps(dict(work=str(server.WORK),cat=str(server.WORKSPACE_CATALOGUE),helper=str(startup_support.data_folder()),child=os.environ['TUNNEL_STUDIO_DATA'])))"""
        env=dict(os.environ,TUNNEL_STUDIO_DATA=str(self.catalogue),TUNNEL_STUDIO_SESSION_ROOT='')
        raw=subprocess.check_output([sys.executable,'-B','-c',code],cwd=self.app,env=env,text=True)
        status=json.loads(raw.strip().splitlines()[-1]);dest=self.parent/'Second tunnel'
        self.assertEqual(set(status.values()),{str(dest),str(self.catalogue)})
        self.assertTrue((dest/'project.json').is_file())
        self.assertFalse((self.catalogue/'project.json').exists())

    def test_unavailable_or_wrong_drive_does_not_recreate_or_reuse_folder(self):
        self.create('Ring 78')
        root=self.parent/'Ring 78';moved=self.parent/'Drive removed';root.rename(moved)
        _,location,warning=self.startup();self.assertEqual(location,self.catalogue);self.assertTrue(warning)
        self.assertFalse(root.exists())
        wf.initialize_root(root,'Different project')
        _,location,warning=self.startup();self.assertEqual(location,self.catalogue);self.assertTrue(warning)
        self.assertFalse((root/'project.json').exists())

    def test_reject_preexisting_nested_reserved_and_installation_paths(self):
        self.create('A')
        for parent,name in ((self.parent,'A'),(self.parent/'A','B'),(self.parent,'../escape'),
                            (self.parent,'PRN.csv'),(self.catalogue,'Other'),(self.app,'userfiles')):
            with self.subTest(parent=parent,name=name),self.assertRaises(ValueError):
                wf.new_path(self.catalogue,parent,name)
        link=self.home/'Linked'
        try:link.symlink_to(self.app,target_is_directory=True)
        except OSError:return
        with self.assertRaises(ValueError):wf.new_path(self.catalogue,link,'Project')

    def test_partial_create_or_registry_error_preserves_active_choice(self):
        self.create('A')
        previous=(wf.settings_folder(self.catalogue)/wf.CATALOGUE).read_bytes()
        with patch.object(wf,'_save',side_effect=OSError('low disk')),self.assertRaises(OSError):
            self.create('B')
        self.assertFalse((self.parent/'B').exists())
        self.assertEqual((wf.settings_folder(self.catalogue)/wf.CATALOGUE).read_bytes(),previous)
        self.s.JOB['busy']=True
        with self.assertRaisesRegex(ValueError,'job'):self.create('C')

    def test_explicit_isolation_beats_inherited_process_profile(self):
        self.create('A')
        temp_instance=self.home/'Smoke workspace';temp_instance.mkdir()
        env=dict(TUNNEL_STUDIO_DATA=str(temp_instance),TUNNEL_STUDIO_PROFILE_ROOT=str(self.catalogue),
                 TUNNEL_STUDIO_SESSION_ROOT=str(self.parent/'A'))
        with patch.dict(os.environ,env):
            self.assertEqual(wf.startup(),(temp_instance,temp_instance,''))

if __name__=='__main__':unittest.main()
