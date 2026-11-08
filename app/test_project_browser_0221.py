"""Project previews remain display-only and do not open or rewrite survey data."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import jobdata
import project_browser as browser
import projects
import registration_pose
import workflows


class ProjectBrowser0221Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.s=SimpleNamespace(WORK=self.root)
        self.data=workflows.empty();self.data['job_items']=[]
        rng=np.random.default_rng(721)
        self.points=rng.normal(size=(18003,3))+[500000,200000,100]
        self.path=self.root/'source.xyz64';self.points.astype('<f8').tofile(self.path)
        self.mask=self.root/'source.mask';flags=np.zeros(len(self.points),'u1');flags[::3]=1;flags.tofile(self.mask)
        self.meta=dict(file=str(self.path),mask_file=str(self.mask),count=len(self.points),
                       minimum=self.points.min(0).tolist(),maximum=self.points.max(0).tolist())
        registration_pose.set_pose(self.meta,np.eye(3),[8,-3,2])
        self.data['job_items']=[dict(id='scan',name='North tunnel',kind='scan',meta=self.meta,visible=True)]

    def digest(self):return hashlib.sha256(self.path.read_bytes()+self.mask.read_bytes()).hexdigest()

    def test_save_preview_preserves_sources_and_exact_pose_mask_semantics(self):
        before=self.digest();original=copy.deepcopy(self.data)
        saved=projects.save_named(self.root,self.data,'North tunnel — night survey',True)
        self.assertEqual(self.data,original);self.assertEqual(self.digest(),before)
        preview=browser.checked(saved['project_preview']);self.assertEqual(preview['quality'],'sampled')
        self.assertLessEqual(len(preview['points']),browser.MAX_POINTS)
        xyz=np.array(preview['points'])[:,:3]+preview['origin']
        from scipy.spatial import cKDTree
        expected=self.points[np.arange(len(self.points))%3!=0]+[8,-3,2]
        self.assertLess(cKDTree(expected).query(xyz)[0].max(),2e-5)
        self.assertLess(len(json.dumps(preview).encode()),browser.MAX_PREVIEW_BYTES)

    def test_listing_and_preview_show_title_not_uuid(self):
        saved=projects.save_named(self.root,self.data,'Crown inspection',True)
        rows=projects.projects_storage_overview(self.root)
        self.assertEqual(rows[0]['project_title'],'Crown inspection')
        self.assertTrue(rows[0]['has_preview'])
        with patch.object(jobdata,'_item_sample_uncached',side_effect=AssertionError('Saved overview should be reused')):
            result=browser.request(self.s,dict(action='preview',id=saved['project_id']))
        self.assertEqual(result['project_title'],'Crown inspection')
        thumb=browser.request(self.s,dict(action='preview',id=saved['project_id'],thumbnail=True))
        self.assertLessEqual(len(thumb['preview']['points']),1200)

    def test_browse_uuid_snapshots_and_portable_names_without_extracting_assets(self):
        saved=projects.save_named(self.root,self.data,'Ring survey',True)
        archive=self.root/'encoded-project.tstudio'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('project.json',json.dumps({'format':'TunnelStudioProject','version':1,'state':saved}))
            z.writestr('assets/never-read',b'not a point array')
        before={p.name for p in self.root.iterdir()}
        listing=browser.request(self.s,dict(action='browse',folder=str(self.root)))
        self.assertEqual(len(listing['projects']),2)
        self.assertEqual({r['project_title'] for r in listing['projects']},{'Ring survey'})
        portable=next(r for r in listing['projects'] if r['kind']=='portable')
        result=browser.request(self.s,dict(action='preview',browse_id=listing['browse_id'],key=portable['key']))
        self.assertTrue(result['preview']['points'])
        self.assertEqual({p.name for p in self.root.iterdir()},before)

    def test_legacy_portable_reports_bounds_and_does_not_follow_asset_paths(self):
        archive=self.root/'old.tstudio'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('project.json',json.dumps(self.data))
        listing=browser.request(self.s,dict(action='browse',folder=str(self.root)))
        with patch.object(jobdata,'_item_sample_uncached',side_effect=AssertionError('Do not read portable source paths')):
            r=browser.request(self.s,dict(action='preview',browse_id=listing['browse_id'],key=listing['projects'][0]['key']))
        self.assertEqual(r['preview']['quality'],'bounds_only');self.assertEqual(len(r['preview']['lines']),24)

    def test_external_references_cannot_supply_legacy_local_sample(self):
        work=self.root/'metadata';work.mkdir()
        preview=browser.build(self.data,work)
        self.assertEqual(preview['quality'],'bounds_only')
        self.assertEqual(preview['points'],[])

    def test_stale_folder_search_is_refused(self):
        saved=projects.save_named(self.root,self.data,'First name',True)
        listing=browser.request(self.s,dict(action='browse',folder=str(self.root)))
        path=self.root/'projects'/(saved['project_id']+'.json');saved['project_title']='Changed name'
        projects.atomic_json(path,saved)
        with self.assertRaisesRegex(ValueError,'changed'):
            browser.request(self.s,dict(action='preview',browse_id=listing['browse_id'],key=listing['projects'][0]['key']))

    def test_parent_folder_resolves_opaque_workspace_and_deduplicates_autosave(self):
        root=self.root/('a'*32);root.mkdir()
        saved=projects.save_named(root,workflows.empty(),'Actual saved project name',True)
        projects.atomic_json(root/'project.json',saved)
        listing=browser.request(self.s,dict(action='browse',folder=str(self.root)))
        self.assertEqual(len(listing['projects']),1)
        self.assertEqual(listing['projects'][0]['project_title'],'Actual saved project name')
        self.assertEqual(Path(listing['projects'][0]['path']).parent,root/'projects')

    def test_unknown_and_bad_preview_content_is_not_uploaded(self):
        for rows in ([[1,2,3,0,0]],[[1,2,3,2,0,0]],[[1,2,float('nan'),0,0,0]]):
            with self.assertRaises(ValueError):browser.checked(dict(format='TunnelStudioDisplayPreview',version=1,origin=[0,0,0],points=rows,lines=[]))
        with self.assertRaises(ValueError):browser.request(self.s,dict(action='preview',id=123))
        with self.assertRaises(ValueError):browser.request(self.s,dict(action='preview',browse_id='missing',key='missing'))
        folder=self.root/'projects';folder.mkdir()
        projects.atomic_json(folder/'malformed-id.json',dict(project_id=['invalid'],project_title='Legacy record',job_items=[]))
        listing=browser.request(self.s,dict(action='browse',folder=str(self.root)))
        self.assertEqual(listing['projects'][0]['project_title'],'Legacy record')

    def test_budget_and_cancellation_do_not_rewrite_sources(self):
        before=self.digest();calls=[]
        real=jobdata._gather_display_rows
        def checked_read(path,count,ids,*args,**kwargs):
            calls.append(len(ids));self.assertLessEqual(len(ids),browser.MAX_POINTS)
            return real(path,count,ids,*args,**kwargs)
        with patch.object(jobdata,'_gather_display_rows',side_effect=checked_read):browser.build(self.data,self.root)
        self.assertTrue(calls)
        with self.assertRaises(InterruptedError):browser.build(self.data,self.root,cancel=lambda:True)
        with self.assertRaises(jobdata.Cancelled):browser.attach(self.data,self.root,lambda _:(_ for _ in ()).throw(jobdata.Cancelled('cancelled')))
        self.assertEqual(before,self.digest())

    def test_optional_preview_failure_does_not_prevent_project_save(self):
        with patch.object(browser,'build',side_effect=ValueError('bad display data')):
            saved=projects.save_named(self.root,self.data,'Still saved',True)
        self.assertEqual(projects.read_named(self.root,saved['project_id'])['project_title'],'Still saved')
        self.assertNotIn('project_preview',saved)

    def test_portable_contains_small_preview_and_preserves_sources(self):
        before=self.digest();dest=self.root/'survey.tstudio'
        projects.portable(self.root,self.data,dest)
        with zipfile.ZipFile(dest) as z:
            state=json.loads(z.read('project.json'))['state']
            self.assertTrue(browser.checked(state['project_preview'])['points'])
        self.assertEqual(self.digest(),before)

    def test_optional_preview_cannot_block_a_valid_metadata_size_limit(self):
        with patch.object(projects,'MAX_MANIFEST_BYTES',5000):
            saved=projects.save_named(self.root,self.data,'Near metadata guard',True)
            self.assertNotIn('project_preview',saved)
            destination=self.root/'near-limit.tstudio'
            projects.portable(self.root,self.data,destination)
        with zipfile.ZipFile(destination) as archive:
            state=json.loads(archive.read('project.json'))['state']
            self.assertNotIn('project_preview',state)
            self.assertTrue(state['job_items'])


if __name__=='__main__':unittest.main()
