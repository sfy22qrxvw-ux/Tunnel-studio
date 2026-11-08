# Copyright (c) 2026 Damian Gulczynski.
import sys
import unittest
from unittest.mock import Mock, patch
import desktop


class StartupTests(unittest.TestCase):
    def test_failed_bridge_does_not_acquire_or_restore_workspace(self):
        server = Mock()
        with patch.dict(sys.modules, {'server': server}), \
             patch.object(desktop, 'desktop_backend', side_effect=RuntimeError('bridge failed')), \
             patch.object(desktop, 'show_failure') as failure:
            desktop.main()
        server.acquire_project_lock.assert_not_called()
        server.restore.assert_not_called()
        self.assertTrue(failure.call_args.kwargs['allow_browser'])

    def test_failed_window_releases_workspace_before_offering_recovery(self):
        server = Mock()
        server.JOB = {'busy': False}
        http = server.ThreadingHTTPServer.return_value
        webview = Mock(settings={})
        webview.start.side_effect = RuntimeError('window failed')
        def check_released(*args, **kwargs):
            http.shutdown.assert_called_once()
            http.server_close.assert_called_once()
            server._PROJECT_LOCK.close.assert_called_once()
            self.assertTrue(kwargs['allow_browser'])
        with patch.dict(sys.modules, {'server': server}), \
             patch.object(desktop, 'desktop_backend', return_value=webview), \
             patch.object(desktop, 'show_failure', side_effect=check_released), \
             patch.object(desktop.threading, 'Thread'):
            desktop.main()
        server._PROJECT_LOCK.close.assert_called_once()

    def test_existing_session_does_not_start_competing_recovery(self):
        server = Mock()
        server.acquire_project_lock.side_effect = ValueError('already open')
        with patch.dict(sys.modules, {'server': server}), \
             patch.object(desktop, 'desktop_backend', return_value=Mock()), \
             patch.object(desktop, 'show_failure') as failure:
            desktop.main()
        self.assertFalse(failure.call_args.kwargs['allow_browser'])
        server.restore.assert_not_called()
        server._PROJECT_LOCK.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
