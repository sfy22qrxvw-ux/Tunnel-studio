"""Fast deletion summaries survive active aliases without stale exact counts."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import job_api
import scan_edits


class DeleteAlias0187Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        points = np.arange(18, dtype='<f8').reshape(6, 3)
        points.tofile(self.root / 'scan.xyz64')
        np.ones((6, 4), dtype='<f4').tofile(self.root / 'scan.attr32')
        np.array([0, 1, 0, 0, 0, 0], dtype='u1').tofile(self.root / 'scan.mask')
        self.meta = dict(file=str(self.root / 'scan.xyz64'),
                         attributes_file=str(self.root / 'scan.attr32'),
                         mask_file=str(self.root / 'scan.mask'), count=6,
                         minimum=points.min(0).tolist(), maximum=points.max(0).tolist(),
                         attribute_counts=[6] * 4)
        self.meta['retained_summary'] = dict(
            revision=self.meta['mask_file'], count=5,
            source_signature=scan_edits._properties_signature(self.meta))
        self.item = dict(id='scan', kind='scan', name='Scan', meta=self.meta)
        self.cloud = copy.deepcopy(self.meta)
        self.server = SimpleNamespace(STATE=dict(
            job_items=[self.item], active_job_scan='scan', cloud=self.cloud,
            edits=[], redo=[]))

    def test_stale_active_alias_cannot_erase_exact_count_only_summary(self):
        self.cloud['retained_summary']['revision'] = 'obsolete.mask'
        expected = copy.deepcopy(self.meta['retained_summary'])
        with patch.object(scan_edits, 'properties', side_effect=AssertionError('Unexpected XYZ statistics')):
            job_api.sync_active(self.server)
            display = scan_edits.display_properties(self.meta)
        self.assertEqual(self.meta['retained_summary'], expected)
        self.assertEqual(self.cloud['retained_summary'], expected)
        self.assertEqual(display['count'], 5)
        self.assertTrue(display['count_exact'])
        self.assertNotIn('retained_properties', self.meta)

    def test_current_active_summary_is_retained_when_item_copy_is_missing(self):
        self.meta.pop('retained_summary')
        expected = copy.deepcopy(self.cloud['retained_summary'])
        job_api.sync_active(self.server)
        self.assertEqual(self.meta['retained_summary'], expected)
        self.assertEqual(scan_edits.retained_count(self.meta), 5)

    def test_removing_active_mask_drops_the_old_excluded_count(self):
        self.cloud.pop('mask_file')
        self.cloud.pop('retained_summary')
        job_api.sync_active(self.server)
        self.assertIsNone(self.meta.get('mask_file'))
        self.assertNotIn('retained_summary', self.meta)
        self.assertEqual(scan_edits.display_properties(self.meta)['count'], 6)

    def test_same_size_mask_replacement_cannot_keep_old_summary(self):
        path = Path(self.meta['mask_file'])
        replacement = self.root / 'replacement.mask'
        np.array([0, 1, 1, 1, 0, 0], dtype='u1').tofile(replacement)
        replacement.replace(path)
        job_api.sync_active(self.server)
        self.assertNotIn('retained_summary', self.meta)
        self.assertNotIn('retained_summary', self.cloud)
        self.assertEqual(scan_edits.retained_count(self.meta), 3)


if __name__ == '__main__':
    unittest.main()
