import unittest
from pathlib import Path

class ProjectStorageUI01711Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parent/'web'
        cls.js=(root/'ribbon.js').read_text(encoding='utf8')
        cls.css=(root/'ribbon.css').read_text(encoding='utf8')
        cls.browser=(root/'project-browser-ui.js').read_text(encoding='utf8')
    def test_project_rows_show_storage_sizes(self):
        self.assertIn('projectSizeSummary',self.js)
        self.assertIn('Uses ${total} · frees ${free}',self.js)
        self.assertIn('project-size',self.browser)
    def test_bulk_select_and_delete_are_present(self):
        for token in ('project-select-all','project-clear-selection','project-delete-selected','Delete selected…'):
            self.assertIn(token,self.js)
    def test_batch_delete_uses_ids_and_background_message(self):
        self.assertIn("action:'delete_projects'",self.js)
        self.assertIn('releasing ${storageSize(freeing)} in background',self.js)
        self.assertIn('project-purge-status',self.js)
    def test_checkbox_and_bulk_layout_have_css(self):
        self.assertIn('.project-select',self.css)
        self.assertIn('.project-bulk-toolbar',self.css)
        self.assertIn('.project-purge-status',self.css)

if __name__=='__main__':unittest.main()
