"""Confirmed example learning, full footprints, atomic edits and persistence.

Constructed shapes establish implementation behaviour, not field recognition.
The optional measured-crop assessment is recorded separately by validation.
"""
from pathlib import Path
from types import SimpleNamespace
import copy,hashlib,json,sqlite3,tempfile,unittest
from unittest import mock
import numpy as np
import compact_shapes as compact
import shape_examples as examples
import shape_library as library
import shape_library_api as api
import shape_matching as matching
import shape_selection as selection
import projects,registration_pose,workspace_history,scan_edits,storage_inventory
from test_shape_matching_0214 import streamed
from test_lining_features_0182 import scene


def local_scene():
    x,y=np.meshgrid(np.arange(-.35,.351,.005),np.arange(-.30,.301,.005),indexing='ij')
    pocket=(abs(x)<.075)&(abs(y)<.035)
    z=.02*x*x+.015*y*y-np.where(pocket,.035,0)
    return np.c_[x.ravel(),y.ravel(),z.ravel()],pocket.ravel()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        work=self.root/'Project';work.mkdir()
        self.s=SimpleNamespace(WORK=work,WORKSPACE_CATALOGUE=self.root,STATE={'job_items':[]},JOB={'busy':False},SELECTION=None,progress=lambda _:None)
        self.s.save=lambda:projects.atomic_json(work/'project.json',self.s.STATE)
        self.s.save();self.ident=library.class_action(self.s,dict(action='create_type',name='Pocket 1'))['class_id']

    def crop(self,depth=.04,shape=(9,17),name='crop'):
        t=dict(mask=np.ones(shape,bool),depth=np.full(shape,depth),grid_mm=5.,example_sign=1,size_m=(np.asarray(shape)*.005).tolist())
        rr,cc=np.indices(shape);p=np.c_[rr.ravel()*.005,cc.ravel()*.005,np.full(rr.size,-depth)]
        folder=self.root/(name+'-'+str(depth));examples.write(folder,p,np.array([[0,0,0],[1,0,0],[0,1,0]]),t,dict(kind='fixture'))
        return folder

    def record(self,action,fn):
        with workspace_history.request(self.s,'/api/shape-library'):
            workspace_history.configure('/api/shape-library',{'action':action});return fn()

    def add(self,folder,label='positive'):
        return self.record('capture',lambda:library.transaction(self.s,[folder],self.ident,label))

    def total(self,label='positive'):
        return next(c for c in library.catalogue(self.s)['classes'] if c['id']==self.ident)[label]

    def scan(self,p):
        f=self.s.WORK/'scan.xyz64';p.astype('<f8').tofile(f);a=f.with_suffix('.attr32');np.ones((len(p),4),'<f4').tofile(a)
        m=dict(name='Measured tunnel',file=str(f),attributes_file=str(a),count=len(p),minimum=p.min(0).tolist(),maximum=p.max(0).tolist(),attribute_counts=[len(p)]*4)
        item=dict(id='source',name=m['name'],kind='scan',meta=m,visible=True,edit_history=[],redo_history=[],deleted_groups=[])
        self.s.STATE['job_items']=[item];self.s.save();return item

    def test_library_survives_source_and_bin_deletion_and_reopens_in_other_project(self):
        folder=self.crop();saved=self.add(folder);shutil=__import__('shutil');shutil.rmtree(folder)
        self.s.STATE.clear();self.s.STATE.update(job_items=[]);self.s.save()
        other=copy.copy(self.s);other.WORK=self.root/'Other';other.WORK.mkdir();other.STATE={'job_items':[]}
        self.assertEqual(library.catalogue(other)['classes'][0]['positive'],1)
        view=library.preview_example(other,saved['examples'][0]);self.assertGreater(view['count'],150)
        bank=library.bank(other,[self.ident]);self.assertTrue(library.load_template(bank['entries'][0],5)['mask'].all())
        self.assertFalse(str(self.s.WORK) in json.dumps(bank))

    def test_duplicate_confirmation_does_not_duplicate_geometry(self):
        folder=self.crop();a=self.add(folder);b=self.add(folder)
        self.assertEqual(a['examples'],b['examples']);self.assertEqual(self.total(),1);self.assertEqual(b['added'],0)
        self.assertEqual(len(list((library.root(self.s)/'examples').iterdir())),1)

    def test_positive_and_negative_examples_have_separate_labels(self):
        folder=self.crop();self.add(folder);self.add(folder,'negative')
        self.assertEqual((self.total(),self.total('negative')),(1,1))
        self.assertEqual(len(library.bank(self.s,[self.ident])['entries']),2)

    def test_undo_redo_reverses_teaching_and_keeps_immutable_assets(self):
        self.add(self.crop());self.assertEqual(self.total(),1)
        workspace_history.apply(self.s);self.assertEqual(self.total(),0)
        workspace_history.apply(self.s,True);self.assertEqual(self.total(),1)
        self.assertEqual(len(list((library.root(self.s)/'examples').iterdir())),1)

    def test_undo_one_project_preserves_another_projects_confirmation(self):
        folder=self.crop();self.add(folder)
        other=copy.copy(self.s);other.WORK=self.root/'Other';other.WORK.mkdir();other.STATE={'job_items':[]};other.save=lambda:projects.atomic_json(other.WORK/'project.json',other.STATE)
        other.save();library.transaction(other,[folder],self.ident)
        workspace_history.apply(self.s);self.assertEqual(self.total(),1)

    def test_failed_project_save_rolls_back_teaching_and_staged_labels(self):
        before=copy.deepcopy(self.s.STATE)
        with mock.patch.object(self.s,'save',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError,'disk full'):self.add(self.crop())
        self.assertEqual(self.s.STATE,before);self.assertEqual(self.total(),0)
        self.assertEqual(list((library.root(self.s)/'examples').iterdir()),[])

    def test_crash_between_project_save_and_label_activation_recovers(self):
        real=library._recover;calls=0
        def stop(db):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('interrupted after project save')
            return real(db)
        with mock.patch.object(library,'_recover',side_effect=stop):
            with self.assertRaisesRegex(OSError,'interrupted'):self.add(self.crop())
        self.assertEqual(self.total(),1)
        db=sqlite3.connect(library.root(self.s)/'library.sqlite');self.assertEqual(db.execute('SELECT count(*) FROM pending').fetchone()[0],0);db.close()

    def test_forget_example_is_reversible(self):
        saved=self.add(self.crop());self.record('forget_example',lambda:library.transaction(self.s,[],self.ident,deactivate=saved['examples']))
        self.assertEqual(self.total(),0);workspace_history.apply(self.s);self.assertEqual(self.total(),1)

    def test_invalid_labels_names_ids_and_damaged_descriptor_fail_clearly(self):
        for name in ('','a'*81,'a\nb'):
            with self.assertRaises(ValueError):library.class_action(self.s,dict(action='create_type',name=name))
        with self.assertRaises(ValueError):library.bank(self.s,['../../outside'])
        with self.assertRaises(ValueError):library.transaction(self.s,[self.crop()],self.ident,'automatic')
        self.add(self.crop());entry=library.bank(self.s,[self.ident])['entries'][0]
        Path(entry['path']).write_bytes(b'bad descriptor')
        with self.assertRaises((ValueError,__import__('zipfile').BadZipFile)):library.load_template(entry,5)

    def test_storage_cleanup_protects_all_library_assets_including_inactive(self):
        saved=self.add(self.crop());library.transaction(self.s,[],self.ident,deactivate=saved['examples'])
        for path in (library.root(self.s)/'examples').rglob('*'):
            if path.is_file():
                self.assertFalse(storage_inventory.eligible(self.root,path))
                self.assertEqual(storage_inventory.describe(self.root,path,path.stat(),set(),False,False,False,False)[0],'library')

    def test_capture_separates_object_from_wall_and_keeps_exact_world_observations(self):
        p,hole=local_scene();item=self.scan(p);original=Path(item['meta']['file']).read_bytes()
        spec=dict(mode='box',minimum=[-.10,-.06,-.1],maximum=[.10,.06,.1]);folder=self.root/'manual'
        metadata=examples.capture(item,spec,folder,matching.settings({'side':'recess','grid_mm':5}),self.s.progress)
        q=np.fromfile(folder/'object.xyz64','<f8').reshape(-1,3);w=np.fromfile(folder/'context.xyz64','<f8').reshape(-1,3)
        self.assertEqual(len(q),hole.sum());self.assertTrue((q[:,2]<-.03).all());self.assertTrue((w[:,2]>-.001).all())
        self.assertTrue(set(map(tuple,q)).issubset(set(map(tuple,p))));self.assertTrue(metadata['reference']['converged'])
        self.assertEqual(Path(item['meta']['file']).read_bytes(),original)

    def test_capture_cancelled_before_commit_preserves_library_and_source(self):
        p,_=local_scene();item=self.scan(p);self.s.progress=lambda _:(_ for _ in ()).throw(ValueError('stopped'))
        with self.assertRaisesRegex(ValueError,'stopped'):
            api.capture(self.s,dict(confirmed=True,source_id=item['id'],class_id=self.ident,example=dict(mode='box',minimum=[-.1,-.06,-.1],maximum=[.1,.06,.1]),settings={'side':'recess'}))
        self.assertEqual(self.total(),0);self.assertFalse(list(self.s.WORK.glob('shape-teaching-*')))

    def test_all_original_pocket_points_selected_not_just_template_intersection(self):
        m,p,_=scene(empty=True,bolts=True);expected=np.flatnonzero(np.linalg.norm(p[:,1:],axis=1)>1.02)
        with tempfile.TemporaryDirectory() as tmp:
            r=matching.run(streamed(p),dict(mode='box',minimum=[6.8,.8,-.2],maximum=[7.2,1.2,.2]),tmp,{'side':'recess'},model=m)
            np.testing.assert_array_equal(np.fromfile(r['point_ids_file'],'<i8'),expected)

    def test_repeat_search_skips_only_confirmed_pockets_and_undo_restores_them(self):
        model,points,_=scene(empty=True,bolts=True)
        item=self.scan(points)
        request=dict(action='preview',source_id=item['id'],
                     example=dict(mode='box',minimum=[6.8,.8,-.2],maximum=[7.2,1.2,.2]),
                     settings={'side':'recess'})
        run=matching.run
        with mock.patch.object(matching,'run',side_effect=lambda *a,**k:run(*a,model=model,**k)):
            first=selection.preview(self.s,request)
            groups=selection.public(self.s,include_points=False)['groups']
            self.assertEqual(len(groups),2)
            self.assertFalse((Path(first['folder'])/'descriptors').exists())
            selection.choose(self.s,dict(action='select',operation='toggle',selected=False,group_ids=[groups[1]['id']],
                                         preview_id=first['id'],revision=first['revision']))
            body=dict(action='teach_selected',confirmed=True,class_id=self.ident,
                      preview_id=first['id'],revision=first['revision'])
            self.record('teach_selected',lambda:api.teach(self.s,body))
            self.s.STATE=json.loads((self.s.WORK/'project.json').read_text())
            second=selection.preview(self.s,request)
            self.assertEqual(second['reviewed_skipped'],1)
            remaining=selection.public(self.s,include_points=False)['groups']
            self.assertEqual([g['station_m'] for g in remaining],[groups[1]['station_m']])
            included=selection.preview(self.s,dict(request,settings={'side':'recess','include_reviewed':True}))
            self.assertEqual(included['count'],first['count'])
            workspace_history.apply(self.s)
            restored=selection.preview(self.s,request)
            self.assertEqual(restored['count'],first['count'])
            self.assertEqual(restored['reviewed_skipped'],0)

    def test_nearby_wall_crops_share_reads_and_keep_exact_samples(self):
        points,_=local_scene()
        item=self.scan(points)
        crops=[(np.array([x-.03,-.04,-.02]),np.array([x+.03,.04,.02])) for x in (0,.01,.02)]
        read=examples.RowFile.read
        starts=[]
        def track(reader,start,count):
            if reader.path==Path(item['meta']['file']):
                starts.append(start)
            return read(reader,start,count)
        with mock.patch.object(examples.RowFile,'read',track):
            expected=[examples.context(item,low,high,self.s.progress) for low,high in crops]
            separate_reads=len(starts)
            starts.clear()
            actual=examples.context_many(item,crops,self.s.progress)
        self.assertTrue(starts)
        self.assertLess(len(starts),separate_reads)
        self.assertEqual(len(starts),len(set(starts)))
        for a,b in zip(actual,expected):
            np.testing.assert_array_equal(a,b)

    def test_remove_and_teach_is_one_undo_and_retains_independent_geometry(self):
        m,p,_=scene(empty=True,bolts=True);item=self.scan(p);original=Path(item['meta']['file']).read_bytes();folder=self.s.WORK/'review';folder.mkdir()
        r=matching.run(streamed(p),dict(mode='box',minimum=[6.8,.8,-.2],maximum=[7.2,1.2,.2]),folder,{'side':'recess'},model=m)
        self.s.SHAPE_SELECTION={**r,'id':'preview','revision':'one','mode':'shape','source_id':item['id'],'source_name':item['name'],'source_fingerprint':selection.feature_selection.fingerprint(item),'folder':str(folder),'settings':matching.settings({'side':'recess'})}
        body=dict(action='delete',confirmed=True,teach=True,class_id=self.ident,preview_id='preview',revision='one')
        with workspace_history.request(self.s,'/api/shape-selection'):
            workspace_history.configure('/api/shape-selection',body);result=selection.edit(self.s,body)
        self.assertEqual(self.total(),2);self.assertEqual(result['count'],100);self.assertEqual(result['taught'],2)
        self.assertEqual(int(np.fromfile(self.s.STATE['job_items'][0]['meta']['mask_file'],'u1').sum()),100)
        workspace_history.apply(self.s);self.assertEqual(self.total(),0);self.assertFalse(self.s.STATE['job_items'][0]['meta'].get('mask_file'))
        workspace_history.apply(self.s,True);self.assertEqual(self.total(),2)
        self.assertEqual(Path(item['meta']['file']).read_bytes(),original)

    def test_multiple_examples_expand_depth_coverage_and_negatives_veto_false_match(self):
        def search():
            bank=library.bank(self.s,[self.ident]);tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
            e=matching.Evidence(Path(tmp.name)/'evidence.sqlite')
            for r,depth in [(20,.04),(70,.09),(120,.05)]:
                rr,cc=np.indices((9,17));rr=rr.ravel()+r;cc=cc.ravel()+20
                e.accumulate(rr,cc,np.full(len(rr),depth),np.zeros(len(rr),bool))
            cfg=matching.settings({'grid_mm':5,'similarity_percent':75,'depth_tolerance_mm':20})
            compact.match(e,None,cfg,200,self.s.progress,lambda:False,matching.emit,bank=bank,folder=tmp.name)
            found=[round(t/.005) for (t,) in e.db.execute('SELECT station FROM matches ORDER BY station')];e.close();return found
        self.add(self.crop(.04));self.assertEqual(len(search()),2)
        self.add(self.crop(.09));self.assertEqual(len(search()),3)
        self.add(self.crop(.05),'negative');found=search();self.assertEqual(len(found),2);self.assertLess(found[0],40);self.assertLess(found[1],100)

    def test_oblique_rotation_and_size_variation_keep_complete_footprints(self):
        mask=np.zeros((41,41),bool);rr,cc=np.indices(mask.shape);mask[(abs(rr-20)<5)&(abs(cc-20)<13)]=True
        from scipy.ndimage import rotate,zoom
        taught=compact.descriptor(mask,np.full(mask.shape,.04));cfg=matching.settings({'depth_tolerance_mm':20})
        turned=rotate(mask.astype('u1'),37,reshape=True,order=0)>0
        candidate=compact.descriptor(turned,np.full(turned.shape,.04))
        self.assertGreater(compact.agreement(taught,candidate,cfg),.85)
        bigger=zoom(mask.astype('u1'),1.15,order=0)>0
        self.assertGreater(compact.agreement(taught,compact.descriptor(bigger,np.full(bigger.shape,.04)),cfg),.85)

    def test_rotated_component_at_angular_and_station_tile_boundaries_is_owned_once(self):
        from scipy.ndimage import rotate
        mask=np.ones((9,25),bool);turned=rotate(mask.astype('u1'),37,reshape=True,order=0)>0
        t=dict(mask=mask,depth=np.full(mask.shape,.04),long=False)
        cfg=matching.settings({'grid_mm':5,'similarity_percent':75})
        with tempfile.TemporaryDirectory() as tmp:
            e=matching.Evidence(Path(tmp)/'evidence.sqlite');rr,cc=np.where(turned)
            rr=rr+250;cc=(cc-turned.shape[1]//2)%200
            e.accumulate(rr,cc,np.full(len(rr),.04),np.zeros(len(rr),bool))
            compact.match(e,t,cfg,200,self.s.progress,lambda:False,matching.emit)
            self.assertEqual(e.db.execute('SELECT count(*) FROM matches').fetchone()[0],1)
            self.assertEqual(e.db.execute('SELECT count(*) FROM cells WHERE gid>0').fetchone()[0],turned.sum());e.close()

    def test_manual_capture_preserves_pose_and_honours_existing_deletions(self):
        p,hole=local_scene();item=self.scan(p);mask=np.zeros(len(p),'u1');mask[np.flatnonzero(hole)[::3]]=1
        mp=self.s.WORK/'existing.mask';mask.tofile(mp);item['meta']['mask_file']=str(mp)
        registration_pose.set_pose(item['meta'],np.eye(3),[100,20,0])
        folder=self.root/'posed-crop';examples.capture(item,dict(mode='box',minimum=[99.9,19.94,-.1],maximum=[100.1,20.06,.1]),folder,matching.settings({'side':'recess','grid_mm':5}),self.s.progress)
        q=np.fromfile(folder/'object.xyz64','<f8').reshape(-1,3)
        np.testing.assert_allclose(q,p[hole&(mask==0)]+[100,20,0],atol=1e-12)
        np.testing.assert_array_equal(np.fromfile(mp,'u1'),mask)

    def test_combined_edit_save_failure_restores_mask_and_library(self):
        p,_=local_scene();item=self.scan(p);before=copy.deepcopy(self.s.STATE);folder=self.crop()
        with mock.patch.object(self.s,'save',side_effect=OSError('save refused')):
            with self.assertRaisesRegex(OSError,'save refused'):
                library.transaction(self.s,[folder],self.ident,mutate=lambda:scan_edits.apply(self.s,item,'delete_selection',dict(mode='box',minimum=[-.1,-.1,-1],maximum=[.1,.1,1]),commit=False))
        self.assertEqual(self.s.STATE,before);self.assertEqual(self.total(),0)
        np.testing.assert_array_equal(np.fromfile(item['meta']['file'],'<f8').reshape(-1,3),p)

    def test_cancellation_between_example_copies_rolls_back_the_entire_teaching(self):
        folders=[self.crop(.04),self.crop(.09)]
        def stop(message):
            if '2/2' in message:raise ValueError('stopped')
        self.s.progress=stop
        with self.assertRaisesRegex(ValueError,'stopped'):library.transaction(self.s,folders,self.ident)
        self.assertEqual(self.total(),0);self.assertFalse(list((library.root(self.s)/'examples').iterdir()))

    def test_screen_capture_uses_the_visible_layer_in_front_of_the_opposite_wall(self):
        p,hole=local_scene();back=p.copy();back[:,2]-=2
        item=self.scan(np.vstack([p,back]));folder=self.root/'front'
        # Looking along -Z: the nearest measured layer has minimum forward depth.
        spec=dict(mode='screen_polygon',target=[0,0,0],right=[1,0,0],up=[0,1,0],forward=[0,0,-1],scale=1,aspect=1,polygon=[[-.1,-.06],[.1,-.06],[.1,.06],[-.1,.06]],depth_min=-.1,depth_max=2.1)
        examples.capture(item,spec,folder,matching.settings({'side':'recess','grid_mm':5}),self.s.progress)
        q=np.fromfile(folder/'object.xyz64','<f8').reshape(-1,3)
        self.assertEqual(len(q),hole.sum());self.assertTrue((q[:,2]>-.1).all())

    def test_fine_and_coarse_descriptor_grids_preserve_depth_at_the_object_boundary(self):
        folder=self.crop();self.add(folder);entry=library.bank(self.s,[self.ident])['entries'][0]
        for grid in (2.5,10):
            t=library.load_template(entry,grid)
            np.testing.assert_allclose(t['depth'][t['mask']],.04,atol=1e-12)

    def test_teach_existing_separated_scan_as_multiple_objects_without_reselecting(self):
        import shape_separated
        p,hole=local_scene();lining=np.vstack([p+[-.5,0,0],p+[.5,0,0]])
        reference=self.scan(lining);isolated=lining[np.tile(hole,2)]
        f=self.s.WORK/'separated.xyz64';isolated.astype('<f8').tofile(f)
        source=copy.deepcopy(reference);source.update(id='pockets',name='Already separated pockets');source['meta'].update(file=str(f),count=len(isolated),minimum=isolated.min(0).tolist(),maximum=isolated.max(0).tolist(),attributes_file=None,attribute_counts=[0]*4)
        self.s.STATE['job_items'].append(source);self.s.save()
        before={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in (f,Path(reference['meta']['file']))}
        body=dict(action='capture_scan',confirmed=True,source_id='source',example_source_id='pockets',class_id=self.ident,settings={'side':'recess','grid_mm':5})
        self.record('capture_scan',lambda:shape_separated.capture(self.s,body))
        self.assertEqual(self.total(),2);self.assertEqual(self.s.SHAPE_LIBRARY_IMPORT['usable'],2);self.assertEqual(self.s.SHAPE_LIBRARY_IMPORT['skipped'],[])
        for path,digest in before.items():self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(),digest)
        workspace_history.apply(self.s);self.assertEqual(self.total(),0);self.assertEqual(len(self.s.STATE['job_items']),2)

    def test_teaching_from_separated_scan_requires_confirmation_and_main_wall_reference(self):
        import shape_separated
        p,_=local_scene();self.scan(p)
        for body in (dict(confirmed=False),dict(confirmed=True,source_id='source',example_source_id='source')):
            with self.assertRaises(ValueError):shape_separated.capture(self.s,body)
        self.assertEqual(self.total(),0)

    def test_failed_undo_save_does_not_change_confirmed_labels(self):
        self.add(self.crop());before=copy.deepcopy(self.s.STATE)
        with mock.patch.object(self.s,'save',side_effect=OSError('undo save failed')):
            with self.assertRaisesRegex(OSError,'undo save failed'):workspace_history.apply(self.s)
        self.assertEqual(self.s.STATE,before);self.assertEqual(self.total(),1)
        self.assertEqual(workspace_history.status(self.s)['undo_count'],1)

    def test_abandoned_pending_change_uses_saved_project_head(self):
        saved=self.add(self.crop());db=library._connect(self.s)
        try:
            claim=db.execute('SELECT id FROM claims WHERE example_id=?',(saved['examples'][0],)).fetchone()[0]
            library._stage(self.s,db,[[claim,1,0]],copy.deepcopy(self.s.STATE))
        finally:db.close()
        self.assertEqual(self.total(),1)

    def test_a_second_process_cannot_recover_another_processes_live_transaction(self):
        import subprocess,sys
        db=library._connect(self.s)
        try:
            code='''import sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\nsys.path.insert(0,sys.argv[1])\nimport shape_library\ns=SimpleNamespace(WORK=Path(sys.argv[2]),WORKSPACE_CATALOGUE=Path(sys.argv[3]))\ntry:shape_library.catalogue(s)\nexcept ValueError as e:\n assert 'Another Tunnel Studio' in str(e);print('blocked safely')\nelse:raise AssertionError('A live library action was not protected')\n'''
            result=subprocess.run([sys.executable,'-B','-c',code,str(Path(__file__).resolve().parent),str(self.s.WORK),str(self.root)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('blocked safely',result.stdout)
        finally:db.close()

    def test_either_side_teaching_does_not_guess_a_pocket_sign_from_camera_orientation(self):
        p,hole=local_scene();item=self.scan(p);folder=self.root/'either'
        examples.capture(item,dict(mode='box',minimum=[-.1,-.06,-.1],maximum=[.1,.06,.1]),folder,matching.settings({'side':'either','grid_mm':5}),self.s.progress)
        self.add(folder);bank=library.bank(self.s,[self.ident]);self.assertEqual(bank['entries'][0]['side'],'either')
        with tempfile.TemporaryDirectory() as tmp:
            e=matching.Evidence(Path(tmp)/'evidence.sqlite');t=library.load_template(bank['entries'][0],5);rr,cc=np.where(t['mask'])
            for side,r in [(1,20),(-1,120)]:e.accumulate(rr+r,cc+20,t['depth'][t['mask']]*side,np.zeros(len(rr),bool))
            compact.match(e,None,matching.settings({'grid_mm':5}),200,self.s.progress,lambda:False,matching.emit,bank=bank,folder=tmp)
            self.assertEqual(e.db.execute('SELECT count(*) FROM matches').fetchone()[0],2);e.close()

    def test_named_projects_sharing_a_workspace_keep_independent_confirmations(self):
        self.s.STATE['project_id']='project-one';self.s.save();folder=self.crop();self.add(folder)
        other=copy.copy(self.s);other.STATE={'job_items':[],'project_id':'project-two'};other.save=lambda:projects.atomic_json(other.WORK/'project.json',other.STATE)
        other.save();library.transaction(other,[folder],self.ident)
        self.s.save();workspace_history.apply(self.s);self.assertEqual(self.total(),1)

    def test_startup_recovers_teaching_before_retiring_the_prior_autosave(self):
        real=library._recover;calls=0
        def stop(db):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('process ended after save')
            return real(db)
        with mock.patch.object(library,'_recover',side_effect=stop):
            with self.assertRaises(OSError):self.add(self.crop())
        library.recover_pending(self.s)
        self.s.STATE.clear();self.s.STATE['job_items']=[];self.s.save()
        self.assertEqual(self.total(),1)

    def test_production_startup_orders_library_recovery_before_blank_workspace_save(self):
        import server
        events=[]
        with mock.patch.object(library,'recover_pending',side_effect=lambda _:events.append('library')),mock.patch.object(server.session_start,'begin',side_effect=lambda *a:events.append('workspace')):
            server.start_empty()
        self.assertEqual(events,['library','workspace'])


if __name__=='__main__':unittest.main()
