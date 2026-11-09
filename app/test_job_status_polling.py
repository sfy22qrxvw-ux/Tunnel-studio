"""Exercise progress-only and complete workspace status over local HTTP."""
import json
import threading
import unittest
from urllib.request import urlopen
from unittest.mock import patch

import server


class JobStatusPollingTests(unittest.TestCase):
    def test_poll_omits_large_results_until_completion_and_keeps_legacy_api(self):
        workspace = server.workflows.empty()
        workspace['analysis_tool_result'] = {'points': [[1.25, 2.5, 3.75]] * 10000}
        job = dict(busy=True, message='Cleaning tunnel', error=None, cancelled=False)
        with server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler) as listener:
            thread = threading.Thread(target=listener.serve_forever)
            thread.start()
            try:
                def status(query=''):
                    with urlopen(f'http://127.0.0.1:{listener.server_port}/api/status{query}', timeout=5) as reply:
                        body = reply.read()
                    return json.loads(body), len(body)

                with patch.object(server, 'STATE', workspace), patch.object(server, 'JOB', job):
                    with patch.object(server, 'status', side_effect=AssertionError('Full snapshot built during progress polling')):
                        while_running, small = status('?during_job=1')
                    self.assertEqual(while_running, {'job': job})
                    full, large = status()
                    self.assertEqual(full['analysis_tool_result'], workspace['analysis_tool_result'])
                    self.assertGreater(large, 100 * small)

                    job.update(busy=False, cancelled=True, message='Cancelled safely')
                    completed, _ = status('?during_job=1')
                    self.assertEqual(completed['job'], job)
                    self.assertEqual(completed['analysis_tool_result'], workspace['analysis_tool_result'])
                    section, _ = status('?job_only=1')
                    self.assertEqual(section, {'job': job})
            finally:
                listener.shutdown()
                thread.join()


if __name__ == '__main__':
    unittest.main()
