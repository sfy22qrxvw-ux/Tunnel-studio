# Copyright (c) 2026 Damian Gulczynski.
import unittest,tempfile,json
from pathlib import Path
import numpy as np
from core import Alignment,project_cloud,slice_analysis
from editing import select,apply_edit,mask_for,export_clean
import heatmap,structures,reporting

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.axis=Alignment([dict(chainage=s,e=0,n=s,h=0,radius=3.5) for s in (0,10)])
        angles=np.arange(0,360,5)+2.5; stations=np.arange(.025,10,.05)
        s,t=np.meshgrid(stations,np.radians(angles));r=3.5+.01*np.sin(t)
        pts=np.column_stack(((r*np.sin(t)).ravel(),s.ravel(),(r*np.cos(t)).ravel()))
        path=self.root/'cloud';pts.astype('<f8').tofile(path);self.meta={'file':str(path),'count':len(pts),'name':'test scan'}
        self.derived,_=project_cloud(self.meta,self.axis,self.root/'derived')
    def tearDown(self):self.tmp.cleanup()
    def spec(self):return dict(origin=[0,0,0],target=[0,5,0],right=[1,0,0],up=[0,1,0],forward=[0,0,1],scale=5,aspect=1,rect=[0,-1,1,1],start=0,end=10,band=.3)
    def test_full_density_delete_undo_export_and_hidden(self):
        before=slice_analysis(self.meta,self.derived,self.axis,5,.3,.3,25)[0]
        spec=self.spec();sel=select(self.meta,self.derived,spec,self.root/'ids');self.assertEqual(sel['count'],self.meta['count']//2)
        hidden={**spec,'hidden':[spec]};self.assertEqual(select(self.meta,self.derived,hidden,self.root/'hidden')['count'],0)
        history=[];entry=apply_edit(self.meta,sel,self.root,history,'temporary pipe')
        after=slice_analysis(self.meta,self.derived,self.axis,5,.3,.3,25)[0];self.assertEqual(after['kept_points'],before['kept_points']//2);self.assertFalse(after['fit_valid'])
        export_clean(self.meta,self.root/'clean.csv');clean=np.loadtxt(self.root/'clean.csv',delimiter=',',skiprows=1);self.assertEqual(len(clean),self.meta['count']//2)
        self.meta['mask_file']=entry['before_mask'];undo=slice_analysis(self.meta,self.derived,self.axis,5,.3,.3,25)[0];self.assertEqual(undo['kept_points'],before['kept_points'])
        self.meta['mask_file']=entry['after_mask'];self.assertEqual(int(mask_for(self.meta).sum()),sel['count'])
    def test_heatmap_signs_count_and_mesh_methods(self):
        h=heatmap.calculate(self.meta,self.derived,self.axis,0,10,.1,5,.3,'mean');self.assertEqual(h['points'],self.meta['count']);a=np.array(h['values']).reshape(h['ny'],h['nx']);self.assertGreater(a[18,0],9);self.assertLess(a[54,0],-9)
        self.assertTrue(heatmap.image_uri(h,25).startswith('data:image/png;base64,'))
        for method in ['mean','nearest','idw']:
            v,f,summary=structures.scan_mesh(self.meta,self.derived,self.axis,0,10,.1,5,.3,1,1,method,2,1)
            self.assertGreater(len(f),1000);self.assertTrue(np.isfinite(v).all());self.assertGreater(summary['area_m2'],100)
    def test_nearest_and_idw_are_distinct(self):
        # Two measurements in the same cell: near-centre point has 10 mm, far point 100 mm.
        rows=np.array([[.51,3.51/np.sqrt(2),3.51/np.sqrt(2),.01],[.9,0,3.6,.10]],dtype='<f8');path=self.root/'math';rows.tofile(path)
        meta={'count':2}; results={m:heatmap.calculate(meta,path,self.axis,0,1,1,90,.3,m)['values'][0] for m in ['mean','nearest','idw']}
        self.assertAlmostEqual(results['mean'],55);self.assertAlmostEqual(results['nearest'],10);self.assertGreater(results['idw'],10);self.assertLess(results['idw'],11)
    def test_report_escaping_and_sparse_sections(self):
        state={'axis':self.axis,'cloud':self.meta,'derived':self.derived,'demo':False,'rings':[],'edits':[]}
        settings=dict(start=1,end=2,step=1,width=.3,band=.3,tolerance=25,creep=10,project='<script>alert(1)</script>')
        text,rows=reporting.create(state,settings);self.assertEqual(len(rows),2);self.assertIn('&lt;script&gt;',text);self.assertNotIn('<script>alert',text);self.assertIn('data:image/png',text)
    def test_primitive_and_transform(self):
        v,f=structures.mesh(dict(kind='box',e=10,n=20,h=30,length=2,width=4,height=6),self.axis);self.assertEqual(len(v),8);self.assertEqual(len(f),6)
        a=np.asarray(v);out=np.asarray(structures.transform(a,dict(tx=1,ty=2,tz=3,scale=2,rotation=90)))
        np.testing.assert_allclose(out.mean(axis=0),a.mean(axis=0)+[1,2,3]);np.testing.assert_allclose(np.linalg.norm(out[0]-out[1]),4)

if __name__=='__main__':unittest.main()
