"""Exercise the native FormClosing protocol without a Windows display."""
import types
import unittest
from unittest.mock import Mock
from desktop_exit import ExitReview


class ControlledTimer:
    def __init__(self, seconds, fn, args):
        self.callback = lambda: fn(*args)
    def start(self): pass
    def cancel(self): pass


class DesktopExitTests(unittest.TestCase):
    def setUp(self):
        self.win=Mock()
        self.win.evaluate_js.return_value=True
        self.busy=Mock(return_value=False)
        self.guard=Mock()
        self.review=ExitReview(self.win,self.busy,self.guard,timer=ControlledTimer)
        self.queued=[]
        self.review._post=lambda fn,*args:self.queued.append(lambda:fn(*args))

    def x(self,reason='UserClosing',already_cancelled=False):
        event=types.SimpleNamespace(CloseReason=reason,Cancel=already_cancelled)
        self.review.on_closing(None,event)
        return event.Cancel

    def run_next(self):
        self.queued.pop(0)()

    def test_x_is_nonblocking_and_keep_cancels_without_shutdown(self):
        self.assertTrue(self.x())
        self.win.evaluate_js.assert_not_called()
        self.guard.assert_not_called()
        token=self.review.ticket
        self.assertTrue(self.x())
        self.assertEqual(len(self.queued),1)
        self.run_next()
        self.assertTrue(self.review.finish(token,'cancelled'))
        self.assertFalse(self.review.finish(token,'cleaned'))
        self.assertFalse(self.queued)
        self.assertTrue(self.x())
        self.assertNotEqual(self.review.ticket,token)

    def test_review_or_skip_releases_once_then_native_guard_runs(self):
        for outcome in ('cleaned','skipped'):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.assertTrue(self.x())
                self.run_next()
                token=self.review.ticket
                self.assertFalse(self.review.finish('wrong',outcome))
                self.assertTrue(self.review.finish(token,outcome))
                self.assertFalse(self.review.finish(token,outcome))
                self.run_next()
                self.win.destroy.assert_called_once()
                self.assertFalse(self.x())
                self.guard.assert_called_once()

    def test_missing_or_unresponsive_bridge_skips_without_cleanup(self):
        self.win.evaluate_js.return_value=False
        self.x();self.run_next();self.run_next()
        self.win.destroy.assert_called_once()
        self.assertFalse(self.x())
        # Timeout before the worker returns. An old acknowledgement must not
        # revive the review or trigger a duplicate destroy.
        self.setUp();self.x()
        token,probe=self.review.ticket,self.review.probe
        self.review._acknowledge(token,probe,False)
        self.run_next();self.run_next()
        self.assertFalse(self.review.finish(token,'cleaned'))
        self.win.destroy.assert_called_once()

    def test_repeated_x_after_ack_reuses_review_and_ignores_finished_probe(self):
        self.x();ticket=self.review.ticket;old=self.review.probe
        self.run_next()
        self.x();self.assertEqual(self.review.ticket,ticket)
        self.review._acknowledge(ticket,old,False)
        self.run_next()
        self.win.destroy.assert_not_called()
        self.assertTrue(self.review.finish(ticket,'cancelled'))

    def test_busy_commit_blocks_closure_and_os_shutdown_can_proceed(self):
        self.x();token=self.review.ticket
        self.busy.return_value=True
        self.assertTrue(self.x())
        self.assertTrue(self.review.finish(token,'skipped'))
        while self.queued:self.run_next()
        self.win.destroy.assert_not_called()
        self.assertFalse(self.x('WindowsShutDown'))
        self.guard.assert_called_once()

    def test_respects_an_earlier_close_veto(self):
        self.assertTrue(self.x(already_cancelled=True));self.assertFalse(self.queued)
        self.guard.assert_not_called()
