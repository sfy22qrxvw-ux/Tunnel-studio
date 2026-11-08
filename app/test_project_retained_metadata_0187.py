# Copyright (c) 2026 Damian Gulczynski.
"""Portable relocation preserves verified counts without reviving stale caches."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import job_api
import pointio
import projects
import scan_edits


class ProjectRetainedMetadata0187Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        n=4099;ids=np.arange(n,dtype=float)
        xyz=np.column_stack([ids,ids*.25,ids*.5])
        attrs=np.column_stack([ids%256,ids%255,ids%254,ids%253]).astype('<f4')
        attrs[::7,0]=np.nan
        xyz.astype('<f8').tofile(self.root/'scan.xyz64')
        attrs.tofile(self.root/'scan.attr32')
        self.before=np.zeros(n,'u1');self.before[1::9]=1
        self.after=self.before.copy();self.after[5::11]=1
        self.before.tofile(self.root/'before.mask');self.after.tofile(self.root/'after.mask')
        meta=dict(file=str(self.root/'scan.xyz64'),attributes_file=str(self.root/'scan.attr32'),
                  count=n,minimum=xyz.min(0).tolist(),maximum=xyz.max(0).tolist(),
                  attribute_counts=np.isfinite(attrs).sum(0).tolist())
        old=dict(meta,mask_file=str(self.root/'before.mask'))
        meta['mask_file']=str(self.root/'after.mask')
        before=copy.deepcopy(scan_edits.properties(old))
        after=copy.deepcopy(scan_edits.properties(meta))
        def summary(properties):
            return {key:copy.deepcopy(properties[key]) for key in ('revision','count','source_signature')}
        meta['retained_summary']=summary(after)
        history=dict(before_mask=old['mask_file'],after_mask=meta['mask_file'],
                     before_properties=before,after_properties=after,
                     before_summary=summary(before),after_summary=summary(after))
        self.state=dict(axis=None,project_title='Exact portable edits',cloud=copy.deepcopy(meta),
                        job_items=[dict(id='scan',kind='scan',meta=meta,edit_history=[history],redo_history=[])])
        self.archive=self.root/'portable.tstudio'

    def item(self,state):return state['job_items'][0]

    def roundtrip(self):
        projects.portable(self.root,self.state,self.archive)
        state=projects.unpack(self.root,self.archive)
        self.addCleanup(projects.cleanup_import,self.root,state)
        return state

    def assert_valid_metadata(self,state):
        current=self.item(state)['meta']
        with patch.object(pointio,'arrays',side_effect=AssertionError('Relocated metadata rescanned coordinates')):
            for meta in (current,state['cloud']):
                props=scan_edits.properties(meta)
                self.assertEqual(props['count'],int(np.count_nonzero(self.after==0)))
                self.assertEqual(props['source_signature'],scan_edits._properties_signature(meta))
                self.assertEqual(meta['retained_summary']['source_signature'],props['source_signature'])
                self.assertIsNotNone(scan_edits.cached_summary(meta))
            history=self.item(state)['edit_history'][0]
            for side,flags in (('before',self.before),('after',self.after)):
                meta=dict(current,mask_file=history[side+'_mask'],retained_properties=history[side+'_properties'])
                props=scan_edits.properties(meta)
                self.assertEqual(props['count'],int(np.count_nonzero(flags==0)))
                self.assertEqual(props['source_signature'],scan_edits._properties_signature(meta))
                summary=history[side+'_summary']
                self.assertEqual(summary['revision'],history[side+'_mask'])
                self.assertEqual(summary['count'],props['count'])
                self.assertEqual(summary['source_signature'],props['source_signature'])
                meta['retained_summary']=summary
                self.assertIsNotNone(scan_edits.cached_summary(meta))

    def test_portable_then_named_save_reopen_preserves_verified_current_and_undo_metadata(self):
        before=copy.deepcopy(self.state)
        state=self.roundtrip()
        self.assertEqual(self.state,before)
        self.assertNotEqual(self.item(state)['meta']['file'],self.item(self.state)['meta']['file'])
        self.assert_valid_metadata(state)
        saved=projects.save_named(self.root,state,'Reopened portable',True)
        self.assert_valid_metadata(projects.read_named(self.root,saved['project_id']))

    def test_cache_invalidates_after_same_path_data_changes(self):
        for key in ('file','attributes_file','mask_file'):
            with self.subTest(key=key):
                state=self.roundtrip();meta=self.item(state)['meta']
                self.assertIsNotNone(scan_edits.cached_properties(meta))
                path=Path(meta[key]);info=path.stat()
                with path.open('r+b') as out:
                    first=out.read(1);out.seek(0);out.write(bytes([first[0]^1]))
                os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+1_000_000))
                self.assertIsNone(scan_edits.cached_properties(meta))
                self.assertIsNone(scan_edits.cached_summary(meta))
                self.assertNotEqual(meta['retained_summary']['source_signature'],scan_edits._properties_signature(meta))

    def test_summary_without_exact_properties_and_unmasked_undo_survives_reopen(self):
        item=self.item(self.state);meta=item['meta']
        old=copy.deepcopy(meta);old.pop('mask_file');old.pop('retained_properties');old.pop('retained_summary')
        before=scan_edits.properties(old)
        entry=item['edit_history'][0]
        entry.update(before_mask=None,before_properties=before,
                     before_summary={key:copy.deepcopy(before[key]) for key in ('revision','count','source_signature')})
        for owner in (meta,self.state['cloud']):owner.pop('retained_properties')
        state=self.roundtrip();meta=self.item(state)['meta'];entry=self.item(state)['edit_history'][0]
        self.assertNotIn('retained_properties',meta)
        with patch.object(pointio,'arrays',side_effect=AssertionError('Summary reopen read coordinates')):
            self.assertEqual(scan_edits.cached_summary(meta)['count'],int(np.count_nonzero(self.after==0)))
            restored=dict(meta,retained_properties=entry['before_properties'],retained_summary=entry['before_summary'])
            restored.pop('mask_file')
            self.assertEqual(scan_edits.cached_summary(restored)['count'],len(self.after))
            self.assertEqual(scan_edits.properties(restored)['count'],len(self.after))

    def test_active_survey_history_preserves_verified_undo_and_redo_metadata(self):
        self.state.update(active_job_scan='scan',edits=copy.deepcopy(self.item(self.state)['edit_history']),redo=[])
        state=self.roundtrip();entry=state['edits'][0]
        for side in ('before','after'):
            self.assertEqual(entry[side+'_summary'],self.item(state)['edit_history'][0][side+'_summary'])
            self.assertEqual(entry[side+'_properties'],self.item(state)['edit_history'][0][side+'_properties'])
        server=SimpleNamespace(STATE=state,WORK=self.root,SELECTION=None,save=lambda:None,progress=lambda _:None)
        # Exercise the alias-to-item transfer used when active survey edits
        # change the mask, then undo/redo through the reopened item history.
        meta=self.item(state)['meta'];meta['mask_file']=entry['before_mask']
        with (patch.object(pointio,'arrays',side_effect=AssertionError('Portable active Undo read XYZ')),
              patch.object(scan_edits,'_read_mask_count',side_effect=AssertionError('Portable active Undo recounted the mask'))):
            job_api.sync_active(server)
            self.assertEqual(meta['mask_file'],entry['after_mask'])
            scan_edits.apply(server,self.item(state),'undo_edit')
            self.assertEqual(scan_edits.cached_summary(meta)['count'],int(np.count_nonzero(self.before==0)))
            self.assertEqual(scan_edits.properties(meta)['count'],int(np.count_nonzero(self.before==0)))
            scan_edits.apply(server,self.item(state),'redo_edit')
            self.assertEqual(scan_edits.cached_summary(meta)['count'],int(np.count_nonzero(self.after==0)))
            self.assertEqual(scan_edits.properties(meta)['count'],int(np.count_nonzero(self.after==0)))

    def test_stale_local_signatures_are_not_promoted_by_packaging(self):
        meta=self.item(self.state)['meta']
        for owner in (meta,self.state['cloud']):
            owner['retained_properties']['source_signature'][1][0][-1]-=1
            owner['retained_summary']['source_signature'][1][0][-1]-=1
        state=self.roundtrip()
        for owner in (self.item(state)['meta'],state['cloud']):
            self.assertNotIn('retained_properties',owner)
            self.assertNotIn('retained_summary',owner)
        # Independent Undo revisions still describe the unmodified assets.
        self.assertIn('before_summary',self.item(state)['edit_history'][0])

    def test_unverified_legacy_signatures_are_not_reanchored_on_import(self):
        raw=self.root/'legacy.json';projects.atomic_json(raw,self.state)
        state=projects.unpack(self.root,raw)
        self.addCleanup(projects.cleanup_import,self.root,state)
        meta=self.item(state)['meta'];history=self.item(state)['edit_history'][0]
        self.assertNotIn('retained_properties',meta)
        self.assertNotIn('retained_summary',meta)
        self.assertNotIn('before_properties',history)
        self.assertNotIn('after_summary',history)

    def test_replaced_archive_asset_with_valid_zip_crc_does_not_reuse_old_metadata(self):
        projects.portable(self.root,self.state,self.archive)
        with zipfile.ZipFile(self.archive) as source:
            entries={entry.filename:source.read(entry) for entry in source.infolist()}
        manifest=json.loads(entries['project.json'])
        mask=self.item(manifest['state'])['meta']['mask_file']
        content=bytearray(entries[mask]);content[0]=1 if content[0]==0 else 0
        entries[mask]=bytes(content)
        with zipfile.ZipFile(self.archive,'w',compression=zipfile.ZIP_STORED) as target:
            for name,content in entries.items():target.writestr(name,content)
        state=projects.unpack(self.root,self.archive)
        self.addCleanup(projects.cleanup_import,self.root,state)
        meta=self.item(state)['meta'];history=self.item(state)['edit_history'][0]
        self.assertNotIn('retained_properties',meta);self.assertNotIn('retained_summary',meta)
        self.assertNotIn('after_properties',history);self.assertNotIn('after_summary',history)
        self.assertIn('before_properties',history);self.assertIn('before_summary',history)

    def test_source_mutation_during_copy_preserves_previous_archive(self):
        self.archive.write_bytes(b'previous portable project')
        source=Path(self.item(self.state)['meta']['file'])
        before=source.stat()
        def mutate(message):
            if message.startswith('Packaging project asset 1/') and ' bytes' in message:
                os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000))
        with self.assertRaisesRegex(ValueError,'changed while copying'):
            projects.portable(self.root,self.state,self.archive,mutate)
        self.assertEqual(self.archive.read_bytes(),b'previous portable project')
        self.assertFalse(list(self.root.glob('portable.tstudio.tmp_*')))

    def test_unverified_derived_statistics_are_dropped_without_losing_source_counts(self):
        derived=self.root/'scan.analysis64'
        np.zeros((len(self.after),4),'<f8').tofile(derived)
        info=derived.stat()
        for owner in (self.item(self.state)['meta'],self.state['cloud']):
            for key in ('retained_properties','retained_summary'):
                owner[key].update(derived=str(derived),valid_points=owner[key]['count'],
                                  derived_signature=[str(derived),info.st_size,info.st_mtime_ns,info.st_ctime_ns])
        state=self.roundtrip();self.assert_valid_metadata(state)
        for owner in (self.item(state)['meta'],state['cloud']):
            for key in ('retained_properties','retained_summary'):
                self.assertNotIn('derived',owner[key]);self.assertNotIn('valid_points',owner[key])
                self.assertNotIn('derived_signature',owner[key])


if __name__=='__main__':unittest.main()
