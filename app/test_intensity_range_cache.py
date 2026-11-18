import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import jobdata

class IntensityRangeTests(unittest.TestCase):
 def test_exact_restart_reuse_invalid_file_and_source_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'a';values=np.random.default_rng(8).normal(size=(1000,4)).astype('<f4');values.tofile(p)
   meta=dict(attributes_file=str(p),count=len(values));expected=tuple(np.percentile(values[:,0],[2,98]))
   jobdata._intensity_window_cached.cache_clear();self.assertEqual(jobdata.intensity_window(meta),expected)
   jobdata._intensity_window_cached.cache_clear()
   with patch.object(jobdata,'_display_rows',side_effect=AssertionError('Must reuse exact disk result')):self.assertEqual(jobdata.intensity_window(meta),expected)
   cache=next((p.parent/'display_cache').glob('intensity-range-*.json'));cache.write_text('{broken')
   jobdata._intensity_window_cached.cache_clear();self.assertEqual(jobdata.intensity_window(meta),expected)
   values[:,0]+=4;values.tofile(p);jobdata._intensity_window_cached.cache_clear()
   self.assertEqual(jobdata.intensity_window(meta),tuple(np.percentile(values[:,0],[2,98])))
 def test_cancelled_cold_range_is_not_published(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'a';np.zeros((50,4),'<f4').tofile(p)
   jobdata._intensity_window_cached.cache_clear()
   with self.assertRaises(InterruptedError):jobdata.intensity_window(dict(attributes_file=str(p),count=50),cancel=lambda:True)
   self.assertFalse(list(p.parent.glob('display_cache/*.json')))

if __name__=='__main__':unittest.main()
