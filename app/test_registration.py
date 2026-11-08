# Copyright (c) 2026 Damian Gulczynski.
import tempfile, unittest, json, zipfile
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
import registration, pointio, exchange, projects
from demo_targets import generate


class RegistrationTests(unittest.TestCase):
    def test_large_coordinates_rotation_fixed_scale_and_independent_checks(self):
        rng=np.random.default_rng(7); p=rng.uniform(-20,20,(7,3))+[12345,54321,300]
        r=Rotation.from_euler('xyz',[.1,-.2,.3]).as_matrix(); q=p@r.T+[200000,375000,100]
        rows=[{'id':str(i),'source':a.tolist(),'control':b.tolist(),'role':'check' if i==6 else 'fit'} for i,(a,b) in enumerate(zip(p,q))]
        rows[-1]['control'][0]+=.02
        sol=registration.solve(rows,5)
        np.testing.assert_allclose(sol['rotation'],r,atol=1e-11)
        self.assertLess(sol['fit']['rms_mm'],1e-5); self.assertAlmostEqual(sol['check']['max_mm'],20,places=5)
        self.assertEqual(sol['scale'],1); self.assertAlmostEqual(np.linalg.det(sol['rotation']),1)
        self.assertTrue(sol['warnings']); np.testing.assert_allclose(registration.transform(p,sol),q,atol=1e-8)
    def test_reject_collinear_duplicate_missing_and_too_few_targets(self):
        for p in [[[0,0,0],[1,0,0],[2,0,0]],[[0,0,0],[0,0,0],[1,1,1]],[[0,0,0],[1,1,1]]]:
            rows=[{'id':str(i),'source':a,'control':a} for i,a in enumerate(p)]
            with self.assertRaises(ValueError): registration.solve(rows)
        with self.assertRaises(ValueError): registration.solve([{'id':'a','source':[None,0,0],'control':[0,0,0]}])
    def test_checker_positive_tilted_and_uniform_negative(self):
        rng=np.random.default_rng(3); u,v=np.meshgrid(np.linspace(-.099,.099,80),np.linspace(-.099,.099,80)); uv=np.c_[u.ravel(),v.ravel()]; r=Rotation.from_euler('xyz',[.8,.2,-.4]).as_matrix()
        p=np.c_[uv,rng.normal(0,.0002,len(uv))]@r.T+[20,30,8]; intensity=np.where(uv[:,0]*uv[:,1]>0,200.,10.)+rng.normal(0,1,len(uv))
        c=registration.fit_target(p,intensity,[20.006,30,8.004],.2)
        self.assertLess(np.linalg.norm(np.array(c['source'])-[20,30,8]),.0004); self.assertGreater(c['score'],.9)
        with self.assertRaises(ValueError): registration.fit_target(p,np.ones(len(p)),[20,30,8],.2)
        with self.assertRaises(ValueError): registration.fit_target(p,np.where(uv[:,0]>0,200.,10.),[20,30,8],.2)
    def test_known_diameter_sphere_target_and_flat_negative(self):
        rng=np.random.default_rng(31);centre=np.array([12.3,-4.2,1.7]);radius=.0994
        phi=rng.uniform(0,2*np.pi,7000);z=rng.uniform(-.85,.85,7000);rr=np.sqrt(1-z*z)
        directions=np.c_[rr*np.cos(phi),rr*np.sin(phi),z]
        sphere=centre+radius*directions+rng.normal(0,.00045,(len(directions),3))
        # Add nearby wall/clutter; robust fixed-radius fitting must keep the sphere.
        wall=np.c_[rng.uniform(centre[0]-.22,centre[0]+.22,3500),rng.uniform(centre[1]-.22,centre[1]+.22,3500),np.full(3500,centre[2]-.14)]+rng.normal(0,.0007,(3500,3))
        points=np.r_[sphere,wall];seed=(centre+np.array([radius,0,0])).tolist()
        c=registration.fit_sphere_target(points,seed,.1988)
        self.assertLess(np.linalg.norm(np.array(c['source'])-centre),.0012)
        self.assertLess(c['sphere_rms_mm'],1.5);self.assertLess(abs(c['fitted_radius_m']-radius),.0015);self.assertGreater(c['support_points'],2000)
        plane=np.c_[rng.uniform(-.15,.15,4000),rng.uniform(-.15,.15,4000),rng.normal(0,.0003,4000)]
        with self.assertRaises(ValueError):registration.fit_sphere_target(plane,[0,0,0],.1988)
    def test_sphere_detection_uses_spatial_index_and_exact_original_points(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);rng=np.random.default_rng(44);centre=np.array([10.,20.,3.]);radius=.0994
            # Structured scanner rows are spatially coherent. Build coherent distant blocks so
            # the conservative index can reject them without ever replacing exact point tests.
            clouds=[]
            for c0 in ([-40,-30,2],[-20,-25,4],[30,-15,1],[35,25,7],[0,-35,3]):
                clouds.append(np.asarray(c0)+rng.normal(0,1.2,(18000,3)))
            phi=rng.uniform(0,2*np.pi,9000);z=rng.uniform(-.9,.9,9000);rr=np.sqrt(1-z*z)
            sphere=centre+radius*np.c_[rr*np.cos(phi),rr*np.sin(phi),z]+rng.normal(0,.00035,(9000,3))
            xyz=np.r_[*clouds,sphere];cache=d/'scan.xyz64';xyz.astype('<f8').tofile(cache)
            mask=d/'scan.mask';np.zeros(len(xyz),np.uint8).tofile(mask)
            meta={'file':str(cache),'count':len(xyz),'mask_file':str(mask)}
            result=registration.detect(meta,.1988,seed=(centre+[radius,0,0]).tolist(),target_type='sphere')
            c=result['candidates'][0]
            self.assertLess(np.linalg.norm(np.asarray(c['source'])-centre),.0012)
            self.assertLess(c['sphere_rms_mm'],1.5)
            self.assertLess(result['candidate_points_read'],len(xyz))
            self.assertTrue(Path(str(cache)+'.viewindex.npz').is_file())

    def test_text_attribute_alignment_and_no_guessing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'points.csv'; p.write_text('id,z,x,y,red,green,blue,intensity\n1,3,1,2,10,20,30,40\n2,3,nan,2,50,60,70,80\n3,6,4,5,90,100,110,120\n')
            m=pointio.import_cloud(p,Path(d)/'scan.xyz64'); xyz,a=pointio.arrays(m)
            np.testing.assert_equal(xyz,[[1,2,3],[4,5,6]]); np.testing.assert_equal(a,[[40,10,20,30],[120,90,100,110]])
            p=Path(d)/'plain.xyz'; p.write_text('1 2 3 999\n'); m=pointio.import_cloud(p,Path(d)/'plain.xyz64'); self.assertNotIn('attributes_file',m)
    def test_exchange_roundtrip_and_mask(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); p=d/'p.csv'; xyz=np.array([[200000.123456789,375000.87654321,100.001],[200001,375002,101],[200002.123456789,375003.5,102.55]])
            np.savetxt(p,np.c_[xyz,[0,500,1000],[[65535,0,0],[0,65535,0],[0,0,65535]]],header='e,n,h,intensity,red,green,blue',delimiter=',',comments='',fmt='%.12f')
            m=pointio.import_cloud(p,d/'a.xyz64'); mask=d/'mask'; np.array([0,1,0],dtype='u1').tofile(mask);m['mask_file']=str(mask)
            for fmt in ['e57','las','laz','ply','pts','xyz','csv']:
                with self.subTest(fmt=fmt):
                    out=d/('exchange.'+fmt); exchange.export_cloud(m,out,fmt); imported=pointio.import_cloud(out,d/(fmt+'.xyz64')); self.assertEqual(imported['count'],2)
                    back,attrs=pointio.arrays(imported); np.testing.assert_allclose(back,xyz[[0,2]],rtol=0,atol=.000051 if fmt in ('las','laz') else 1e-8)
                    if fmt!='xyz': self.assertEqual(imported['attribute_counts'],[2,2,2,2]);self.assertTrue(np.isfinite(attrs).all())
    def test_project_save_as_and_portable(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); path,rows=generate(d/'demo'); meta=pointio.import_cloud(path,d/'cloud.xyz64'); state={'axis':None,'cloud':None,'registration':{'cloud':meta,'rows':rows},'structures':[]}
            first=projects.save_named(d,state,'First'); second=projects.save_named(d,first,'Second',True)
            second['registration']['rows'][0]['id']='CHANGED';projects.save_named(d,second)
            self.assertEqual(projects.read_named(d,first['project_id'])['registration']['rows'][0]['id'],'CP01')
            portable=d/'portable.tstudio'; projects.portable(d,first,portable); unpacked=projects.unpack(d,portable); self.assertTrue(Path(unpacked['registration']['cloud']['file']).is_file());self.assertNotEqual(unpacked['registration']['cloud']['file'],meta['file'])
            bad=d/'bad.tstudio'
            with zipfile.ZipFile(bad,'w') as z:z.writestr('project.json',json.dumps({'format':'TunnelStudioProject','version':1,'state':{'cloud':{'file':'../escape'}}}));z.writestr('../escape','unsafe')
            with self.assertRaises(ValueError):projects.unpack(d,bad)

    def test_xyz_only_export_metadata_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);p=d/'plain.xyz';p.write_text('1 2 3\n4 5 6\n')
            meta=pointio.import_cloud(p,d/'plain.xyz64')
            for fmt in ('csv','e57','las','laz','ply','pts','xyz'):
                info=exchange.export_cloud(meta,d/('out.'+fmt),fmt)
                json.dumps(info,allow_nan=False);self.assertIs(info['intensity'],False);self.assertIs(info['colour'],False)
if __name__=='__main__': unittest.main()
