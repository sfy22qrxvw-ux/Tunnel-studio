import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import detail_preview as detail
import priority_view_cache as cache
import preview_store
import registration_pose


class ExactViewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); rng = np.random.default_rng(22)
        q = rng.uniform(-5, 5, (40000, 3)); q.astype('<f8').tofile(root/'xyz')
        a = rng.uniform(0, 1, (len(q), 4)).astype('<f4'); a.tofile(root/'attrs')
        flags = np.zeros(len(q), 'u1'); flags[::9] = 1; flags.tofile(root/'mask')
        self.meta = dict(file=str(root/'xyz'), attributes_file=str(root/'attrs'), mask_file=str(root/'mask'), count=len(q), minimum=q.min(0).tolist(), maximum=q.max(0).tolist())
        self.item = dict(id='t', kind='scan', meta=self.meta)
        self.view = dict(target=[0,0,0], right=[1,0,0], up=[0,1,0], forward=[0,0,1], scale=3, aspect=1)
        self.root = root

    def run_sample(self, accelerated):
        ids = []
        with patch.object(preview_store, 'read', return_value=None), patch.object(preview_store, 'write'):
            if accelerated:
                result = cache.sample(self.item, 100, np.zeros(3), [.2,.3,.4], 'rgb', None, self.view, decorate=lambda i,p:ids.append(i.copy()))
            else:
                with patch.object(cache, 'info', return_value=None):
                    result = detail.sample(self.item, 100, np.zeros(3), [.2,.3,.4], 'rgb', None, self.view, decorate=lambda i,p:ids.append(i.copy()))
        return ids[0], result

    def test_exact_identity_pose_edit_and_visibility(self):
        cache.build(self.meta, target=10000)
        for transform in (False, True):
            if transform:
                registration_pose.set_pose(self.meta, [[0,-1,0],[1,0,0],[0,0,1]], [1,2,3])
                self.view['target'] = [1,2,3]
            for selection in ({}, {'hidden_boxes':[dict(mode='box', minimum=[-1,-1,-9], maximum=[1,1,9])]}, {'isolate_box':dict(mode='box', minimum=[-2,-2,-9], maximum=[2,2,9])}):
                self.item.pop('hidden_boxes',None); self.item.pop('isolate_box',None); self.item.update(selection)
                expected = self.run_sample(False); actual = self.run_sample(True)
                np.testing.assert_array_equal(actual[0], expected[0]); np.testing.assert_array_equal(actual[1], expected[1])
        flags = np.zeros(self.meta['count'], 'u1'); flags[::2] = 1; flags.tofile(self.root/'mask')
        expected = self.run_sample(False); actual = self.run_sample(True)
        np.testing.assert_array_equal(actual[0], expected[0]); np.testing.assert_array_equal(actual[1], expected[1])

    def test_insufficient_falls_back_and_stale_source_rejected(self):
        cache.build(self.meta, target=10)
        self.assertIsNone(cache.sample(self.item,100,[0,0,0],[1,1,1],'bundle',None,self.view))
        expected = self.run_sample(False)
        ids=[]
        with patch.object(preview_store,'read',return_value=None),patch.object(preview_store,'write'):
            actual=detail.sample(self.item,100,[0,0,0],[.2,.3,.4],'rgb',None,self.view,decorate=lambda i,p:ids.append(i.copy()))
        np.testing.assert_array_equal(ids[0],expected[0]);np.testing.assert_array_equal(actual,expected[1])
        with (self.root/'attrs').open('ab') as f:f.write(b'changed')
        self.assertIsNone(cache.info(self.meta))

    def test_second_level_preserves_exact_rows_and_colours(self):
        cache.build(self.meta, target=300)
        self.view['scale'] = 2
        rows = np.fromfile(cache.paths(self.meta)[0], cache.ROW)
        self.assertLess(int(detail._view_mask(rows['xyz'],self.view,detail.validate_view(self.view)).sum()),100)
        expected=self.run_sample(False);actual=self.run_sample(True)
        np.testing.assert_array_equal(actual[0],expected[0]);np.testing.assert_array_equal(actual[1],expected[1])

    def test_cancel_and_truncated_cache(self):
        with self.assertRaises(InterruptedError):cache.build(self.meta,cancel=lambda:True)
        self.assertFalse(list((self.root/'display_cache').glob('*.tmp')))
        cache.build(self.meta,target=10000)
        with self.assertRaises(InterruptedError):cache.sample(self.item,100,[0,0,0],[1,1,1],'bundle',None,self.view,cancel=lambda:True)
        cache.paths(self.meta)[0].write_bytes(b'broken')
        self.assertIsNone(cache.info(self.meta))

    def test_cache_files_remain_owned_for_project_cleanup(self):
        import explorer_deletion
        cache.build(self.meta,target=300)
        binary,manifest=cache.paths(self.meta)
        files={binary,manifest,binary.with_suffix('.extra.bin')}
        self.assertEqual(explorer_deletion._owned(self.root,files,set()),files)
        for path in files:
            self.assertTrue(Path(str(path)+'.owner.json').is_file())


if __name__ == '__main__':unittest.main()
