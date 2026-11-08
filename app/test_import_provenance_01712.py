import tempfile,unittest
from pathlib import Path
import pointio,jobdata

class ImportProvenance01712Tests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
 def tearDown(self):self.t.cleanup()
 def test_xyz_csv_pts_store_source_identity(self):
  cases=[('a.xyz','1 2 3\n4 5 6\n'),('b.csv','E,N,H\n1,2,3\n'),('c.pts','1\n1 2 3\n')]
  for i,(name,text) in enumerate(cases):
   src=self.root/name;src.write_text(text);dest=self.root/f'{i}.xyz64'
   meta=pointio.import_cloud(src,dest)
   self.assertEqual(meta['source_name'],name);self.assertEqual(meta['source_format'],src.suffix[1:]);self.assertEqual(Path(meta['source_path']),src.resolve());self.assertEqual(meta['source_bytes'],src.stat().st_size)
 def test_obj_stores_source_identity(self):
  src=self.root/'mesh.obj';src.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
  meta=jobdata.import_obj(src,self.root)
  self.assertEqual(meta['source_format'],'obj');self.assertEqual(meta['source_name'],'mesh.obj');self.assertEqual(Path(meta['source_path']),src.resolve())
if __name__=='__main__':unittest.main()
