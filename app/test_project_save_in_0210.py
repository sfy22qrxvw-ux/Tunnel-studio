import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import projects
import workflows


class ProjectSaveInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'working data'
        self.work.mkdir()
        self.dest = self.root / 'Survey copy.tstudio'
        self.asset = self.work / 'segment.xyz64'
        self.asset.write_bytes(bytes(24 * 20))
        state = workflows.empty()
        state['cloud'] = {'name': 'Survey', 'count': 20, 'file': str(self.asset)}
        self.s = SimpleNamespace(WORK=self.work, STATE=state, JOB={'busy': False},
                                 progress=lambda _: None, start_job=lambda work, kind: work())

    def request(self, **options):
        return workflows.save_portable_in(self.s, dict(destination=str(self.dest), name='Lining', **options))

    def test_portable_round_trip_on_destination_drive_and_remember_folder(self):
        original = copy.deepcopy(self.s.STATE)
        result = self.request()
        self.assertEqual(self.s.LAST_PROJECT_COPY['id'], result['id'])
        self.assertEqual(result['destination'], str(self.dest))
        self.assertFalse(list(self.work.glob('*.tstudio*')))
        self.assertEqual(self.s.STATE, original)
        self.assertEqual(json.loads((self.work/'project_save_preferences.json').read_text())['last_folder'], str(self.root))
        with zipfile.ZipFile(self.dest) as archive:
            self.assertIsNone(archive.testzip())
            data = json.loads(archive.read('project.json'))['state']
            self.assertEqual(data['project_title'], 'Lining')
            self.assertEqual(archive.read(data['cloud']['file']), self.asset.read_bytes())
        reopened = projects.unpack(self.work, self.dest)
        self.assertEqual(Path(reopened['cloud']['file']).read_bytes(), self.asset.read_bytes())

    def test_actual_locations_and_fast_named_snapshot(self):
        self.assertIsNone(workflows.project_location(self.s)['project_file'])
        saved = projects.save_named(self.work, self.s.STATE, 'Named project')
        self.s.STATE.update(saved)
        info = workflows.project_location(self.s)
        self.assertEqual(info['work_folder'], str(self.work))
        self.assertEqual(info['project_file'], str(self.work / 'projects' / (saved['project_id']+'.json')))
        self.assertGreater(info['free_bytes'], 0)
        self.assertFalse(self.dest.exists())

    def test_conflict_reconfirmation_and_protected_data_root(self):
        self.dest.write_bytes(b'prior project')
        review = self.request()
        self.assertTrue(review['needs_overwrite'])
        self.assertEqual(self.dest.read_bytes(), b'prior project')
        self.dest.write_bytes(b'second version')
        rereview = self.request(replace_version=review['replace_version'])
        self.assertNotEqual(review['replace_version'], rereview['replace_version'])
        self.assertTrue(self.request(replace_version=rereview['replace_version'])['started'])
        with self.assertRaisesRegex(ValueError, 'outside the internal'):
            workflows.save_portable_in(self.s, {'destination': str(self.work/'internal.tstudio')})

    def test_destination_changed_or_appeared_during_write_is_preserved(self):
        def race(message):
            if message.startswith('Finishing portable'):
                self.dest.write_bytes(b'external change')
        self.s.progress = race
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            self.request()
        self.assertEqual(self.dest.read_bytes(), b'external change')
        self.assertFalse(list(self.root.glob('*.tmp_*')))

    def test_cancel_disk_full_and_missing_asset_preserve_copy(self):
        self.dest.write_bytes(b'earlier save')
        token = self.request()['replace_version']
        def cancel(message):
            if 'Packaging project asset' in message:
                raise RuntimeError('Cancelled')
        self.s.progress = cancel
        with self.assertRaisesRegex(RuntimeError, 'Cancelled'):
            self.request(replace_version=token)
        self.s.progress = lambda _: None
        with patch.object(projects.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, 'destination drive'):
                self.request(replace_version=token)
        self.asset.unlink()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.request(replace_version=token)
        self.assertEqual(self.dest.read_bytes(), b'earlier save')
        self.assertFalse(list(self.root.glob('*.tmp_*')))
