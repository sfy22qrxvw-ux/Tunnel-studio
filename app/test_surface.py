# Copyright (c) 2026 Damian Gulczynski.
"""Numerical and data-integrity tests for the independent SMRG implementation."""
from pathlib import Path
from types import SimpleNamespace
import copy, json, tempfile, unittest
import numpy as np
import editing, exchange, pointio, projects, surface_api, workflows
from surface_demo import generate
from surface_extraction import Settings, metric, features, growth_allowed, hull_vertices, recover, extract as extract_raw, prune_graph


def extract(points,settings=None,**kwargs):
    # This suite targets SMRG; the user-facing default is GuSAC.
    return extract_raw(points,{'method':'smrg',**(settings or {})},**kwargs)


class SurfaceMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.root = Path(cls.tmp.name)
        _,_,cls.points,cls.truth = generate(cls.root,spacing=.09)
        cls.r = extract(cls.points)
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_metric_numeric_and_opposite_normal_invariance(self):
        c=Settings(); p=np.array([0,0,0]); q=np.array([.1,0,.02]); n=np.array([0,0,1])
        expected=.4*np.sqrt(.1**2+.02**2)/.2+.6*.02
        self.assertAlmostEqual(float(metric(p,q,n,n,c)),expected)
        self.assertAlmostEqual(float(metric(p,q,-n,n,c)),expected)

    def test_pca_planar_patch_has_low_saliency_and_zero_curvature(self):
        x,y=np.meshgrid(np.arange(-2,3),np.arange(-2,3)); p=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        f=features(p,np.zeros(len(p),int),np.ones(len(p)))
        self.assertTrue(f['low'][0]); self.assertEqual(f['curvature'][0],0)
        self.assertAlmostEqual(f['planarity'][0],.5); self.assertAlmostEqual(f['linearity'][0],0)
        self.assertAlmostEqual(abs(f['normal'][0,2]),1)

    def test_three_growth_cases_and_directional_equation(self):
        c=Settings(); ni=np.array([0,0,1.]); nj=ni.copy(); ci=np.zeros(3); cj=np.array([1,0,.05])
        self.assertTrue(growth_allowed(True,True,ni,nj,ci,cj,c))
        self.assertTrue(growth_allowed(True,False,ni,nj,ci,cj,c))
        self.assertFalse(growth_allowed(False,True,nj,ni,cj,ci,c))
        self.assertTrue(growth_allowed(False,False,ni,nj,ci,cj,c))
        c.convexity_mode='symmetric'
        self.assertEqual(growth_allowed(True,False,ni,nj,ci,cj,c),growth_allowed(False,True,nj,ni,cj,ci,c))
        # Large normal offset prevents LOW/LOW growth, even with parallel normals.
        self.assertFalse(growth_allowed(True,True,ni,nj,ci,np.array([0,0,1.]),c))

    def test_hull_degeneracies_and_overlap_recovery(self):
        plane=np.array([[x,y,0] for x in (0,1,2) for y in (0,1,2)],float)
        self.assertEqual(len(hull_vertices(plane)),4)
        self.assertEqual(len(hull_vertices(np.c_[np.arange(8),np.zeros((8,2))])),2)
        pts=np.vstack([plane,plane+[0,0,.05],plane+[0,0,5]])
        kept,overlap=recover(pts,np.repeat([0,1,2],len(plane)),np.full(len(pts),.1),Settings(),lambda s:None)
        self.assertEqual(set(kept),{0,1});self.assertEqual(overlap[1],1);self.assertEqual(overlap[2],0)

    def test_mad_prunes_an_outlying_neighbour(self):
        centre=np.array([[0,0,0],[-1,0,0],[1,0,0],[0,-1,0],[0,1,0],[.1,.2,.5]],float)
        f={'centre':centre};edges=np.c_[np.zeros(5,dtype=int),np.arange(1,6)]
        retained,count=prune_graph(f,edges,Settings(),lambda s:None)
        self.assertGreater(count,0);self.assertFalse(any(tuple(e)==(0,5) for e in retained))

    def test_synthetic_intersection_with_obstructions(self):
        predicted=np.isin(self.r['labels'],self.r['kept']);truth=self.truth.astype(bool)
        tp=int((predicted&truth).sum());fp=int((predicted&~truth).sum());fn=int((~predicted&truth).sum())
        # This is a regression bound for our generated scene, not real-scan accuracy.
        self.assertGreater(tp/(tp+fp),.97);self.assertGreater(tp/(tp+fn),.90)
        self.assertEqual(len(self.r['labels']),len(self.points)); self.assertEqual(sum(c['points'] for c in self.r['clusters'])+self.r['statistics']['noise_points'],len(self.points))
        print(f'\nSynthetic intersection only: N={len(truth)} TP={tp} FP={fp} FN={fn} IoU={tp/(tp+fp+fn):.6f} precision={tp/(tp+fp):.6f} recall={tp/(tp+fn):.6f}')

    def test_translation_and_duplicate_point_identity(self):
        # Binary-exact coordinates avoid manufacturing different measurement round-off on translation.
        x,y=np.meshgrid(np.arange(25)/16,np.arange(25)/16)
        p=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        a=extract(p,{'resolution':.25}); b=extract(p+[200000,375000,100],{'resolution':.25})
        np.testing.assert_array_equal(a['labels'],b['labels']); self.assertEqual(a['kept'],b['kept'])
        duplicate=extract(np.vstack([p,p[:10]]),{'resolution':.25})
        self.assertEqual(duplicate['statistics']['duplicate_locations'],10)
        np.testing.assert_array_equal(duplicate['labels'][-10:],duplicate['labels'][:10])

    def test_validation_cap_no_sampling_and_cancellation(self):
        for setting in [{'resolution':0},{'normal_neighbours':7.5},{'offset':float('nan')},{'graph_mode':'unknown'},{'repair':1},{'interior':[1,2]}]:
            with self.subTest(setting=setting),self.assertRaises(ValueError):Settings.read(setting)
        with self.assertRaisesRegex(ValueError,'batch limit'):extract(self.points,{'max_points':1000})
        with self.assertRaisesRegex(ValueError,'finite'):extract(np.full((60,3),np.nan))
        with self.assertRaisesRegex(ValueError,'cancelled'):extract(self.points,cancel=lambda:True)
        with self.assertRaisesRegex(ValueError,'distinct'):extract(np.zeros((60,3)))


class SurfaceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        x,y=np.meshgrid(np.arange(20)/16,np.arange(20)/16);self.points=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        p=self.root/'scan.csv';self.attrs=np.c_[np.arange(len(self.points)),np.tile([100,150,200],(len(self.points),1))]
        np.savetxt(p,np.c_[self.points,self.attrs],delimiter=',',header='e,n,h,intensity,red,green,blue',comments='',fmt='%.12f')
        self.meta=pointio.import_cloud(p,self.root/'scan.xyz64')
        state=workflows.empty();state['surface_source']=self.meta
        self.s=SimpleNamespace(STATE=state,WORK=self.root,SELECTION=None,JOB={'busy':False},progress=lambda s:None,save=lambda:None)
        surface_api.CANCEL.clear()

    def tearDown(self):self.tmp.cleanup()

    def run_surface(self):
        surface_api.run(self.s,{'source':'separate','settings':{'method':'smrg','resolution':.25},'selection':{'mode':'full'}})
        return self.s.STATE['surface_result']

    def crafted_result(self,source='survey'):
        meta=copy.deepcopy(self.meta);oldmask=self.root/'old.mask';mask=np.zeros(meta['count'],'u1');mask[0]=1;mask.tofile(oldmask);meta['mask_file']=str(oldmask)
        self.s.STATE['cloud']=meta
        ids=np.arange(50,150,dtype='<i8');labels=np.r_[np.zeros(40,'<i4'),np.ones(50,'<i4'),np.full(10,-1,'<i4')]
        idpath=self.root/'ids.surface_ids';lp=self.root/'labels.surface_labels';ids.tofile(idpath);labels.tofile(lp)
        r={'id':'test','revision':'initial','created_at':1,'source_kind':source,'source_meta':copy.deepcopy(meta),'point_ids_file':str(idpath),'labels_file':str(lp),'statistics':{'input_points':100,'kept_points':40,'noise_points':10},'clusters':[{'id':0,'points':40,'automatic_keep':True,'supervoxels':4,'centre':[0,0,0],'overlap':1},{'id':1,'points':50,'automatic_keep':False,'supervoxels':5,'centre':[1,1,0],'overlap':0}], 'kept':[0],'review_log':[],'settings':{},'selection':{'mode':'box','minimum':[0,0,-1],'maximum':[2,2,1]},'warnings':[],'method':'test','doi':'10.1038/s41598-026-73755-6','outputs':{}}
        self.s.STATE['surface_result']=r;return r

    def test_crop_respects_existing_mask_and_cap(self):
        r=self.crafted_result();ids,p,spec=surface_api.select_area(self.s,self.s.STATE['cloud'],'survey',{'mode':'box','minimum':[0,0,-.1],'maximum':[.6,.6,.1]},1000)
        self.assertNotIn(0,ids);self.assertTrue((p[:,:2]<=.6).all());self.assertEqual(len(ids),99)
        with self.assertRaisesRegex(ValueError,'compatibility cap'):surface_api.select_area(self.s,self.meta,'separate',{'mode':'full'},100)
        derived=np.c_[self.points[:,0],np.zeros((len(self.points),3))];dp=self.root/'derived';derived.astype('<f8').tofile(dp);self.s.STATE['derived']=str(dp)
        ids,p,_=surface_api.select_area(self.s,self.meta,'survey',{'mode':'chainage','start':.25,'end':.5},1000)
        self.assertEqual(len(ids),100);self.assertTrue(((p[:,0]>=.25)&(p[:,0]<=.5)).all())

    def test_apply_only_roi_undo_and_stale_result_guard(self):
        r=self.crafted_result();before=(self.root/'scan.xyz64').read_bytes()
        surface_api.apply(self.s,{'revision':r['revision'],'reviewed':True})
        mask=np.asarray(editing.mask_for(self.s.STATE['cloud']));expected=np.zeros(400,'u1');expected[0]=1;expected[90:150]=1
        np.testing.assert_array_equal(mask,expected);self.assertEqual(self.s.STATE['edits'][0]['count'],60)
        self.assertEqual((self.root/'scan.xyz64').read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'differs'):surface_api.apply(self.s,{'revision':r['revision'],'reviewed':True})
        self.s.STATE['cloud']['mask_file']=self.s.STATE['edits'][0]['before_mask']
        self.assertEqual(int(editing.mask_for(self.s.STATE['cloud']).sum()),1)
        self.assertTrue(surface_api.public_result(self.s,r)['can_apply'])

    def test_apply_rejects_non_boolean_review_confirmation(self):
        r=self.crafted_result()
        for reviewed in ('false', 1, False, None):
            with self.subTest(reviewed=reviewed), self.assertRaisesRegex(ValueError,'Review the current result'):
                surface_api.apply(self.s,{'revision':r['revision'],'reviewed':reviewed})
        self.assertEqual(self.s.STATE['cloud']['mask_file'],r['source_meta']['mask_file'])
        self.assertFalse(self.s.STATE['edits'])

    def test_point_undo_blocks_stale_cleaning_output_even_if_cached(self):
        import scan_edits, workspace_history
        r=self.crafted_result('separate')
        item=dict(id='source',kind='scan',name=self.meta['name'],meta=copy.deepcopy(r['source_meta']))
        self.s.STATE.update(job_items=[item],surface_source_item=item['id'],surface_source=copy.deepcopy(item['meta']))
        r.update(source_item_id=item['id'],source_fingerprint=surface_api.patch_fingerprint(item),
                 settings={'method':'irregular'},kept=[0,1])
        r['clusters'][1]['irregular_candidate']=True
        with workspace_history.request(self.s,'/api/surface-action'):
            workspace_history.configure('/api/surface-action',{'action':'delete_patches'})
            workspace_history.background(self.s,lambda:surface_api.delete_patches(
                self.s,{'revision':r['revision'],'clusters':[1]}))()
        after=self.s.STATE['surface_result']
        self.assertEqual(surface_api.filtered_cloud(self.s)['count'],40)
        scan_edits.apply(self.s,item,'undo_edit')
        self.assertTrue(surface_api.public_result(self.s,after)['source_changed'])
        with self.assertRaisesRegex(ValueError,'source scan changed'):
            surface_api.add_clean_scan(self.s,{'revision':after['revision'],'reviewed':True})
        with self.assertRaisesRegex(ValueError,'source scan changed'):
            surface_api.filtered_cloud(self.s,False)
        self.assertEqual(len(self.s.STATE['job_items']),1)
        scan_edits.apply(self.s,item,'redo_edit')
        self.assertFalse(surface_api.public_result(self.s,after)['source_changed'])
        workspace_history.apply(self.s)
        self.assertEqual(surface_api.filtered_cloud(self.s)['count'],90)

    def test_result_belongs_to_original_item_when_active_scan_changes(self):
        r=self.crafted_result()
        self.s.STATE['job_items']=[dict(id='source',kind='scan',meta=copy.deepcopy(r['source_meta']))]
        r['source_item_id']='source'
        self.s.STATE['cloud']={**r['source_meta'],'file':str(self.root/'another.xyz64')}
        self.assertFalse(surface_api.public_result(self.s,r)['source_changed'])
        self.assertEqual(surface_api.filtered_cloud(self.s)['count'],40)
        self.s.STATE['job_items'][0]['meta']['mask_file']=None
        self.assertTrue(surface_api.public_result(self.s,r)['source_changed'])
        with self.assertRaisesRegex(ValueError,'source scan changed'):
            surface_api.filtered_cloud(self.s)

    def test_review_export_keeps_coordinate_and_attribute_identity(self):
        r=self.crafted_result();surface_api.review(self.s,{'revision':'initial','kept':[1,-1]})
        with self.assertRaisesRegex(ValueError,'changed'):surface_api.review(self.s,{'revision':'initial','kept':[0]})
        meta=surface_api.filtered_cloud(self.s);p,a=pointio.arrays(meta)
        np.testing.assert_array_equal(p,self.points[90:150]);np.testing.assert_array_equal(a,self.attrs[90:150])
        self.assertEqual(meta['attribute_counts'],[60]*4);self.assertEqual(surface_api.filtered_cloud(self.s,False)['count'],40)
        self.assertEqual(len(self.s.STATE['surface_result']['review_log']),1)
        dest=self.root/'surface.e57';exchange.export_cloud(meta,dest,'e57');read=pointio.import_cloud(dest,self.root/'back.xyz64')
        xyz,_=pointio.arrays(read);np.testing.assert_allclose(xyz,p,rtol=0,atol=1e-10)

    def test_result_survives_failed_run_and_cancel(self):
        r=self.run_surface();ident=r['id']
        surface_api.CANCEL.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):self.run_surface()
        self.assertEqual(self.s.STATE['surface_result']['id'],ident)
        surface_api.CANCEL.clear()
        with self.assertRaisesRegex(ValueError,'50'):surface_api.run(self.s,{'source':'separate','selection':{'mode':'box','minimum':[500,500,500],'maximum':[501,501,501]}})
        self.assertEqual(self.s.STATE['surface_result']['id'],ident)

    def test_portable_project_preserves_result_review_and_truth(self):
        # The truth file is a first-class project asset, not an absolute path left behind.
        truth=self.root/'demo.truth8';np.ones(400,'u1').tofile(truth);self.meta.update(truth_file=str(truth),synthetic_surface_demo=True)
        r=self.run_surface();surface_api.review(self.s,{'revision':r['revision'],'kept':[r['clusters'][0]['id'],-1]})
        saved=projects.save_named(self.root,workflows.serial(self.s),'Surface project')
        copy_state=projects.save_named(self.root,saved,'Second',True);copy_state['surface_result']['kept']=[];projects.save_named(self.root,copy_state)
        self.assertTrue(projects.read_named(self.root,saved['project_id'])['surface_result']['kept'])
        dest=self.root/'portable.tstudio';projects.portable(self.root,saved,dest);opened=projects.unpack(self.root,dest);workflows.restore_data(self.s,opened)
        rr=self.s.STATE['surface_result'];self.assertNotEqual(rr['labels_file'],r['labels_file'])
        self.assertEqual(surface_api.metrics(rr)['scope'],'Synthetic selected area only; not a real-scan accuracy estimate')
        self.assertTrue(Path(rr['source_meta']['truth_file']).is_file());self.assertEqual(rr['kept'],saved['surface_result']['kept'])
        report,ctype,name=surface_api.report(self.s,'json');self.assertIsInstance(report,dict);json.dumps(report,allow_nan=False)
        text,_,_=surface_api.report(self.s,'html');self.assertIn('10.1038/s41598-026-73755-6',text)
        # Tampered point IDs must not reach an index operation.
        bad=copy.deepcopy(opened);np.full(400,500,dtype='<i8').tofile(bad['surface_result']['point_ids_file'])
        # A damaged optional analysis now follows explicit project recovery:
        # quarantine the unusable result, retain the original cloud/current mask,
        # and keep diagnostic metadata instead of aborting all valid assets.
        mask_path=self.root/'recovered-current.mask';mask=np.zeros(400,'u1');mask[[7,31]]=1;mask.tofile(mask_path)
        bad['surface_source']['mask_file']=str(mask_path)
        raw=Path(bad['surface_source']['file']).read_bytes();attrs=Path(bad['surface_source']['attributes_file']).read_bytes()
        workflows.restore_data(self.s,bad)
        self.assertIsNone(self.s.STATE['surface_result'])
        with self.assertRaises(ValueError):surface_api.result(self.s)
        restored=self.s.STATE['surface_source'];self.assertEqual(Path(restored['file']).read_bytes(),raw)
        self.assertEqual(Path(restored['attributes_file']).read_bytes(),attrs);self.assertEqual(Path(restored['mask_file']).read_bytes(),mask.tobytes())
        recovery=self.s.STATE['project_recovery'];self.assertTrue(any('identities' in warning for warning in recovery['warnings']))
        quarantined=next(row for row in recovery['unavailable'] if row['kind']=='cleaning_result')
        self.assertEqual(json.loads(quarantined['original_data_json'])['point_ids_file'],bad['surface_result']['point_ids_file'])

if __name__=='__main__':unittest.main()
