"""Registered cleaning/meshing keeps measured IDs and one coordinate frame."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import exchange, feature_selection, jobdata, mesh_sampling, meshing, preview_index, registration_pose
import scan_export, surface_api, surface_streaming, workflows


class RegisteredProcessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        x,y=np.meshgrid(np.arange(20)/16,np.arange(20)/16)
        self.points=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        self.attrs=np.c_[np.arange(len(self.points)),np.tile([100,150,200],(len(self.points),1))].astype('<f4')
        path=self.root/'source.xyz64';attr=self.root/'source.attr32';mask_path=self.root/'source.mask'
        self.points.astype('<f8').tofile(path);self.attrs.tofile(attr)
        self.mask=np.zeros(len(self.points),'u1');self.mask[[0,43,217]]=1;self.mask.tofile(mask_path)
        self.meta=dict(name='registered setup',file=str(path),attributes_file=str(attr),count=len(self.points),
                       minimum=self.points.min(0).tolist(),maximum=self.points.max(0).tolist(),mask_file=str(mask_path),
                       attribute_counts=[len(self.points)]*4,attribute_min=self.attrs.min(0).tolist(),attribute_max=self.attrs.max(0).tolist())
        self.R=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
        self.t=np.array([180000.,399000.,50.]);registration_pose.set_pose(self.meta,self.R,self.t)
        self.world=self.points@self.R.T+self.t
        state=workflows.empty();state.update(surface_source=self.meta,cloud=copy.deepcopy(self.meta))
        self.s=SimpleNamespace(STATE=state,WORK=self.root,SELECTION=None,JOB={'busy':False},progress=lambda _:None,save=lambda:None)
        self.before={p:p.read_bytes() for p in (path,attr,mask_path)}
        surface_api.CANCEL.clear()

    def tearDown(self):
        for p,raw in self.before.items():self.assertEqual(p.read_bytes(),raw)
        self.tmp.cleanup()

    def selection(self):
        return dict(mode='box',minimum=[self.t[0]-.65,self.t[1]+.2,self.t[2]-.1],
                    maximum=[self.t[0]-.1,self.t[1]+.9,self.t[2]+.1])

    def crafted(self):
        ids=np.flatnonzero(self.mask==0).astype('<i8');labels=np.zeros(len(ids),'<i4')
        idpath=self.root/'result.surface_ids';labelpath=self.root/'result.surface_labels'
        ids.tofile(idpath);labels.tofile(labelpath)
        r=dict(id='result',revision='initial',created_at=1,source_kind='survey',source_meta=copy.deepcopy(self.meta),
               point_ids_file=str(idpath),labels_file=str(labelpath),statistics=dict(input_points=len(ids),kept_points=len(ids),noise_points=0),
               clusters=[dict(id=0,points=len(ids),centre=self.world[ids].mean(0).tolist(),automatic_keep=True)],kept=[0],
               review_log=[],settings={},selection={'mode':'full'},warnings=[],method='known frame fixture',doi='',outputs={})
        self.s.STATE['surface_result']=r
        return r,ids

    def test_cleaning_count_and_both_streams_share_world_selection_and_original_ids(self):
        selection=self.selection();expected=np.flatnonzero(jobdata.selection_mask(self.world,selection)&(self.mask==0))
        count,_=surface_api._area_count(self.s,self.meta,'separate',selection)
        self.assertEqual(count,len(expected));self.assertGreater(count,50)
        for reader in (surface_api._area_batches,surface_streaming.batches):
            with self.subTest(reader=reader.__module__):
                batches=list(reader(self.s,self.meta,'separate',selection,57))
                ids=np.concatenate([x[0] for x in batches]);q=np.concatenate([x[1] for x in batches])
                np.testing.assert_array_equal(ids,expected);np.testing.assert_array_equal(q,self.world[expected])
        # Full mode with a viewport clip must use the identical world bounds.
        clipped={'mode':'full','clip_box':{k:v for k,v in selection.items() if k!='mode'}}
        count,_=surface_api._area_count(self.s,self.meta,'separate',clipped)
        self.assertEqual(count,len(expected))

    def test_cleaning_preview_manual_review_and_filtered_export_retain_pose_once(self):
        r,ids=self.crafted();preview=surface_api.preview(self.s)
        rows=np.asarray(preview['points']);np.testing.assert_array_equal(rows[:,:3]+preview['origin'],self.world[rows[:,4].astype(int)])
        source=surface_api.source_preview(self.s,'separate');rows=np.asarray(source['points'])
        np.testing.assert_array_equal(rows[:,:3]+source['origin'],self.world[rows[:,4].astype(int)])
        lo,hi=registration_pose.world_bounds(self.meta)
        np.testing.assert_array_equal(source['minimum'],lo);np.testing.assert_array_equal(source['maximum'],hi)
        self.assertEqual(len(surface_api.status(self.s)['sources']),1)
        expected=jobdata.selection_mask(self.world[ids],self.selection())
        surface_api.review_selection(self.s,{'revision':r['revision'],'keep':False,'selection':self.selection()})
        reviewed=self.s.STATE['surface_result'];labels=np.fromfile(reviewed['labels_file'],'<i4')
        np.testing.assert_array_equal(labels==1,expected)
        removed=next(c for c in reviewed['clusters'] if c['id']==1)
        np.testing.assert_allclose(removed['centre'],self.world[ids[expected]].mean(0),atol=1e-9)
        output=surface_api.filtered_cloud(self.s);kept=ids[~expected]
        np.testing.assert_array_equal(np.fromfile(output['file'],'<f8').reshape(-1,3),self.points[kept])
        np.testing.assert_array_equal(np.fromfile(output['attributes_file'],'<f4').reshape(-1,4),self.attrs[kept])
        np.testing.assert_array_equal(registration_pose.get(output)[0],self.R);np.testing.assert_array_equal(registration_pose.get(output)[1],self.t)
        exported=self.root/'kept.xyz'
        scan_export.export_cloud(output,exported,'xyz',self.root,lambda _:None,exchange.export_cloud)
        np.testing.assert_allclose(np.loadtxt(exported),self.world[kept],atol=1e-9,rtol=0)

    def test_review_guards_invalidate_when_only_registration_pose_changes(self):
        r,_=self.crafted();item={'meta':copy.deepcopy(self.meta)}
        before=surface_api.patch_fingerprint(item)
        registration_pose.compose(item['meta'],np.eye(3),[1.,0.,0.])
        self.assertFalse(surface_api.same_geometry(item['meta'],r['source_meta']))
        self.assertNotEqual(before,surface_api.patch_fingerprint(item))
        self.s.STATE['cloud']=item['meta']
        self.assertFalse(surface_api.public_result(self.s,r)['can_apply'])
        with self.assertRaisesRegex(ValueError,'differs'):
            surface_api.apply(self.s,{'revision':r['revision'],'reviewed':True})

    def test_actual_cleaning_run_classifies_registered_coordinates(self):
        surface_api.run(self.s,{'source':'separate','settings':{'method':'smrg','resolution':.25},'selection':{'mode':'full'}})
        r=self.s.STATE['surface_result'];ids=np.fromfile(r['point_ids_file'],'<i8')
        self.assertEqual(len(ids),int(np.count_nonzero(self.mask==0)))
        self.assertTrue(r['clusters'])
        for cluster in r['clusters']:
            centre=np.asarray(cluster['centre'])
            self.assertTrue((centre>=self.world.min(0)-1e-8).all());self.assertTrue((centre<=self.world.max(0)+1e-8).all())

    def test_mesh_reader_and_created_mesh_use_registered_frame_with_cached_bounds(self):
        builder=preview_index.BoundsBuilder(self.meta);builder.add(0,self.points);builder.finish()
        selection=self.selection();expected=np.flatnonzero(jobdata.selection_mask(self.world,selection)&(self.mask==0))
        source=mesh_sampling.Source(self.meta,selection)
        batches=list(source.chunks());q=np.concatenate([x[0] for x in batches]);ids=np.concatenate([x[1] for x in batches])
        np.testing.assert_array_equal(ids,expected);np.testing.assert_array_equal(q,self.world[expected])
        mesh=meshing.create(self.meta,self.root,'registered mesh',method='delaunay',selection=selection,max_points=1000,max_edge=.5)
        vertices,_=meshing._read_mesh(mesh)
        self.assertTrue((vertices>=self.world[expected].min(0)-1e-8).all());self.assertTrue((vertices<=self.world[expected].max(0)+1e-8).all())
        self.assertNotIn('registration_rotation',mesh)
        self.assertEqual(mesh['mesh_summary']['source_points'],len(expected))

    def test_local_feature_seed_revision_and_refinement_use_registered_frame(self):
        item=dict(id='scan',kind='scan',name=self.meta['name'],meta=self.meta,visible=True)
        self.s.STATE['job_items']=[item]
        seed=self.world[210];selection=self.selection();cfg=feature_selection.settings({'radius_m':.6,'connection_mm':90})
        p,ids=feature_selection.local_points(item,seed,cfg,lambda _:None,selection)
        expected=np.flatnonzero((np.linalg.norm(self.world-seed,axis=1)<=cfg['radius_m'])&
                                jobdata.selection_mask(self.world,selection)&(self.mask==0))
        np.testing.assert_array_equal(ids,expected);np.testing.assert_array_equal(p,self.world[ids])
        row=feature_selection.preview(self.s,dict(source_id=item['id'],mode='region',seed=seed.tolist(),
            source_revision=registration_pose.revision(self.meta),settings={'radius_m':.6,'connection_mm':90}))
        cache_ids=np.fromfile(row['point_ids_file'],'<i8');cached=np.fromfile(row['points_file'],'<f8').reshape(-1,3)
        np.testing.assert_array_equal(cached,self.world[cache_ids])
        feature_selection.refine(self.s,dict(preview_id=row['id'],revision=row['revision'],operation='keep',selection=selection))
        selected=np.fromfile(row['selected_file'],'u1').astype(bool)
        self.assertTrue((~selected|jobdata.selection_mask(cached,selection)).all())
        registration_pose.compose(item['meta'],np.eye(3),[1.,0.,0.])
        with self.assertRaisesRegex(ValueError,'scan or its visible area changed'):
            feature_selection.current(self.s,dict(preview_id=row['id'],revision=row['revision']))


if __name__=='__main__':unittest.main()
