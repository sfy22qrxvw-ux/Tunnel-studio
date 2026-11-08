import shutil
import subprocess
import unittest
from pathlib import Path


class ProgressivePreviewTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is required for the browser-controller harness')
    def test_controller_retries_partial_preview_and_discards_stale_response(self):
        result=subprocess.run([shutil.which('node'),str(Path(__file__).with_suffix('.js'))],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
