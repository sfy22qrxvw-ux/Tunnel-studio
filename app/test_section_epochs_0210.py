"""Independent section geometry and two-epoch workflow checks on synthetic scans."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import core
import jobdata
import job_api
import registration_pose
import scan_geometry as sg
import section_epochs as epoch


def ring(radii, centre, right, up, n=600, angle=None):
    a=np.linspace(0,2*np.pi,n,endpoint=False) if angle is None else np.asarray(angle)
    return np.asarray(centre)+radii[0]*np.cos(a[:,None])*right + radii[1]*np.sin(a[:,None])*up


class FitTests(unittest.TestCase):
    def test_circle_tilt_large_origin_gross_and_radial_outliers(self):
        rng=np.random.default_rng(381);right=np.array([1.,2.,0.])/np.sqrt(5);up=np.array([-2.,1.,5.])/np.sqrt(30)
        # These vectors are orthogonal; the 3D plane is tilted to horizontal.
        origin=np.array([576329.545,237469.667,-67.451]);points=ring((3.02,3.02),origin,right,up)
        points += rng.normal(0,.0002,points.shape)
        points=np.vstack((points,ring((2.2,2.2),origin,right,up,18),origin+np.array([35,26,74.])))
        calls=[];r=sg.fit_circle_section(points,progress=calls.append)
        self.assertAlmostEqual(r['radius_m'],3.02,places=4)
        np.testing.assert_allclose(r['centre_xyz'],origin,atol=.0001,rtol=0)
        self.assertLess(r['inliers'],len(points));self.assertLess(r['rms_mm'],.4);self.assertGreater(len(calls),1)

    def test_ellipse_fit_unequal_density_rotated_axis(self):
        right=np.array([0.,.8,.6]);up=np.array([1.,0.,0.]);c=np.array([43.,78.,-.7])
        ang=np.r_[np.arange(720)*np.pi/360, np.linspace(.1,.35,3600)]
        q=ring((4.19,3.77), c,right,up,angle=ang)
        r=sg.fit_ellipse_section(q)
        self.assertAlmostEqual(r['major_radius_m'],4.19,places=6);self.assertAlmostEqual(r['minor_radius_m'],3.77,places=6)
        self.assertAlmostEqual(abs(np.dot(r['major_direction_xyz'],right)),1.,places=6)
        np.testing.assert_allclose(r['centre_xyz'],c,atol=1e-7)

    def test_partial_arc_refused(self):
        t=np.linspace(0,4,950);q=np.c_[3*np.cos(t),3*np.sin(t)]
        for f in (sg.fit_circle_section,sg.fit_ellipse_section):
            with self.assertRaisesRegex(ValueError,'arc|supported'):
                f(q)

    def test_cancel_during_solver(self):
        calls=[];q=ring((3.,3.),[0,0,0],np.eye(3)[0],np.eye(3)[1]);
        for f in (sg.fit_circle_section,sg.fit_ellipse_section):
            calls.clear()
            with self.assertRaises(jobdata.Cancelled):
                f(q,lambda s:calls.append(s),lambda:len(calls)>1)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name);self.origin=np.array([450000,320000,120.]);e,n,h=self.origin
        axis=core.Alignment([dict(chainage=100,e=e,n=n,h=h,radius=3),dict(chainage=125,e=e,n=n+25,h=h,radius=3)],'Reference')
        self.s=SimpleNamespace(WORK=self.work, STATE=dict(job_items=[],axis=axis),JOB={'busy':False},progress=lambda _:None, save=lambda:None)
        self.s.start_job=lambda fn,*args,**kw:fn(); job_api.CANCEL.clear()
        for name,params in [('A',(3.0,3.0,0,0)),('B',(2.996,3.002,.005,-.003))]:
            a,b,dx,dz=params;q=np.concatenate([ring((a,b),[dx,station,dz],np.eye(3)[0],np.eye(3)[2]) for station in (4.,14.)]);q=np.vstack((q,[[15,4,0],[0,4,3.2]]));path=self.work/(name+'.xyz64');q.astype('<f8').tofile(path)
            # First invalid point falls outside the band. Mask the second in-band outlier.
            flags=np.zeros(len(q),dtype='u1');flags[-1]=1;mask=path.with_suffix('.mask');flags.tofile(mask)
            m=dict(name=name,file=str(path),mask_file=str(mask),count=len(q),minimum=q.min(0).tolist(),maximum=q.max(0).tolist());registration_pose.set_pose(m,np.eye(3),self.origin)
            self.s.STATE['job_items'].append(dict(kind='scan',name=name,id=name,meta=m))
        self.body=dict(action='section_fits',id='A',comparison_id='B',datum='Grid',common_datum=True,stations=[114,104,120],width=.1,band=.3,request_id='request')

    def test_normal_endpoint_world_poses_masks_chord_signs_and_missing(self):
        messages=[];self.s.progress=messages.append; replies=[];h=SimpleNamespace(send=replies.append)
        sg.post(self.s,h,'/api/scan-geometry',self.body);sg.get(self.s,h,'/api/scan-section-fits',{})
        result=replies[-1]['result'];json.dumps(result,allow_nan=False)
        self.assertEqual(result['request_id'],'request');self.assertEqual([r['chainage'] for r in result['sections_a']],[104,114,120]);self.assertEqual(len(result['sections_a']),3)
        self.assertEqual(result['sections_a'][0]['total_points'],600);self.assertEqual(result['sections_a'][0]['ellipse']['inliers'],600)
        for row in result['comparison']['rows'][:2]:
            self.assertAlmostEqual(row['horizontal_closure_mm'],8.,places=4);self.assertAlmostEqual(row['vertical_closure_mm'],-4.,places=4)
            self.assertAlmostEqual(row['centre_right_mm'],5.,places=4);self.assertAlmostEqual(row['centre_up_mm'],-3.,places=4)
            chord=2/(.75/(2.996**2)+.25/(3.002**2))**.5
            for t in ('30','150'):self.assertAlmostEqual(row['chord_closure_mm'][t],1000*(6-chord),places=4)
        self.assertIn('error',result['sections_b'][2]);self.assertIn('error',result['comparison']['rows'][2]);self.assertTrue(messages)

    def test_refuse_shared_file_unconfirmed_datum_and_changed_frame(self):
        with self.assertRaisesRegex(ValueError,'datum'):epoch.run(self.s,{**self.body,'common_datum':False},self.s.progress)
        with self.assertRaisesRegex(ValueError,'independently observed'):epoch.run(self.s,{**self.body,'comparison_id':'A'},self.s.progress)
        record=epoch.run(self.s,{**self.body,'stations':[104],'comparison_id':None},self.s.progress)['sections_a'][0]
        invalid=copy.deepcopy(record);invalid['frame']['origin'][2]+=.004
        with self.assertRaisesRegex(ValueError,'different section planes'):sg.convergence_analysis([record],[invalid])
        invalid=copy.deepcopy(record);invalid['band_m']=.1
        with self.assertRaisesRegex(ValueError,'wall band'):sg.convergence_analysis([record],[invalid])
        with self.assertRaisesRegex(ValueError,'Duplicate'):sg.convergence_analysis([record,record],[record])

    def test_cancel_does_not_publish_partial_report(self):
        old={'request_id':'previous'}; self.s.STATE['scan_section_fits']=old
        def stop(s):
            if 'chainage' in s:raise jobdata.Cancelled('Stop')
        self.s.progress=stop
        with self.assertRaises(jobdata.Cancelled):sg.post(self.s,SimpleNamespace(send=lambda _:None),'/api/scan-geometry',self.body)
        self.assertEqual(self.s.STATE['scan_section_fits'],old)
