"""Persistence boundaries for the startup metadata repair.

These generated fixtures exercise transactional writes and archive limits;
the separate large-workspace tests cover the real legacy 20 MB boundary.
"""
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import tempfile
import unittest
import zipfile

import projects


class ProjectStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tunnel_metadata_storage_')
        self.root=Path(self.tmp.name)

    def tearDown(self):self.tmp.cleanup()

    def state(self):
        return {'project_title':'Pomiar Łódź — tunnel 🔦', 'axis':None,
                'report_settings':{'coordinate':[440000.123456789,530000.987654321,-0.0001],
                                   'visible':True,'empty':None}}

    def test_unicode_and_float64_metadata_roundtrip_exactly(self):
        data=self.state();path=self.root/'project.json'
        projects.atomic_json(path,data)
        self.assertEqual(projects.read_json(path),data)
        self.assertEqual(json.loads(path.read_text(encoding='utf8')),data)

    def test_guard_counts_utf8_bytes_and_accepts_exact_boundary(self):
        data=self.state();size=projects.validate_metadata(data);path=self.root/'project.json'
        with patch.object(projects,'MAX_MANIFEST_BYTES',size):
            projects.atomic_json(path,data)
            self.assertEqual(path.stat().st_size,size)
            self.assertEqual(projects.read_json(path),data)
        original=path.read_bytes()
        with patch.object(projects,'MAX_MANIFEST_BYTES',size-1):
            with self.assertRaises(projects.MetadataTooLarge):projects.atomic_json(path,data)
            with self.assertRaises(projects.MetadataTooLarge):projects.read_json(path)
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse(list(self.root.glob('*.tmp_*')))

    def test_invalid_numbers_never_replace_previous_snapshot(self):
        path=self.root/'project.json';projects.atomic_json(path,self.state());original=path.read_bytes()
        for value in (float('nan'),float('inf'),-float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):projects.atomic_json(path,{'axis':None,'value':value})
                self.assertEqual(path.read_bytes(),original)
        self.assertFalse(list(self.root.glob('*.tmp_*')))

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary(self):
        path=self.root/'project.json';projects.atomic_json(path,self.state());original=path.read_bytes()
        with patch.object(projects.os,'replace',side_effect=OSError('simulated blocked replacement')):
            with self.assertRaisesRegex(OSError,'blocked replacement'):
                projects.atomic_json(path,{'axis':None,'project_title':'replacement'})
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse(list(self.root.glob('*.tmp_*')))

    def test_large_string_and_numeric_lists_use_bounded_sink_writes(self):
        data={'project_title':'chunks','axis':None,'notes':'Ł🔦'*500000,
              'values':[[i,i+.125,-i] for i in range(40000)]}
        class Sink:
            def __init__(self):self.maximum=0;self.count=0;self.digest=hashlib.sha256()
            def write(self,block):
                self.maximum=max(self.maximum,len(block));self.count+=len(block);self.digest.update(block)
        sink=Sink();size=projects.write_json(sink,data)
        expected=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')
        self.assertEqual(size,len(expected));self.assertEqual(sink.count,len(expected))
        self.assertEqual(sink.digest.hexdigest(),hashlib.sha256(expected).hexdigest())
        self.assertLessEqual(sink.maximum,512*1024)

    def test_cancel_during_metadata_export_keeps_previous_portable(self):
        destination=self.root/'existing.tstudio';destination.write_bytes(b'previous complete export')
        data=self.state();data['report_settings']['notes']='x'*4500000
        def cancel(message):
            if ' MB' in message:raise RuntimeError('cancel metadata export')
        with self.assertRaisesRegex(RuntimeError,'cancel metadata export'):
            projects.portable(self.root,data,destination,cancel)
        self.assertEqual(destination.read_bytes(),b'previous complete export')
        self.assertFalse(list(self.root.glob('existing.tstudio.tmp_*')))

    def test_portable_guard_uses_uncompressed_size_and_cleans_import(self):
        path=self.root/'compressed.tstudio'
        manifest={'format':'TunnelStudioProject','version':1,'state':self.state()}
        manifest['state']['report_settings']['notes']='a'*50000
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('project.json',json.dumps(manifest))
        self.assertLess(path.stat().st_size,4096)
        with patch.object(projects,'MAX_MANIFEST_BYTES',4096):
            with self.assertRaises(projects.MetadataTooLarge):projects.unpack(self.root,path)
        self.assertFalse(list(self.root.glob('project_assets_*')))

    def test_oversized_portable_write_preserves_previous_destination(self):
        destination=self.root/'existing.tstudio';destination.write_bytes(b'original export')
        with patch.object(projects,'MAX_MANIFEST_BYTES',80):
            with self.assertRaises(projects.MetadataTooLarge):projects.portable(self.root,self.state(),destination)
        self.assertEqual(destination.read_bytes(),b'original export')
        self.assertFalse(list(self.root.glob('existing.tstudio.tmp_*')))


if __name__=='__main__':unittest.main()
