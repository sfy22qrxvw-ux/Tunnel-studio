"""Bundle retirement must preserve completed clouds and their saved outputs."""
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import analysis_sources
import explorer_deletion
import job_api
import jobdata
import projects
import registered_cloud
import workspace_tools
import workflows
import test_registered_snapshot_0187 as fixture


class BundleIndependenceTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.RegisteredSnapshot0187Tests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.s=self.fx.s
        self.group=workspace_tools.inventory(self.s)[0]
        self.source_ids={x['id'] for x in self.fx.sources}
        # A separately materialised cleaned result, like the user's upper row.
        kept=jobdata.extract_selection(self.fx.merged['meta'],dict(mode='box',minimum=[-10,-10,-10],maximum=[10,10,10]),self.fx.root,'Tunnel · Registered cloud',self.s.progress)
        self.clean=dict(id='cleaned',kind='scan',name=kept['name'],meta=kept,visible=True,
                        cleaning_provenance=dict(source_item_id=self.fx.merged['id'],method='reviewed cleaning'))
        self.s.STATE['job_items'].append(self.clean)
        self.s.save()
        self.clean_hash=hashlib.sha256(Path(kept['file']).read_bytes()).hexdigest()

    def test_legacy_bundle_review_excludes_registered_cloud_and_saved_results(self):
        # Old project records attached the result to its source bundle.
        self.fx.merged['bundle_id']=self.group['id']
        body=dict(bundle_ids=[self.group['id']])
        review=explorer_deletion.review(self.s,body)
        self.assertEqual({x['id'] for x in review['items']},self.source_ids)
        with patch.object(projects,'_schedule_purge') as schedule:
            explorer_deletion.delete(self.s,{**body,'confirmed':True,'revision':review['revision']})
            if schedule.called:projects._purge_stage(schedule.call_args.args[0])
        job_api.snapshot(self.s)
        self.fx.assert_saved_results(self.s.STATE)
        self.assertFalse(self.s.STATE['bundles'])
        kept=next(x for x in self.s.STATE['job_items'] if x['id']=='cleaned')
        self.assertTrue(kept['visible'])
        self.assertEqual(hashlib.sha256(Path(kept['meta']['file']).read_bytes()).hexdigest(),self.clean_hash)
        self.assertTrue(all(not Path(x['meta']['file']).exists() for x in self.fx.sources))
        reopened=self.fx.save_and_reopen()
        self.fx.assert_saved_results(reopened)
        for ident in (self.fx.merged['id'],'cleaned'):
            self.assertEqual(analysis_sources.resolve(self.s,ident)['meta']['count'],44)

    def test_inventory_keeps_registered_result_at_project_level(self):
        for _ in range(2):
            self.fx.merged['bundle_id']=self.group['id']
            snapshot=job_api.snapshot(self.s)
            merged=next(x for x in snapshot['items'] if x['id']==self.fx.merged['id'])
            self.assertIsNone(merged.get('bundle_id'))
            self.assertEqual(set(snapshot['bundles'][0]['scan_ids']),self.source_ids)
        self.assertEqual(self.fx.asset_hashes,{path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in self.fx.asset_hashes})

    def test_explicit_result_selection_still_deletes_that_result(self):
        body=dict(bundle_ids=[self.group['id']],ids=[self.fx.merged['id']])
        review=explorer_deletion.review(self.s,body)
        self.assertEqual({x['id'] for x in review['items']},self.source_ids|{self.fx.merged['id']})

    def test_bundle_visibility_controls_inputs_and_preserves_result_bytes(self):
        self.assertTrue(self.fx.merged['visible'])
        workspace_tools.bundle_action(self.s,dict(action='visibility',id=self.group['id'],visible=True))
        self.assertTrue(all(x['visible'] for x in self.fx.sources))
        self.assertFalse(self.fx.merged['visible'])
        registered_cloud.set_visibility(self.s.STATE['job_items'],self.fx.merged,True)
        self.assertFalse(any(x['visible'] for x in self.fx.sources))
        self.fx.assert_saved_results(self.s.STATE)

if __name__=='__main__':unittest.main()
