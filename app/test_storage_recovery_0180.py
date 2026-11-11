"""Deletion ownership and physical-byte accounting across recovery/scan copies."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import projects


class StorageRecovery0180Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def asset(self,letter,size):
        path=self.root/(letter*32+'.xyz64')
        path.write_bytes(b'x'*size)
        return str(path)

    def save(self,title,assets):
        return projects.save_named(self.root,{'assets':[{'file':p} for p in assets]},title,True)

    def recover(self,data):
        folder=self.root/'startup_recovery';folder.mkdir(exist_ok=True)
        path=folder/'workspace_latest.json';projects.atomic_json(path,data)
        return path

    def test_named_project_owns_its_hidden_recovery_and_unsaved_cache(self):
        saved=self.asset('a',31);unsaved=self.asset('b',47)
        p=self.save('Tunnel',[saved])
        recovery=self.recover({**p,'latest_result':{'file':unsaved}})
        review=projects.project_storage_review(self.root,p['project_id'],{})
        self.assertEqual(review['exclusive_bytes'],78)
        self.assertEqual(review['shared_bytes'],0)
        row=projects.projects_storage_overview(self.root,{})[0]
        self.assertEqual(row['total_bytes'],78)
        self.assertEqual(row['exclusive_bytes'],78)
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            out=projects.delete_project_permanently(self.root,p['project_id'],{},78)
            self.assertFalse(Path(saved).exists())
            self.assertFalse(Path(unsaved).exists())
            self.assertFalse(recovery.exists())
            self.assertEqual(out['exclusive_bytes'],78)
            stage=Path(schedule.call_args.args[0])
            self.assertTrue((stage/'recovery'/recovery.name).is_file())
            projects._purge_stage(stage)

    def test_other_named_project_still_protects_shared_recovery_cache(self):
        shared=self.asset('c',19);exclusive=self.asset('d',23)
        selected=self.save('Delete',[shared,exclusive])
        other=self.save('Keep',[shared]);recovery=self.recover(selected)
        review=projects.project_storage_review(self.root,selected['project_id'],{})
        self.assertEqual(review['exclusive_bytes'],23)
        self.assertEqual(review['shared_bytes'],19)
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            projects.delete_project_permanently(self.root,selected['project_id'],{},23)
            self.assertTrue(Path(shared).is_file())
            self.assertTrue((self.root/'projects'/(other['project_id']+'.json')).is_file())
            self.assertFalse(recovery.exists())
            projects._purge_stage(schedule.call_args.args[0])

    def test_different_project_and_unnamed_recoveries_remain_protected(self):
        cache=self.asset('e',29);selected=self.save('Delete',[cache])
        for recovered_id in ('f'*32,None):
            with self.subTest(recovered_id=recovered_id):
                recovery=self.recover({'project_id':recovered_id,'project_title':selected['project_title'],'cloud':{'file':cache}})
                review=projects.project_storage_review(self.root,selected['project_id'],{})
                self.assertEqual(review['exclusive_bytes'],0)
                self.assertEqual(review['shared_bytes'],29)
                self.assertTrue(recovery.is_file())

    def test_failed_recovery_retirement_rolls_back_assets_and_named_snapshot(self):
        cache=self.asset('1',17);selected=self.save('Keep on failure',[cache])
        recovery=self.recover(selected);replace=projects.os.replace
        def fail_recovery(src,dst):
            if Path(src)==recovery:raise OSError('Recovery is locked')
            return replace(src,dst)
        with mock.patch.object(projects.os,'replace',side_effect=fail_recovery):
            with self.assertRaisesRegex(OSError,'locked'):
                projects.delete_project_permanently(self.root,selected['project_id'],{},17)
        self.assertTrue(Path(cache).is_file())
        self.assertTrue(recovery.is_file())
        self.assertTrue((self.root/'projects'/(selected['project_id']+'.json')).is_file())

    def test_workspace_totals_count_shared_cache_and_e57_source_once(self):
        cache=self.asset('2',43)
        meta={'file':cache,'name':'setup','count':1,'source_path':'original/tunnel.e57','source_bytes':101}
        state={'job_items':[{'id':'a','meta':meta},{'id':'b','meta':dict(meta)}]}
        out=projects.workspace_storage_overview(self.root,state)
        self.assertEqual(len(out['items']),2)
        self.assertEqual([row['cache_bytes'] for row in out['items']],[43,43])
        self.assertEqual(out['cache_bytes'],43)
        self.assertEqual(out['source_bytes'],101)

    def test_recovery_deletion_never_removes_external_original_source(self):
        with tempfile.TemporaryDirectory() as external:
            original=Path(external)/'original.e57';original.write_bytes(b'original scan')
            cache=self.asset('3',37)
            selected=projects.save_named(self.root,{'cloud':{'file':cache,'source_path':str(original)}},'Tunnel',True)
            self.recover(selected)
            with mock.patch.object(projects,'_schedule_purge') as schedule:
                projects.delete_project_permanently(self.root,selected['project_id'],{},37)
                projects._purge_stage(schedule.call_args.args[0])
            self.assertEqual(original.read_bytes(),b'original scan')

    def test_corrupt_live_manifest_blocks_cleanup_before_any_data_is_removed(self):
        for relative in ('projects/'+'9'*32+'.json','project.json','startup_recovery/workspace_bad.json'):
            with self.subTest(manifest=relative):
                cache=self.asset('6',41)
                broken=self.root/relative;broken.parent.mkdir(parents=True,exist_ok=True)
                broken.write_text('{incomplete JSON')
                with mock.patch.object(projects,'_schedule_purge') as schedule:
                    with self.assertRaisesRegex(ValueError,broken.name):
                        projects.clean_orphan_storage(self.root)
                    schedule.assert_not_called()
                self.assertEqual(Path(cache).stat().st_size,41)
                self.assertTrue(broken.is_file());broken.unlink()

    def test_corrupt_unselected_named_project_blocks_selected_project_deletion(self):
        cache=self.asset('7',43);selected=self.save('Selected',[cache])
        broken=self.root/'projects'/('8'*32+'.json');broken.write_text('{broken')
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            with self.assertRaisesRegex(ValueError,broken.name):
                projects.delete_project_permanently(self.root,selected['project_id'],{},43)
            schedule.assert_not_called()
        self.assertTrue(Path(cache).is_file())
        self.assertTrue((self.root/'projects'/(selected['project_id']+'.json')).is_file())

    def test_retired_diagnostic_snapshots_do_not_block_or_protect_cleanup(self):
        cache=self.asset('8',47)
        retired=self.root/'diagnostics'/'retired_previous_sessions';retired.mkdir(parents=True)
        (retired/'bad.json').write_text('{broken diagnostics')
        projects.atomic_json(retired/'old.json',{'cloud':{'file':cache}})
        review=projects.orphan_storage_review(self.root,{})
        self.assertEqual(review['bytes'],47)
        self.assertIn(cache,review['paths'])

    def test_spatial_index_follows_cache_ownership_and_orphan_cleanup(self):
        cache=self.asset('a',31);index=Path(cache+'.viewindex.npz');index.write_bytes(b'index')
        selected=self.save('Tunnel',[cache])
        review=projects.project_storage_review(self.root,selected['project_id'],{})
        self.assertEqual(review['exclusive_bytes'],36)
        self.assertIn(str(index),review['exclusive_paths'])
        self.assertEqual(projects.orphan_storage_review(self.root,{})['bytes'],0)
        (self.root/'projects'/(selected['project_id']+'.json')).unlink()
        review=projects.orphan_storage_review(self.root,{})
        self.assertEqual(review['bytes'],36)
        self.assertIn(str(index),review['paths'])

    def test_already_removed_purge_stage_is_benign(self):
        projects._purge_stage(self.root/'no-longer-present')


class StorageWorkflow0180Tests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        import workflows
        self.workflows=workflows;self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.s=SimpleNamespace(WORK=self.root,STATE=workflows.empty(),JOB={'busy':False},
                               SELECTION=None,WORKSPACE_HISTORY={'undo':[],'redo':[]})
        self.s.save=lambda:projects.atomic_json(self.root/'project.json',self.s.STATE)
        self.s.progress=lambda _:None
        self.h=SimpleNamespace(send=lambda value:setattr(self,'response',value))

    def tearDown(self):self.temp.cleanup()

    def test_legacy_notice_stays_in_report_without_prompting_on_reopen(self):
        state,warnings=projects.normalize_manifest(self.workflows.empty())
        state['_project_import']={'warnings':warnings}
        self.workflows.restore_data(self.s,state)
        saved=projects.save_named(self.root,self.s.STATE,'Imported tunnel',True)
        for _ in range(2):
            self.workflows.restore_data(self.s,projects.read_named(self.root,saved['project_id']))
            self.workflows.get(self.s,self.h,'/api/project-recovery',{})
            self.assertEqual(self.response['warnings'],['Opened a legacy raw project snapshot.'])
            self.assertFalse(self.response['needs_review'])

    def test_recovery_issues_prompt_even_with_a_legacy_notice(self):
        for warnings,unavailable in (
            ([projects.LEGACY_RAW_NOTICE,'A scan cache is missing.'],[]),
            ([projects.LEGACY_RAW_NOTICE],[{'kind':'scan','original_data_json':'{}'}]),
        ):
            with self.subTest(warnings=warnings,unavailable=unavailable):
                self.s.STATE['project_recovery']={'warnings':warnings,'unavailable':unavailable}
                self.workflows.get(self.s,self.h,'/api/project-recovery',{})
                self.assertTrue(self.response['needs_review'])
                self.assertEqual(self.response['warnings'],warnings)
                if unavailable:self.assertEqual(self.response['unavailable'],[{'kind':'scan'}])

    def test_switching_projects_keeps_one_hidden_recovery_without_named_copies(self):
        saved=projects.save_named(self.root,self.s.STATE,'Tunnel',True);self.s.STATE.update(saved)
        for change in range(3):
            self.s.STATE['notes']='unsaved '+str(change)
            self.workflows._save_previous(self.s)
        self.assertEqual([x['project_id'] for x in projects.listing(self.root)],[saved['project_id']])
        self.assertNotIn('notes',projects.read_named(self.root,saved['project_id']))
        recoveries=list((self.root/'startup_recovery').glob('workspace_*.json'))
        self.assertEqual(len(recoveries),1)
        self.assertEqual(projects.read_json(recoveries[0])['notes'],'unsaved 2')
        # An explicit Save As still creates a distinct user-owned project.
        copied=projects.save_named(self.root,self.s.STATE,'Separate project',True)
        self.assertNotEqual(copied['project_id'],saved['project_id'])
        self.assertEqual(len(projects.listing(self.root)),2)

    def test_failed_recovery_write_preserves_previous_recovery_and_named_project(self):
        saved=projects.save_named(self.root,self.s.STATE,'Tunnel',True);self.s.STATE.update(saved)
        self.workflows._save_previous(self.s)
        previous=list((self.root/'startup_recovery').glob('*.json'))[0];contents=previous.read_bytes()
        with mock.patch.object(projects,'atomic_json',side_effect=OSError('No space')):
            with self.assertRaisesRegex(OSError,'No space'):self.workflows._save_previous(self.s)
        self.assertEqual(previous.read_bytes(),contents)
        self.assertEqual(len(projects.listing(self.root)),1)

    def test_storage_cleanup_keeps_caches_required_by_undo_and_redo(self):
        for side in ('undo','redo'):
            with self.subTest(side=side):
                cache=self.root/('4'*32+'.xyz64');cache.write_bytes(b'xyz')
                self.s.WORKSPACE_HISTORY={'undo':[],'redo':[]}
                self.s.WORKSPACE_HISTORY[side]=[{'before':{'cloud':{'file':str(cache)}},'after':{}}]
                self.workflows.get(self.s,self.h,'/api/project-orphans',{})
                self.assertEqual(self.response['bytes'],0)
                with mock.patch.object(projects,'_schedule_purge') as schedule:
                    self.workflows.post(self.s,self.h,'/api/project-action',{'action':'clean_orphans','confirm':True,'expected_bytes':0,'expected_token':self.response['review_token']})
                    projects._purge_stage(schedule.call_args.args[0])
                self.assertTrue(cache.is_file())
                self.assertNotIn('_session_undo_assets',self.s.STATE)

    def test_deleting_active_project_clears_only_its_session_history_after_success(self):
        cache=self.root/('5'*32+'.xyz64');cache.write_bytes(b'xyz')
        saved=projects.save_named(self.root,self.s.STATE,'Tunnel',True);self.s.STATE.update(saved)
        self.s.WORKSPACE_HISTORY['undo']=[{'before':{'cloud':{'file':str(cache)}},'after':{}}]
        with mock.patch.object(projects,'_schedule_purge') as schedule:
            self.workflows.get(self.s,self.h,'/api/project-storage',{'id':[saved['project_id']]})
            self.workflows.post(self.s,self.h,'/api/project-action',{'action':'delete_projects','ids':[saved['project_id']],'confirm':True,'expected_bytes':3,'expected_token':self.response['review_token']})
            projects._purge_stage(schedule.call_args.args[0])
        self.assertFalse(cache.exists())
        self.assertEqual(self.s.WORKSPACE_HISTORY,{'undo':[],'redo':[]})
        self.assertIsNone(self.s.STATE['project_id'])


if __name__=='__main__':unittest.main()
