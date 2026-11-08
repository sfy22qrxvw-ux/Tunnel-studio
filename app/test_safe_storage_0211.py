"""Safety boundaries for a live workspace containing survey data and unrelated files."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import projects
import storage_cleanup as clean
import storage_inventory as audit


class SafeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.s=SimpleNamespace(WORK=self.root,STATE={},JOB={'busy':False},WORKSPACE_HISTORY={'undo':[],'redo':[]})
    def file(self,name,data=b'payload'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path
    def named(self,title,**state):
        return projects.save_named(self.root,state,title,True)
    def request(self,action,**args):
        return clean.request(self.s,dict(action=action,**args))
    def apply(self,rows,mode='manual'):
        ids=[r['id'] for r in rows];review=self.request('review',mode=mode,ids=ids)
        with patch.object(projects,'_schedule_purge') as schedule:
            out=self.request('apply',mode=mode,ids=ids,confirm=True,expected_token=review['review_token'])
            projects._purge_stage(schedule.call_args.args[0])
        return out
    def test_whole_project_never_removes_software_settings_original_archives_or_unknown_files(self):
        media=[self.file(n) for n in ('TunnelStudioBackend.exe','settings.toml','saved.tstudio','my.e57','notes.txt')]
        cache=self.file('1'*32+'.analysis64');asserted_source=self.file('2'*32+'.xyz64')
        p=self.named('Old',records=[{'file':str(f)} for f in [*media,cache,asserted_source]],original_path=str(asserted_source))
        review=projects.projects_storage_review(self.root,[p['project_id']],{})
        self.assertEqual(review['exclusive_paths'],[str(cache)])
        with patch.object(projects,'_schedule_purge') as schedule:
            projects.delete_projects_permanently(self.root,[p['project_id']],{},expected_token=review['review_token'])
            projects._purge_stage(schedule.call_args.args[0])
        self.assertTrue(all(f.exists() for f in [*media,asserted_source]))
        self.assertFalse(cache.exists())
    def test_real_paths_and_deduplicated_storage_preserve_link_and_unknown_directories(self):
        old=self.file('a'*32+'.xyz64',b'a'*400)
        link=self.root/('b'*32+'.xyz64');os.link(old,link)
        mockfolder=self.file('surface_demo_fake/important.tstudio',b'save')
        external=self.root/'junction'
        try:external.symlink_to(self.root/'surface_demo_fake',target_is_directory=True)
        except OSError:pass
        inventory=audit.inventory(self.root,{})
        self.assertEqual(inventory['used_bytes'],404);self.assertFalse(projects.orphan_storage_review(self.root,{})['paths'])
        self.assertTrue(mockfolder.is_file())
    def test_reference_damage_and_embedded_recovery_remain_safe(self):
        data=self.file('3'*32+'.xyz64')
        self.named('Previous',project_recovery={'unavailable':[{'original_data_json':json.dumps({'file':str(data)})}]})
        self.assertFalse(self.request('list')['files'][0]['path']==str(data))
        duplicate=self.root/'projects'/('4'*32+'.json')
        projects.atomic_json(duplicate,dict(project_id='3'*32))
        with self.assertRaisesRegex(ValueError,'identity'):self.request('list')
        report=audit.inventory(self.root,self.s.STATE)
        self.assertFalse(report['complete']);self.assertTrue(data.exists())
    def test_snapshot_only_then_file_cleanup_is_a_separate_transaction(self):
        old=self.file('a'*32+'.analysis64');shared=self.file('b'*32+'.xyz64')
        p=self.named('Retired',derived=str(old),cloud={'file':str(shared)})
        current=self.named('Current',cloud={'file':str(shared)});self.s.STATE=current
        rows=self.request('list',category='snapshots')['files']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['owners'],['Retired'])
        self.apply(rows)
        self.assertTrue(old.exists() and shared.exists())
        rows=self.request('list')['files']
        self.assertEqual([r['path'] for r in rows],[str(old)])
        self.apply(rows);self.assertFalse(old.exists());self.assertTrue(shared.exists())
    def test_close_uses_only_genuine_unused_outputs_and_rebuildable_analysis(self):
        cloud=self.file('a'*32+'.xyz64')
        axis={'rows':[[0,0,0,0,1],[1,1,0,0,1]]};meta={'file':str(cloud)}
        self.s.STATE={'axis':axis,'cloud':meta,'project_title':'Retained'}
        exact=self.file('a'*32+'.analysis64');clean.record_projection(meta,axis,exact)
        detached=self.file('b'*32+'.analysis64')
        preview=self.file('b'*32+'.preview64');export=self.file('3'*32+'.e57');private=self.file('4'*32+'.e57')
        clean.record_product(self.root,export,'exports',self.s.STATE)
        report=self.file('survey_report.html');clean.record_product(self.root,report,'reports',self.s.STATE)
        demo=self.file('demo/demo_scan.xyz');clean.record_product(self.root,demo,'demo',self.s.STATE)
        rows=self.request('list',mode='close')['files']
        self.assertEqual({Path(r['path']) for r in rows},{exact,preview,export})
        self.s.STATE['report_ready']=True;self.s.STATE['demo']=True
        rows=self.request('list')['files']
        self.assertEqual({Path(r['path']) for r in rows},{exact,detached,preview,export})
        self.assertTrue(private.exists())
    def test_equal_size_replacement_and_newly_referenced_files_block_confirm(self):
        data=self.file('a'*32+'.mask')
        row=self.request('list')['files'][0];ids=[row['id']]
        token=self.request('review',ids=ids)['review_token']
        os.replace(self.file('new.bin',b'changed'),data)
        with self.assertRaisesRegex(ValueError,'changed'):self.request('apply',ids=ids,confirm=True,expected_token=token)
        self.assertEqual(data.read_bytes(),b'changed')
        row=self.request('list')['files'][0];ids=[row['id']]
        token=self.request('review',ids=ids)['review_token']
        self.s.WORKSPACE_HISTORY['undo']=[{'before':{'file':str(data)}}]
        with self.assertRaisesRegex(ValueError,'eligible'):self.request('apply',ids=ids,confirm=True,expected_token=token)
    def test_partial_move_rolls_back_and_uncommitted_stage_never_purges(self):
        first=self.file('a'*32+'.preview64')
        second=self.file('b'*32+'.preview64')
        rows=self.request('list')['files'];ids=[r['id'] for r in rows];token=self.request('review',ids=ids)['review_token']
        with patch.object(projects,'_write_purge_meta',side_effect=OSError('Cannot write deletion commit')),patch.object(projects,'_schedule_purge') as schedule:
            with self.assertRaisesRegex(OSError,'commit'):self.request('apply',ids=ids,confirm=True,expected_token=token)
            self.assertTrue(first.exists() and second.exists());schedule.assert_not_called()
        stranded=self.file('.project_delete_queue/select_20261104T000000Z_'+'f'*32+'/assets/my.tstudio')
        with patch.object(projects,'_schedule_purge') as schedule:
            projects.resume_storage_purges(self.root);schedule.assert_not_called()
        self.assertTrue(stranded.exists())
    def test_lists_are_paginated_and_only_sign_explicit_ids(self):
        for i in range(111):self.file(f'{i:032x}.mask',bytes(i+1))
        rows=self.request('list',limit=100)['files'];more=self.request('list',offset=100)['files']
        self.assertEqual((len(rows),len(more)),(100,11));self.assertEqual(set(r['id'] for r in rows)&set(r['id'] for r in more),set())
        with self.assertRaisesRegex(ValueError,'selection'):self.request('review',ids=['../other-file'])
        self.assertEqual(self.apply(more[:1])['files'],1);self.assertEqual(self.request('list')['total_files'],110)
    def test_legacy_report_and_verified_demonstration_identification(self):
        from demo_targets import generate
        scan,_=generate(self.root/'target_demo')
        f=self.file('survey_report.html',b'<!doctype html><html lang="en"><meta charset="utf-8"><title>Tunnel Studio survey report</title>')
        self.assertIn(str(f),[row['path'] for row in self.request('list')['files']])
        self.assertIn(str(scan),[row['path'] for row in self.request('list',category='demo')['files']])
        scan.write_text('my original data');f.write_text('my notes')
        self.assertNotIn(str(scan),[r['path'] for r in self.request('list')['files']])
        self.assertNotIn(str(f),[r['path'] for r in self.request('list')['files']])
    def test_parallel_commit_is_blocked(self):
        with clean.incoming(),clean.incoming():
            with self.assertRaisesRegex(ValueError,'other workspace'): self.request('apply',mode='manual',ids=[],confirm=True,expected_token='x')


if __name__=='__main__':unittest.main()
