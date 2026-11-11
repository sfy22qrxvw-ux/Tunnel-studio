"""Known-coordinate tests for whole-tunnel scopes, poses, masks and sections."""
from pathlib import Path
from types import SimpleNamespace
import copy, csv, json, tempfile, unittest
from unittest.mock import patch
import numpy as np
import analysis_sources, design_compare, design_api, inspection, inspection_api
import inspection_points, large_sections, registration_pose, scan_geometry
from core import Alignment, project_cloud, slice_analysis


class FullTunnelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.origin=np.array([423000.,340000.,120.])
        self.s=SimpleNamespace(STATE=dict(job_items=[],scan_links=[],bundles=[],cad={},structures=[]),WORK=self.root,JOB={'busy':False},progress=lambda _:None)
        self.axis=Alignment([dict(chainage=i,e=self.origin[0],n=self.origin[1]+i,h=self.origin[2],radius=3.5) for i in (0,10)])
        self.s.STATE['axis']=self.axis
    def tearDown(self):self.tmp.cleanup()
    def scan(self,ident,world,angle=0.,mask=None):
        c,s=np.cos(angle),np.sin(angle);R=np.array([[c,-s,0.],[s,c,0.],[0.,0.,1.]])
        t=self.origin+np.array([12.,-7.,3.]) if angle else np.zeros(3)
        local=(np.asarray(world)-t)@R;path=self.root/(ident+'.xyz64');local.astype('<f8').tofile(path)
        finite=local[np.isfinite(local).all(1)]
        m=dict(name=ident,file=str(path),count=len(local),minimum=finite.min(0).tolist(),maximum=finite.max(0).tolist())
        if angle:registration_pose.set_pose(m,R,t)
        if mask is not None:
            mp=self.root/(ident+'.mask');np.asarray(mask,'u1').tofile(mp);m['mask_file']=str(mp)
        row=dict(id=ident,name=ident,kind='scan',meta=m,visible=True)
        self.s.STATE['job_items'].append(row);return row
    def ring(self,n=720,offset=.01):
        a=np.arange(n)*2*np.pi/n+.001
        return self.origin+np.c_[(3.5+offset)*np.cos(a),np.full(n,5.),(3.5+offset)*np.sin(a)]
    def bundle(self,a,b):
        self.s.STATE['scan_links']=[dict(id='link',a=a['id'],b=b['id'],status='accepted')]
        return next(x for x in analysis_sources.collections(self.s) if x['id'].startswith(analysis_sources.BUNDLE_PREFIX))
    def test_section_navigation_uses_the_scan_extent_on_a_long_alignment(self):
        self.s.STATE['axis']=Alignment([dict(chainage=i,e=self.origin[0],n=self.origin[1]+i,h=self.origin[2],radius=3.5)
            for i in range(0,5001,10)])
        world=np.vstack([self.ring(n=36)+[0.,v,0.] for v in np.linspace(2102.,2107.,21)])
        source=self.scan('short',world,.6)
        path=scan_geometry.section_overview(source,self.s)['paths']['alignment']
        self.assertEqual([path['stations'][0],path['stations'][-1]],[0.,5000.])
        np.testing.assert_allclose(path['view_range'],[2107.,2112.],rtol=0,atol=1e-5)
    def test_full_bundle_design_includes_poses_masks_and_reversible_indices(self):
        world=self.ring();mask=np.zeros(len(world),'u1');mask[0]=1
        a=self.scan('a',world,mask=mask);b=self.scan('b',world,.6,mask)
        source=self.bundle(a,b);before=[Path(x['meta']['file']).read_bytes() for x in (a,b)]
        with patch.object(analysis_sources,'BLOCK',73):
            result=design_compare.run(source['meta'],self.root,axis_rows=self.axis.a,band_m=0)
        self.assertEqual(result['used'],2*len(world)-2);self.assertEqual(result['deleted_points'],2)
        self.assertAlmostEqual(result['mean_mm'],10,places=6)
        self.assertAlmostEqual(result['p95_absolute_mm'],10,places=6)
        records=np.fromfile(result['results_file'],design_compare.RECORD)
        self.assertEqual(records['point_id'][0],1);self.assertEqual(records['point_id'][len(world)-1],len(world)+1)
        np.testing.assert_allclose(records['xyz'][:len(world)-1],world[1:],rtol=0,atol=1e-8)
        self.assertEqual(before,[Path(x['meta']['file']).read_bytes() for x in (a,b)])
        result.update(source_id=source['id']);out=self.root/'result.csv';design_compare.export_csv(result,out)
        with out.open() as f:rows=list(csv.DictReader(f))
        self.assertEqual(rows[len(world)-1]['source_scan_id'],'b')
        self.assertEqual(rows[len(world)-1]['original_scan_point_index_0based'],'1')
        revision=design_compare.fingerprint(source['meta']);source['meta']['cloud_parts'][1]['meta']['registration_translation'][2]+=.01
        self.assertNotEqual(revision,design_compare.fingerprint(source['meta']))
    def test_inspection_source_and_reference_use_world_coordinates(self):
        q=self.origin+np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[1.,1.,0.]])
        ref=self.scan('ref',q,.5);a=self.scan('a',q+[0.,0.,.03],.4,[1,0,0,0]);b=self.scan('b',q+[0.,0.,.03],-.8)
        source=self.bundle(a,b)
        result=inspection.run(source,inspection_api.resolve(self.s,ref['id']),self.root,'cloud_cloud')
        self.assertEqual(result['used'],7);self.assertAlmostEqual(result['mean_mm'],30,places=7)
        self.assertEqual(result['reference_count'],4)
        self.s.STATE['inspection_result']=result
        self.assertFalse(inspection_api.current(self.s)['stale'])
        ref['meta']['registration_translation'][0]+=.1
        self.assertTrue(inspection_api.current(self.s)['stale'])
    def test_reference_pose_changes_invalidate_spatial_index(self):
        item=self.scan('a',self.ring(72),.3)
        with inspection_points.CloudReference(item['meta'],self.root) as engine:
            item['meta']['registration_translation'][0]+=.01
            with self.assertRaisesRegex(ValueError,'changed'):engine.evaluate(self.ring(4))
    def test_scope_does_not_double_count_current_or_stale_merge(self):
        world=self.ring(72);a=self.scan('a',world);b=self.scan('b',world,.4)
        group=self.bundle(a,b)
        merged=self.scan('m',world);merged.update(registered_merge=True,merge_source_ids=['a','b'])
        chosen=analysis_sources.resolve(self.s,group['id'])
        self.assertEqual([p['id'] for p in chosen['meta']['cloud_parts']],['m'])
        merged.update(merge_stale='Registration changed',merge_archived=True)
        chosen=analysis_sources.resolve(self.s,group['id'])
        self.assertEqual([p['id'] for p in chosen['meta']['cloud_parts']],['a','b'])
        self.assertNotIn('m',[x['id'] for x in inspection_api.options(self.s)['sources']])
    def test_perpendicular_section_uses_exact_same_plane_at_every_density(self):
        q=self.ring();a=self.scan('a',q,.4)
        derived,count=project_cloud(a['meta'],self.axis,self.root/'d.analysis64')
        self.assertEqual(count,len(q))
        info=np.fromfile(derived,'<f8').reshape(-1,4)
        np.testing.assert_allclose(info[:,0],5.,atol=1e-7)
        r,uv,dev=slice_analysis(a['meta'],derived,self.axis,5,.2,.1,25)
        self.assertEqual(r['kept_points'],len(q));self.assertTrue(r['fit_valid'])
        np.testing.assert_allclose(dev,10.,atol=1e-6)
        uv._mmap.close()
        old=copy.deepcopy(a['meta']);a['meta']['registration_translation'][1]+=1
        empty,_,_=slice_analysis(a['meta'],derived,self.axis,5,.2,.1,25)
        self.assertEqual(empty['kept_points'],0);self.assertFalse(empty['fit_valid'])
        self.assertNotEqual(inspection.fingerprint(old),inspection.fingerprint(a['meta']))
    def test_bundle_section_csv_and_world_selection_keep_all_source_points(self):
        q=self.ring();a=self.scan('a',q);b=self.scan('b',q,.6);source=self.bundle(a,b)
        before=set(self.root.glob('*.xyz64'))
        overview=scan_geometry.section_overview(source,self.s)
        self.assertEqual(overview['source_id'],source['id']);self.assertIn('alignment',overview['paths'])
        self.assertLessEqual(overview['sample_count'],40000)
        r=scan_geometry.section(self.s,source,dict(plane='custom',normal=[0,1,0],origin=(self.origin+[0,5,0]).tolist(),position=0,width=.2),lambda _:None)
        self.assertEqual(r['count'],1440);self.assertEqual(before,set(self.root.glob('*.xyz64')))
        with Path(r['file']).open() as f:rows=list(csv.DictReader(f))
        self.assertEqual({x['source_scan_id'] for x in rows},{'a','b'})
        np.testing.assert_allclose([float(x['n']) for x in rows],self.origin[1]+5,atol=1e-7)
        revision=scan_geometry.section_revision(self.s,source,None)
        source['meta']['cloud_parts'][1]['meta']['registration_translation'][0]+=.1
        self.assertNotEqual(revision,scan_geometry.section_revision(self.s,source,None))
    def test_failed_or_cancelled_design_does_not_leave_partial_results(self):
        a=self.scan('a',self.ring());before=set(self.root.iterdir())
        with self.assertRaisesRegex(ValueError,'cancelled'):
            design_compare.run(a['meta'],self.root,axis_rows=self.axis.a,cancel=lambda:True)
        self.assertEqual(before,set(self.root.iterdir()))
    def test_sloping_section_uses_3d_normal_instead_of_plan_chainage_band(self):
        axis=Alignment([dict(chainage=i,e=self.origin[0],n=self.origin[1]+i,h=self.origin[2]+i*.5,radius=3.5) for i in (0,10)])
        centre,normal,right,up,_=axis.at(5);angles=np.arange(720)*np.pi/360+.001
        ring=centre+3.51*np.cos(angles)[:,None]*right+3.51*np.sin(angles)[:,None]*up
        a=self.scan('slope',np.vstack((ring,ring+normal*.11)),.4)
        report,uv,dev=large_sections.calculate(a['meta'],None,axis,5,.2,.1,25,self.root)
        self.assertEqual(report['kept_points'],720)
        np.testing.assert_allclose(dev,10.,atol=1e-6)
        uv._mmap.close()
    def test_legacy_sampled_screening_reads_retained_indices_in_world_frame(self):
        import analysis_tools
        q=self.ring(7200);mask=np.zeros(len(q),'u1');mask[::3]=1
        a=self.scan('sample',q,.4,mask)
        sampled=analysis_tools.sample_points(a['meta'],1000)
        expected=q[np.flatnonzero(mask==0)[::5]]
        self.assertEqual(len(sampled),960)
        np.testing.assert_allclose(sampled,expected,rtol=0,atol=1e-8)
    def test_heatmap_selection_uses_registered_world_coordinates_and_live_mask(self):
        import heatmap
        q=self.ring();mask=np.zeros(len(q),'u1');mask[::3]=1
        a=self.scan('heatmap',q,.7,mask)
        derived,_=project_cloud(a['meta'],self.axis,self.root/'heatmap.analysis64')
        lo=self.origin+[3.4,4.9,-1.];hi=self.origin+[3.6,5.1,1.]
        selection=dict(mode='box',minimum=lo.tolist(),maximum=hi.tolist())
        expected=int((((q>=lo)&(q<=hi)).all(1)&(mask==0)).sum())
        result=heatmap.calculate(a['meta'],derived,self.axis,0,10,1,90,.3,'mean',selection=selection)
        self.assertGreater(expected,0);self.assertEqual(result['points'],expected)
        np.testing.assert_allclose([x for x in result['values'] if x is not None],10.,atol=1e-6)
    def test_design_csv_and_sparse_preview_fallback_read_bounded_owned_blocks(self):
        n=200003;records=np.zeros(n,design_compare.RECORD)
        records['point_id']=np.arange(n,dtype=np.uint64)*2
        records['xyz']=self.origin;records['deviation_m']=.01;records['chainage_m']=5.
        records['status']=1;records[-1]['status']=0
        path=self.root/'bounded.design-results';records.tofile(path)
        result=dict(results_file=str(path),record_count=n,used=1,signed=True,tolerance_mm=25.,
            id='bounded',mode='alignment',band_m=.5,source_id='scope',source_ranges=[
                dict(id='a',name='Setup A',start=0,count=200000),
                dict(id='b',name='Setup B',start=200000,count=200006)])
        original=np.fromfile;reads=[]
        def bounded_read(*args,**kwargs):
            count=kwargs.get('count',args[2] if len(args)>2 else -1)
            self.assertGreater(count,0);self.assertLessEqual(count,100000);reads.append(count)
            return original(*args,**kwargs)
        mappings=[];mapped=np.memmap
        def track_mapping(*args,**kwargs):
            value=mapped(*args,**kwargs);mappings.append(value);return value
        # The only included record is missed by the three-row display stride.
        with patch.object(np,'fromfile',side_effect=bounded_read),patch.object(np,'memmap',side_effect=track_mapping):
            shown=design_compare.preview(result)
        self.assertEqual(shown['sample_count'],1);self.assertEqual(reads,[100000,100000,3])
        self.assertTrue(all(value._mmap.closed for value in mappings))
        reads.clear();destination=self.root/'bounded.csv'
        with patch.object(np,'fromfile',side_effect=bounded_read),patch.object(np,'memmap',side_effect=AssertionError('CSV must not map the whole result')):
            design_compare.export_csv(result,destination)
        self.assertEqual(reads,[100000,100000,3])
        with destination.open() as stream:
            count=0
            for row in csv.DictReader(stream):count+=1;last=row
        self.assertEqual(count,n);self.assertEqual(last['source_scan_id'],'b')
        self.assertEqual(last['source_point_index_0based'],str((n-1)*2))
        self.assertEqual(last['original_scan_point_index_0based'],str((n-1)*2-200000))
        self.assertEqual(last['status'],'included')


if __name__=='__main__':unittest.main()
