# Copyright (c) 2026 Damian Gulczynski.
"""Current installer activation, integrity and rollback; no Windows DLLs executed."""
from pathlib import Path
import hashlib,importlib.util,json,tempfile,unittest
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
installer_path=root/'build_support/windows_installer.py'
if not installer_path.is_file():installer_path=root/'packaging/windows_installer.py'
spec=importlib.util.spec_from_file_location('current_installer',installer_path)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class UpdateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.base=self.root/'releases';self.base.mkdir();self.stage=self.base/'.installing-test';self.stage.mkdir();self.data=self.root/'data';self.data.mkdir();(self.data/'project.json').write_text('{"keep":"survey"}')
  self.target=self.base/installer.VERSION;self.target.mkdir();(self.target/'old').write_text('Previous application')
  required=['TunnelStudio.exe','runtime/python.exe','runtime/pythonw.exe','app/desktop.py','app/release.py'];files={}
  for name in required:
   p=self.stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode());files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
  self.manifest=dict(version=installer.VERSION,files=files);self.write_manifest()
 def write_manifest(self):(self.stage/'FILES_SHA256.json').write_text(json.dumps(self.manifest))
 def tearDown(self):self.temp.cleanup()
 def test_activation_keeps_previous_and_survey(self):
  result=installer.activate(self.stage,self.base,self.data,smoke=lambda _: 'fixture component check')
  self.assertFalse(result['survey_data_changed']);self.assertTrue((self.target/'app/desktop.py').is_file());self.assertEqual((Path(result['previous_same_version'])/'old').read_text(),'Previous application')
  self.assertEqual((self.data/'project.json').read_text(),'{"keep":"survey"}');self.assertEqual(len(list((self.data/'project_indexes').glob('*.json'))),1)
 def test_corrupt_payload_stops_before_activation(self):
  (self.stage/'app/desktop.py').write_text('corrupt')
  with self.assertRaisesRegex(RuntimeError,'verification'):installer.activate(self.stage,self.base,self.data,smoke=lambda _:None)
  self.assertTrue((self.target/'old').exists());self.assertTrue(self.stage.exists())
 def test_failed_pointer_write_rolls_back_previous(self):
  with patch.object(installer,'atomic_json',side_effect=OSError('simulated disk failure')):
   with self.assertRaisesRegex(OSError,'simulated'):installer.activate(self.stage,self.base,self.data,smoke=lambda _:None)
  self.assertTrue((self.target/'old').exists());self.assertTrue(self.stage.exists());self.assertEqual((self.data/'project.json').read_text(),'{"keep":"survey"}')
 def test_failed_components_stop_before_activation(self):
  def fail(_):raise RuntimeError('component failed')
  with self.assertRaisesRegex(RuntimeError,'component failed'):installer.activate(self.stage,self.base,self.data,smoke=fail)
  self.assertTrue((self.target/'old').exists())
 def test_reject_path_escape_and_wrong_version(self):
  self.manifest['files']['../escape']='bad';self.write_manifest()
  with self.assertRaisesRegex(RuntimeError,'payload path'):installer.verify(self.stage)
  self.manifest['version']='wrong';self.write_manifest()
  with self.assertRaisesRegex(RuntimeError,'wrong version'):installer.verify(self.stage)

if __name__=='__main__':unittest.main()
