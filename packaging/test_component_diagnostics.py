"""Installer diagnostics must explain failures without activating the update."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_installer
import windows_installer as install


class ComponentDiagnosticsTests(unittest.TestCase):
    # Reuse the transaction fixture without inheriting/rerunning its test methods.
    setUp = test_installer.InstallerTests.setUp

    def failed_activation(self, returncode=1, output=b'', *, timeout=False):
        before = {path: path.read_bytes() for path in
                  (self.data / 'project.json', self.data / 'points.xyz64', self.marker,
                   self.old / 'old.txt')}
        call = {}

        def child(command, **kwargs):
            call.update(command=command, **kwargs)
            temporary = Path(kwargs['env']['TUNNEL_STUDIO_DATA'])
            self.assertTrue(temporary.is_dir())
            self.assertNotEqual(temporary, self.data)
            self.assertTrue(temporary.name.startswith('TunnelStudio_setup_check_'))
            if timeout:
                raise subprocess.TimeoutExpired(command, 180, output=output)
            return subprocess.CompletedProcess(command, returncode, stdout=output)

        with patch.object(install.subprocess, 'run', side_effect=child):
            with self.assertRaises(RuntimeError) as caught:
                install.activate(self.stage, self.base, self.data)
        self.assertEqual(Path(call['command'][0]), self.stage / 'runtime/python.exe')
        self.assertFalse(Path(call['env']['TUNNEL_STUDIO_DATA']).exists())
        self.assertTrue(self.stage.is_dir())
        self.assertFalse((self.base / install.VERSION).exists())
        for path, original in before.items():
            self.assertEqual(path.read_bytes(), original)
        return str(caught.exception)

    def test_first_nsis_line_retains_actual_python_exception(self):
        detail = ('Checking isolated startup and hidden recovery\n'
                  'Traceback (most recent call last):\n'
                  '  File "startup_smoke.py", line 80, in verify_startup\n'
                  'RuntimeError: Startup self-check changed the prior autosave bytes.\n')
        error = self.failed_activation(output=detail.encode('utf-8'))
        first = error.splitlines()[0]
        self.assertIn('exit 1', first)
        self.assertIn('RuntimeError: Startup self-check changed the prior autosave bytes.', first)
        self.assertIn('Traceback (most recent call last):', error)
        self.assertIn('Checking isolated startup and hidden recovery', error)

    def test_empty_native_crash_output_includes_windows_exit_status(self):
        # Windows access-violation status can be represented signed or unsigned.
        for status in (-1073741819, 3221225477):
            with self.subTest(status=status):
                first = self.failed_activation(returncode=status).splitlines()[0]
                self.assertIn(str(status), first)
                self.assertIn('0xC0000005', first)
                self.assertIn('No diagnostic output was returned.', first)

    def test_invalid_utf8_does_not_hide_component_failure(self):
        error = self.failed_activation(
            output=b'Checking Open3D\nImportError: DLL load failed: \xff\xfe\n')
        self.assertIn('ImportError: DLL load failed:', error.splitlines()[0])
        self.assertIn('\ufffd', error)
        self.assertIn('Checking Open3D', error)

    def test_timeout_retains_last_phase_and_cleans_isolated_workspace(self):
        error = self.failed_activation(timeout=True, output=b'Checking CAD engines\n')
        self.assertIn('startup check timed out after 180 seconds', error.splitlines()[0])
        self.assertIn('Checking CAD engines', error)

    def test_host_interpreter_requires_explicit_override(self):
        result = subprocess.CompletedProcess([], 0, stdout=b'components verified\n')
        with patch.object(install.subprocess, 'run', return_value=result) as child:
            self.assertEqual(install.smoke_check(self.stage), 'components verified')
            self.assertEqual(Path(child.call_args.args[0][0]),
                             self.stage / 'runtime/python.exe')
            self.assertFalse(Path(child.call_args.kwargs['env']['TUNNEL_STUDIO_DATA']).exists())
            self.assertEqual(install.smoke_check(self.stage, interpreter=sys.executable),
                             'components verified')
            self.assertEqual(child.call_args.args[0][0], sys.executable)
            self.assertFalse(Path(child.call_args.kwargs['env']['TUNNEL_STUDIO_DATA']).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
