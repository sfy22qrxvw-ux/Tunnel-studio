"""Independent review: real registration APIs/history with controlled jobs."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import job_api
import jobdata
import registration
import registration_network as network
import registration_pose as pose
import registration_workspace as rw
import workspace_history as history
import workflows
import numpy as np
from scipy.spatial.transform import Rotation


class Server:
    def __init__(self, folder):
        self.WORK = Path(folder)
        self.STATE = workflows.empty()
        self.STATE.update(job_items=[], scan_links=[], survey_controls={})
        self.JOB = {'busy': False}
        self.JOB_CANCEL_FILE = self.WORK / 'cancel'
        self.SELECTION = None
        self.saved = 0
    def save(self):
        self.saved += 1
    def progress(self, message):
        pass
    def start_job(self, action, kind='general', native_progress=False):
        wrapped = history.background(self, action)
        self.JOB['busy'] = True
        job_api.CANCEL.clear()
        try:
            wrapped()
        finally:
            self.JOB['busy'] = False


def add(s, ident, points):
    points = np.asarray(points, dtype='<f8')
    path = s.WORK / (ident + '.xyz64')
    attrs = s.WORK / (ident + '.attr32')
    points.tofile(path)
    np.zeros((len(points), 4), '<f4').tofile(attrs)
    meta = dict(name=ident, file=str(path), attributes_file=str(attrs), count=len(points),
                minimum=points.min(0).tolist(), maximum=points.max(0).tolist(),
                attribute_counts=[len(points)] * 4)
    item = dict(id=ident, kind='scan', name=ident, meta=meta, visible=True,
                hidden_boxes=[], isolate_box=None, edit_history=[], redo_history=[])
    item['registration_state'] = dict(cloud=copy.deepcopy(meta), candidates=[],
        rows=[dict(id='T'+str(i), role='fit', source=p.tolist(), control=[None]*3)
              for i,p in enumerate(points)], solution=None, transformed=None, revision='initial')
    s.STATE['job_items'].append(item)
    return item


def run(s, a, b, mode='rigid', anchor='a', matches=None):
    path = '/api/scan-registration'
    body = dict(action='register_setups', a=a, b=b, mode=mode, anchor=anchor,
                tolerance=.1, matches=matches)
    with history.request(s, path):
        history.configure(path, body)
        rw.api(s, SimpleNamespace(send=lambda value: None), path, body)
    return s.STATE['last_registration_result']


P = np.array([[0.,0.,0.],[3.,0.,0.],[0.,2.,1.],[1.,1.,2.]])


class RegistrationTransaction0186Tests(unittest.TestCase):
    def setUp(self):
        job_api.CANCEL.clear()
    def tearDown(self):
        job_api.CANCEL.clear()

    def test_single_checkpoint_undo_redo_no_duplicate_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder);add(s,'a',P);add(s,'b',P+[10.,2.,3.])
            old=history.snapshot(s)
            files={p.name:p.read_bytes() for p in s.WORK.iterdir()}
            with mock.patch.object(jobdata,'room',side_effect=AssertionError('XYZ allocation')):
                result=run(s,'a','b')
            registered=history.snapshot(s)
            self.assertTrue(result['applied'])
            self.assertEqual(history.status(s)['undo_count'],1)
            history.apply(s)
            self.assertTrue(history.equal(history.snapshot(s),old))
            history.apply(s,redo=True)
            self.assertTrue(history.equal(history.snapshot(s),registered))
            self.assertEqual(files,{p.name:p.read_bytes() for p in s.WORK.iterdir()})

    def test_cancel_before_commit_restores_whole_state_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder);add(s,'a',P);add(s,'b',P+[10.,2.,3.])
            old=history.snapshot(s)
            with mock.patch.object(job_api,'progress',side_effect=jobdata.Cancelled('review cancellation')):
                with self.assertRaises(jobdata.Cancelled):run(s,'a','b')
            self.assertTrue(history.equal(history.snapshot(s),old),
                            'Cancelled registration retained a newly solved draft or other mutation')
            self.assertEqual(history.status(s)['undo_count'],0)

    def test_save_failure_restores_whole_state_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder);add(s,'a',P);add(s,'b',P+[10.,2.,3.])
            old=history.snapshot(s)
            with mock.patch.object(s,'save',side_effect=OSError('review disk failure')):
                with self.assertRaises(OSError):run(s,'a','b')
            self.assertTrue(history.equal(history.snapshot(s),old),
                            'Failed registration retained a newly solved draft or other mutation')
            self.assertEqual(history.status(s)['undo_count'],0)

    def test_existing_draft_survives_failed_new_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder);add(s,'a',P);add(s,'b',P+[10.,2.,3.])
            rw.solve_pair(s,'a','b')
            old=history.snapshot(s)
            with self.assertRaisesRegex(ValueError,'at least 3'):
                run(s,'a','b',matches=[dict(a='T0',b='T0'),dict(a='T1',b='T1')])
            self.assertTrue(history.equal(history.snapshot(s),old))
            self.assertEqual(history.status(s)['undo_count'],0)

    def test_mid_apply_failure_restores_both_moving_setups(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder);add(s,'a',P);add(s,'b',P+[10.,2.,3.]);add(s,'c',P+[20.,4.,6.])
            run(s,'a','b')
            old=history.snapshot(s);old_count=history.status(s)['undo_count']
            s.SELECTION='original-selection'
            compose=pose.compose;calls=[]
            def broken_compose(meta,R,t):
                compose(meta,R,t);calls.append(meta['name'])
                if len(calls)==2:raise RuntimeError('review failure after second pose mutation')
            with mock.patch.object(pose,'compose',side_effect=broken_compose):
                with self.assertRaises(RuntimeError):run(s,'b','c',anchor='c')
            self.assertEqual(len(calls),2)
            self.assertTrue(history.equal(history.snapshot(s),old))
            self.assertEqual(history.status(s)['undo_count'],old_count)
            self.assertEqual(s.SELECTION,'original-selection')

    def test_resolving_existing_vertical_edge_keeps_rotated_axis(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Server(folder)
            add(s,'a',P);add(s,'b',P+[5.,0.,0.]);add(s,'c',P+[10.,0.,0.])
            Q=Rotation.from_euler('xyz',[.2,-.1,.3]).as_matrix()
            add(s,'d',P@Q.T+[100.,200.,3.])
            two=[dict(a='T0',b='T0'),dict(a='T1',b='T1')]
            run(s,'a','b','vertical','a',two)
            run(s,'b','c','vertical','a',two)
            run(s,'a','d','rigid','d')
            fixed=copy.deepcopy(rw.scan(s,'d')['meta'])
            old_axes=[np.array(e['vertical_axis']) for e in s.STATE['scan_links'] if e['mode']=='vertical']
            self.assertEqual(len(old_axes),2)
            self.assertGreater(np.linalg.norm(old_axes[0]-[0.,0.,1.]),.1)
            result=run(s,'a','b','vertical','d',two)
            self.assertEqual(rw.scan(s,'d')['meta'],fixed)
            self.assertLess(result['solution']['fit']['rms_mm'],1e-6)
            for edge in s.STATE['scan_links']:
                if edge['mode']=='vertical':np.testing.assert_allclose(edge['vertical_axis'],Q[:,2],atol=1e-9)

    def test_noisy_two_vertical_groups_mixed_loop_preserves_constraints_and_anchor(self):
        for anchor in ('a','b','c','d'):
            with self.subTest(anchor=anchor),tempfile.TemporaryDirectory() as folder:
                s=Server(folder);rng=np.random.default_rng(723);world=P+[180000.,399000.,120.]
                tilt=Rotation.from_euler('xyz',[.24,-.19,.1]).as_matrix()
                rotations=[np.eye(3),Rotation.from_euler('z',.43).as_matrix(),
                           tilt@Rotation.from_euler('z',.4).as_matrix(),
                           tilt@Rotation.from_euler('z',-.8).as_matrix()]
                translations=[np.zeros(3),np.array([180010.,399003.,122.]),
                              np.array([180005.,399002.,118.]),np.array([180001.,399009.,124.])]
                for ident,R,t in zip('abcd',rotations,translations):
                    measured=world+rng.normal(0,.0003,world.shape)
                    add(s,ident,(measured-t)@R)
                two=[dict(a='T0',b='T0'),dict(a='T1',b='T1')]
                run(s,'a','b','vertical','a',two)
                run(s,'c','d','vertical','c',two)
                run(s,'b','c','rigid','a')
                fixed=copy.deepcopy(rw.scan(s,anchor)['meta'])
                old_axes={e['id']:np.asarray(e['vertical_axis']) for e in s.STATE['scan_links'] if e['mode']=='vertical'}
                draft=network.solve(s,'a','d',None,.1,'rigid',anchor)
                for edge in draft['network_edges']:
                    if edge['mode']!='vertical':continue
                    axis=old_axes[edge['id']]
                    Ra=np.asarray(draft['transforms'].get(edge['a'],dict(rotation=np.eye(3)))['rotation'])
                    Rb=np.asarray(draft['transforms'].get(edge['b'],dict(rotation=np.eye(3)))['rotation'])
                    np.testing.assert_allclose(Ra@axis,Rb@axis,atol=1e-11)
                self.assertLessEqual(draft['solution']['fit']['rms_mm'],draft['solution']['before']['rms_mm']+1e-8)
                rw.accept(s,draft['id'])
                self.assertEqual(rw.scan(s,anchor)['meta'],fixed)
                self.assertEqual(len(s.STATE['scan_links']),4)
                self.assertEqual([e['mode'] for e in s.STATE['scan_links']].count('vertical'),2)
                self.assertTrue(all(np.isfinite(r['error_mm']) for r in draft['solution']['residuals']))
                self.assertGreater(draft['solution']['fit']['rms_mm'],.1)
                self.assertTrue(s.STATE['last_registration_result']['applied'])

    def test_nonfinite_and_collinear_targets_do_not_apply(self):
        for points in (np.array([[0.,0.,0.],[1.,1.,1.],[2.,2.,2.]]),
                       np.array([[0.,0.,0.],[1.,0.,0.],[float('nan'),2.,1.]])):
            with self.subTest(points=points.tolist()),tempfile.TemporaryDirectory() as folder:
                s=Server(folder);a=add(s,'a',np.nan_to_num(points));b=add(s,'b',np.nan_to_num(points)+[3.,4.,5.])
                for row,p in zip(a['registration_state']['rows'],points):row['source']=p.tolist()
                fixed=copy.deepcopy([x['meta'] for x in s.STATE['job_items']])
                with self.assertRaises(ValueError):run(s,'a','b')
                self.assertEqual([x['meta'] for x in s.STATE['job_items']],fixed)
                self.assertFalse(s.STATE['scan_links'])


if __name__=='__main__':
    unittest.main()
