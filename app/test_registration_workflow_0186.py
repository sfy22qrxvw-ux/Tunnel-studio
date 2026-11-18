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


def run_registration(s,a,b,mode,anchor,matches=None):
    sent=[]
    rw.api(s,SimpleNamespace(send=sent.append),'/api/scan-registration',
           dict(action='register_setups',a=a,b=b,mode=mode,anchor=anchor,tolerance=5,matches=matches))
    result=s.STATE['last_registration_result']
    assert result['id']==sent[-1]['registration_id']
    return result


class RegistrationWorkflow0186Tests(unittest.TestCase):
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
