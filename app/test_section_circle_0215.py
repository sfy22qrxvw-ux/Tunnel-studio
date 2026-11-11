"""Local-circle cleaning on independently constructed curved tunnel surfaces."""
from pathlib import Path
import tempfile,unittest
from types import SimpleNamespace
from unittest import mock
import numpy as np
import section_circle as sc
import shape_selection as ss
import workspace_history
import test_shape_matching_0214 as shape_fixture
from core import Alignment


def curved(length=40.,bend_radius=45.,tube_radius=2.5,spacing=.08):
    stations=np.arange(.04,length,spacing);angles=np.arange(90)*2*np.pi/90
    s,a=np.meshgrid(stations,angles,indexing='ij');t=s/bend_radius
    centre=np.stack([bend_radius*np.sin(t),bend_radius*(1-np.cos(t)),np.zeros_like(t)],axis=-1)
    right=np.stack([-np.sin(t),np.cos(t),np.zeros_like(t)],axis=-1)
    normal=right*np.cos(a)[...,None];normal[:,:,2]=np.sin(a)
    inward=(s>12)&(s<15)&(a>.8)&(a<1.2);outward=(s>27)&(s<28.5)&(a>3)&(a<3.3)
    d=np.where(inward,-.14,np.where(outward,.07,.0006*np.sin(s*3+a*7)))
    points=centre+(tube_radius+d)[...,None]*normal
    nodes=np.arange(0,length+.25,.5);theta=nodes/bend_radius
    route=np.c_[bend_radius*np.sin(theta),bend_radius*(1-np.cos(theta)),np.zeros(len(theta))]
    return points.reshape(-1,3),route,inward.ravel(),outward.ravel(),s.ravel(),a.ravel()


def stream(points,repeats=1,offset=0):
    def read():
        for copy in range(repeats):
            for start in range(0,len(points),100000):
                p=points[start:start+100000]
                yield np.arange(len(p),dtype='<i8')+start+copy*len(points)+offset,p,{'mode':'full'}
    return read


class SectionCircleTests(unittest.TestCase):
    def test_five_metre_windows_preserve_curved_wall_and_detect_both_departure_sides(self):
        p,route,inward,outward,station,_=curved()
        original=p.copy()
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p),tmp,{'inward_mm':5,'outward_mm':5},prepared_guide=sc.Curve(route))
            ids=np.fromfile(r['point_ids_file'],'<i8')
            np.testing.assert_array_equal(ids,np.flatnonzero(inward|outward))
            np.testing.assert_array_equal(p,original)
            self.assertEqual(r['statistics']['input_points'],len(p));self.assertEqual(r['statistics']['selected_points'],0)
            self.assertEqual(r['statistics']['unassessed_points'],0);self.assertEqual(r['statistics']['supported_windows'],r['statistics']['fit_windows'])
            self.assertTrue(all(abs(q['radius_m']-2.5)<.002 for q in r['circle_sections']))
            self.assertFalse((Path(tmp)/'circle_samples.sqlite').exists())

    def test_looking_from_other_end_keeps_same_signed_world_departures(self):
        p,route,inside,outside,_,_=curved(length=32.)
        selected=[]
        for nodes in (route,route[::-1]):
            with tempfile.TemporaryDirectory() as tmp:
                r=sc.run(stream(p),tmp,{'inward_mm':8,'outward_mm':8},prepared_guide=sc.Curve(nodes))
                selected.append(np.fromfile(r['point_ids_file'],'<i8'))
        np.testing.assert_array_equal(selected[0],selected[1]);np.testing.assert_array_equal(selected[0],np.flatnonzero(inside|outside))

    def test_automatic_centreline_does_not_delete_good_wall_in_a_bend(self):
        p,_,inside,outside,_,_=curved()
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p),tmp,{'inward_mm':8,'outward_mm':8})
            ids=np.fromfile(r['point_ids_file'],'<i8')
            self.assertTrue((inside|outside)[ids].all())
            self.assertEqual(len(ids),int((inside|outside).sum()))
            self.assertGreater(r['statistics']['supported_windows'],0)

    def test_axis_is_not_tied_to_horizontal_or_global_coordinate_origin(self):
        p,route,inside,outside,_,_=curved(length=32.)
        angle=.41;R=np.array([[1,0,0],[0,np.cos(angle),-np.sin(angle)],[0,np.sin(angle),np.cos(angle)]])
        shift=np.array([400000.,600000.,120.]);p=p@R.T+shift;route=route@R.T+shift
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p),tmp,{'inward_mm':8,'outward_mm':8},prepared_guide=sc.Curve(route))
            np.testing.assert_array_equal(np.fromfile(r['point_ids_file'],'<i8'),np.flatnonzero(inside|outside))

    def test_unverified_half_tunnel_windows_stay_unselected(self):
        p,route,_,_,s,a=curved(length=32.)
        take=~((s>8)&(s<23)&(a>np.pi));p=p[take];s=s[take]
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p),tmp,{'inward_mm':8,'outward_mm':8},prepared_guide=sc.Curve(route))
            ids=np.fromfile(r['point_ids_file'],'<i8');self.assertFalse(((s[ids]>12)&(s[ids]<19)).any())
            self.assertGreater(r['statistics']['unassessed_points'],0);self.assertTrue(any(not q['supported'] for q in r['circle_sections']))

    def test_short_crop_on_long_project_alignment_keeps_supported_lining(self):
        s,a=np.meshgrid(np.arange(3100.04,3106.,.08),np.arange(90)*2*np.pi/90,indexing='ij')
        offset=.018*np.sin((s-3100.)*.5)
        p=np.column_stack((s.ravel(),(offset+2.5*np.cos(a)).ravel(),(2.5*np.sin(a)).ravel()))
        for chainage in (np.arange(0.,5805.,5.),np.array([0.,5800.])):
            with self.subTest(alignment_nodes=len(chainage)),tempfile.TemporaryDirectory() as tmp:
                rows=np.column_stack((chainage,chainage,np.zeros((len(chainage),2)),np.full(len(chainage),2.5)))
                result=sc.run(stream(p),tmp,{'axis_source':'project'},axis_rows=rows)
                self.assertEqual(result['statistics']['input_points'],len(p))
                self.assertGreater(result['statistics']['supported_windows'],2)
                self.assertEqual(result['statistics']['candidate_points'],0)
                np.testing.assert_array_equal(result['statistics']['effective_bounds'],[p.min(0),p.max(0)])
                self.assertTrue(any('These fits share much of the same data' in text for text in result['warnings']))

    def test_project_planes_follow_curved_graded_alignment_and_fit_measured_radius(self):
        station=np.arange(0.,24.1,.4);bend=station/65
        xyz=np.array([400000.,550000.,125.])+np.c_[65*np.sin(bend),65*(1-np.cos(bend)),.05*station]
        axis=Alignment([dict(chainage=s-20,e=p[0],n=p[1],h=p[2],radius=4.)
                        for s,p in zip(station,xyz)])
        angles=np.arange(90)*2*np.pi/90
        angles=angles[np.abs(angles-np.pi/2)>.44]
        duct_angles=np.linspace(np.pi,2*np.pi,21)
        wall=[];services=[]
        for s in np.arange(7.03,17.03,.10):
            c,_,r,u,_=axis.at(s-20)
            # The measured centre shifts across stations. The section planes
            # still follow the loaded alignment through its curve and grade.
            c=c+(.04+.012*np.sin(s*.5))*r-.025*u
            wall.append(c+3.775*(np.cos(angles)[:,None]*r+np.sin(angles)[:,None]*u))
            services.append(c+(.72*np.cos(duct_angles))[:,None]*r+
                            (2.4+.72*np.sin(duct_angles))[:,None]*u)
        wall=np.vstack(wall);points=np.vstack([wall,*services])
        with tempfile.TemporaryDirectory() as tmp:
            result=sc.run(stream(points),tmp,{'axis_source':'project','inward_mm':100,'outward_mm':600},axis_rows=axis.a)
            stats=result['statistics']
            self.assertTrue(stats['has_supported_span'])
            self.assertEqual(stats['supported_windows'],stats['fit_windows'])
            np.testing.assert_array_equal(np.fromfile(result['point_ids_file'],'<i8'),np.arange(len(wall),len(points)))
            self.assertTrue(all(q['chainage_m']<0 for q in result['circle_sections']))
            for q in result['circle_sections']:
                c,normal,_,_,_=axis.at(q['chainage_m'])
                np.testing.assert_allclose(q['normal'],normal,rtol=0,atol=1e-9)
                self.assertAlmostEqual(float((np.asarray(q['centre'])-c)@normal),0.,places=8)
                self.assertAlmostEqual(q['radius_m'],3.775,delta=.012)

    def test_oval_tunnel_is_not_forced_into_a_circular_deletion(self):
        s,a=np.meshgrid(np.arange(0,15,.1),np.arange(160)*2*np.pi/160,indexing='ij')
        p=np.c_[s.ravel(),(2.5*np.cos(a)).ravel(),(2.8*np.sin(a)).ravel()]
        passes=0
        def source():
            nonlocal passes
            passes+=1
            yield from stream(p)()
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(source,tmp,{'inward_mm':5,'outward_mm':5},prepared_guide=sc.Curve([[0,0,0],[15,0,0]]))
            self.assertEqual(r['statistics']['candidate_points'],0);self.assertEqual(r['statistics']['unassessed_points'],len(p))
            self.assertFalse(r['statistics']['has_supported_span'])
            self.assertEqual(passes,2)

    def test_stream_over_one_million_observations_and_64bit_ids_has_no_admission_cap(self):
        p,route,inside,outside,_,_=curved(length=24.);repeats=40;total=len(p)*repeats;self.assertGreater(total,1000000)
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p,repeats,2**31),tmp,{'inward_mm':8,'outward_mm':8},prepared_guide=sc.Curve(route))
            ids=np.fromfile(r['point_ids_file'],'<i8');self.assertEqual(r['statistics']['input_points'],total)
            expected=np.concatenate([np.flatnonzero(inside|outside)+copy*len(p)+2**31 for copy in range(repeats)])
            np.testing.assert_array_equal(ids,expected)

    def test_cancelled_fitting_leaves_original_points_and_no_partial_sample_database(self):
        p,route,*_=curved(length=12.);cancelled=False
        def progress(message):
            nonlocal cancelled
            if 'Fitting local circles' in message:cancelled=True
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'stopped'):
                sc.run(stream(p),tmp,{},progress,lambda:cancelled,prepared_guide=sc.Curve(route))
            self.assertFalse((Path(tmp)/'circle_samples.sqlite').exists())

    def test_bad_input_settings_and_loaded_alignment_are_clear_errors(self):
        for cfg in ({'section_m':0},{'inward_mm':float('nan')},{'axis_source':'guess'},{'respect_view':'yes'}):
            with self.assertRaises(ValueError):sc.settings(cfg)
        p,_,*_=curved(length=12.)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Load a project alignment'):
                sc.run(stream(p),tmp,{'axis_source':'project'})


class WindowSampleTests(unittest.TestCase):
    def test_sample_is_stable_across_blocks_and_disk_spill(self):
        rng=np.random.default_rng(3)
        p=np.column_stack((rng.uniform(0,30,4000),rng.uniform(-.2,.2,(4000,2))))
        # Interleave bins and include repeated voxels with different XYZ.
        p=np.vstack((p,p+[.002,.001,.001]))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sc,'WINDOW_SAMPLE',40), mock.patch.object(sc,'RESIDENT_SAMPLE_BINS',2):
            full=sc.Samples(Path(tmp)/'full.sqlite',5.)
            chunked=sc.Samples(Path(tmp)/'chunked.sqlite',5.)
            try:
                full.add(p,p[:,0],p[:,1:])
                for block in np.array_split(p[::-1],21):
                    chunked.add(block,block[:,0],block[:,1:])
                self.assertGreater(chunked.db.execute('SELECT count(*) FROM p').fetchone()[0],0)
                for b in range(6):
                    actual=chunked.read(b)
                    self.assertEqual(len(actual),40)
                    np.testing.assert_array_equal(actual,full.read(b))
            finally:
                full.close();chunked.close()

    def test_distinct_voxels_with_tied_rank_keep_key_order_and_true_xyz(self):
        rows=np.empty(4,sc.SAMPLE_ROW)
        rows['keys']=[[0,4,1],[0,-2,1],[0,-2,1],[1,4,1]]
        rows['rank']=[1,1,1,2]
        rows['xyz']=[[.01,.105,.03],[.03,-.04,.035],[.03,-.04,.03],[.22,.10,.03]]
        with mock.patch.object(sc,'WINDOW_SAMPLE',2):
            np.testing.assert_array_equal(sc.Samples._compact(rows),rows[[2,0]])


class CircleReviewTests(unittest.TestCase):
    def test_restricted_preview_reads_exact_rows_once_and_reuses_the_index(self):
        import preview_index
        import registration_pose

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'merged.xyz64';mask=root/'merged.mask'
            step=preview_index.BLOCK;n=3*step
            p=np.column_stack((np.arange(n),np.zeros((n,2)))).astype('<f8')
            p.tofile(path)
            deleted=np.zeros(n,'u1');deleted[step+25]=1;deleted.tofile(mask)
            meta=dict(name='Registered cloud',file=str(path),count=n,mask_file=str(mask),
                      minimum=p.min(0).tolist(),maximum=p.max(0).tolist())
            rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
            registration_pose.set_pose(meta,rotation,[100000.,400000.,0.])
            station=400000.+step
            selection=dict(mode='box',minimum=[99999.,station+10,-1.],maximum=[100001.,station+200,1.])
            limit=dict(minimum=[99999.,station+20,-1.],maximum=[100001.,station+150,1.])
            item=dict(id='merged',kind='scan',meta=meta,name=meta['name'],limit_box=limit)
            messages=[]
            s=SimpleNamespace(STATE={'job_items':[item]},WORK=root,progress=messages.append)
            expected=np.arange(step+20,step+151);expected=expected[deleted[expected]==0]
            world=registration_pose.points(meta,p[expected])

            def review(batches,*args,**kwargs):
                for _ in range(3):
                    parts=list(batches())
                    np.testing.assert_array_equal(np.concatenate([ids for ids,_,_ in parts]),expected)
                    np.testing.assert_array_equal(np.concatenate([xyz for _,xyz,_ in parts]),world)
                return {}

            reads=[];read=np.fromfile
            def observe(file,*args,**kwargs):
                out=read(file,*args,**kwargs)
                if getattr(file,'name',None)==str(path):reads.append(out.size//3)
                return out

            request=dict(mode='circle',source_id='merged',selection=selection,settings={'respect_view':True})
            with mock.patch.object(sc,'run',side_effect=review), mock.patch.object(np,'fromfile',side_effect=observe),mock.patch.object(ss,'CIRCLE_SCOPE_CACHE_BYTES',1):
                row=ss.preview(s,request)
                self.assertEqual(row['search_area'],'selection')
                self.assertEqual(sum(reads),n)
                self.assertTrue(Path(str(path)+'.viewindex.npz').is_file())
                reads.clear()
                request.pop('selection')
                row=ss.preview(s,request)
                self.assertEqual(row['search_area'],'limit_box')
                self.assertEqual(reads,[step])
                self.assertTrue(any(f'{step:,}/{step:,} source rows read · {len(expected):,} retained' in text for text in messages))

    def test_separate_inward_outward_selection_and_one_step_undo_use_original_ids(self):
        fx=shape_fixture.ShapeReviewTests();fx.setUp();self.addCleanup(fx.doCleanups);row=fx.row;row['mode']='circle';row['settings']=sc.settings()
        ss.choose(fx.s,fx.body('select',operation='side',side='inward'))
        np.testing.assert_array_equal(np.fromfile(row['selected_file'],'u1'),[1,1,0,0])
        ss.choose(fx.s,fx.body('select',operation='side',side='outward'))
        np.testing.assert_array_equal(np.fromfile(row['selected_file'],'u1'),[0,0,1,1])
        before=Path(fx.item['meta']['file']).read_bytes()
        with workspace_history.request(fx.s,'/api/shape-selection'):
            body=fx.body('delete',confirmed=True);workspace_history.configure('/api/shape-selection',body);ss.edit(fx.s,body)
        mask=np.fromfile(fx.item['meta']['mask_file'],'u1');np.testing.assert_array_equal(np.flatnonzero(mask),fx.ids[2:])
        self.assertEqual(Path(fx.item['meta']['file']).read_bytes(),before);workspace_history.apply(fx.s)
        self.assertFalse(fx.s.STATE['job_items'][0]['meta'].get('mask_file'))


if __name__=='__main__':unittest.main()
