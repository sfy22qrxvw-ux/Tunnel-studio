"""Local-circle cleaning on independently constructed curved tunnel surfaces."""
from pathlib import Path
import tempfile,unittest
from unittest import mock
import numpy as np
import section_circle as sc
import shape_selection as ss
import workspace_history
import test_shape_matching_0214 as shape_fixture


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

    def test_oval_tunnel_is_not_forced_into_a_circular_deletion(self):
        s,a=np.meshgrid(np.arange(0,15,.1),np.arange(160)*2*np.pi/160,indexing='ij')
        p=np.c_[s.ravel(),(2.5*np.cos(a)).ravel(),(2.8*np.sin(a)).ravel()]
        with tempfile.TemporaryDirectory() as tmp:
            r=sc.run(stream(p),tmp,{'inward_mm':5,'outward_mm':5},prepared_guide=sc.Curve([[0,0,0],[15,0,0]]))
            self.assertEqual(r['statistics']['candidate_points'],0);self.assertEqual(r['statistics']['unassessed_points'],len(p))

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


class CircleReviewTests(unittest.TestCase):
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
