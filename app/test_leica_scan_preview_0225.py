"""Regression coverage for display-only native Leica point sampling."""
from pathlib import Path
import hashlib
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

import numpy as np
import zstandard

import hspca_import
import project_browser
from hspca_codec import NAMES, WIDTHS, archive_entries, decode_node
from test_leica_catalog_0222 import GUID, metadata_fixture


def _point_node(xyz, intensity):
    """Encode test observations using the point-node layout the reader supports."""
    relative = np.rint(xyz * 1000).astype(np.int64)
    quant = relative + 2**31
    codes = []
    for row in quant.tolist():
        codes.append(sum(((v >> bit) & 1) << (3*bit+axis)
                         for axis,v in enumerate(row) for bit in range(32)))
    order = sorted(range(len(codes)),key=codes.__getitem__)
    sorted_codes = [codes[i] for i in order]
    morton_deltas = [b-a for a,b in zip([0,*sorted_codes],sorted_codes)]
    ordered = np.asarray(intensity,dtype=np.uint16)[order]
    intensity_deltas = np.diff(ordered,prepend=np.uint16(0))
    frames = [bytes((v >> (8*i)) & 255 for v in morton_deltas) for i in range(12)]
    zeros = bytes(len(xyz))
    frames.extend([zeros]*4)
    frames.extend(((intensity_deltas >> (8*i)) & 255).astype(np.uint8).tobytes() for i in range(2))
    frames.extend([zeros]*(sum(WIDTHS)-len(frames)))
    body = bytearray()
    starts = []
    compressor = zstandard.ZstdCompressor()
    for raw in frames:
        starts.append(len(body))
        swapped = bytearray(raw)
        n = len(raw)//2*2
        swapped[:n:2],swapped[1:n:2] = raw[1:n:2],raw[:n:2]
        encoded = compressor.compress(swapped)
        body.extend(struct.pack('<Q',len(encoded))+encoded)
    groups = struct.pack('<BB10Q',100,10,*(starts[i] for i in (0,12,16,18,19,21,23,25,26,27)))
    names = b''.join(struct.pack('<6xH',len(name))+name.encode('ascii') for name in NAMES)
    end = 172 + len(names)
    header = bytearray(172)
    header[:4] = b'APRN'
    struct.pack_into('<QI',header,4,end+len(groups)+len(body),22)
    struct.pack_into('<Q',header,20,len(xyz))
    struct.pack_into('<3q3d3d',header,36,*([2**31]*3),.001,.001,.001,0.,0.,0.)
    struct.pack_into('<Q6qI',header,112,end,*relative.min(0),*relative.max(0),len(NAMES))
    return bytes(header)+names+groups+body


def write_scan(path, xyz, intensity, nodes=1):
    path.parent.mkdir(parents=True,exist_ok=True)
    entries = [_point_node(xyz[ids],intensity[ids])
               for ids in np.array_split(np.arange(len(xyz)),nodes)]
    index = b''.join(struct.pack('<I',len(entry)) for entry in entries)
    blocks = [struct.pack('<I',len(raw))+zlib.compress(raw) for raw in (b'test point nodes',index)]
    header = bytearray(56)
    header[:4] = b'mMPK'
    struct.pack_into('<I',header,4,1)
    struct.pack_into('<I',header,12,len(entries))
    struct.pack_into('<IIQ',header,24,*map(len,blocks),56+sum(map(len,blocks)))
    path.write_bytes(bytes(header)+b''.join(blocks)+b''.join(entries))


class LeicaScanPreview0225Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root/GUID
        self.db = metadata_fixture(self.project)
        self.scan = self.project/'SetupsFolder/0.hspca'
        self.xyz = np.array([[1.,2.,.5],[2.,3.,.7],[-1.,0.,1.],[1.5,1.5,-1.]])
        write_scan(self.scan,self.xyz,np.array([100,50000,43000,3900]))
        self.state = SimpleNamespace(WORK=self.root)

    def preview(self, **kwargs):
        catalogue = project_browser.request(self.state,dict(action='browse',folder=str(self.project)))
        return project_browser.request(self.state,dict(action='preview',browse_id=catalogue['browse_id'],
                                        key=catalogue['projects'][0]['key'],**kwargs))['preview']

    def test_sampled_codec_agrees_with_full_decode(self):
        offset,length = archive_entries(self.scan)[0]
        block = self.scan.read_bytes()[offset:offset+length]
        xyz,attrs,stats = decode_node(block)
        sampled,sattrs,sstats = decode_node(block,sample_size=3)
        np.testing.assert_array_equal(sampled,xyz[[0,1,3]])
        np.testing.assert_array_equal(sattrs['INTENSITY'],attrs['INTENSITY'][[0,1,3]])
        self.assertEqual(stats,sstats)

    def test_preview_uses_chosen_snapshot_pose_and_keeps_source_bytes(self):
        alternate = metadata_fixture(self.project,'project_deliv_2000.db')
        with sqlite3.connect(alternate) as connection:
            connection.execute('UPDATE Hds_OdbSetupRef SET mPositionX=500012, mRotationZ=?, mRotationW=? WHERE mSetupId=0',
                               (2**-.5,2**-.5))
        before = {p:hashlib.sha256(p.read_bytes()).digest() for p in self.project.rglob('*') if p.is_file()}
        preview = self.preview(database=str(alternate))
        self.assertEqual(preview['quality'],'sampled')
        self.assertEqual((preview['sampled_setups'],preview['item_count']),(1,3))
        self.assertEqual(preview['lines'],[])
        world = np.asarray(preview['points'])[:,:3]+preview['origin']
        expected = self.xyz[:,[1,0,2]]*[-1,1,1]+[500012,200000,50]
        np.testing.assert_allclose(world[world[:,2].argsort()],expected[expected[:,2].argsort()],rtol=0,atol=1e-6)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).digest() for p in self.project.rglob('*') if p.is_file()})

    def test_thumbnails_never_decode_native_points(self):
        with patch('leica_scan_preview._sample',side_effect=AssertionError('Thumbnail decoded source')):
            preview = self.preview(thumbnail=True)
        self.assertEqual(preview['quality'],'setup_layout')

    def test_missing_unsafe_or_unreadable_files_keep_layout_honest(self):
        self.scan.unlink()
        outside = self.root/'outside.hspca'
        write_scan(outside,self.xyz,np.ones(len(self.xyz)))
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE Hds_OdbSetup SET mCloudURI=? WHERE mId=0',('../outside.hspca',))
        preview = self.preview()
        self.assertEqual(preview['quality'],'setup_layout')
        self.assertIn('SetupsFolder',preview['scan_message'])
        bad = self.project/'SetupsFolder/1.hspca'
        bad.write_bytes(b'Unsupported scan')
        preview = self.preview()
        self.assertEqual(preview['quality'],'setup_layout')
        self.assertEqual(preview['sampled_setups'],0)
        self.assertIn('could not be sampled',preview['scan_message'])

    def test_sample_is_bounded_and_survives_unavailable_neighbour(self):
        results = hspca_import.sample_setups([str(self.scan),str(self.scan.with_name('missing.hspca'))],4)
        self.assertEqual([r['status'] for r in results],['sampled','unavailable'])
        self.assertEqual(len(results[0]['rows']),2)
