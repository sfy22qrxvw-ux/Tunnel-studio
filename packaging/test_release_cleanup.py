import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import release_cleanup as cleanup


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ReleaseCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / 'LocalAppData'
        self.base = self.local / 'Programs/TunnelStudio/releases'
        self.base.mkdir(parents=True)
        self.data = self.local / 'TunnelStudio'
        self.data.mkdir()
        (self.data / 'project.json').write_bytes(b'{"survey":"preserve"}')
        (self.data / 'points.xyz64').write_bytes(b'original survey points')
        self.current = self.release('0.18.5')
        self.previous = self.release('0.18.4')
        self.commit(self.current)

    def release(self, version, name=None, extra=None):
        folder = self.base / (name or version)
        folder.mkdir(parents=True)
        files = {
            'TunnelStudio.exe': ('application ' + version).encode(),
            'runtime/python.exe': b'bundled interpreter',
            'runtime/pythonw.exe': b'bundled GUI interpreter',
            'runtime/pkg/engine.dll': b'owned numerical engine',
            'runtime/pkg/reference.npy': b'bundled NumPy test fixture',
            'runtime/pkg/reference.npz': b'bundled NumPy archive fixture',
            'app/desktop.py': b'print("desktop application")\n',
            'app/release.py': ("VERSION=" + repr(version) + "\nAPP_NAME='Tunnel Studio'\n"
                               "COPYRIGHT='© 2026 Damian Gulczynski'\n").encode(),
            'COPYRIGHT.txt': b'Tunnel Studio\nCopyright (c) 2026 Damian Gulczynski. All rights reserved.\n',
            'app/web/index.html': b'<html>Tunnel Studio</html>',
        }
        files.update(extra or {})
        for name, data in files.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = {'version': version, 'files': {name: sha(data) for name, data in files.items()}}
        (folder / 'FILES_SHA256.json').write_text(json.dumps(manifest), encoding='utf-8')
        return folder

    def commit(self, path):
        manifest = json.loads((path / 'FILES_SHA256.json').read_text())
        (self.base / 'current_release.json').write_text(json.dumps({
            'version': manifest['version'], 'program_folder': str(path)}))

    def run_cleanup(self, previous=None, **kwargs):
        return cleanup.cleanup(self.base, self.current,
                               self.previous if previous is None else previous,
                               local_app_data=self.local, data_root=kwargs.get('data_root', self.data))

    def remove(self, path, **kwargs):
        return cleanup.remove_release(self.base, path, local_app_data=self.local,
                                      data_root=kwargs.get('data_root', self.data))

    def snapshot(self, folder):
        return {path.relative_to(folder).as_posix(): path.read_bytes()
                for path in folder.rglob('*') if path.is_file() and not path.is_symlink()}

    def symlink(self, target, path, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest('Symbolic links are unavailable in this test environment: ' + str(error))

    def test_upgrade_keeps_current_and_one_previous_while_retiring_other_owned_program_files(self):
        old = [self.release(version) for version in ('0.18.0', '0.18.1', '0.18.2')]
        current_before, previous_before, data_before = map(self.snapshot, (self.current, self.previous, self.data))
        result = self.run_cleanup()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(len(result['releases']), 3)
        self.assertGreater(result['removed_files'], 0)
        self.assertGreater(result['removed_bytes'], 0)
        self.assertEqual(self.snapshot(self.current), current_before)
        self.assertEqual(self.snapshot(self.previous), previous_before)
        self.assertEqual(self.snapshot(self.data), data_before)
        for path in old:
            self.assertEqual(set(self.snapshot(path)), cleanup._ANCHORS)

    def test_missing_or_wrong_commit_marker_prevents_all_cleanup(self):
        old = self.release('0.18.1')
        before = self.snapshot(old)
        marker = self.base / 'current_release.json'
        marker.unlink()
        self.assertEqual(self.run_cleanup()['status'], 'refused')
        self.commit(self.previous)
        self.assertEqual(self.run_cleanup()['status'], 'refused')
        self.assertEqual(self.snapshot(old), before)

    def test_exact_same_version_backup_is_preserved_during_reinstall(self):
        keep = self.release('0.18.5', '0.18.5_previous_20261030_223344_deadbeef')
        retire = self.release('0.18.5', '0.18.5_previous_20261029_223344_12345678')
        before = self.snapshot(keep)
        result = self.run_cleanup(previous=keep)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(self.snapshot(keep), before)
        self.assertEqual(set(self.snapshot(retire)), cleanup._ANCHORS)
        self.assertFalse((self.previous / 'TunnelStudio.exe').exists())

    def test_unrecognized_folders_and_foreign_release_are_never_deleted(self):
        unrelated = self.base / 'my survey documents'
        unrelated.mkdir()
        (unrelated / 'points.xyz64').write_bytes(b'never remove')
        staging = self.base / '.installing-0.18.1-123-456'
        staging.mkdir()
        (staging / 'work.txt').write_bytes(b'not this cleanup policy')
        foreign = self.release('0.9.9')
        manifest = json.loads((foreign / 'FILES_SHA256.json').read_text())
        code = b"VERSION='0.9.9'\nAPP_NAME='Another App'\nCOPYRIGHT='Other Author'\n"
        (foreign / 'app/release.py').write_bytes(code)
        manifest['files']['app/release.py'] = sha(code)
        (foreign / 'FILES_SHA256.json').write_text(json.dumps(manifest))
        before = {str(path): self.snapshot(path) for path in (unrelated, staging, foreign)}
        result = self.run_cleanup()
        self.assertEqual(result['status'], 'partial')
        for path in (unrelated, staging, foreign):
            self.assertEqual(self.snapshot(path), before[str(path)])

    def test_manifest_traversal_ads_and_case_conflicts_refuse_before_any_deletion(self):
        old = self.release('0.18.1')
        manifest_path = old / 'FILES_SHA256.json'
        clean = json.loads(manifest_path.read_text())
        for name in ('../outside.py', '/absolute/file.py', 'runtime\\outside.py',
                     'runtime/file.py:stream', 'app/../desktop.py', 'app/name.', 'APP/DESKTOP.PY'):
            with self.subTest(name=name):
                manifest = {'version': clean['version'], 'files': dict(clean['files'])}
                manifest['files'][name] = sha(b'outside')
                manifest_path.write_text(json.dumps(manifest))
                before = self.snapshot(old)
                self.assertEqual(self.remove(old)['status'], 'refused')
                self.assertEqual(self.snapshot(old), before)

    def test_modified_and_unlisted_files_survive_with_retry_anchors(self):
        old = self.release('0.18.1')
        changed = old / 'app/desktop.py'
        changed.write_bytes(b'user modified source that must be kept')
        (old / 'personal notes.txt').write_bytes(b'user notes')
        (old / 'UninstallTunnelStudio.exe').write_bytes(b'unlisted previous uninstaller')
        original = {name: (old / name).read_bytes() for name in (
            'app/desktop.py', 'personal notes.txt', 'UninstallTunnelStudio.exe')}
        result = self.remove(old)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['pending_files'], 0)
        self.assertEqual(result['protected_files'], 1)
        self.assertIn('personal notes.txt', result['unlisted_paths'])
        self.assertIn('UninstallTunnelStudio.exe', result['unlisted_paths'])
        for name, data in original.items():
            self.assertEqual((old / name).read_bytes(), data)
        for name in cleanup._ANCHORS:
            self.assertTrue((old / name).is_file())

    def test_survey_cache_project_formats_are_protected_even_if_manifest_listed(self):
        protected = {
            'app/source.e57': b'original survey', 'runtime/source.xyz64': b'original XYZ',
            'runtime/cache.analysis64': b'analysis cache', 'app/points.npy': b'point array',
            'app/points.npz': b'point archive', 'app/project.json': b'{"project":"keep"}',
            'app/result.mesh.json': b'{"mesh":"keep"}', 'app/measurements.csv': b'E,N,H\n1,2,3\n',
        }
        old = self.release('0.18.1', extra=protected)
        result = self.remove(old)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['protected_files'], len(protected))
        for name, data in protected.items():
            self.assertEqual((old / name).read_bytes(), data)
        self.assertFalse((old / 'runtime/pkg/reference.npy').exists())
        self.assertFalse((old / 'runtime/pkg/reference.npz').exists())

    def test_custom_data_workspace_inside_old_release_is_preserved(self):
        old = self.release('0.18.1')
        custom_data = old / 'runtime/pkg'
        before = self.snapshot(custom_data)
        result = self.remove(old, data_root=custom_data)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(self.snapshot(custom_data), before)
        self.assertFalse((old / 'TunnelStudio.exe').exists())
        self.assertEqual(result['protected_files'], len(before))

    def test_survey_workspace_containing_release_root_refuses_all_cleanup(self):
        old = self.release('0.18.1')
        before = self.snapshot(old)
        result = self.run_cleanup(data_root=self.local)
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(self.snapshot(old), before)

    def test_locked_file_remains_pending_and_retry_works_after_partial_removal(self):
        old = self.release('0.18.1')
        blocked = old / 'runtime/pkg/engine.dll'
        actual_unlink = Path.unlink
        def deny_one(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('File is in use by Windows')
            return actual_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny_one):
            first = self.remove(old)
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(first['pending_files'], 1)
        self.assertTrue(blocked.exists())
        self.assertFalse((old / 'TunnelStudio.exe').exists())
        second = self.remove(old)
        self.assertEqual(second['status'], 'complete', second)
        self.assertEqual(second['removed_files'], 1)
        self.assertFalse(blocked.exists())
        third = self.remove(old)
        self.assertEqual(third['removed_files'], 0)
        self.assertEqual(third['status'], 'complete')

    def test_directory_symlink_is_never_traversed_or_used_to_remove_external_file(self):
        old = self.release('0.18.1', extra={'runtime/linked/engine.dll': b'external original'})
        original = self.root / 'survey originals'
        original.mkdir()
        external = original / 'engine.dll'
        external.write_bytes(b'external original')
        (old / 'runtime/linked/engine.dll').unlink()
        (old / 'runtime/linked').rmdir()
        self.symlink(original, old / 'runtime/linked', directory=True)
        result = self.remove(old)
        self.assertEqual(external.read_bytes(), b'external original')
        self.assertTrue((old / 'runtime/linked').is_symlink())
        self.assertGreaterEqual(result['protected_files'], 1)

    def test_release_symlink_is_refused_without_touching_target(self):
        real = self.release('0.18.1')
        elsewhere = self.root / 'external'
        real.rename(elsewhere)
        self.symlink(elsewhere, real, directory=True)
        before = self.snapshot(elsewhere)
        self.assertEqual(self.remove(real)['status'], 'refused')
        self.assertEqual(self.snapshot(elsewhere), before)

    def test_path_replaced_by_symlink_after_hash_is_kept_without_touching_target(self):
        old = self.release('0.18.1')
        selected = old / 'runtime/pkg/engine.dll'
        external = self.root / 'external.dll'
        external.write_bytes(selected.read_bytes())
        # Check link support before the injected race so a skipped test stays clean.
        probe = self.root / 'link-probe'
        self.symlink(external, probe)
        probe.unlink()
        actual_read = cleanup._read
        changed = False
        def swap_after_hash(path, maximum=None):
            nonlocal changed
            result = actual_read(path, maximum)
            if path == selected and maximum is None and not changed:
                changed = True
                path.unlink()
                path.symlink_to(external)
            return result
        with patch.object(cleanup, '_read', side_effect=swap_after_hash):
            report = self.remove(old)
        self.assertTrue(changed)
        self.assertTrue(selected.is_symlink())
        self.assertEqual(external.read_bytes(), b'owned numerical engine')
        self.assertGreaterEqual(report['protected_files'], 1)

    def test_noncanonical_install_root_and_modified_owner_anchor_are_refused(self):
        old = self.release('0.18.1')
        before = self.snapshot(old)
        result = cleanup.remove_release(self.base, old, local_app_data=self.root / 'wrong', data_root=self.data)
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(self.snapshot(old), before)
        (old / 'COPYRIGHT.txt').write_bytes(b'Changed ownership record')
        before = self.snapshot(old)
        self.assertEqual(self.remove(old)['status'], 'refused')
        self.assertEqual(self.snapshot(old), before)

    def test_explicit_uninstall_removes_current_owned_program_without_deleting_data(self):
        before_data = self.snapshot(self.data)
        before_previous = self.snapshot(self.previous)
        marker = (self.base / 'current_release.json').read_bytes()
        result = self.remove(self.current)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(set(self.snapshot(self.current)), cleanup._ANCHORS)
        self.assertEqual(self.snapshot(self.data), before_data)
        self.assertEqual(self.snapshot(self.previous), before_previous)
        self.assertEqual((self.base / 'current_release.json').read_bytes(), marker)

    def test_descriptor_permission_bits_do_not_misidentify_unchanged_windows_program_files(self):
        old = self.release('0.18.1')
        original_fstat = os.fstat
        def different_permissions(descriptor):
            info = original_fstat(descriptor)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                st_mode=info.st_mode ^ 0o111,
                st_file_attributes=getattr(info, 'st_file_attributes', 0))
        with patch.object(cleanup.os, 'fstat', side_effect=different_permissions):
            report = self.remove(old)
        self.assertEqual(report['status'], 'complete', report)
        self.assertGreater(report['removed_files'], 0)
        self.assertEqual(set(self.snapshot(old)), cleanup._ANCHORS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
