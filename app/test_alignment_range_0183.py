"""Banded centreline parity and real long-tunnel estimate/save/use coverage."""
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import projects
import scan_geometry
import tunnel_axis_fit
import workflows

EVIDENCE={}


def dense_reference(stations,values,weights,amount):
    """Original n-by-n equations, intentionally independent of band assembly."""
    n=len(stations);good=np.flatnonzero(weights>0)
    if len(good)<2:return values.copy(),np.zeros(n)
    filled=np.column_stack([np.interp(stations,stations[good],values[good,j]) for j in range(values.shape[1])])
    if not amount:return filled,np.linalg.norm(filled-values,axis=1)
    ds=float(np.median(np.diff(stations)));d=np.diff(np.eye(n),2,axis=0)
    penalty=((2+amount*.5)/max(ds,.1))**4*(d.T@d)
    offset=filled[good[0]].copy();local=filled-offset;w=weights.copy();result=local.copy()
    for _ in range(4):
        result=np.linalg.solve(np.diag(w+1e-9)+penalty,w[:,None]*local)
        error=np.linalg.norm(result-local,axis=1)
        scale=max(.02,1.4826*float(np.median(error[good])))
        w=weights*np.minimum(1,2*scale/np.maximum(error,1e-12))
    return result+offset,np.linalg.norm(result-local,axis=1)


class AlignmentRange0183Tests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        path=os.environ.get('TUNNEL_ALIGNMENT_EVIDENCE')
        if path:Path(path).write_text(json.dumps(EVIDENCE,indent=2,allow_nan=False)+'\n',encoding='utf8')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.saves=0;self.messages=[]
        self.s=SimpleNamespace(STATE=workflows.empty(),WORK=self.root,JOB={'busy':False},progress=self.messages.append)
        self.s.STATE.update(job_items=[],scene_objects=[],scan_links=[])
        def save():
            self.saves+=1;projects.atomic_json(self.root/'project.json',workflows.serial(self.s))
        self.s.save=save
        self.s.start_job=lambda action,*args,**kwargs:action()
        self.responses=[];self.h=SimpleNamespace(send=self.responses.append)

    def tearDown(self):self.temp.cleanup()

    def add_scan(self,ident,points):
        path=self.root/(ident+'.xyz64');np.asarray(points,'<f8').tofile(path)
        meta=dict(file=str(path),name=ident,count=len(points),minimum=points.min(0).tolist(),maximum=points.max(0).tolist())
        row=dict(id=ident,kind='scan',name=ident,visible=True,meta=meta)
        self.s.STATE['job_items'].append(row);return row

    def post(self,body):
        return scan_geometry.post(self.s,self.h,'/api/scan-geometry',body)

    def test_banded_smoothing_matches_dense_objective_with_gaps_and_large_offsets(self):
        coordinate_error=0.;shift_error=0.;cases=0
        for n in (2,3,4,11,127):
            for ds in (.1,1.37,5.):
                stations=2300.+np.arange(n)*ds;rng=np.random.default_rng(66+n)
                values=np.c_[np.sin(np.arange(n)*.04),np.arange(n)*ds,np.cos(np.arange(n)*.1)]
                values[n//2,0]+=1.4;values+=np.array([423000.,340000.,100.])
                for gaps in (False,True):
                    weights=.25+rng.random(n)
                    if gaps and n>5:weights[n//3:2*n//3]=0.;weights[:2]=0.;weights[-2:]=0.
                    for amount in (0,1,4):
                        expected,expected_shift=dense_reference(stations,values,weights,amount)
                        result,shift=tunnel_axis_fit.smooth(stations,values,weights,amount)
                        np.testing.assert_allclose(result,expected,rtol=0,atol=2e-8)
                        np.testing.assert_allclose(shift,expected_shift,rtol=0,atol=2e-8)
                        coordinate_error=max(coordinate_error,float(abs(result-expected).max()))
                        shift_error=max(shift_error,float(abs(shift-expected_shift).max()));cases+=1
        # The no-support behavior remains unchanged as well.
        points=np.ones((8,3))*423000.
        result,shift=tunnel_axis_fit.smooth(np.arange(8),points,np.zeros(8),4)
        np.testing.assert_array_equal(result,points);np.testing.assert_array_equal(shift,np.zeros(8))
        EVIDENCE['dense_oracle']=dict(cases=cases,maximum_coordinate_difference_m=coordinate_error,
            maximum_shift_difference_m=shift_error,absolute_tolerance_m=2e-8,
            varied_node_counts=[2,3,4,11,127],varied_station_intervals_m=[.1,1.37,5.],
            smoothing_values=[0,1,4],includes_weight_gaps_outliers_and_survey_offsets=True)

    def test_large_smoother_uses_only_five_diagonal_rows(self):
        n=100003;stations=np.arange(n)*2.;values=np.c_[np.sin(stations*.001),stations,np.cos(stations*.001)]
        shapes=[];solve=tunnel_axis_fit.solve_banded
        def observed(bandwidth,a,b,**kwargs):
            shapes.append(list(a.shape));self.assertEqual(bandwidth,(2,2));return solve(bandwidth,a,b,**kwargs)
        with patch.object(tunnel_axis_fit,'solve_banded',side_effect=observed), \
                patch.object(tunnel_axis_fit.np,'eye',side_effect=AssertionError('Dense identity allocation')), \
                patch.object(tunnel_axis_fit.np,'diag',side_effect=AssertionError('Dense diagonal allocation')), \
                patch.object(tunnel_axis_fit.np.linalg,'solve',side_effect=AssertionError('Dense system solve')):
            result,shift=tunnel_axis_fit.smooth(stations,values,np.ones(n),2)
        self.assertTrue(np.isfinite(result).all());self.assertTrue(np.isfinite(shift).all())
        self.assertEqual(shapes,[[5,n]]*4)
        EVIDENCE['large_smoother']=dict(nodes=n,coefficient_shape=[5,n],robust_passes=4,dense_matrix_allocations=False)

    def test_real_lining_over_300_nodes_and_200_sources_can_estimate_save_and_use(self):
        origin=np.array([423000.,340000.,100.]);interval=2.;radius=3.5
        stations=np.arange(0.,650.01,2.);angles=np.arange(96)*2*np.pi/96+.017
        s,a=np.meshgrid(stations,angles,indexing='ij')
        points=origin+np.c_[(radius*np.cos(a)).ravel(),s.ravel(),(radius*np.sin(a)).ravel()]
        ids=[];originals={}
        for number,part in enumerate(np.array_split(points,201)):
            ident=f'setup-{number:03}';row=self.add_scan(ident,part);ids.append(ident)
            path=Path(row['meta']['file']);originals[path]=hashlib.sha256(path.read_bytes()).hexdigest()
        self.s.STATE['scan_links']=[dict(id='link-'+str(i),a=ids[i-1],b=ids[i],status='accepted') for i in range(1,len(ids))]
        started=time.monotonic()
        self.post(dict(action='estimate',id=ids[0],ids=ids,interval=interval,smoothing=1,start=1000.,name='Long controlled lining'))
        result=copy.deepcopy(self.s.STATE['estimated_alignment']);rows=np.asarray(result['rows'])
        self.assertGreater(len(rows),300);self.assertEqual(result['source_count'],len(points));self.assertEqual(result['sample_count'],len(points))
        self.assertEqual(len(result['source_ids']),201);self.assertEqual(result['interval'],interval)
        self.assertTrue(np.isfinite(rows).all());self.assertTrue((np.diff(rows[:,0])>0).all())
        self.assertGreater(sum(q['supported'] for q in result['quality']),.95*len(rows))
        centre_error=float(np.max(np.linalg.norm(rows[:,[1,3]]-origin[[0,2]],axis=1)))
        radius_error=float(np.max(abs(rows[:,4]-radius)))
        self.assertLess(centre_error,.005);self.assertLess(radius_error,.007)
        np.testing.assert_allclose(np.diff(rows[:,2]),interval,atol=2e-6,rtol=0)
        self.assertTrue(any('not the design alignment' in note for note in result['warnings']))
        self.post(dict(action='save_estimate',revision=result['id'],rows=result['rows'],name='Reviewed long lining'))
        self.assertTrue(self.s.STATE['estimated_alignment']['reviewed'])
        self.post(dict(action='use_estimate',revision=result['id'],rows=result['rows']))
        axis=self.s.STATE['axis'];self.assertEqual(len(axis.a),len(rows))
        np.testing.assert_array_equal(axis.a,rows)
        saved=json.loads((self.root/'project.json').read_text());self.assertEqual(len(saved['axis']['rows']),len(rows))
        self.assertEqual(self.s.STATE['axis_provenance']['kind'],'scan_estimate')
        self.assertEqual({p:hashlib.sha256(p.read_bytes()).hexdigest() for p in originals},originals)
        EVIDENCE['actual_lining_workflow']=dict(nodes=len(rows),source_scans=len(ids),source_points=len(points),
            fitting_sample_points=result['sample_count'],requested_interval_m=interval,
            maximum_axis_interval_difference_m=float(np.max(abs(np.diff(rows[:,2])-interval))),
            maximum_centre_error_m=centre_error,maximum_radius_error_m=radius_error,
            supported_sections=sum(q['supported'] for q in result['quality']),
            estimate=True,save_estimate=True,use_estimate=True,persisted_axis_nodes=len(saved['axis']['rows']),
            original_source_bytes_unchanged=True,actual_lining_algorithm=True,seconds=time.monotonic()-started)

    def test_source_and_node_validity_guards_remain(self):
        p=np.array([[0.,0.,0.],[1.,1.,1.],[2.,2.,2.]])
        a=self.add_scan('a',p);b=self.add_scan('b',p+5.)
        for ids in ([],['a','a'],['a',{}],['a',''],['a','missing']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                self.post(dict(action='estimate',id='a',ids=ids))
        with self.assertRaisesRegex(ValueError,'connected'):
            self.post(dict(action='estimate',id='a',ids=['a','b']))
        self.s.STATE['scan_links']=[dict(id='link',a='a',b='b')]
        self.s.STATE['job_items'][1]['merge_stale']='Registration changed'
        with self.assertRaisesRegex(ValueError,'predates'):
            self.post(dict(action='estimate',id='a',ids=['a','b']))
        # A failed worker replaces STATE with its rollback snapshot, so address
        # current items rather than stale fixture references for later checks.
        self.s.STATE['job_items'][1].pop('merge_stale',None)
        self.s.STATE['estimated_alignment']=dict(id='estimate',source_name='a',source_id='a',rows=[])
        bad_rows=[[[0.,0.,0.,0.,1.]],[[0.,0.,0.,0.,1.],[0.,0.,1.,0.,1.]],
                  [[0.,0.,0.,0.,1.],[1.,0.,1.,0.,0.]],[[0.,0.,0.,0.,1.],[1.,0.,float('nan'),0.,1.]]]
        for action in ('save_estimate','use_estimate'):
            for rows in bad_rows:
                before=copy.deepcopy(self.s.STATE)
                with self.subTest(action=action,rows=rows),self.assertRaisesRegex(ValueError,'finite nodes'):
                    self.post(dict(action=action,revision='estimate',rows=rows))
                self.assertEqual(self.s.STATE,before)


if __name__=='__main__':unittest.main()
