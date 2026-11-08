# Copyright (c) 2026 Damian Gulczynski.
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import copy
import math
import tempfile
import unittest

import numpy as np

import exchange
import job_api
import jobdata
import mesh_to_cloud as sampler
import workflows


def mesh(root, vertices=None, faces=None, colours=None):
    vertices = np.asarray(vertices if vertices is not None else
        [[0,0,0],[1,0,0],[0,1,0],[10,0,0],[13,0,0],[10,3,0]], dtype='<f8')
    faces = np.asarray(faces if faces is not None else [[0,1,2],[3,4,5],[0,0,1]], dtype='<u4')
    vp, fp = root/'source.vertices64', root/'source.faces32'
    vertices.tofile(vp); faces.tofile(fp)
    out = dict(name='Fixture mesh', vertices_file=str(vp), faces_file=str(fp),
               vertices=len(vertices), triangles=len(faces), units='metres',
               minimum=vertices.min(0).tolist(), maximum=vertices.max(0).tolist())
    if colours is not None:
        path=root/'source.vcolour32';np.asarray(colours,dtype='<f4').tofile(path)
        out['vertex_colours_file']=str(path)
    return out


class MeshToCloudTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        job_api.CANCEL.clear()

    def tearDown(self):
        job_api.CANCEL.clear();self.temp.cleanup()

    def test_area_weighted_sampling_counts_disconnected_components_and_skips_degenerate_faces(self):
        meta=mesh(self.root);original={p.name:p.read_bytes() for p in self.root.iterdir()}
        info=sampler.estimate(meta,{'mode':'count','count':10000})
        self.assertEqual(info['surface_area'],5.);self.assertEqual(info['valid_triangles'],2)
        self.assertEqual(info['degenerate_triangles_skipped'],1)
        result=sampler.sample(meta,self.root,{'mode':'count','count':10000},'Sampled geometry',prepared=info)
        points=np.fromfile(result['file'],'<f8').reshape(-1,3)
        self.assertEqual(len(points),10000);small=points[:,0]<5
        self.assertEqual(int(small.sum()),1000);self.assertEqual(int((~small).sum()),9000)
        self.assertTrue(np.all(points[small,0]+points[small,1]<=1+1e-12))
        self.assertTrue(np.all((points[~small,0]-10)+points[~small,1]<=3+1e-12))
        self.assertTrue(np.all(points[:,1]>=0));self.assertTrue(np.all(points[:,2]==0))
        self.assertNotIn('attributes_file',result);self.assertTrue(result['generated_geometry'])
        self.assertIn('not survey observations',result['position_status'])
        for name,data in original.items():self.assertEqual((self.root/name).read_bytes(),data)

    def test_spacing_estimate_drives_exact_count_and_storage(self):
        meta=mesh(self.root);info=sampler.estimate(meta,{'mode':'spacing','spacing':.3})
        self.assertEqual(info['count'],math.ceil(5/.3**2))
        result=sampler.sample(meta,self.root,{'mode':'spacing','spacing':.3},'Spacing sample',prepared=info)
        self.assertEqual(result['count'],info['count'])
        self.assertEqual(sum(Path(result[k]).stat().st_size for k in ('file','preview_file')),info['estimated_cache_bytes'])
        self.assertIn('minimum point separation is not enforced',info['spacing_note'])

    def test_saved_transformed_large_coordinates_are_preserved_without_double_transform(self):
        base=np.array([[0.,0.,0.],[2.,0.,0.],[0.,3.,0.]])
        angle=.63;rotation=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        meta=mesh(self.root,base,[[0,1,2]])
        result=sampler.sample(meta,self.root,{'mode':'count','count':2000},'Base')
        original=np.fromfile(result['file'],'<f8').reshape(-1,3)
        transformed=base@rotation.T*1.7+np.array([6500000.,10500000.,133.])
        meta=mesh(self.root,transformed,[[0,1,2]])
        result=sampler.sample(meta,self.root,{'mode':'count','count':2000},'Transformed')
        actual=np.fromfile(result['file'],'<f8').reshape(-1,3)
        np.testing.assert_allclose(actual,original@rotation.T*1.7+[6500000.,10500000.,133.],rtol=0,atol=3e-9)

    def test_vertex_rgb_import_interpolation_and_export_do_not_invent_intensity(self):
        obj=self.root/'colour.obj';obj.write_text('v 0 0 0 1 0 0\nv 1 0 0 0 1 0\nv 0 1 0 0 0 1\nf 1 2 3\n')
        meta=jobdata.import_obj(obj,self.root);self.assertEqual(meta['coloured_vertices'],3)
        jobdata.validate(meta,mesh=True)
        result=sampler.sample(meta,self.root,{'mode':'count','count':1000},'Colour sample')
        p=np.fromfile(result['file'],'<f8').reshape(-1,3);a=np.fromfile(result['attributes_file'],'<f4').reshape(-1,4)
        self.assertTrue(np.isnan(a[:,0]).all());self.assertEqual(result['attribute_counts'],[0,1000,1000,1000])
        np.testing.assert_allclose(a[:,1:],255*np.c_[1-p[:,0]-p[:,1],p[:,0],p[:,1]],atol=2e-5,rtol=0)
        dest=self.root/'sample.csv';export=exchange.export_cloud(result,dest,'csv')
        self.assertEqual(dest.read_text().splitlines()[0],'e,n,h,red,green,blue')
        self.assertEqual(len(dest.read_text().splitlines()),1001)
        self.assertTrue(any('not measured scan observations' in text for text in export['warnings']))
        ply=self.root/'sample.ply';exchange.export_cloud(result,ply,'ply')
        self.assertIn('comment Generated mesh samples; not measured scan observations',ply.read_text())

    def test_missing_vertex_colour_stays_unavailable_in_affected_triangles(self):
        colours=np.array([[255,0,0]]*3+[[np.nan]*3]*3)
        meta=mesh(self.root,colours=colours)
        result=sampler.sample(meta,self.root,{'mode':'count','count':1000},'Partial colours')
        p=np.fromfile(result['file'],'<f8').reshape(-1,3);a=np.fromfile(result['attributes_file'],'<f4').reshape(-1,4)
        self.assertTrue(np.isnan(a[p[:,0]>5]).all());self.assertEqual(result['attribute_counts'],[0,100,100,100])

    def test_large_output_is_chunked_and_face_batch_boundaries_do_not_change_samples(self):
        meta=mesh(self.root);count=250003;seen=[];gathers=[]
        original=jobdata._display_rows
        def gather(path,n,ids,*args):
            gathers.append(len(ids));return original(path,n,ids,*args)
        def progress(message):
            if message.startswith('Creating sampled cloud:'):seen.append(int(message.split(':')[1].split('/')[0].replace(',','')))
        with mock.patch.object(jobdata,'_display_rows',gather):
            a=sampler.sample(meta,self.root,{'mode':'count','count':count},'A',progress)
        with mock.patch.object(sampler,'FACE_BLOCK',1):
            b=sampler.sample(meta,self.root,{'mode':'count','count':count},'B')
        self.assertTrue(all(n<=100000 for n in np.diff([0,*seen])))
        self.assertTrue(all(n<=98304 for n in gathers));self.assertLessEqual(a['preview_count'],250000)
        self.assertEqual(Path(a['file']).read_bytes(),Path(b['file']).read_bytes())

    def test_cancellation_and_late_failure_remove_every_new_asset(self):
        meta=mesh(self.root);before={p.name:p.read_bytes() for p in self.root.iterdir()}
        for prefix in ('Creating sampled cloud:','Sampled cloud ready:'):
            def progress(message):
                if message.startswith(prefix):raise jobdata.Cancelled('Cancelled fixture operation')
            with self.assertRaises(jobdata.Cancelled):sampler.sample(meta,self.root,{'mode':'count','count':200001},'Cancelled',progress)
            self.assertEqual({p.name:p.read_bytes() for p in self.root.iterdir()},before)

    def test_invalid_geometry_settings_disk_space_and_stale_estimates_fail_cleanly(self):
        meta=mesh(self.root);info=sampler.estimate(meta,{'mode':'count','count':10})
        for values in ({'mode':'count','count':1.2},{'mode':'count','count':True},{'mode':'spacing','spacing':float('nan')},{'mode':'spacing','spacing':1e-300}):
            with self.assertRaises(ValueError):sampler.estimate(meta,values)
        with self.assertRaisesRegex(ValueError,'changed'):sampler.sample(meta,self.root,{'mode':'count','count':11},'Stale',prepared=info)
        with mock.patch.object(jobdata,'room',side_effect=ValueError('No free space')),self.assertRaisesRegex(ValueError,'free space'):
            sampler.sample(meta,self.root,{'mode':'count','count':10},'No disk',prepared=info)
        meta=mesh(self.root,faces=[[0,0,1]])
        with self.assertRaisesRegex(ValueError,'non-degenerate'):sampler.estimate(meta,{'mode':'count','count':10})
        meta=mesh(self.root,faces=[[0,1,99]])
        with self.assertRaisesRegex(ValueError,'missing vertex'):sampler.estimate(meta,{'mode':'count','count':10})
        self.assertEqual(len(list(self.root.iterdir())),2)

    def server(self,meta,fail=False):
        state=workflows.empty();state['job_items']=[dict(id='mesh',kind='mesh',name='Fixture',meta=meta,visible=True,bundle_id=None)]
        def save():
            if fail:raise OSError('Simulated save failure')
        server=SimpleNamespace(STATE=state,WORK=self.root,JOB={'busy':False},progress=lambda _:None,save=save)
        server.start_job=lambda function,kind,**kwargs:function()
        handler=SimpleNamespace(send=lambda value:None)
        return server,handler

    def test_production_action_creates_a_separate_scene_cloud(self):
        meta=mesh(self.root);server,handler=self.server(meta)
        job_api.post(server,handler,'/api/job-action',{'action':'mesh_to_cloud_estimate','id':'mesh','mode':'count','count':1234})
        estimate=server.MESH_TO_CLOUD_ESTIMATE
        job_api.post(server,handler,'/api/job-action',{'action':'mesh_to_cloud','id':'mesh','mode':'count','count':1234,'estimate_revision':estimate['revision']})
        self.assertEqual(len(server.STATE['job_items']),2)
        self.assertEqual(server.STATE['job_items'][0]['meta'],meta)
        output=server.STATE['job_items'][1];self.assertEqual(output['kind'],'scan');self.assertEqual(output['meta']['count'],1234)
        self.assertTrue(output['meta']['generated_geometry']);self.assertEqual(output['meta']['source_mesh_id'],'mesh')

    def test_scene_save_failure_rolls_back_state_and_generated_files(self):
        meta=mesh(self.root);server,handler=self.server(meta,fail=True)
        job_api.post(server,handler,'/api/job-action',{'action':'mesh_to_cloud_estimate','id':'mesh','mode':'count','count':1234})
        before=copy.deepcopy(server.STATE);files={p.name:p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaisesRegex(OSError,'save failure'):
            job_api.post(server,handler,'/api/job-action',{'action':'mesh_to_cloud','id':'mesh','mode':'count','count':1234,'estimate_revision':server.MESH_TO_CLOUD_ESTIMATE['revision']})
        self.assertEqual(server.STATE,before);self.assertEqual({p.name:p.read_bytes() for p in self.root.iterdir()},files)


if __name__=='__main__':unittest.main()
