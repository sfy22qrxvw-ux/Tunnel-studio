"""Exact full-input geometry fitting with bounded owned binary reads."""
from contextlib import closing
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from scipy.optimize import least_squares  # Import before forbidding memory maps.

from bounded_rows import RowFile
import cad_api
import geometry_tools as geometry
import projects
import registration_pose
import workspace_tools
import workflows


class BoundedGeometry0183Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.s=SimpleNamespace(WORK=self.root,STATE=workflows.empty(),JOB={'busy':False},
                               progress=lambda message:None,SELECTION=None)
        self.s.STATE.update(job_items=[],cad=cad_api.empty())
        self.s.save=lambda:projects.atomic_json(self.root/'project.json',workflows.serial(self.s))

    def tearDown(self):self.tmp.cleanup()

    def source(self,points,attrs=None,excluded=None,name='source'):
        points=np.asarray(points,dtype='<f8');path=self.root/(name+'.xyz64');points.tofile(path)
        meta=dict(name=name,file=str(path),count=len(points),minimum=points.min(0).tolist(),maximum=points.max(0).tolist())
        if attrs is not None:
            p=self.root/(name+'.attr32');np.asarray(attrs,dtype='<f4').tofile(p);meta['attributes_file']=str(p)
        if excluded is not None:
            p=self.root/(name+'.mask');np.asarray(excluded,dtype='u1').tofile(p);meta['mask_file']=str(p)
        item=dict(id=name,kind='scan',name=name,meta=meta,visible=True,hidden_boxes=[],isolate_box=None)
        self.s.STATE['job_items'].append(item);return item

    def world_fixture(self):
        ids=np.arange(96);points=np.c_[.15*(ids%12),.2*(ids//12),.03*(ids%12)+.08*(ids//12)]
        attrs=np.c_[ids,ids+100,ids+200,ids+300];mask=np.zeros(len(points),'u1');mask[[2,17,81]]=1
        item=self.source(points,attrs,mask)
        angle=.51;r=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1.]])
        registration_pose.set_pose(item['meta'],r,[450000.,330000.,-20.])
        return item,registration_pose.points(item['meta'],points),attrs.astype('<f4'),mask

    def test_bounded_rows_preserve_sparse_order_and_duplicate_original_ids(self):
        values=np.arange(1200,dtype='<f8').reshape(400,3);path=self.root/'rows.xyz64';values.tofile(path)
        selected=np.array([399,0,65,65,129,8,398,64],dtype=np.int64)
        reads=[];read=np.fromfile
        def track(stream,dtype,count):reads.append(count);return read(stream,dtype,count=count)
        with mock.patch.object(np,'fromfile',side_effect=track),RowFile(path,'<f8',400,3,32) as source:
            np.testing.assert_array_equal(source.take(selected),values[selected])
            np.testing.assert_array_equal(source[100:113],values[100:113])
            with self.assertRaises(ValueError):source[:40]
        self.assertTrue(reads);self.assertLessEqual(max(reads),32*3)

    def test_bounded_reader_rejects_invalid_ids_and_detects_shortened_cache(self):
        path=self.root/'rows.xyz64';np.arange(60,dtype='<f8').reshape(20,3).tofile(path)
        with RowFile(path,'<f8',20,3,8) as source:
            for ids in (np.array([-1]),np.array([20]),np.array([.5]),np.array([2**63],dtype=np.uint64)):
                with self.subTest(ids=ids.tolist()):
                    with self.assertRaises(ValueError):source.take(ids)
            with path.open('r+b') as changed:changed.truncate(19*24)
            with self.assertRaisesRegex(ValueError,'changed'):source[16:20]
        self.assertIsNone(source.stream)

    def test_whole_scan_ignores_view_hiding_but_respects_masks_and_registered_positions(self):
        item,world,attrs,mask=self.world_fixture()
        item.update(hidden_boxes=[{'mode':'box','minimum':world.min(0).tolist(),'maximum':world.max(0).tolist()}])
        with mock.patch.object(np,'memmap',side_effect=AssertionError('No whole-source mapping')):
            out=list(workspace_tools.batches(self.s,{'ids':[item['id']],'whole':True},self.s.progress))
        np.testing.assert_array_equal(np.concatenate([x for x,_ in out]),world[mask==0])
        np.testing.assert_array_equal(np.concatenate([a for _,a in out]),attrs[mask==0])

    def test_review_rows_preserve_exact_order_visibility_and_attribute_identity(self):
        item,world,attrs,mask=self.world_fixture();meta=item['meta']
        ids=np.array([95,0,12,12,17,48,1,80],dtype='<i8');labels=np.array([2,1,2,2,2,1,2,2],dtype='<i4')
        ip=self.root/'review.ids';lp=self.root/'review.labels';ids.tofile(ip);labels.tofile(lp)
        self.s.STATE['surface_result']=dict(id='review',revision='v1',source_item_id=item['id'],source_meta=copy.deepcopy(meta),
            point_ids_file=str(ip),labels_file=str(lp),statistics={'input_points':len(ids)},clusters=[{'id':1},{'id':2}])
        request=dict(ids=[item['id']],review={'id':'review','revision':'v1','clusters':[2],'visible_clusters':[2]})
        with mock.patch.object(np,'memmap',side_effect=AssertionError('No full review mapping')):
            out=list(workspace_tools.batches(self.s,request,self.s.progress))
        expected=ids[(labels==2)&(mask[ids]==0)]
        np.testing.assert_array_equal(np.concatenate([x for x,_ in out]),world[expected])
        np.testing.assert_array_equal(np.concatenate([a for _,a in out]),attrs[expected])

    def test_cancellation_closes_source_attribute_and_mask_handles(self):
        item,world,attrs,mask=self.world_fixture();opened=[];real_open=Path.open
        sources={Path(item['meta'][key]) for key in ('file','attributes_file','mask_file')}
        def track(path,*args,**kwargs):
            handle=real_open(path,*args,**kwargs)
            if Path(path) in sources:opened.append(handle)
            return handle
        def cancel(message):raise RuntimeError('Fixture cancellation')
        with mock.patch.object(Path,'open',track):
            with self.assertRaisesRegex(RuntimeError,'Fixture cancellation'):
                list(workspace_tools.batches(self.s,{'ids':[item['id']],'whole':True},cancel))
        self.assertEqual(len(opened),3);self.assertTrue(all(h.closed for h in opened))

    def test_fitting_cancellation_removes_spool_and_preserves_original_xyz(self):
        item,world,attrs,mask=self.world_fixture();path=Path(item['meta']['file']);original=path.read_bytes()
        def cancel(message):raise RuntimeError('Stop fit during full residual pass')
        with self.assertRaisesRegex(RuntimeError,'Stop fit'):
            with geometry.selected_points(self.s,{'ids':[item['id']],'whole':True},self.s.progress) as (points,centre,covariance,revision):
                geometry.fit_plane(points,centre,covariance,cancel)
        self.assertFalse(list(self.root.glob('geometry-fit-*')))
        self.assertEqual(path.read_bytes(),original)

    def test_three_point_plane_recovers_exact_registered_sources_and_is_editable(self):
        item,world,attrs,mask=self.world_fixture();indices=[0,11,84];revision=registration_pose.revision(item['meta'])
        original=Path(item['meta']['file']).read_bytes()
        body=dict(operation='plane',plane=dict(points=world[indices].tolist(),
            point_sources=[{'source_id':item['id'],'source_revision':revision,'index':i} for i in indices]))
        preview=geometry.preview(self.s,body);stats=preview['statistics']
        np.testing.assert_array_equal(stats['picked_points'],world[indices])
        self.assertEqual([row['index'] for row in stats['point_sources']],indices)
        normal=np.asarray(stats['normal']);centre=np.asarray(stats['centre'])
        self.assertLess(np.max(np.abs((world[indices]-centre)@normal)),1e-8)
        self.assertEqual(len(preview['geometry']['faces']),2)
        result=geometry.apply(self.s,{'token':preview['token']})
        self.assertEqual(self.s.STATE['cad']['objects'][0]['id'],result['id'])
        self.assertEqual(Path(item['meta']['file']).read_bytes(),original)

    def test_three_point_plane_rejects_collinear_points_and_stale_source_before_apply(self):
        origin=np.array([450000.,330000.,-20.])
        with self.assertRaisesRegex(ValueError,'non-collinear'):
            geometry.preview(self.s,{'operation':'plane','plane':{'points':(origin+np.c_[[0.,1.,2.],[0.,0.,0.],[0.,0.,0.]]).tolist()}})
        item,world,attrs,mask=self.world_fixture();indices=[0,11,84];revision=registration_pose.revision(item['meta'])
        body=dict(operation='plane',plane=dict(points=world[indices].tolist(),
            point_sources=[{'source_id':item['id'],'source_revision':revision,'index':i} for i in indices]))
        preview=geometry.preview(self.s,body)
        registration_pose.compose(item['meta'],np.eye(3),[.01,0.,0.])
        with self.assertRaisesRegex(ValueError,'changed'):
            geometry.apply(self.s,{'token':preview['token']})
        self.assertFalse(self.s.STATE['cad']['objects'])

    def test_cylinder_rejects_degenerate_and_unsupported_geometry(self):
        x,y=np.meshgrid(np.linspace(-2,2,30),np.linspace(-2,2,20));plane=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        line=np.c_[np.arange(60),np.zeros((60,2))];coincident=np.ones((60,3))
        for points in (plane,line,coincident):
            with self.subTest(kind='degenerate',points=len(points)):
                with self.assertRaisesRegex(ValueError,'planar|linear'):
                    geometry.fit_cylinder(points,points.mean(0),np.cov(points,rowvar=False,bias=True))
        a,z=np.meshgrid(np.linspace(0,np.pi/8,96),np.linspace(-4,4,45))
        narrow=np.c_[(3.4*np.cos(a)).ravel(),(3.4*np.sin(a)).ravel(),z.ravel()]
        with self.assertRaisesRegex(ValueError,'less than 45'):
            geometry.fit_cylinder(narrow,narrow.mean(0),np.cov(narrow,rowvar=False,bias=True))
        a,z=np.meshgrid(np.linspace(0,2*np.pi,96,endpoint=False),np.linspace(-.001,.001,45))
        ring=np.c_[(3.4*np.cos(a)).ravel(),(3.4*np.sin(a)).ravel(),z.ravel()]
        with self.assertRaisesRegex(ValueError,'too little length'):
            geometry.fit_cylinder(ring,ring.mean(0),np.cov(ring,rowvar=False,bias=True))

    def test_cylinder_preview_rejects_nonfinite_or_unsupported_coordinate_range(self):
        for name,points,message in (
                ('nonfinite',np.c_[np.arange(30),np.full(30,np.inf),np.zeros(30)],'retained, finite'),
                ('outside_range',np.c_[np.arange(30)+1e13,np.zeros((30,2))],'coordinate range')):
            with self.subTest(kind=name):
                item=self.source(points,name=name)
                with self.assertRaisesRegex(ValueError,message):
                    geometry.preview(self.s,{'operation':'fit_cylinder','request':{'ids':[item['id']],'whole':True}})
                self.assertFalse(self.s.STATE['cad']['objects'])
                self.assertFalse(list(self.root.glob('geometry-fit-*')))

    def test_poor_cylinder_fit_is_labeled_reviewable_and_keeps_quality_with_model(self):
        a,z=np.meshgrid(np.linspace(0,2*np.pi,96,endpoint=False),np.linspace(-3,3,45));r=2.+.4*z
        cone=np.c_[(r*np.cos(a)).ravel(),(r*np.sin(a)).ravel(),z.ravel()]
        item=self.source(cone);before=Path(item['meta']['file']).read_bytes()
        preview=geometry.preview(self.s,{'operation':'fit_cylinder','request':{'ids':[item['id']],'whole':True}})
        stats=preview['statistics']
        self.assertEqual(stats['points'],len(cone));self.assertTrue(stats['all_selected_points'])
        self.assertEqual(stats['fit_quality'],'poor');self.assertEqual(stats['poor_fit_threshold_percent'],5.)
        self.assertGreater(stats['rms_to_radius_percent'],5.)
        self.assertAlmostEqual(stats['rms_to_radius_percent'],stats['rms_mm']/(10*stats['radius_m']))
        self.assertTrue(stats['converged']);self.assertTrue(np.isfinite(preview['geometry']['vertices']).all())
        self.assertIn('Poor cylinder fit',preview['warnings'][0]);self.assertIn('5% poor-fit threshold',preview['warnings'][0])
        self.assertIn(f'{stats["rms_mm"]:.2f} mm',preview['warnings'][0])
        self.assertFalse(self.s.STATE['cad']['objects'])
        created=geometry.apply(self.s,{'token':preview['token']})
        model=next(row for row in self.s.STATE['cad']['objects'] if row['id']==created['id'])
        self.assertEqual(model['fit_evidence']['statistics']['fit_quality'],'poor')
        self.assertEqual(model['fit_evidence']['statistics']['rms_mm'],stats['rms_mm'])
        self.assertEqual(model['fit_evidence']['warnings'],preview['warnings'])
        self.assertEqual(Path(item['meta']['file']).read_bytes(),before)

    def test_whole_3100123_point_cylinder_and_plane_use_every_retained_point_without_maps(self):
        count=3_100_123;path=self.root/'large.xyz64';mask_path=self.root/'large.mask'
        offset=np.array([450000.,330000.,-20.]);axis=np.array([.31,.82,.48]);axis/=np.linalg.norm(axis)
        B=geometry.basis(axis);radius=3.4;lo=np.full(3,np.inf);hi=-lo;start=time.monotonic()
        with path.open('wb') as stream,mask_path.open('wb') as masks:
            for first in range(0,count,100000):
                ids=np.arange(first,min(count,first+100000));a=2*np.pi*(ids%1000)/1000
                z=(ids//1000)/3100.*80.-40.
                points=offset+np.c_[radius*np.cos(a),radius*np.sin(a),z]@B.T
                points.astype('<f8').tofile(stream);(ids<3).astype('u1').tofile(masks)
                lo=np.minimum(lo,points.min(0));hi=np.maximum(hi,points.max(0))
        def digest():
            with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
        original=digest();meta=dict(name='Large cylinder',file=str(path),mask_file=str(mask_path),count=count,minimum=lo.tolist(),maximum=hi.tolist())
        item=dict(id='large',name='Large cylinder',kind='scan',meta=meta,visible=True)
        self.s.STATE['job_items']=[item];reads=[];real_read=RowFile.read
        def measured(reader,first,n):reads.append((reader.path.name,int(n)));return real_read(reader,first,n)
        cad_api.g.engines()
        with mock.patch.object(np,'memmap',side_effect=AssertionError('A whole cache must not be mapped')),mock.patch.object(RowFile,'read',measured):
            fitted=geometry.preview(self.s,{'operation':'fit_cylinder','request':{'ids':['large'],'whole':True}})
            plane=geometry.preview(self.s,{'operation':'fit_plane','request':{'ids':['large'],'whole':True}})
        stats=fitted['statistics'];self.assertEqual(stats['points'],count-3)
        self.assertEqual(plane['statistics']['points'],count-3)
        self.assertTrue(stats['all_selected_points']);self.assertIsNone(stats['input_point_limit'])
        self.assertEqual(stats['fit_quality'],'good');self.assertEqual(stats['poor_fit_threshold_percent'],5.)
        self.assertAlmostEqual(stats['radius_m'],radius,places=6)
        self.assertGreater(abs(np.dot(stats['axis'],axis)),1-1e-10)
        self.assertLess(stats['rms_mm'],.001);self.assertLessEqual(max(n for _,n in reads),100000)
        self.assertEqual(digest(),original);self.assertFalse(list(self.root.glob('geometry-fit-*')))
        report=dict(kind='synthetic full-input geometry validation',original_points=count,retained_points=count-3,
            cylinder_statistics=stats,plane_statistics=plane['statistics'],point_read_block_max=max(n for _,n in reads),
            source_sha256=original,source_unchanged=True,whole_file_mappings=0,seconds=time.monotonic()-start,
            native_windows_execution=False,real_tunnel_accuracy_claim=False)
        destination=os.environ.get('TUNNEL_STUDIO_GEOMETRY_EVIDENCE')
        if destination:Path(destination).write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':unittest.main()
