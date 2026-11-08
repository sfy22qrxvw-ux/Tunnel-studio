"""Crown/wall/invert matching, local support, bounded fits and display contrast.

Constructed geometry verifies implementation behaviour; it is not a field
recognition rate or a substitute for checking the user's measured wall data.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib,sqlite3,tempfile,unittest,warnings
from unittest import mock
import numpy as np
import compact_shapes,shape_examples,shape_library,shape_lining,shape_matching,projects,native_progress
import test_lining_features_0182 as lining_fixture
from test_shape_matching_0214 import streamed


def tunnel():
    station,angle=np.meshgrid(np.arange(.005,12.,.01),(np.arange(720)+.5)*2*np.pi/720,indexing='ij')
    radial=1.+.020*np.sin(2*np.pi*station/7)*np.maximum(0,np.sin(angle))**2
    truth=np.zeros(station.shape,'i4')
    for k in range(8):
        t=1.5+k;a=(k+.5)*np.pi/4;dt=station-t;arc=(angle-a+np.pi)%(2*np.pi)-np.pi
        mask=(abs(dt)<.075)&(abs(arc)<.045)&~((dt>.015)&(arc>.005))
        radial[mask]+=.04+.008*dt[mask]/.075+.006*arc[mask]/.045;truth[mask]=k+1
    return np.c_[station.ravel(),(radial*np.cos(angle)).ravel(),(radial*np.sin(angle)).ravel()],truth.ravel()


class CircumferenceMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(tmp.cleanup);cls.root=Path(tmp.name);work=cls.root/'Project';work.mkdir()
        cls.points,cls.truth=tunnel();path=work/'scan.xyz64';cls.points.astype('<f8').tofile(path)
        item=dict(id='source',name='Measured tunnel',kind='scan',visible=True,meta=dict(file=str(path),count=len(cls.points),minimum=cls.points.min(0).tolist(),maximum=cls.points.max(0).tolist()))
        cls.server=SimpleNamespace(WORK=work,WORKSPACE_CATALOGUE=cls.root,STATE={'job_items':[item]},JOB={'busy':False},progress=lambda _:None)
        cls.server.save=lambda:projects.atomic_json(work/'project.json',cls.server.STATE);cls.server.save()
        cid=shape_library.class_action(cls.server,dict(action='create_type',name='Pocket 1'))['class_id']
        cls.cfg=shape_matching.settings({'side':'recess','grid_mm':5,'similarity_percent':65})
        crop=cls.root/'crop';shape_examples.capture_points(item,cls.points[cls.truth==6],crop,cls.cfg,cls.server.progress)
        shape_library.transaction(cls.server,[crop],cid);cls.bank=shape_library.bank(cls.server,[cid]);cls.path=path;cls.before=hashlib.sha256(path.read_bytes()).hexdigest();cls.results={};cls.models={}

    @classmethod
    def solve(cls,reference):
        if reference not in cls.results:
            folder=cls.root/reference;folder.mkdir();real=shape_matching.candidates
            def tracked(points,model,cfg,**kwargs):cls.models[reference]=model;return real(points,model,cfg,**kwargs)
            with mock.patch.object(shape_matching,'candidates',tracked):
                cls.results[reference]=shape_matching.run(streamed(cls.points),None,folder,{**cls.cfg,'lining_reference':reference},bank=cls.bank)
        return cls.results[reference]

    def test_local_wall_recovers_the_upper_pocket_missed_by_the_old_longitudinal_reference(self):
        old=self.solve('longitudinal');new=self.solve('local')
        before=np.fromfile(old['point_ids_file'],'<i8');after=np.fromfile(new['point_ids_file'],'<i8')
        self.assertFalse((self.truth[before]==2).any(),'The constructed crown change must reproduce the earlier miss')
        np.testing.assert_array_equal(after,np.flatnonzero(self.truth))
        self.assertEqual(set(self.truth[after]),set(range(1,9)))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.before)

    def test_full_circumference_diagnostics_account_for_every_original_observation(self):
        result=self.solve('local');rows=result['coverage']
        self.assertEqual(sum(r['processed'] for r in rows),len(self.points));self.assertEqual(sum(r['matched'] for r in rows),int((self.truth>0).sum()))
        self.assertTrue(all(r['processed']>=r['supported']>=r['offset_candidates']>=r['matched'] for r in rows))
        self.assertGreater(rows[0]['matched'],0);self.assertGreater(rows[4]['matched'],0)
        self.assertIn('quadratic',result['lining_reference']['method'])

    def test_local_reference_respects_registered_world_frames(self):
        self.solve('local');model=self.models['local'];r=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
        offset=np.array([177000.,398000.,85.]);posed=dict(model)
        for key in ('axis','u','v'):posed[key]=model[key]@r.T
        posed['reference']=model['reference']@r.T+offset
        q=self.points[self.truth>0];d,known=shape_lining.deviations(q,model);world,supported=shape_lining.deviations(q@r.T+offset,posed)
        np.testing.assert_allclose(d,world,atol=2e-10,rtol=0);np.testing.assert_array_equal(known,supported)

    def test_missing_crown_measurements_do_not_become_inferred_support(self):
        m=lining_fixture.model();sample=self.points[(self.truth==0)&(self.points[:,2]<-.2)][::7]
        reference=shape_lining.build(sample,m);crown=np.array([[6.,0.,1.04],[7.,0.,1.]])
        _,support=shape_lining.deviations(crown,reference);self.assertFalse(support.any())

    def test_degenerate_wall_context_is_unassessed_without_warnings_or_nan_predictions(self):
        m=lining_fixture.model();points=np.c_[np.linspace(0,12,500),np.ones(500),np.zeros(500)]
        with warnings.catch_warnings():
            warnings.simplefilter('error');reference=shape_lining.build(points,m)
        self.assertFalse(reference['shape_reference']['supported'].any());self.assertTrue(np.isfinite(reference['shape_reference']['target']).all())

    def test_patch_queries_are_bounded_and_cancel_preserves_source(self):
        sizes=[];real=shape_lining._patches
        def tracked(xy,z,queries,*args):sizes.append(len(queries));return real(xy,z,queries,*args)
        with mock.patch.object(shape_lining,'_patches',tracked):
            shape_lining.build(self.points[::20],lining_fixture.model())
        self.assertLessEqual(max(sizes),shape_lining.PATCH_BATCH)
        with self.assertRaisesRegex(ValueError,'cancel'):
            shape_lining.build(self.points[::20],lining_fixture.model(),cancel=lambda:True)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.before)

    def test_wall_rougher_than_a_submillimetre_offset_is_not_claimed_as_supported(self):
        t,a=np.meshgrid(np.linspace(0,12,100),(np.arange(90)+.5)*2*np.pi/90,indexing='ij')
        radius=1.+.001*np.where((np.indices(t.shape).sum(0)%2)==0,1,-1)
        points=np.c_[t.ravel(),(radius*np.cos(a)).ravel(),(radius*np.sin(a)).ravel()]
        reference=shape_lining.build(points,lining_fixture.model(),min_offset_mm=.5)
        self.assertAlmostEqual(reference['shape_reference']['fit_limit_mm'],.325)
        self.assertFalse(reference['shape_reference']['supported'].any())

    def test_invalid_reference_settings_and_samples_are_refused(self):
        for value in ('unknown',None):
            with self.assertRaises(ValueError):shape_matching.settings({'lining_reference':value})
        for sample in ([],np.ones((100,2)),np.full((100,3),np.nan)):
            with self.assertRaises(ValueError):shape_lining.build(sample,lining_fixture.model())
        with self.assertRaises(ValueError):shape_lining.build(np.ones((100,3)),lining_fixture.model(),float('nan'))


class OrientationAndNativeContrastTests(unittest.TestCase):
    def test_quarter_turn_of_square_depth_pattern_matches_when_rotations_are_allowed(self):
        row,col=np.indices((17,17));mask=np.ones((17,17),bool);depth=.04+.001*row+.0003*col
        taught=compact_shapes.descriptor(mask,depth);turned=compact_shapes.descriptor(mask,np.rot90(depth));cfg=shape_matching.settings({'depth_tolerance_mm':2})
        self.assertGreater(compact_shapes.agreement(taught,turned,cfg),.99)
        self.assertLess(compact_shapes.agreement(taught,turned,{**cfg,'rotations':False}),.1)

    def test_native_progress_uses_saved_theme_and_readable_text_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            from ui_preferences import AppearancePreferences
            pref=AppearancePreferences(Path(tmp)/'ui-preferences.json')
            for mode in ('light','dark'):
                pref.set(mode);palette=native_progress.palette(Path(tmp)/'job_status.json')
                def luminance(hex):
                    rgb=[int(hex[i:i+2],16)/255 for i in (1,3,5)];rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
                    return sum(a*b for a,b in zip(rgb,[.2126,.7152,.0722]))
                for text in ('text','muted'):
                    lo,hi=sorted([luminance(palette[text]),luminance(palette['background'])]);self.assertGreaterEqual((hi+.05)/(lo+.05),4.5)
                self.assertEqual(palette['background'],'#172333' if mode=='dark' else '#F3F7FB')


if __name__=='__main__':unittest.main()
