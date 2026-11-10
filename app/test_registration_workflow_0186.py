"""User-facing registration regressions: usable fits and mixed-mode setup links."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from scipy.spatial.transform import Rotation

import bundle_workflow
import jobdata
import registration
import registration_network as network
import registration_pose
import registration_workspace as rw
import target_ops
import workflows
from test_registration_virtual_0176 import FakeServer, add_scan


WORLD=np.array([[0.,0.,0.],[3.,0.,0.],[0.,2.,1.],[1.,1.,2.]])


def server(folder):
    s=FakeServer(folder);s.STATE.update(workflows.empty())
    s.STATE.update(job_items=[],scan_links=[]);s.JOB={'busy':False};s.SELECTION=None
    s.start_job=lambda fn,*args,**kwargs:fn()
    return s


def fixture(s):
    specs=[('a',np.eye(3),np.zeros(3),[0,1]),
           ('b',Rotation.from_euler('z',.37).as_matrix(),np.array([12.,-4.,.8]),[0,1,2,3]),
           ('c',Rotation.from_euler('xyz',[.17,-.26,.61]).as_matrix(),np.array([-3.,7.,2.]),[1,2,3])]
    for name,R,t,visible in specs:
        item=add_scan(s,name,np.zeros(3));raw=(WORLD-t)@R
        raw.astype('<f8').tofile(item['meta']['file'])
        item['meta'].update(minimum=raw.min(0).tolist(),maximum=raw.max(0).tolist())
        item['registration_state'].update(cloud=copy.deepcopy(item['meta']),
            rows=[dict(id=f'T{i+1}',role='fit',source=raw[i].tolist(),control=[None]*3) for i in visible])
    return {x['id']:x for x in s.STATE['job_items']}


def run_registration(s,a,b,mode,anchor=None,matches=None,**options):
    sent=[]
    rw.api(s,SimpleNamespace(send=sent.append),'/api/scan-registration',
           dict(action='register_setups',a=a,b=b,mode=mode,anchor=anchor,tolerance=5,matches=matches,**options))
    result=s.STATE['last_registration_result']
    assert result['id']==sent[-1]['registration_id']
    return result


class RegistrationWorkflow0186Tests(unittest.TestCase):
    def test_bundle_marker_metadata_keeps_fit_check_and_excluded_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);item=add_scan(s,'setup',np.zeros(3));r=item['registration_state']
            r['rows'][0]['candidate_id']='fitted'
            r['rows'][1]['role']='check'
            r['rows'][2]['role']='off'
            r['candidates']=[dict(id='fitted',source=[0,0,0],status='accepted'),
                             dict(id='legacy',source=[1,1,0],status='accepted')]
            markers={m['id']:m for m in rw.scan_summary(s,item)['markers']}
            self.assertEqual({name:markers[name]['role'] for name in ('A','B','C','legacy')},
                             {'A':'fit','B':'check','C':'off','legacy':None})

    def test_control_import_resolves_selected_bundle_or_merge_without_manual_pairing(self):
        with tempfile.TemporaryDirectory() as folder:
            from workspace_tools import inventory
            s=server(folder)
            first=add_scan(s,'first',np.zeros(3));last=add_scan(s,'last',np.ones(3))
            other=add_scan(s,'other',np.zeros(3))
            for item,names in [(first,('1L','1R','tie')),(last,('2L','2R','tie')),(other,('X','Y','Z'))]:
                for row,name in zip(item['registration_state']['rows'],names):row['id']=name
            link=dict(a='first',b='last',status='accepted',target_stamp=rw.stamp(s,['first','last']))
            s.STATE['scan_links']=[link]
            s.STATE['job_items'].append(dict(id='merged',kind='scan',registered_merge=True,
                                            merge_source_ids=['last','first']))
            bundle=inventory(s)[0]['id']
            controls={name:[i,1.,2.] for i,name in enumerate(('1L','1R','2L','2R'))}
            self.assertEqual(rw.control_import_anchor(s,controls,None),'first')
            self.assertEqual(rw.control_import_anchor(s,controls,'merged'),'last')
            self.assertEqual(rw.control_import_anchor(s,controls,None,bundle),'first')
            sent=[]
            rw.api(s,SimpleNamespace(send=sent.append),'/api/scan-registration',
                   dict(action='controls',controls=controls,use_for_fit=True,source_item_id='merged'))
            self.assertEqual(s.STATE['active_job_registration'],'last')
            rw.persist(s)
            self.assertEqual(link['status'],'accepted')
            self.assertEqual(rw.targets(first)['1L']['control'],controls['1L'])
            self.assertEqual(rw.targets(last)['2R']['control'],controls['2R'])
            self.assertEqual(rw.targets(first)['tie']['control'],[None]*3)
            _,summary,_=rw.control_group(s,'last')
            self.assertEqual(summary['matches'],[
                dict(id=name,role='fit',setups=[setup]) for name,setup in
                [('1L','first'),('1R','first'),('2L','last'),('2R','last')]])
            other['registration_state']['rows'][0]['id']='1L'
            with self.assertRaisesRegex(ValueError,'more than one disconnected group'):
                rw.control_import_anchor(s,controls,None)

    def test_survey_control_spans_both_ends_of_linked_group(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder)
            first=add_scan(s,'first',np.zeros(3));middle=add_scan(s,'middle',np.zeros(3))
            last=add_scan(s,'last',np.zeros(3));other=add_scan(s,'unlinked',np.zeros(3))
            end_offset=np.array([20.,3.,.4])
            registration_pose.set_pose(last['meta'],np.eye(3),end_offset)
            def set_targets(item,values):
                item['registration_state']['rows']=[
                    dict(id=name,source=point,control=[None]*3,role=role) for name,point,role in values]
            set_targets(first,[('1L',[0.,0.,0.],'fit'),('1R',[0.,2.,1.],'fit'),('2RS',[1.,1.,0.],'fit')])
            set_targets(middle,[('1R',[0.,2.,1.],'fit'),('2RS',[1.,1.,0.],'fit')])
            set_targets(last,[('2L',[0.,0.,0.],'fit'),('2R',[0.,2.,-1.],'check')])
            set_targets(other,[('2L',[100.,0.,0.],'fit')])
            s.STATE['scan_links']=[dict(a='first',b='middle'),dict(a='middle',b='last')]
            rw.activate(s,'first')
            R=Rotation.from_euler('xyz',[.03,-.12,.7]).as_matrix();t=np.array([182241.,399163.,77.])
            control_world={'1L':[0.,0.,0.],'1R':[0.,2.,1.],
                           '2L':end_offset,'2R':end_offset+[0,2,-1]}
            controls={name:(R@point+t).tolist() for name,point in control_world.items()}
            controls['Unseen']=[1.,2.,3.]
            s.STATE['survey_controls']={'2RS':[1.,2.,3.]}
            handler=SimpleNamespace(send=lambda _:None)
            rw.api(s,handler,'/api/scan-registration',dict(action='controls',controls=controls,use_for_fit=True))
            rows,summary,_=rw.control_group(s,'first')
            self.assertEqual([row['id'] for row in rows],['1L','1R','2L','2R'])
            self.assertEqual((summary['fit'],summary['check'],summary['setups']),(3,1,3))
            self.assertEqual(summary['unmatched'],['Unseen'])
            def request(action,**body):
                workflows.post(s,handler,'/api/registration-action',dict(action=action,**body))
            request('solve_group_control',scan_id='first')
            solution=s.STATE['registration']['solution']
            self.assertLess(solution['fit']['max_mm'],1e-5)
            self.assertLess(solution['check']['max_mm'],1e-5)
            with self.assertRaisesRegex(ValueError,'Review'):
                request('apply',solution_id=solution['id'])
            s.STATE['survey_controls']['2R']=(np.asarray(controls['2R'])+[.01,0,0]).tolist()
            with self.assertRaisesRegex(ValueError,'changed'):
                request('apply',solution_id=solution['id'],reviewed=True)
            s.STATE['survey_controls']['2R']=controls['2R']
            paths=[Path(x['meta']['file']) for x in rw.scans(s)]
            original=[p.read_bytes() for p in paths]
            sent=[]
            workflows.get(s,SimpleNamespace(send=sent.append),'/api/registration',{})
            self.assertIn('Import an alignment',sent[-1]['survey_analysis_unavailable'])
            with self.assertRaisesRegex(ValueError,'Import an alignment'):
                request('apply',solution_id=solution['id'],reviewed=True,to_survey=True)
            request('apply',solution_id=solution['id'],reviewed=True,to_survey=False)
            request('apply',solution_id=solution['id'],reviewed=True)
            self.assertIsNone(s.STATE['axis'])
            for item in rw.scans(s):
                if item['id']=='unlinked':
                    self.assertTrue(registration_pose.is_identity(item['meta']))
                    continue
                rotation,translation=registration_pose.get(item['meta'])
                np.testing.assert_allclose(rotation,R,atol=1e-10)
                np.testing.assert_allclose(translation,t+(R@end_offset if item['id']=='last' else 0),atol=1e-9)
            self.assertEqual([p.read_bytes() for p in paths],original)

    def test_project_control_import_then_explicit_bundle_apply_and_saved_qa(self):
        from workspace_tools import inventory
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder)
            for ident in ('a','b','c','d'):
                add_scan(s,ident,np.zeros(3))
            for ident,a,b in [('ab','a','b'),('cd','c','d')]:
                s.STATE['scan_links'].append(dict(id=ident,a=a,b=b,status='accepted',
                    solution=dict(fit=dict(rms_mm=1.2),cloud=dict(before=dict(rms_mm=3.),
                                                                after=dict(rms_mm=.8)))))
            first,second=inventory(s)
            rw.activate(s,'a')
            active=copy.deepcopy(s.STATE['registration'])
            raw={x['id']:Path(x['meta']['file']).read_bytes() for x in rw.scans(s)}
            sent=[];handler=SimpleNamespace(send=sent.append)
            def post(action,**body):
                rw.api(s,handler,'/api/scan-registration',dict(action=action,**body))
                return sent[-1]
            offset=np.array([182000.,399000.,78.])
            controls={r['id']:(r['source']+offset).tolist() for r in rw.targets(rw.scan(s,'c')).values()}
            post('controls',controls=controls,project_wide=True,use_for_fit=True,source_item_id=None)
            self.assertEqual(s.STATE['registration'],active)
            self.assertEqual(s.STATE['active_job_registration'],'a')
            qa=post('bundle_control_status',bundle_id=second['id'])
            self.assertEqual(qa['summary']['fit'],3)
            self.assertEqual(qa['links'][0]['plane_after'],.8)
            fit=post('bundle_control_solve',bundle_id=second['id'])['solution']
            self.assertTrue(all(registration_pose.is_identity(x['meta']) for x in rw.scans(s)))
            with self.assertRaisesRegex(ValueError,'review'):
                post('bundle_control_apply',bundle_id=first['id'],solution_id=fit['id'],reviewed=True)
            s.STATE['survey_controls']['A'][0]+=.1
            with self.assertRaisesRegex(ValueError,'changed'):
                post('bundle_control_apply',bundle_id=second['id'],solution_id=fit['id'],reviewed=True)
            s.STATE['survey_controls']['A'][0]-=.1
            fit=post('bundle_control_solve',bundle_id=second['id'])['solution']
            post('bundle_control_apply',bundle_id=second['id'],solution_id=fit['id'],reviewed=True)
            self.assertEqual(s.STATE['active_job_registration'],'a')
            self.assertEqual(s.STATE['registration'],active)
            self.assertIsNone(s.STATE['axis'])
            for x in rw.scans(s):
                self.assertEqual(Path(x['meta']['file']).read_bytes(),raw[x['id']])
                if x['id'] in ('a','b'):
                    self.assertTrue(registration_pose.is_identity(x['meta']))
                else:
                    np.testing.assert_allclose(registration_pose.get(x['meta'])[1],offset,atol=1e-8)
            s.STATE=workflows.prepare_data(s,workflows.serial(s))
            qa=post('bundle_control_status',bundle_id=second['id'])
            self.assertFalse(qa['control_report']['stale'])
            self.assertLess(qa['control_report']['solution']['fit']['rms_mm'],1e-6)
            self.assertIsNone(post('bundle_control_status',bundle_id=first['id'])['control_report'])

    def test_viewport_target_rename_keeps_the_scan_and_measured_centre(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);item=add_scan(s,'setup',np.zeros(3));add_scan(s,'other',np.ones(3))
            r=rw.activate(s,'setup')
            r['rows'][0]['candidate_id']='fitted-a'
            r['candidates']=[dict(id='fitted-a',name='A',source=[0.,0.,0.],status='accepted')]
            handler=SimpleNamespace(send=lambda _:None)

            def rename(name,**target):
                workflows.post(s,handler,'/api/registration-action',
                               dict(action='rename_target',name=name,**target))

            rename('Sphere 1',id='fitted-a',scan_id='setup')
            rename('Manual 2',row_id='B',scan_id='setup')
            self.assertEqual(r['candidates'][0]['name'],'Sphere 1')
            self.assertEqual(list(rw.targets(item)),['Sphere 1','Manual 2','C'])
            np.testing.assert_array_equal(rw.targets(item)['Sphere 1']['source'],[0,0,0])
            np.testing.assert_array_equal(rw.targets(item)['Manual 2']['source'],[1,0,0])
            with self.assertRaisesRegex(ValueError,'already has that name'):
                rename('Manual 2',id='fitted-a',scan_id='setup')
            with self.assertRaisesRegex(ValueError,'active scan changed'):
                rename('Wrong scan',id='fitted-a',scan_id='other')
            with mock.patch.object(s,'save',side_effect=OSError('disk error')):
                with self.assertRaisesRegex(OSError,'disk error'):
                    rename('Unsaved',id='fitted-a',scan_id='setup')
            self.assertEqual(s.STATE['registration']['candidates'][0]['name'],'Sphere 1')

    def test_cold_setup_detail_reuses_quick_overview_and_keeps_visible_detail(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);item=add_scan(s,'setup',np.zeros(3));meta=item['meta']
            rng=np.random.default_rng(186)
            points=rng.uniform([-3.,-10.,-3.],[3.,10.,3.],(100000,3))
            points.astype('<f8').tofile(meta['file'])
            np.ones((len(points),4),'<f4').tofile(meta['attributes_file'])
            meta.update(count=len(points),minimum=points.min(0).tolist(),maximum=points.max(0).tolist())
            deleted=Path(folder)/'removed.mask'
            (points[:,0]>0).astype('u1').tofile(deleted);meta['mask_file']=str(deleted)
            view=dict(projection='perspective',target=[0,0,0],right=[1,0,0],
                      up=[0,0,1],forward=[0,1,0],scale=.7,aspect=1,near=.03,far=20)
            with mock.patch.object(jobdata,'_source_overview',wraps=jobdata._source_overview) as overview:
                _,quick=jobdata.preview_binary([item],detail=100,view=view,setup=True,quick=True)
                self.assertGreater(quick['sample_count'],0)
                self.assertLessEqual(quick['sample_count'],20000)
                blob,fine=jobdata.preview_binary([item],detail=100,view=view,setup=True)
                self.assertGreater(fine['sample_count'],quick['sample_count'])
                self.assertEqual(overview.call_count,1,'Refinement should reuse the cached overview')
                self.assertFalse(overview.call_args.kwargs['build'])
            rows=np.frombuffer(blob,dtype='<f4',offset=56).reshape(-1,6)
            self.assertTrue(np.all(rows[:,0]+(points[:,0].min()+points[:,0].max())/2<=1e-6))

    def test_high_rms_sphere_and_checker_return_finite_included_fits(self):
        rng=np.random.default_rng(186);v=rng.normal(size=(6000,3));v/=np.linalg.norm(v,axis=1)[:,None]
        radius=.0994;centre=np.array([12.,20.,3.])
        points=centre+(radius+rng.uniform(-.011,.011,len(v)))[:,None]*v
        sphere=registration.fit_sphere_target(points,(centre+[radius,0,0]).tolist(),2*radius)
        self.assertGreater(sphere['sphere_rms_mm'],4.97)
        self.assertEqual(sphere['status'],'accepted');self.assertTrue(sphere['usable'])
        self.assertEqual(sphere['quality'],'warning');self.assertTrue(sphere['quality_warnings'])
        self.assertTrue(np.isfinite(sphere['source']).all())
        u,w=np.meshgrid(np.linspace(-.099,.099,75),np.linspace(-.099,.099,75));uv=np.c_[u.ravel(),w.ravel()]
        points=np.c_[uv,rng.normal(0,.016,len(uv))]+[20,30,8]
        values=np.where(uv[:,0]*uv[:,1]>0,200.,10.)
        checker=registration.fit_target(points,values,[20,30,8],.2)
        self.assertGreater(checker['plane_rms_mm'],11.)
        self.assertEqual(checker['status'],'accepted');self.assertTrue(checker['quality_warnings'])
        self.assertTrue(checker['validation_passed']);self.assertTrue(np.isfinite(checker['source']).all())
        json.dumps([sphere,checker],allow_nan=False)

    def test_actual_picked_sphere_is_inserted_without_accept_action(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);x=add_scan(s,'sphere',np.zeros(3));rng=np.random.default_rng(19)
            v=rng.normal(size=(3000,3));v/=np.linalg.norm(v,axis=1)[:,None]
            centre=np.array([10.,20.,3.]);p=centre+(.0994+rng.uniform(-.011,.011,len(v)))[:,None]*v
            p.astype('<f8').tofile(x['meta']['file']);np.zeros((len(p),4),'<f4').tofile(x['meta']['attributes_file'])
            x['meta'].update(count=len(p),minimum=p.min(0).tolist(),maximum=p.max(0).tolist(),attribute_counts=[len(p)]*4)
            x['registration_state'].update(cloud=copy.deepcopy(x['meta']),rows=[])
            rw.activate(s,'sphere');original=Path(x['meta']['file']).read_bytes()
            workflows.post(s,SimpleNamespace(send=lambda _:None),'/api/registration-action',
                dict(action='detect',target_type='sphere',diameter=.1988,seed=(centre+[.0994,0,0]).tolist()))
            r=s.STATE['registration'];self.assertEqual(len(r['candidates']),1);self.assertEqual(len(r['rows']),1)
            candidate=r['candidates'][0];self.assertEqual(candidate['status'],'accepted')
            self.assertGreater(candidate['sphere_rms_mm'],4.97)
            self.assertEqual(r['rows'][0]['candidate_id'],candidate['id'])
            self.assertIn(r['rows'][0]['id'],rw.targets(x))
            self.assertEqual(Path(x['meta']['file']).read_bytes(),original)
            target_ops.remove_targets(r,[candidate['id']]);rw.persist(s)
            self.assertEqual(rw.targets(x),{});self.assertFalse(r['candidates'])

    def test_world_pick_keeps_the_clicked_sphere_with_a_denser_neighbour(self):
        rng=np.random.default_rng(705)
        radius=.0994;centre=np.array([412345.,289000.,12.])

        def cap(count, origin):
            z=rng.uniform(.2,1,count);angle=rng.uniform(0,2*np.pi,count);xy=np.sqrt(1-z*z)
            directions=np.c_[xy*np.cos(angle),xy*np.sin(angle),z]
            return origin+radius*directions+rng.normal(0,.00035,(count,3))

        picked=cap(500,centre);seed=picked[picked[:,2].argmax()]
        points=np.r_[picked,cap(8000,centre+[.22,0,0])]
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);item=add_scan(s,'sphere',np.zeros(3));meta=item['meta']
            points.astype('<f8').tofile(meta['file'])
            np.zeros((len(points),4),'<f4').tofile(meta['attributes_file'])
            meta.update(count=len(points),minimum=points.min(0).tolist(),maximum=points.max(0).tolist(),
                        attribute_counts=[len(points)]*4)
            rotation=Rotation.from_euler('xyz',[.25,-.14,.37]).as_matrix()
            registration_pose.set_pose(meta,rotation,[8200.,-3100.,17.])
            item['registration_state'].update(cloud=copy.deepcopy(meta),rows=[])
            rw.activate(s,item['id'])
            workflows.post(s,SimpleNamespace(send=lambda _:None),'/api/registration-action',
                dict(action='detect',target_type='sphere',diameter=2*radius,
                     seed=registration_pose.point(meta,seed).tolist(),seed_frame='world'))
            row,=s.STATE['registration']['rows']
            np.testing.assert_allclose(row['source'],centre,rtol=0,atol=.001)
            marker,=rw.scan_summary(s,item)['markers']
            np.testing.assert_allclose(marker['source'],registration_pose.point(meta,centre),rtol=0,atol=.001)

    def test_setup_cloud_pick_reuses_index_and_checks_world_limit_box(self):
        import preview_index, setup_camera
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);item=add_scan(s,'setup',np.zeros(3));meta=item['meta']
            meta['has_scanner_pose']=True
            rotation=Rotation.from_euler('z',.6).as_matrix()
            registration_pose.set_pose(meta,rotation,[400.,200.,10.])
            seed=registration_pose.point(meta,[1.,0.,0.])
            item['limit_box']=dict(minimum=(seed-.01).tolist(),maximum=(seed+.01).tolist())
            preview_index.bounds(meta)
            with mock.patch.object(setup_camera.setup_photos,'blocks',side_effect=AssertionError('second point index')), \
                 mock.patch.object(preview_index.BoundsBuilder,'add',side_effect=AssertionError('rebuilt point index')):
                pick=setup_camera.measured_pick(s,dict(id=item['id'],key=setup_camera.source_key(item),point=seed))
            self.assertEqual(pick['index'],1)
            self.assertEqual(pick['point'],[1.,0.,0.])

    def test_pending_legacy_targets_promote_without_reviving_rejected_or_off(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);x=add_scan(s,'a',np.zeros(3));r=x['registration_state']
            r['candidates']=[dict(id='pending',source=[1.,2.,3.],status='unreviewed'),
                             dict(id='rejected',source=[2.,3.,4.],status='rejected'),
                             dict(id='off',source=[3.,4.,5.],status='unreviewed'),
                             dict(id='invalid',source=[None,4.,5.],status='unreviewed')]
            r['rows']=[dict(id='off',candidate_id='off',role='off',source=[3.,4.,5.],control=[None]*3)]
            rw.state_for(x);usable=rw.targets(x)
            self.assertEqual(set(usable),{'pending'})
            self.assertEqual(r['candidates'][1]['status'],'rejected')
            self.assertEqual(r['rows'][0]['role'],'off')
            self.assertFalse(r['candidates'][-1]['usable'])
            before=copy.deepcopy(r);rw.state_for(x);self.assertEqual(r,before)

    def test_two_target_2d_then_three_target_3d_after_reopen(self):
        for anchor in ('a','b'):
            with self.subTest(anchor=anchor),tempfile.TemporaryDirectory() as folder:
                s=server(folder);items=fixture(s)
                original={p.name:p.read_bytes() for p in Path(folder).glob('*.xyz64')}
                first=run_registration(s,'a','b','vertical','a')
                self.assertEqual(first['solution']['fit']['count'],2)
                s.STATE=workflows.prepare_data(s,workflows.serial(s))
                fixed={ident:copy.deepcopy(rw.scan(s,ident)['meta']) for ident in ('a','b')}
                with mock.patch.object(jobdata,'room',side_effect=AssertionError('duplicate cloud allocation')):
                    result=run_registration(s,'b','c','rigid',anchor)
                self.assertEqual(result['solution']['mode'],'rigid');self.assertEqual(result['solution']['fit']['count'],3)
                self.assertEqual(result['solution']['anchor'],anchor);self.assertTrue(result['applied'])
                self.assertLess(result['solution']['fit']['rms_mm'],1e-6)
                for ident in ('a','b'):self.assertEqual(rw.scan(s,ident)['meta'],fixed[ident])
                x=rw.scan(s,'c');raw=np.fromfile(x['meta']['file'],'<f8').reshape(-1,3)
                np.testing.assert_allclose(registration_pose.points(x['meta'],raw),WORLD,atol=1e-9)
                self.assertEqual(original,{p.name:p.read_bytes() for p in Path(folder).glob('*.xyz64')})
                self.assertFalse(any(x.get('registered_merge') for x in s.STATE['job_items']))

    def test_floating_registration_moves_both_components_without_changing_target_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            for item in items.values():item['scan_origin']=[0.,0.,0.]
            paths=[Path(x['meta']['file']) for x in items.values()]
            original=[p.read_bytes() for p in paths]
            first=run_registration(s,'a','b','vertical',floating=True)
            self.assertEqual(first['fixed'],[])
            self.assertEqual(first['solution']['datum'],'floating')
            self.assertIsNone(first['solution']['anchor'])
            self.assertTrue(all(not registration_pose.is_identity(items[i]['meta']) for i in ('a','b')))
            before=np.mean([x['scan_origin'] for x in items.values()],axis=0)
            a_before=np.asarray(items['a']['scan_origin'])
            result=run_registration(s,'b','c','rigid',floating=True)
            self.assertEqual(set(result['moving']),set(items))
            self.assertEqual(len(s.STATE['scan_links']),2)
            self.assertGreater(np.linalg.norm(items['a']['scan_origin']-a_before),.01)
            np.testing.assert_allclose(np.mean([x['scan_origin'] for x in items.values()],axis=0),before,atol=1e-9)
            self.assertLess(result['solution']['fit']['rms_mm'],1e-6)
            np.testing.assert_allclose(rw.targets(items['b'])['T2']['source'],rw.targets(items['c'])['T2']['source'],atol=1e-9)
            self.assertEqual([p.read_bytes() for p in paths],original)

    def test_floating_bundle_matches_fixed_fit_and_moves_the_internal_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            run_registration(s,'a','b','vertical','a');run_registration(s,'b','c','rigid','a')
            registration_pose.compose(items['c']['meta'],np.eye(3),np.array([.1,.2,.3]))
            opts=dict(anchor='a',use_cloud=False)
            fixed,_=bundle_workflow.build(s,opts)
            floating,preview=bundle_workflow.build(s,{**opts,'floating':True})
            self.assertEqual(floating['fixed'],[])
            self.assertTrue(all(not p['fixed'] for p in preview))
            self.assertEqual(floating['solution']['datum'],'floating')
            self.assertGreater(np.linalg.norm(floating['transforms']['a']['translation']),.01)
            np.testing.assert_allclose([r['error_mm'] for r in floating['solution']['residuals']],
                                       [r['error_mm'] for r in fixed['solution']['residuals']],atol=1e-8)
            s.STATE['scan_link_draft']=floating
            rw.accept(s,floating['id'])
            self.assertEqual(len(s.STATE['scan_links']),2)
            self.assertFalse(registration_pose.is_identity(items['a']['meta']))
            group=next(iter(s.STATE['bundle_adjustment_reports']))
            qa=rw.bundle_control(s,dict(action='bundle_control_status',bundle_id=group))
            self.assertFalse(qa['adjustment']['stale'])
            self.assertEqual(qa['adjustment']['fit'],floating['solution']['fit'])

    def test_optimise_selected_bundle_uses_saved_links_and_ignores_other_groups(self):
        from workspace_tools import inventory
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            run_registration(s,'a','b','vertical','a')
            run_registration(s,'b','c','rigid','a')
            for ident in ('d','e'):add_scan(s,ident,np.zeros(3))
            s.STATE['scan_links'].append(dict(id='other',a='d',b='e'))
            bundle,other=inventory(s)
            for edge in s.STATE['scan_links']:
                if edge['id']=='other':continue
                edge['target_stamp']=rw.stamp(s,[edge['a'],edge['b']])
            registration_pose.compose(items['c']['meta'],np.eye(3),np.array([.1,.2,.3]))
            opts=dict(bundle_id=bundle['id'],floating=True,use_cloud=False)
            with self.assertRaisesRegex(ValueError,'existing linked bundle'):
                bundle_workflow.build(s,{**opts,'bundle_id':'stale'})
            with self.assertRaisesRegex(ValueError,'saved links'):
                bundle_workflow.build(s,{**opts,'loops':[dict(a='a',b='c')]})
            draft,preview=bundle_workflow.build(s,opts)
            self.assertEqual(set(draft['moving']),set(bundle['scan_ids']))
            self.assertEqual({row['id'] for row in preview},set(bundle['scan_ids']))
            self.assertEqual(len(draft['network_edges']),2)
            self.assertGreater(draft['solution']['before']['rms_mm'],draft['solution']['fit']['rms_mm'])
            self.assertTrue(registration_pose.is_identity(rw.scan(s,'a')['meta']))
            s.STATE['scan_link_draft']=draft
            rw.accept(s,draft['id'])
            self.assertEqual({edge['id'] for edge in s.STATE['scan_links'] if edge['a'] in other['scan_ids']},{'other'})
            self.assertTrue(registration_pose.is_identity(rw.scan(s,'d')['meta']))
            self.assertTrue(registration_pose.is_identity(rw.scan(s,'e')['meta']))

    def test_targets_only_reports_high_rms_without_reading_clouds(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            run_registration(s,'a','b','vertical','a')
            run_registration(s,'b','c','rigid','a')
            items['c']['registration_state']['rows'][0]['source'][0]+=.06
            opts=dict(anchor='a',floating=True,targets_only=True)
            with mock.patch.object(bundle_workflow.pointio,'chunks',side_effect=AssertionError('read cloud in targets-only mode')):
                draft,_=bundle_workflow.build(s,opts)
                self.assertEqual(draft['bundle_cloud_ids'],[])
                self.assertTrue(draft['solution']['targets_only'])
                self.assertEqual(draft['solution']['measured_cloud_links'],0)
                self.assertGreater(draft['solution']['fit']['rms_mm'],5.)
                self.assertTrue(any('exceed the review tolerance' in w for w in draft['solution']['warnings']))
                s.STATE['scan_links'][1]['matches']=[]
                with self.assertRaisesRegex(ValueError,'Saved Fit targets do not connect all setups'):
                    bundle_workflow.build(s,opts)

    def test_targets_first_keeps_cloud_for_insufficient_or_conflicting_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            run_registration(s,'a','b','vertical','a')
            run_registration(s,'b','c','rigid','a')
            first,second=s.STATE['scan_links']
            matches=copy.deepcopy(second['matches'])
            for weak in (True,False):
                with self.subTest(weak=weak):
                    second['matches']=matches[:1] if weak else matches
                    if not weak:
                        items['c']['registration_state']['rows'][0]['source'][0]+=.2
                    with mock.patch.object(bundle_workflow.icp,'_sample_chunks',return_value=(np.empty((0,3)),{})), \
                            mock.patch.object(bundle_workflow.bundle,'optimise',side_effect=RuntimeError('stop after checking edges')) as solve:
                        with self.assertRaisesRegex(RuntimeError,'checking edges'):
                            bundle_workflow.build(s,dict(anchor='a',use_cloud=False))
                    tested={edge['id']:edge['use_cloud'] for edge in solve.call_args.args[1]}
                    self.assertFalse(tested[first['id']])
                    self.assertTrue(tested[second['id']])

    def test_anchor_on_right_holds_its_existing_world_pose_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s);run_registration(s,'a','b','vertical','a')
            R=Rotation.from_euler('xyz',[.08,.03,-.4]).as_matrix();t=np.array([180000.,399000.,100.])
            registration_pose.compose(items['c']['meta'],R,t)
            held=copy.deepcopy(items['c']['meta'])
            result=run_registration(s,'b','c','rigid','c')
            self.assertEqual(items['c']['meta'],held);self.assertEqual(result['fixed'],['c'])
            self.assertEqual(result['request'],dict(a='b',b='c',mode='rigid',anchor='c'))
            btargets=rw.targets(items['b']);ctargets=rw.targets(items['c'])
            for name in set(btargets)&set(ctargets):
                np.testing.assert_allclose(btargets[name]['source'],ctargets[name]['source'],atol=1e-8)
            old=s.STATE['scan_links'][0];self.assertEqual(old['mode'],'vertical')
            self.assertGreater(np.linalg.norm(np.asarray(old['vertical_axis'])-[0,0,1]),.05)

    def test_connected_network_preserves_old_2d_mode_and_new_3d_freedom(self):
        for anchor in ('a','b','c'):
            with self.subTest(anchor=anchor),tempfile.TemporaryDirectory() as folder:
                s=server(folder);items=fixture(s)
                run_registration(s,'a','b','vertical','a');run_registration(s,'b','c','rigid','a')
                fixed=copy.deepcopy(items[anchor]['meta'])
                result=run_registration(s,'b','c','rigid',anchor)
                self.assertTrue(result['network']);self.assertEqual(items[anchor]['meta'],fixed)
                self.assertLess(result['solution']['fit']['rms_mm'],1e-6)
                modes={frozenset((e['a'],e['b'])):e['solution']['mode'] for e in s.STATE['scan_links']}
                self.assertEqual(modes[frozenset(('a','b'))],'vertical')
                self.assertEqual(modes[frozenset(('b','c'))],'rigid')
                self.assertEqual(len(s.STATE['scan_links']),2)

    def test_network_adjusts_rotated_vertical_group_with_external_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s)
            run_registration(s,'a','b','vertical','a');run_registration(s,'b','c','rigid','a')
            R=Rotation.from_euler('xyz',[.2,-.1,.3]).as_matrix();t=np.array([1.,2.,-.4])
            for ident in ('a','b'):registration_pose.compose(items[ident]['meta'],R,t)
            s.STATE['scan_links'][0]['vertical_axis']=(R@np.array([0.,0.,1.])).tolist()
            fixed=copy.deepcopy(items['c']['meta'])
            result=run_registration(s,'b','c','rigid','c')
            self.assertEqual(items['c']['meta'],fixed);self.assertLess(result['solution']['fit']['rms_mm'],1e-5)
            for item in items.values():
                raw=np.fromfile(item['meta']['file'],'<f8').reshape(-1,3)
                np.testing.assert_allclose(registration_pose.points(item['meta'],raw),WORLD,atol=1e-8)
            np.testing.assert_allclose(s.STATE['scan_links'][0]['vertical_axis'],[0,0,1],atol=1e-8)

    def test_auto_matches_preserve_checks_and_explicit_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);a=add_scan(s,'a',np.zeros(3));b=add_scan(s,'b',np.array([10.,0.,0.]))
            for item in (a,b):
                rows=item['registration_state']['rows']
                rows.append(dict(id='CHECK',role='check',source=[20.,20.,20.],control=[None]*3))
                rows.append(dict(id='OFF',role='off',source=[0.,3.,0.],control=[None]*3))
            b['registration_state']['rows'][-2]['source'][0]+=2
            result=run_registration(s,'a','b','rigid','a')
            self.assertEqual(result['solution']['fit']['count'],3);self.assertEqual(result['solution']['check']['count'],1)
            self.assertGreater(result['solution']['check']['rms_mm'],1000)
            self.assertEqual(rw.public(s)['scans'][0]['target_roles']['CHECK'],'check')
            matches=rw.matched_targets(a,b,[dict(a='CHECK',b='CHECK',role='fit')])
            self.assertEqual(matches[0]['role'],'check')
            self.assertNotIn('OFF',rw.targets(a))

    def test_finite_high_registration_residual_is_applied_with_error_report(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);a=add_scan(s,'a',np.zeros(3));b=add_scan(s,'b',np.array([10.,0.,0.]))
            b['registration_state']['rows'][2]['source'][1]+=.08
            result=run_registration(s,'a','b','rigid','a')
            self.assertGreater(result['solution']['fit']['rms_mm'],5.)
            self.assertTrue(result['applied']);self.assertEqual(len(s.STATE['scan_links']),1)
            # No checkbox, accepted flag, or residual threshold gates the result.
            self.assertTrue(result['solution']['residuals'])
            self.assertTrue(any(not r['within_limit'] for r in result['solution']['residuals']))

    def test_3d_insufficient_or_duplicate_geometry_still_has_no_transform(self):
        with tempfile.TemporaryDirectory() as folder:
            s=server(folder);items=fixture(s);before=copy.deepcopy([x['meta'] for x in items.values()])
            with self.assertRaisesRegex(ValueError,'at least 3'):
                run_registration(s,'a','b','rigid','a')
            self.assertFalse(s.STATE['scan_links'])
            self.assertEqual([x['meta'] for x in items.values()],before)
            with self.assertRaisesRegex(ValueError,'only once'):
                rw.matched_targets(items['a'],items['b'],[dict(a='T1',b='T1'),dict(a='T1',b='T2')])


if __name__=='__main__':unittest.main()
