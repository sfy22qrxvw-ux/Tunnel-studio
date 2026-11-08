# Copyright (c) 2026 Damian Gulczynski.
"""Synthetic implementation checks. These do not establish survey accuracy."""
import unittest, tempfile, json
from pathlib import Path
import numpy as np
from core import Alignment, analyse_profile, point_chunks, import_cloud, project_cloud, slice_analysis, ring_checks, suggest_joints

class GeometryTests(unittest.TestCase):
    def axis(self): return Alignment([{'chainage':1000+i,'e':200000,'n':375000+i,'h':100,'radius':3.5} for i in (0,10,20)])
    def test_chainage_frame_and_end_rejection(self):
        a=self.axis(); q=a.project(np.array([[200003,375004,102],[200000,374999,100],[200000,375021,100]]))
        np.testing.assert_allclose(q[0,:3],[1004,3,2],atol=1e-10)
        self.assertTrue(np.isnan(q[1:,0]).all()); centre,t,right,up,r=a.at(1004)
        np.testing.assert_allclose(right,[1,0,0]); np.testing.assert_allclose(up,[0,0,1])
    def test_polyline_projection_matches_brute_force(self):
        rng=np.random.default_rng(12); p=np.cumsum(rng.normal(size=(60,3)),axis=0)*50; p+=np.array([200000,375000,100])
        a=Alignment([dict(zip(['chainage','e','n','h','radius'],[i*20,*v,3.5])) for i,v in enumerate(p)])
        q=rng.uniform(p.min(axis=0),p.max(axis=0),(700,3)); result=a.project(q)
        v=q[:,None,:2]-p[None,:-1,:2]; t=np.clip((v*a.d[None,:,:2]).sum(axis=2)/a.l2,0,1)
        dist=((v-t[:,:,None]*a.d[None,:,:2])**2).sum(axis=2); ix=dist.argmin(axis=1); expected=20*(ix+t[np.arange(len(q)),ix]); keep=np.isfinite(result[:,0])
        np.testing.assert_allclose(result[keep,0],expected[keep],atol=1e-8)
    def test_circle_offset_is_not_ovality(self):
        t=np.linspace(0,2*np.pi,360,endpoint=False)+.002
        q=np.column_stack((3.5*np.cos(t)+.025,3.5*np.sin(t)-.012)); r=analyse_profile(q,3.5)
        self.assertTrue(r['fit_valid']); np.testing.assert_allclose(r['circle_centre'],[.025,-.012],atol=5e-5)
        self.assertLess(r['ovality_mm'],.15)
    def test_known_ellipse(self):
        t=np.linspace(0,2*np.pi,720,endpoint=False)+.001; angle=.41;a=3.54;b=3.46
        x=a*np.cos(t);y=b*np.sin(t);q=np.column_stack((.02+x*np.cos(angle)-y*np.sin(angle),-.01+x*np.sin(angle)+y*np.cos(angle)))
        r=analyse_profile(q,3.5);self.assertTrue(r['fit_valid'])
        np.testing.assert_allclose([r['major_diameter'],r['minor_diameter']],[2*a,2*b],atol=2e-4)
        self.assertAlmostEqual(r['ovality_percent'],100*(a-b)/3.5,places=2)
    def test_partial_arc_withheld(self):
        t=np.linspace(0,np.pi,500); r=analyse_profile(np.column_stack((3.5*np.cos(t),3.5*np.sin(t))),3.5)
        self.assertFalse(r['fit_valid'])
    def test_creep_sign_and_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'rings.csv';p.write_text('ring_id,design_chainage,measured_chainage,reference\nR1,1005,1005.012,Leading face\nR2,1010,1009.997,Leading face\n')
            r=ring_checks(p,self.axis());np.testing.assert_allclose([v['creep_mm'] for v in r],[12,-3],atol=1e-7)
    def test_joint_detection_and_smooth_negative_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);a=self.axis();theta=np.linspace(0,2*np.pi,96,endpoint=False)
            s=np.arange(1000,1020,.01)+.002;ss,tt=np.meshgrid(s,theta,indexing='ij');joints=np.arange(1002,1019,2)
            depth=np.exp(-.5*(np.min(np.abs(s[:,None]-joints),axis=1)/.016)**2)*.014
            for label,dep in [('joint',depth),('smooth',np.zeros_like(depth))]:
                r=3.5+dep[:,None];points=np.column_stack(((200000+r*np.cos(tt)).ravel(),np.broadcast_to(375000+(ss-1000),tt.shape).ravel(),(100+r*np.sin(tt)).ravel()))
                points.astype('<f8').tofile(path/'p.bin');meta={'file':str(path/'p.bin'),'count':len(points)}
                derived,_=project_cloud(meta,a,path/'d.bin');report=suggest_joints(meta,derived,a,1000,1020,2,.02,3,.3)
                if label=='joint':
                    predicted=np.array([v['chainage'] for v in report['candidates']]);self.assertEqual(len(predicted),len(joints));self.assertLess(np.max(np.abs(predicted-joints)),.03)
                else:self.assertEqual(report['candidates'],[])

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.p=np.array([[200000.123,375000.234,99.345],[200001.567,375002.678,100.789],[200003.111,375004.222,101.333]])
    def tearDown(self):self.tmp.cleanup()
    def check(self,path,expected=None,tol=1e-8):
        p=np.concatenate(list(point_chunks(path)));np.testing.assert_allclose(p,self.p if expected is None else expected,atol=tol,rtol=0)
    def test_text_formats_and_header_mapping(self):
        for ext in ['xyz','txt','pts']:
            p=self.path/('points.'+ext)
            np.savetxt(p,self.p,fmt='%.9f',header='3' if ext=='pts' else '',comments='');self.check(p)
        p=self.path/'points.csv';p.write_text('id,N,H,E\n'+''.join(f'P{i},{r[1]},{r[2]},{r[0]}\n' for i,r in enumerate(self.p)));self.check(p)
    def test_las_and_laz_scale_offset(self):
        import laspy
        for ext in ['las','laz']:
            header=laspy.LasHeader(point_format=3,version='1.2');header.scales=np.array([.001]*3);header.offsets=np.array([200000,375000,100])
            las=laspy.LasData(header);las.x,las.y,las.z=self.p.T;p=self.path/('p.'+ext);las.write(p);self.check(p,tol=1e-8)
    def test_ascii_and_binary_ply(self):
        from plyfile import PlyData,PlyElement
        v=np.array([tuple(r) for r in self.p],dtype=[('x','f8'),('y','f8'),('z','f8')])
        for text in [True,False]:
            p=self.path/'p.ply';PlyData([PlyElement.describe(v,'vertex')],text=text).write(p);self.check(p)
    def test_e57_pose_invalid_and_multiple_chunks(self):
        import pye57
        from pyquaternion import Quaternion
        p=self.path/'p.e57';local=np.tile(np.array([[1.,2.,3.],[2.,3.,4.],[3.,4.,5.]]),(34000,1));valid=np.zeros(len(local),np.int8);valid[5]=1
        rot=Quaternion(axis=[0,0,1],angle=.3);translation=np.array([200000.,375000.,100.])
        with pye57.E57(str(p),'w') as out:
            out.write_scan_raw({'cartesianX':local[:,0],'cartesianY':local[:,1],'cartesianZ':local[:,2],'cartesianInvalidState':valid},rotation=rot.elements,translation=translation)
        expected=(local@rot.rotation_matrix.T+translation)[valid==0];self.check(p,expected,tol=1e-8)
    def test_malformed_file_does_not_leave_partial_cache(self):
        p=self.path/'bad.xyz';p.write_text('1 2 3\nBad record\n');dest=self.path/'cache'
        with self.assertRaises(ValueError):import_cloud(p,dest)
        self.assertFalse(dest.exists())

if __name__=='__main__':unittest.main(verbosity=2)
