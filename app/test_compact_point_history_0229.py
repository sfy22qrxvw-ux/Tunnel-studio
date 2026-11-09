"""Point edit history remains replayable after upgrading and relocating a project."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import deleted_points
import jobdata
import projects
import scan_edits
import workflows


class CompactPointHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        points = np.c_[np.arange(30), np.zeros((30, 2))].astype('<f8')
        path = self.work / 'scan.xyz64'
        points.tofile(path)
        meta = dict(name='Scan', file=str(path), count=len(points),
                    minimum=points.min(0).tolist(), maximum=points.max(0).tolist())
        self.item = dict(id='scan', name='Scan', kind='scan', meta=meta,
                         deleted_groups=[], edit_history=[], redo_history=[])
        self.s = SimpleNamespace(WORK=self.work, STATE=workflows.empty(), SELECTION=None,
                                 JOB={'busy': False}, progress=lambda _: None)
        self.s.STATE['job_items'] = [self.item]
        self.s.save = lambda: projects.atomic_json(self.work / 'project.json', self.s.STATE)

    @staticmethod
    def box(lo, hi):
        return dict(mode='box', minimum=[lo, -1, -1], maximum=[hi, 1, 1])

    def reopen(self, data):
        self.s.STATE = workflows.prepare_data(self.s, data)
        self.item = self.s.STATE['job_items'][0]

    def edit(self, action, selection=None):
        scan_edits.apply(self.s, self.item, action, selection)

    def mask(self):
        path = self.item['meta'].get('mask_file')
        return np.flatnonzero(np.fromfile(path, 'u1')).tolist() if path else []

    def test_legacy_undo_and_redo_upgrade_with_partial_restore(self):
        self.edit('delete_selection', self.box(2, 10))
        first = copy.deepcopy(self.item['deleted_groups'])
        deleted_points.restore_selection(self.s, [['scan', first[0]['id']]], self.box(4, 6))
        restored = copy.deepcopy(self.item['deleted_groups'])
        self.edit('undo_edit')
        deletion = self.item['edit_history'][0]
        restoration = self.item['redo_history'][0]
        for entry, before, after in ((deletion, [], first), (restoration, first, restored)):
            entry.pop('group_change')
            entry.update(before_groups=before, after_groups=after)
        saved = projects.save_named(self.work, self.s.STATE, 'Legacy', True)
        self.reopen(projects.read_named(self.work, saved['project_id']))
        for entry in (*self.item['edit_history'], *self.item['redo_history']):
            self.assertEqual(entry['group_change']['version'], 1)
            self.assertNotIn('before_groups', entry)
        self.assertEqual(self.mask(), list(range(2, 11)))
        self.edit('undo_edit')
        self.assertEqual(self.mask(), [])
        self.assertEqual(self.item['deleted_groups'], [])
        self.edit('redo_edit')
        self.assertEqual(self.item['deleted_groups'], first)
        self.edit('redo_edit')
        self.assertEqual(self.item['deleted_groups'], restored)
        self.assertEqual(self.mask(), [2, 3, 7, 8, 9, 10])

    def test_portable_copy_keeps_history_only_remainder_and_separation(self):
        self.edit('delete_selection', self.box(2, 10))
        group = self.item['deleted_groups'][0]
        deleted_points.restore_selection(self.s, [['scan', group['id']]], self.box(4, 6))
        remainder = Path(group['remaining']['ids_file'])
        self.edit('undo_edit')
        self.assertNotIn('remaining', self.item['deleted_groups'][0])
        self.assertIn(remainder, projects._local_asset_paths(self.work, self.s.STATE, owned_only=True))
        dest = self.work / 'copy.tstudio'
        projects.portable(self.work, self.s.STATE, dest)
        self.reopen(projects.unpack(self.work, dest))
        self.edit('redo_edit')
        copied = Path(self.item['deleted_groups'][0]['remaining']['ids_file'])
        self.assertNotEqual(remainder, copied)
        self.assertEqual(remainder.read_bytes(), copied.read_bytes())
        self.edit('separate_selection', self.box(18, 22))
        child = self.s.STATE['job_items'][-1]['id']
        self.edit('undo_edit')
        self.assertEqual(len(self.s.STATE['job_items']), 1)
        self.reopen(projects.read_json(self.work / 'project.json'))
        self.edit('redo_edit')
        self.assertEqual(self.s.STATE['job_items'][-1]['id'], child)
        self.assertEqual(self.mask(), [2, 3, 7, 8, 9, 10, 18, 19, 20, 21, 22])

    def test_unrelated_archive_and_transaction_rollback(self):
        self.edit('delete_selection', self.box(2, 10))
        archive = dict(id='archive', kind='delete', active=False, archive={'result_revision': 'clean'})
        self.item['deleted_groups'].append(archive)
        before = copy.deepcopy(self.s.STATE)
        saved_bytes = (self.work / 'project.json').read_bytes()
        with patch.object(self.s, 'save', side_effect=OSError('Disk write failed')):
            with self.assertRaisesRegex(OSError, 'Disk write failed'):
                self.edit('undo_edit')
        self.assertEqual(self.s.STATE, before)
        self.assertEqual((self.work / 'project.json').read_bytes(), saved_bytes)
        self.item = self.s.STATE['job_items'][0]
        self.edit('undo_edit')
        self.assertEqual(self.item['deleted_groups'], [archive])
        self.edit('redo_edit')
        self.assertEqual(self.item['deleted_groups'][1], archive)
        before_cancel = copy.deepcopy(self.s.STATE)
        with patch.object(self.s, 'progress', side_effect=jobdata.Cancelled('Stop')):
            with self.assertRaises(jobdata.Cancelled):
                self.edit('undo_edit')
        self.assertEqual(self.s.STATE, before_cancel)

    def test_multi_group_legacy_snapshot_is_preserved(self):
        first = dict(id='first', kind='delete', active=True)
        second = dict(id='second', kind='delete', active=True)
        legacy = dict(count=-1, group_id='second', before_groups=[first, second],
                      after_groups=[dict(first, active=False), dict(second, active=False)])
        self.assertIs(scan_edits.compact_history_entry(legacy), legacy)

    def test_missing_history_remainder_is_quarantined(self):
        self.edit('delete_selection', self.box(2, 10))
        group = self.item['deleted_groups'][0]
        deleted_points.restore_selection(self.s, [['scan', group['id']]], self.box(4, 6))
        remainder = Path(group['remaining']['ids_file'])
        self.edit('undo_edit')
        remainder.unlink()
        self.reopen(projects.read_json(self.work / 'project.json'))
        self.assertEqual(self.mask(), list(range(2, 11)))
        self.assertEqual(self.item['redo_history'], [])
        self.assertTrue(self.s.STATE['project_recovery']['warnings'])


if __name__ == '__main__':
    unittest.main()
