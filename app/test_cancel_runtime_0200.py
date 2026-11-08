"""Cancellation boundaries, worker lifetime and atomic mesh publication."""
import ast
import copy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import compute_runtime as compute
import job_api
import jobdata
import mesh_workflows
import surface_api


class RuntimeCancellationTests(unittest.TestCase):
    def tearDown(self):
        job_api.CANCEL.clear()
        surface_api.CANCEL.clear()

    def test_worker_timeout_error_propagates_instead_of_polling_forever(self):
        def fail(_):
            raise TimeoutError('numerical operation timed out')
        with self.assertRaisesRegex(TimeoutError, 'numerical operation timed out'):
            list(compute.ordered_map(fail, [0, 1], worker_count=2))

    def test_unknown_stream_is_consumed_once_and_small_stream_stays_serial(self):
        coordinator = threading.get_ident()
        consumed = []
        def chunks():
            for i in range(3):
                consumed.append(i)
                yield i
        values = list(compute.process_chunks(
            lambda i: (i, threading.get_ident()), chunks(), worker_count=4))
        self.assertEqual(consumed, [0, 1, 2])
        self.assertEqual(values, [(i, coordinator) for i in range(3)])

    def test_early_close_joins_workers_before_caller_removes_assets(self):
        entered = threading.Event()
        finished = []
        def work(i):
            entered.set()
            time.sleep(.015)
            finished.append(i)
            return i
        results = compute.ordered_map(work, list(range(20)), worker_count=3)
        self.assertEqual(next(results), 0)
        results.close()
        snapshot = list(finished)
        time.sleep(.025)
        self.assertTrue(entered.is_set())
        self.assertEqual(finished, snapshot)
        self.assertLessEqual(len(finished), 3)

    def test_surface_stop_is_visible_to_compute_helpers(self):
        surface_api.CANCEL.set()
        with self.assertRaises(compute.ProcessingCancelled):
            compute.check_cancel()


class ServerCancellationTests(unittest.TestCase):
    def setUp(self):
        # Execute the production job functions in an isolated module so the
        # test does not start the desktop server or open a real project cache.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = ModuleType('_tunnel_cancel_regression_0200')
        self.addCleanup(sys.modules.pop, self.module.__name__, None)
        sys.modules[self.module.__name__] = self.module
        self.threads = []
        def thread_factory(*args, **kwargs):
            thread = threading.Thread(*args, **kwargs)
            self.threads.append(thread)
            return thread
        self.diagnostics = []
        self.module.__dict__.update(
            sys=sys, time=time, os=os, Path=Path, jobdata=jobdata,
            ROOT=Path(__file__).parent,
            subprocess=SimpleNamespace(Popen=lambda *args, **kwargs: None),
            threading=SimpleNamespace(Thread=thread_factory),
            workspace_history=SimpleNamespace(background=lambda server, action: action),
            runtime_diagnostics=SimpleNamespace(exception=lambda *args: self.diagnostics.append(args)),
            traceback=SimpleNamespace(print_exc=lambda: None),
            JOB={'busy': False}, JOB_CANCEL_FILE=self.root/'cancel', LOCK=threading.Lock(),
            _write_job_status=lambda **kwargs: None, _start_native_progress=lambda: None)
        names = {'OperationCancelled', '_cancel_requested', '_clear_cancel_events',
                 '_start_native_progress', 'progress', 'start_job'}
        source = ast.parse(Path(__file__).with_name('server.py').read_text(encoding='utf8'))
        selected = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'server.py', 'exec'), self.module.__dict__)
        job_api.CANCEL.clear(); surface_api.CANCEL.clear()
        self.addCleanup(job_api.CANCEL.clear)
        self.addCleanup(surface_api.CANCEL.clear)

    def wait_job(self):
        for thread in self.threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_job_event_stops_general_progress_without_a_cancel_file(self):
        self.module.JOB['busy'] = True
        job_api.CANCEL.set()
        with self.assertRaises(self.module.OperationCancelled):
            self.module.progress('Should not continue')

    def test_opening_native_progress_does_not_erase_a_new_stop_request(self):
        self.module.JOB_CANCEL_FILE.write_text('cancel', encoding='utf8')
        self.module._start_native_progress()
        self.assertTrue(self.module.JOB_CANCEL_FILE.exists())

    def test_cancelled_cleaner_is_reported_as_cancelled_and_next_job_starts_clean(self):
        def action():
            surface_api.CANCEL.set()
            raise ValueError('Clean Tunnel cancelled. Previous results are unchanged.')
        self.module.start_job(action)
        self.wait_job()
        self.assertTrue(self.module.JOB['cancelled'])
        self.assertIsNone(self.module.JOB['error'])
        self.assertFalse(self.module.JOB['busy'])
        self.assertFalse(surface_api.CANCEL.is_set())
        self.assertFalse(job_api.CANCEL.is_set())
        self.assertFalse(self.diagnostics)
        # Also clear stale events present before another job is admitted.
        surface_api.CANCEL.set(); job_api.CANCEL.set()
        observed = []
        self.module.start_job(lambda: observed.append(compute.cancelled()))
        self.wait_job()
        self.assertEqual(observed, [False])
        self.assertFalse(self.module.JOB['cancelled'])

    def test_io_error_is_not_hidden_as_cancellation(self):
        def action():
            job_api.CANCEL.set()
            raise OSError('Disk write failed')
        self.module.start_job(action)
        self.wait_job()
        self.assertFalse(self.module.JOB['cancelled'])
        self.assertEqual(self.module.JOB['error'], 'Disk write failed')
        self.assertEqual(len(self.diagnostics), 1)


class MeshCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root/'source.xyz64'
        path.write_bytes(b'original points')
        self.item = dict(id='scan', kind='scan', name='Scan',
                         meta=dict(name='Scan', file=str(path), count=0))
        self.server = SimpleNamespace(
            STATE={'job_items': [self.item]}, WORK=self.root,
            JOB_CANCEL_FILE=self.root/'cancel', progress=lambda _: None, save=lambda: None)
        job_api.CANCEL.clear()
        self.addCleanup(job_api.CANCEL.clear)

    def test_stop_after_reconstruction_removes_only_unpublished_mesh(self):
        previous = copy.deepcopy(self.server.STATE)
        def create(*args, **kwargs):
            (Path(args[1])/'result.vertices64').write_bytes(b'unpublished')
            job_api.CANCEL.set()
            return {'mesh_summary': {}}
        with patch.object(mesh_workflows.meshing, 'create', side_effect=create):
            with self.assertRaises(compute.ProcessingCancelled):
                mesh_workflows.create_from_scan(self.server, {'source_id': 'scan'})
        self.assertEqual(self.server.STATE, previous)
        self.assertFalse(list(self.root.glob('mesh-*')))
        self.assertEqual(Path(self.item['meta']['file']).read_bytes(), b'original points')

    def test_stop_received_at_final_progress_checkpoint_prevents_publication(self):
        identity = mesh_workflows._source_identity(self.item)
        self.server.progress = lambda _: job_api.CANCEL.set()
        with patch.object(mesh_workflows.job_api, 'append') as append:
            with self.assertRaises(compute.ProcessingCancelled):
                mesh_workflows._publish(self.server, 'scan', identity, {'mesh_summary': {}})
        append.assert_not_called()


if __name__ == '__main__':
    unittest.main()
