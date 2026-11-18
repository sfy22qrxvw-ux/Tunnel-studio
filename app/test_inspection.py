"""Independent known-value and transactional checks for comparison orchestration."""
from pathlib import Path
from types import SimpleNamespace
import copy
import csv
import json
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import cad_api
import cad_geometry
import inspection
import inspection_api
import jobdata
import projects
import workflows
import scan_edits


class Reply:
    def send(self, value, *args, **kwargs):
        self.value = value
    def send_file(self, path, *args, **kwargs):
        self.file = Path(path)


class Workspace:
    def __init__(self, root):
        self.WORK = root
        self.STATE = workflows.empty()
        self.STATE.update(job_items=[], cad=cad_api.empty())
        self.JOB = {'busy': False, 'error': None, 'cancelled': False}
        self.JOB_CANCEL_FILE = root / 'cancel'
        self.fail_save = False
        self.messages = []
    def save(self):
        if self.fail_save:
            raise OSError('Injected save failure')
        (self.WORK / 'project.json').write_text(json.dumps(self.STATE, allow_nan=False))
    def progress(self, value):
        if self.JOB_CANCEL_FILE.exists():
            raise jobdata.Cancelled('Stopped')
        self.messages.append(value)
    def start_job(self, action, *args, **kwargs):
        self.JOB.update(busy=True, error=None, cancelled=False)
        try:
            action()
        except jobdata.Cancelled:
            self.JOB['cancelled'] = True
        except Exception as error:
            self.JOB['error'] = str(error)
        finally:
            self.JOB['busy'] = False


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Workspace(self.root)
        self.xy = np.array([[.1, .1], [.7, .1], [.1, .7], [.4, .4]], float)
        self.offset = np.array([423000., 340000., 120.])
        self.cloud = self.scan('source', np.c_[self.xy, np.full(4, .03)] + self.offset)
        self.reference_cloud = self.scan('reference-cloud', np.c_[self.xy, np.zeros(4)] + self.offset)
        self.mesh = self.mesh_item('source-mesh', .02)
        self.reference_mesh = self.mesh_item('reference-mesh', 0.)
        box = cad_geometry.build(dict(kind='box', origin=(self.offset + [0, 0, -.2]).tolist(),
                                      size=[1., 1., .2]))
        self.cad = cad_api.object_record(box, {'name': 'Reference CAD'})
        self.s.STATE['cad']['objects'].append(self.cad)
        self.s.STATE['structures'].append(dict(
            id='plane', name='Reference plane', kind='plane',
            e=self.offset[0]+.5, n=self.offset[1]+.5, h=self.offset[2],
            length=1., width=1., yaw=0., visible=True))
    def tearDown(self):
        self.temp.cleanup()
    def scan(self, ident, points, mask=None):
        points=np.asarray(points, dtype='<f8')
        path=self.root/(ident+'.xyz64');points.tofile(path)
        finite=points[np.isfinite(points).all(1)]
        meta=dict(file=str(path),count=len(points),name=ident,
                  minimum=finite.min(0).tolist(),maximum=finite.max(0).tolist())
        if mask is not None:
            mp=self.root/(ident+'.mask');np.asarray(mask,'u1').tofile(mp);meta['mask_file']=str(mp)
        item=dict(id=ident,name=ident,kind='scan',meta=meta,visible=True)
        self.s.STATE['job_items'].append(item);return item
    def mesh_item(self, ident, z):
        vertices=np.array([[0,0,z],[1,0,z],[1,1,z],[0,1,z]],dtype='<f8')+self.offset
        faces=np.array([[0,1,2],[0,2,3]],dtype='<u4')
        vp=self.root/(ident+'.vertices');fp=self.root/(ident+'.faces')
        vertices.tofile(vp);faces.tofile(fp)
        item=dict(id=ident,name=ident,kind='mesh',visible=True,meta=dict(
            name=ident,vertices_file=str(vp),faces_file=str(fp),vertices=4,triangles=2,
            minimum=vertices.min(0).tolist(),maximum=vertices.max(0).tolist()))
        self.s.STATE['job_items'].append(item);return item
    def invoke(self, mode, source, reference, **settings):
        h=Reply()
        inspection_api.post(self.s,h,'/api/inspect',dict(
            mode=mode,source_id=source,reference_id=reference,tolerance_mm=25,
            spacing_m=.4,**settings))
        self.assertIsNone(self.s.JOB['error'],self.s.JOB['error'])
        self.assertFalse(self.s.JOB['cancelled'])
        return self.s.STATE['inspection_result']

    def test_six_modes_known_parallel_offsets_and_result_csv(self):
        cases=[
            ('cloud_mesh',self.cloud['id'],self.reference_mesh['id'],30.),
            ('cloud_cloud',self.cloud['id'],self.reference_cloud['id'],30.),
            ('cloud_cad',self.cloud['id'],self.cad['id'],30.),
            ('cloud_geometry',self.cloud['id'],'structure:plane',30.),
            ('mesh_mesh',self.mesh['id'],self.reference_mesh['id'],20.),
            ('mesh_cad',self.mesh['id'],self.cad['id'],20.),
        ]
        for mode,source,reference,expected in cases:
            with self.subTest(mode=mode):
                result=self.invoke(mode,source,reference)
                self.assertAlmostEqual(result['mean_mm'],expected,places=4)
                self.assertAlmostEqual(result['rms_mm'],expected,places=4)
                self.assertAlmostEqual(result['median_mm'],expected,places=4)
                self.assertAlmostEqual(result['p95_absolute_mm'],expected,places=4)
                self.assertAlmostEqual(result['within_percent'],100. if expected<25 else 0.)
                preview=inspection.preview(result,maximum=5)
                self.assertLessEqual(preview['sample_count'],5)
                self.assertEqual(preview['result_id'],result['id'])
                np.testing.assert_allclose(preview['deviations_mm'],expected,atol=1e-4)
                h=Reply();inspection_api.post(self.s,h,'/api/inspection-export',{'id':result['id']})
                self.assertIsNone(self.s.JOB['error'],self.s.JOB['error'])
                inspection_api.get(self.s,h,'/api/inspection.csv',{'id':[result['id']]})
                with h.file.open(newline='') as stream:rows=list(csv.DictReader(stream))
                self.assertEqual(len(rows),result['record_count'])
                self.assertEqual(rows[0]['comparison'],result['mode_label'])
                if mode.startswith('mesh'):
                    self.assertAlmostEqual(result['included_area_m2'],1.)

    def test_deleted_nonfinite_band_counts_and_original_indices(self):
        points=np.c_[np.linspace(.1,.8,8),np.full(8,.2),[.001,.01,.02,.03,.04,.08,np.nan,.5]]+self.offset
        source=self.scan('masked',points,[0,0,1,0,0,0,0,1])
        result=self.invoke('cloud_geometry',source['id'],'structure:plane',band_m=.05)
        self.assertEqual(result['retained_points'],6)
        self.assertEqual(result['deleted_points'],2)
        self.assertEqual(result['invalid_points'],1)
        self.assertEqual(result['evaluated'],5)
        self.assertEqual(result['used'],4)
        self.assertEqual(result['excluded_by_band'],1)
        rows=np.fromfile(result['results_file'],inspection.RECORD)
        self.assertEqual(rows['element_id'].tolist(),[0,1,3,4,5])
        expected=np.array([1.,10.,30.,40.])
        self.assertAlmostEqual(result['mean_mm'],float(expected.mean()),places=7)
        self.assertAlmostEqual(result['median_mm'],float(np.percentile(expected,50)),places=7)

    def test_changed_inputs_stale_and_export_rejected(self):
        result=self.invoke('cloud_cloud',self.cloud['id'],self.reference_cloud['id'])
        path=Path(self.cloud['meta']['file'])
        with path.open('r+b') as stream:
            stream.write(np.array([self.offset[0]+.15],'<f8').tobytes())
        current=inspection_api.current(self.s)
        self.assertTrue(current['stale'])
        with self.assertRaisesRegex(ValueError,'changed'):
            inspection_api.get(self.s,Reply(),'/api/inspection-preview',{})
        self.assertTrue(Path(result['results_file']).is_file())

    def test_count_cache_refresh_does_not_invalidate_saved_comparison(self):
        result=self.invoke('cloud_cloud',self.cloud['id'],self.reference_cloud['id'])
        for source in (self.cloud,self.reference_cloud):
            meta=source['meta']
            meta['retained_summary']=dict(revision='',count=meta['count'],
                source_signature=scan_edits._properties_signature(meta))
        current=inspection_api.current(self.s)
        self.assertFalse(current.get('stale'),current.get('stale_reason'))
        self.assertEqual(current['id'],result['id'])
        h=Reply();inspection_api.get(self.s,h,'/api/inspection-preview',{})
        self.assertEqual(h.value['used'],4)
        np.testing.assert_allclose(h.value['deviations_mm'],30.,atol=1e-4)
        # Named save/reopen can acquire or drop count caches independently of
        # immutable source geometry; neither should change this result's input.
        self.s.STATE=workflows.prepare_data(self.s,workflows.serial(self.s))
        for source in self.s.STATE['job_items']:source['meta'].pop('retained_summary',None)
        current=inspection_api.current(self.s)
        self.assertFalse(current.get('stale'),current.get('stale_reason'))
        self.assertEqual(current['id'],result['id'])

    def test_failed_and_cancelled_runs_preserve_previous(self):
        previous=self.invoke('cloud_cloud',self.cloud['id'],self.reference_cloud['id'])
        before=set(self.root.glob('*.inspection-results'))
        self.s.fail_save=True
        h=Reply();inspection_api.post(self.s,h,'/api/inspect',dict(
            mode='cloud_cloud',source_id=self.cloud['id'],reference_id=self.reference_cloud['id']))
        self.assertIn('save failure',self.s.JOB['error'])
        self.assertEqual(self.s.STATE['inspection_result']['id'],previous['id'])
        self.assertEqual(set(self.root.glob('*.inspection-results')),before)
        self.s.fail_save=False
        self.s.JOB_CANCEL_FILE.write_text('stop')
        inspection_api.post(self.s,h,'/api/inspect',dict(
            mode='cloud_cloud',source_id=self.cloud['id'],reference_id=self.reference_cloud['id']))
        self.assertTrue(self.s.JOB['cancelled'])
        self.assertEqual(self.s.STATE['inspection_result']['id'],previous['id'])
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_portable_reopen_retains_current_results_and_source_snapshots(self):
        previous=self.invoke('cloud_cad',self.cloud['id'],self.cad['id'])
        destination=self.root/'inspection.tstudio'
        projects.portable(self.root,workflows.serial(self.s),destination)
        unpacked=projects.unpack(self.root,destination)
        candidate=workflows.prepare_data(self.s,unpacked)
        other=Workspace(self.root);other.STATE=candidate
        current=inspection_api.current(other)
        self.assertFalse(current.get('stale'),current.get('stale_reason'))
        self.assertEqual(current['id'],previous['id'])
        self.assertNotEqual(current['results_file'],previous['results_file'])
        self.assertAlmostEqual(current['mean_mm'],30.,places=4)
        self.assertEqual(inspection.preview(current)['used'],4)

    def test_stale_result_is_not_revived_by_portable_reopen(self):
        self.invoke('cloud_cloud',self.cloud['id'],self.reference_cloud['id'])
        mask=self.root/'changed.mask';np.array([0,0,0,1],'u1').tofile(mask)
        self.cloud['meta']['mask_file']=str(mask)
        destination=self.root/'stale.tstudio'
        projects.portable(self.root,workflows.serial(self.s),destination)
        candidate=workflows.prepare_data(self.s,projects.unpack(self.root,destination))
        self.assertTrue(candidate['inspection_result']['stale'])

    def test_options_types_and_bad_pairs(self):
        options=inspection_api.options(self.s)
        self.assertEqual(len(options['modes']),6)
        cad=next(x for x in options['references'] if x['id']==self.cad['id'])
        self.assertIn('cloud_cad',cad['modes'])
        self.assertIn('cloud_geometry',cad['modes'])
        self.assertIn('Analytic',cad['representation'])
        with self.assertRaisesRegex(ValueError,'reference type'):
            inspection_api.post(self.s,Reply(),'/api/inspect',dict(
                mode='mesh_cad',source_id=self.mesh['id'],reference_id=self.reference_cloud['id']))

    def test_legacy_box_and_cylinder_global_placement(self):
        for kind in ('box','cylinder'):
            self.s.STATE['structures'].append(dict(id=kind,name=kind,kind=kind,
                e=423000.,n=340000.,h=120.,length=2.,width=2.,height=2.,radius=1.,yaw=30))
            reference=inspection_api.resolve(self.s,'structure:'+kind)
            evaluator=__import__('inspection_geometry').make_reference(reference)
            d,valid=evaluator.evaluate(np.array([[423000.,340000.,123.]]))
            self.assertTrue(valid.all());self.assertAlmostEqual(d[0],1.,places=10)


class QuantileTests(unittest.TestCase):
    def check_values(self,values,weights=None):
        values=np.asarray(values,dtype=float)
        weights=np.ones(len(values)) if weights is None else np.asarray(weights,dtype=float)
        weighted=not np.all(weights==1)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'stats'
            rows=np.empty(len(values),inspection.STAT_RECORD)
            rows['value'],rows['weight']=values,weights;rows.tofile(path)
            with patch.object(inspection.Quantiles,'LIMIT',5),patch.object(inspection.Quantiles,'BINS',4):
                q=inspection.Quantiles(path,len(values),float(weights.sum()),float(values.min()),
                    float(values.max()),weighted,lambda m:None,lambda:False)
                for p in (0,50,95,100):
                    for absolute in (False,True):
                        data=np.abs(values) if absolute else values
                        if weighted:
                            order=np.argsort(data,kind='stable')
                            index=min(len(data)-1,int(np.searchsorted(np.cumsum(weights[order]),
                                                                     weights.sum()*p/100,side='left')))
                            expected=float(data[order[index]])
                        else:expected=float(np.percentile(data,p))
                        self.assertEqual(q.percentile(p,absolute),expected,(p,absolute))
    def test_bounded_exact_uniform_and_skewed_distributions(self):
        self.check_values(np.r_[np.linspace(-4,9,67),np.zeros(50),[10000.,-9000.]])
    def test_two_adjacent_floats_and_constant_values(self):
        self.check_values([1.]*13+[np.nextafter(1.,2.)]*17)
        self.check_values([2.]*31)
    def test_area_weighted_cdf_is_not_unweighted_sample_percentile(self):
        self.check_values(np.arange(21)-8, np.linspace(.1,3.,21))


if __name__=='__main__':
    unittest.main()
