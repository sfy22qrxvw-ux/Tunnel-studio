import unittest
from pathlib import Path
class StorageUI01712Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  root=Path(__file__).resolve().parent/'web';cls.ribbon=(root/'ribbon.js').read_text();cls.layout=(root/'ribbon-layout.js').read_text();cls.index=(root/'index.html').read_text()
 def test_storage_manager_is_direct_home_command(self):
  self.assertIn("'Storage manager'",self.layout);self.assertIn("projectDialog('storage')",self.layout)
 def test_storage_mode_uses_same_project_storage_panel(self):
  self.assertIn("storage:'Storage manager'",self.ribbon);self.assertIn("['open','storage'].includes(mode)",self.ribbon)
 def test_current_imported_data_storage_is_visible(self):
  self.assertIn('workspace-storage-overview',self.index);self.assertIn('/api/workspace-storage',self.ribbon);self.assertIn('Current imported data',self.ribbon)
 def test_previous_session_wording_removed_from_open_command(self):
  self.assertNotIn('Open a saved project or the previous session.',self.layout)
if __name__=='__main__':unittest.main()
