"""Real filesystem + production lifecycle integration with a typed HKCU model.

These tests replace the Windows registry OS adapter and native component launch,
not the activation, registration, safe cleanup or uninstall implementations.
The independent startup suite exercises the real application component command.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import windows_installer as install
import release_cleanup
import release_registry as rr
import test_release_cleanup as cleanup_fixtures
import test_release_registry as registry_fixtures


class UpgradeIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / 'LocalAppData'
        self.base = self.local / 'Programs/TunnelStudio/releases'
        self.base.mkdir(parents=True)
        self.data = self.local / 'TunnelStudio'
        self.data.mkdir()
        (self.data / 'project.json').write_bytes(b'{"survey":"do not alter"}\n')
        (self.data / 'points.xyz64').write_bytes(b'original survey point bytes')
        (self.data / 'session.lock').write_bytes(b'')
        self.previous = self.release('0.18.4')
        self.target = self.base / install.VERSION
        self.stage = self.release(install.VERSION, '.installing-lifecycle-test')
        (self.stage / 'UninstallTunnelStudio.exe').write_bytes(b'MZnew generated uninstaller')
        self.marker = self.base / 'current_release.json'
        self.write_marker(self.previous)
        self.reg = registry_fixtures.FakeRegistry()
        self.reg.seed(rr.VENDOR_KEY, rr.RegistryKey({
            'CurrentInstall': (str(self.previous), rr.REG_SZ),
            'Theme': ('dark', rr.REG_SZ), 'CustomBinary': (b'keep\0this', 3),
        }))
        self.seed_legacy(self.previous)
        self.hook_events = []
        self.check_lock = False

    def release(self, version, name=None, extra=None):
        return cleanup_fixtures.ReleaseCleanupTests.release(self, version, name, extra)

    def snapshot(self, path):
        return cleanup_fixtures.ReleaseCleanupTests.snapshot(self, path)

    def write_marker(self, path):
        version = json.loads((path / 'FILES_SHA256.json').read_text())['version']
        self.marker.write_text(json.dumps({'version': version, 'program_folder': str(path),
                                          'old_custom_field': 'preserve exact original bytes'}, indent=3) + '\n')

    def program_entry(self, path, *, stable=False):
        version = json.loads((path / 'FILES_SHA256.json').read_text())['version']
        return rr.RegistryKey({
            'DisplayName': ('Tunnel Studio' if stable else 'Tunnel Studio ' + version, rr.REG_SZ),
            'DisplayVersion': (version, rr.REG_SZ), 'Publisher': (rr.PUBLISHER, rr.REG_SZ),
            'InstallLocation': (str(path), rr.REG_SZ),
            'UninstallString': ('"' + str(path / 'UninstallTunnelStudio.exe') + '"', rr.REG_SZ),
            'NoModify': (1, rr.REG_DWORD), 'NoRepair': (1, rr.REG_DWORD),
        })

    def seed_legacy(self, path):
        version = json.loads((path / 'FILES_SHA256.json').read_text())['version']
        self.reg.seed(rr.UNINSTALL_ROOT + '\\TunnelStudio-' + version, self.program_entry(path))

    def hook(self, name):
        self.hook_events.append(name)
        if self.check_lock:
            with self.assertRaisesRegex(RuntimeError, 'Close Tunnel Studio'):
                with install.exclusive(self.data):
                    pass

    def read_current(self, base):
        self.hook('read_current')
        return rr.read_current_install(base, registry=self.reg)

    def register(self, base, target, version):
        self.hook('register')
        return rr.register_release(base, target, version, registry=self.reg)

    def clean_old(self, base, current, previous, **kwargs):
        self.hook('cleanup')
        return release_cleanup.cleanup(base, current, previous,
                                       local_app_data=self.local, **kwargs)

    def unregister(self, base, target, version):
        self.hook('unregister')
        return rr.remove_release_registration(base, target, version, registry=self.reg)

    def remove_program(self, base, target, **kwargs):
        self.hook('remove_program')
        return release_cleanup.remove_release(base, target, local_app_data=self.local, **kwargs)

    def activate(self, *, stage=None, smoke=None, registrar=None, cleanup=None):
        return install.activate(stage or self.stage, self.base, self.data,
            smoke=smoke or (lambda _: 'Native launch replaced by controlled test success'),
            registrar=registrar or self.register, previous_reader=self.read_current,
            cleanup=cleanup or self.clean_old)

    def uninstall(self, target=None, *, remover=None, unregistrar=None):
        return install.uninstall_release(target or self.target, self.base, self.data,
            current_reader=self.read_current, unregistrar=unregistrar or self.unregister,
            remover=remover or self.remove_program)

    def assert_survey_unchanged(self):
        self.assertEqual((self.data / 'project.json').read_bytes(), b'{"survey":"do not alter"}\n')
        self.assertEqual((self.data / 'points.xyz64').read_bytes(), b'original survey point bytes')

    def assert_current_published(self):
        self.assertTrue((self.target / 'TunnelStudio.exe').is_file())
        self.assertEqual(json.loads(self.marker.read_text())['program_folder'], str(self.target))
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['DisplayVersion'], (install.VERSION, rr.REG_SZ))
        self.assertEqual(rr.read_current_install(self.base, registry=self.reg), str(self.target))

    def test_three_legacy_versions_migrate_to_one_entry_and_safe_program_cleanup(self):
        old = [self.release(version) for version in ('0.17.3', '0.18.0', '0.18.3')]
        for path in old:
            self.seed_legacy(path)
        user_file = old[0] / 'user-survey.e57'
        user_file.write_bytes(b'unlisted original survey in an old installation')
        previous_before = self.snapshot(self.previous)
        result = self.activate()
        self.assert_current_published()
        self.assertEqual(self.reg.names(rr.UNINSTALL_ROOT), ['TunnelStudio'])
        self.assertEqual(result['previous_program_folder'], str(self.previous))
        self.assertEqual(result['obsolete_program_cleanup']['status'], 'complete')
        self.assertGreater(result['obsolete_program_cleanup']['removed_files'], 0)
        self.assertEqual(self.snapshot(self.previous), previous_before)
        for path in old:
            self.assertFalse((path / 'TunnelStudio.exe').exists())
            self.assertTrue((path / 'FILES_SHA256.json').exists())
        self.assertEqual(user_file.read_bytes(), b'unlisted original survey in an old installation')
        self.assert_survey_unchanged()
        self.assertEqual(self.reg.read(rr.VENDOR_KEY).values['CustomBinary'], (b'keep\0this', 3))

    def test_failed_component_check_never_reads_registry_or_cleans_old_programs(self):
        before_registry = deepcopy(self.reg.data)
        before_old = self.snapshot(self.previous)
        before_marker = self.marker.read_bytes()
        def fail(_):
            raise RuntimeError('Synthetic component import failure')
        with self.assertRaisesRegex(RuntimeError, 'component import failure'):
            self.activate(smoke=fail)
        self.assertEqual(self.hook_events, [])
        self.assertEqual(self.reg.data, before_registry)
        self.assertEqual(self.snapshot(self.previous), before_old)
        self.assertEqual(self.marker.read_bytes(), before_marker)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.target.exists())
        self.assertFalse((self.stage / 'INSTALL_RECEIPT.json').exists())
        self.assert_survey_unchanged()

    def test_registry_publication_failure_restores_marker_directories_and_all_legacy_rows(self):
        extra = self.release('0.18.3')
        self.seed_legacy(extra)
        self.reg.seed(rr.STABLE_KEY, self.program_entry(self.previous, stable=True))
        registry_before = deepcopy(self.reg.data)
        marker_before = self.marker.read_bytes()
        old_before = {str(path): self.snapshot(path) for path in (self.previous, extra)}
        self.reg.fail('set', rr.VENDOR_KEY, 'CurrentInstall')
        with self.assertRaises(rr.RegistrationError):
            self.activate()
        self.assertEqual(self.reg.data, registry_before)
        self.assertEqual(self.marker.read_bytes(), marker_before)
        self.assertTrue(self.stage.is_dir())
        self.assertFalse(self.target.exists())
        self.assertNotIn('cleanup', self.hook_events)
        for path in (self.previous, extra):
            self.assertEqual(self.snapshot(path), old_before[str(path)])
        self.assert_survey_unchanged()

    def test_same_version_reinstall_keeps_renamed_previous_current_byte_for_byte(self):
        existing = self.release(install.VERSION)
        (existing / 'UninstallTunnelStudio.exe').write_bytes(b'MZprevious uninstaller')
        (existing / 'old user note.txt').write_bytes(b'keep with the previous working copy')
        existing_before = self.snapshot(existing)
        self.write_marker(existing)
        self.reg.set_values(rr.VENDOR_KEY, {'CurrentInstall': (str(existing), rr.REG_SZ)})
        self.reg.seed(rr.STABLE_KEY, self.program_entry(existing, stable=True))
        result = self.activate()
        kept = Path(result['previous_program_folder'])
        self.assertEqual(str(kept), result['previous_same_version'])
        self.assertTrue(kept.name.startswith(install.VERSION + '_previous_'))
        self.assertEqual(self.snapshot(kept), existing_before)
        self.assert_current_published()
        self.assertFalse((self.previous / 'TunnelStudio.exe').exists())
        self.assert_survey_unchanged()

    def test_same_version_registry_failure_restores_original_release_and_marker_bytes(self):
        existing = self.release(install.VERSION)
        (existing / 'old state.txt').write_bytes(b'original same-version program folder')
        self.write_marker(existing)
        self.reg.set_values(rr.VENDOR_KEY, {'CurrentInstall': (str(existing), rr.REG_SZ)})
        self.reg.seed(rr.STABLE_KEY, self.program_entry(existing, stable=True))
        before_files, before_registry, before_marker = self.snapshot(existing), deepcopy(self.reg.data), self.marker.read_bytes()
        self.reg.fail('set', rr.STABLE_KEY, 'DisplayVersion')
        with self.assertRaises(rr.RegistrationError):
            self.activate()
        self.assertEqual(self.snapshot(existing), before_files)
        self.assertEqual(self.reg.data, before_registry)
        self.assertEqual(self.marker.read_bytes(), before_marker)
        self.assertTrue(self.stage.is_dir())
        self.assertEqual(list(self.base.glob(install.VERSION + '_previous_*')), [])
        self.assert_survey_unchanged()

    def test_post_commit_cleanup_exception_keeps_successful_install_and_prior_copy(self):
        previous_before = self.snapshot(self.previous)
        def fail_cleanup(*args, **kwargs):
            self.hook('cleanup')
            raise PermissionError('Synthetic old-program cleanup failure')
        result = self.activate(cleanup=fail_cleanup)
        self.assert_current_published()
        self.assertEqual(result['obsolete_program_cleanup']['status'], 'partial')
        self.assertIn('installed', result['obsolete_program_cleanup']['warnings'][0])
        self.assertEqual(self.snapshot(self.previous), previous_before)
        self.assertFalse(self.stage.exists())
        self.assert_survey_unchanged()

    def test_post_commit_old_directory_enumeration_failure_is_nonfatal(self):
        self.marker.unlink()
        self.reg.delete_value(rr.VENDOR_KEY, 'CurrentInstall')
        actual_iterdir = Path.iterdir
        def fail_base(path):
            if path == self.base:
                raise PermissionError('Synthetic release-directory enumeration failure')
            return actual_iterdir(path)
        with patch.object(Path, 'iterdir', fail_base):
            result = self.activate()
        self.assert_current_published()
        self.assertEqual(result['obsolete_program_cleanup']['status'], 'partial')
        self.assertTrue((self.previous / 'TunnelStudio.exe').exists())
        self.assert_survey_unchanged()

    def test_process_lock_is_held_across_registry_cleanup_and_uninstall_hooks(self):
        self.check_lock = True
        self.activate()
        self.uninstall()
        self.assertTrue({'read_current', 'register', 'cleanup', 'remove_program', 'unregister'} <= set(self.hook_events))
        self.assert_survey_unchanged()

    def test_missing_or_invalid_staged_uninstaller_fails_before_activation(self):
        uninstaller = self.stage / 'UninstallTunnelStudio.exe'
        before_registry, before_marker = deepcopy(self.reg.data), self.marker.read_bytes()
        for value in (None, b'not a Windows executable'):
            with self.subTest(value=value):
                if value is None:
                    uninstaller.unlink()
                else:
                    uninstaller.write_bytes(value)
                with self.assertRaisesRegex(RuntimeError, 'staged Windows uninstaller'):
                    self.activate()
                self.assertFalse(self.target.exists())
                self.assertTrue(self.stage.exists())
                self.assertEqual(self.hook_events, [])
                self.assertEqual(self.reg.data, before_registry)
                self.assertEqual(self.marker.read_bytes(), before_marker)
                self.assert_survey_unchanged()

    def test_pending_uninstall_keeps_entry_uninstaller_and_anchors_then_retries_successfully(self):
        self.activate()
        marker_before = self.marker.read_bytes()
        registry_before = deepcopy(self.reg.data)
        blocked = self.target / 'runtime/pkg/engine.dll'
        actual_unlink = Path.unlink
        def deny_one(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('Synthetic Windows file-in-use error')
            return actual_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny_one):
            with self.assertRaisesRegex(RuntimeError, 'kept for retry'):
                self.uninstall()
        self.assertEqual(self.reg.data, registry_before)
        self.assertEqual(self.marker.read_bytes(), marker_before)
        for name in (*release_cleanup._ANCHORS, 'INSTALL_RECEIPT.json', 'UninstallTunnelStudio.exe'):
            self.assertTrue((self.target / name).is_file(), name)
        self.assertTrue(blocked.exists())
        self.assert_survey_unchanged()
        report = self.uninstall()
        self.assertTrue(report['removed_current_release'])
        self.assertIsNone(self.reg.read(rr.STABLE_KEY))
        self.assertIsNone(rr.read_current_install(self.base, registry=self.reg))
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.target.exists())
        self.assert_survey_unchanged()

    def test_successful_uninstall_removes_current_entry_and_owned_files_only(self):
        self.activate()
        originals = {'app/original.e57': b'original import', 'my notes.txt': b'user notes'}
        for name, value in originals.items():
            (self.target / name).write_bytes(value)
        previous_before = self.snapshot(self.previous)
        foreign_key = rr.UNINSTALL_ROOT + '\\UnrelatedProgram'
        foreign_entry = rr.RegistryKey({'Publisher': ('Unrelated company', rr.REG_SZ)})
        self.reg.seed(foreign_key, foreign_entry)
        report = self.uninstall()
        self.assertTrue(report['removed_current_release'])
        self.assertEqual(self.snapshot(self.target), originals)
        self.assertEqual(self.snapshot(self.previous), previous_before)
        self.assertIsNone(self.reg.read(rr.STABLE_KEY))
        self.assertEqual(self.reg.read(foreign_key), foreign_entry)
        self.assertEqual(self.reg.read(rr.VENDOR_KEY).values['Theme'], ('dark', rr.REG_SZ))
        self.assertFalse(self.marker.exists())
        self.assert_survey_unchanged()

    def test_registry_uninstall_failure_retains_retryable_uninstaller_and_restores_entry(self):
        self.activate()
        registry_before, marker_before = deepcopy(self.reg.data), self.marker.read_bytes()
        self.reg.fail('delete_value', rr.VENDOR_KEY, 'CurrentInstall')
        with self.assertRaises(rr.RegistrationError):
            self.uninstall()
        self.assertEqual(self.reg.data, registry_before)
        self.assertEqual(self.marker.read_bytes(), marker_before)
        for name in (*release_cleanup._ANCHORS, 'INSTALL_RECEIPT.json', 'UninstallTunnelStudio.exe'):
            self.assertTrue((self.target / name).is_file(), name)
        self.assert_survey_unchanged()
        self.assertTrue(self.uninstall()['removed_current_release'])

    def test_legacy_row_uninstall_failure_restores_all_entries_and_keeps_retryable_uninstaller(self):
        self.activate()
        self.seed_legacy(self.target)
        legacy_key = rr.UNINSTALL_ROOT + '\\TunnelStudio-' + install.VERSION
        registry_before, marker_before = deepcopy(self.reg.data), self.marker.read_bytes()
        self.reg.fail('delete_key', legacy_key)
        with self.assertRaises(rr.RegistrationError):
            self.uninstall()
        self.assertEqual(self.reg.data, registry_before)
        self.assertEqual(self.marker.read_bytes(), marker_before)
        for name in (*release_cleanup._ANCHORS, 'INSTALL_RECEIPT.json', 'UninstallTunnelStudio.exe'):
            self.assertTrue((self.target / name).is_file(), name)
        self.assert_survey_unchanged()
        self.assertTrue(self.uninstall()['removed_current_release'])
        self.assertIsNone(self.reg.read(rr.STABLE_KEY))
        self.assertIsNone(self.reg.read(legacy_key))
        self.assertFalse(self.target.exists())

    def test_older_release_uninstall_preserves_newer_current_marker_entry_and_shortcut_flag(self):
        self.activate()
        major, minor, patch_number = map(int, install.VERSION.split('.'))
        future_version = f'{major}.{minor}.{patch_number+1}'
        newer = self.release(future_version)
        self.write_marker(newer)
        rr.register_release(self.base, newer, future_version, registry=self.reg)
        before_registry, before_marker, before_new = deepcopy(self.reg.data), self.marker.read_bytes(), self.snapshot(newer)
        report = self.uninstall()
        self.assertFalse(report['removed_current_release'])
        self.assertEqual(self.reg.data, before_registry)
        self.assertEqual(self.marker.read_bytes(), before_marker)
        self.assertEqual(self.snapshot(newer), before_new)
        self.assert_survey_unchanged()

    def test_same_version_backup_uninstall_preserves_current_marker_entry_and_shortcut_flag(self):
        self.activate()
        second = self.release(install.VERSION, '.installing-second-reinstall')
        (second / 'UninstallTunnelStudio.exe').write_bytes(b'MZreplacement uninstaller')
        upgraded = self.activate(stage=second)
        backup = Path(upgraded['previous_same_version'])
        before_registry, before_marker, before_current = deepcopy(self.reg.data), self.marker.read_bytes(), self.snapshot(self.target)
        report = self.uninstall(backup)
        self.assertFalse(report['removed_current_release'])
        self.assertEqual(self.reg.data, before_registry)
        self.assertEqual(self.marker.read_bytes(), before_marker)
        self.assertEqual(self.snapshot(self.target), before_current)
        self.assert_survey_unchanged()

    def test_cli_wires_real_registry_and_cleanup_hooks(self):
        old = self.release('0.18.1')
        self.seed_legacy(old)
        proxy = SimpleNamespace(read_current_install=self.read_current, register_release=self.register,
                                remove_release_registration=self.unregister)
        actual_activate = install.activate
        def controlled_launch(stage, base, data, **kwargs):
            self.assertTrue({'registrar', 'previous_reader', 'cleanup'} <= kwargs.keys())
            return actual_activate(stage, base, data, smoke=lambda _: 'controlled component success', **kwargs)
        argv = ['windows_installer.py', '--stage', str(self.stage), '--base', str(self.base),
                '--error-file', str(self.root / 'install_error.txt')]
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local), 'TUNNEL_STUDIO_DATA': str(self.data)}), \
             patch.object(sys, 'argv', argv), \
             patch.object(install, '_lifecycle_modules', return_value=(proxy, release_cleanup)), \
             patch.object(install, 'activate', side_effect=controlled_launch):
            result = install.main()
        self.assertEqual(result, 0)
        self.assert_current_published()
        self.assertEqual(self.reg.names(rr.UNINSTALL_ROOT), ['TunnelStudio'])
        self.assertFalse((old / 'TunnelStudio.exe').exists())
        self.assert_survey_unchanged()

    def test_bootstrap_lifecycle_modules_import_with_isolated_no_site_interpreter(self):
        bootstrap_root = self.root / 'isolated-bootstrap'
        support = bootstrap_root / 'bootstrap'
        support.mkdir(parents=True)
        sources = Path(__file__).resolve().parent
        shutil.copy2(sources / 'windows_installer.py', bootstrap_root / 'windows_installer.py')
        for name in ('release_registry.py', 'release_cleanup.py'):
            shutil.copy2(sources / name, support / name)
        code = r'''
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('isolated_installer', root / 'windows_installer.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
registry, cleanup = module._lifecycle_modules()
assert Path(registry.__file__).parent == root / 'bootstrap'
assert Path(cleanup.__file__).parent == root / 'bootstrap'
assert 'site' not in sys.modules
print(json.dumps({'isolated': bool(sys.flags.isolated), 'no_site': bool(sys.flags.no_site),
                  'modules': [registry.__name__, cleanup.__name__]}))
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(bootstrap_root)],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'isolated': True, 'no_site': True,
                                                    'modules': ['release_registry', 'release_cleanup']})


if __name__ == '__main__':
    unittest.main(verbosity=2)
