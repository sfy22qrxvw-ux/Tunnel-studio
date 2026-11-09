import ast, hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import windows_installer as install
import startup_smoke


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root / 'Programs' / 'TunnelStudio' / 'releases'
        self.stage = self.base / '.installing-test'
        self.stage.mkdir(parents=True)
        self.data = root / 'TunnelStudio'
        self.data.mkdir()
        (self.data / 'project.json').write_text('{"sentinel":"keep"}')
        (self.data / 'points.xyz64').write_bytes(b'survey data unchanged')
        self.old = self.base / '0.2.1'
        self.old.mkdir()
        (self.old / 'old.txt').write_text('keep previous application')
        self.marker = self.base / 'current_release.json'
        self.marker.write_text('{"version":"0.14.7","program_folder":"previous"}')
        files = {}
        for name in ('TunnelStudio.exe','runtime/python.exe','runtime/pythonw.exe','app/desktop.py','app/release.py'):
            p = self.stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())
            files[name] = install.digest(p)
        (self.stage / 'FILES_SHA256.json').write_text(json.dumps({'version':install.VERSION,'files':files}))

    def activate(self):
        return install.activate(self.stage,self.base,self.data,smoke=lambda _: 'checked')

    def test_install_preserves_data_previous_release_and_index(self):
        messages = []
        result = install.activate(self.stage, self.base, self.data,
                                  smoke=lambda _: 'checked', progress=messages.append,
                                  cleanup=lambda *args, **kwargs: {})
        self.assertEqual(result['verified_files'],5)
        self.assertEqual(messages, [
            'Verifying 5 extracted program files',
            'Verified 5 / 5 program files',
            'Testing application startup and bundled processing engines (up to 3 minutes)',
            'Saving recovery information and activating the new release',
            'Checking older program versions for safe cleanup',
            'Application checks complete',
        ])
        self.assertEqual(set(result['timings_seconds']),
                         {'file_verification', 'startup_and_engines', 'activation', 'older_release_cleanup'})
        self.assertTrue(all(seconds >= 0 for seconds in result['timings_seconds'].values()))
        self.assertEqual((self.data/'project.json').read_text(),'{"sentinel":"keep"}')
        self.assertEqual((self.data/'points.xyz64').read_bytes(),b'survey data unchanged')
        self.assertTrue((self.old/'old.txt').is_file())
        self.assertEqual(len(list((self.data/'project_indexes').glob('*.json'))),1)
        self.assertEqual(json.loads(self.marker.read_text())['version'],install.VERSION)

    def test_damaged_payload_never_activates(self):
        (self.stage/'app/desktop.py').write_text('damaged')
        with self.assertRaisesRegex(RuntimeError,'verification failed'):self.activate()
        self.assertFalse((self.base/install.VERSION).exists())
        self.assertEqual(json.loads(self.marker.read_text())['version'],'0.14.7')

    def test_install_preserves_oversized_workspace_and_backup_byte_for_byte(self):
        # A metadata-size startup failure must not be "repaired" by the
        # installer deleting, truncating or rewriting the user's workspace.
        workspace = self.data / 'project.json'
        workspace.write_text('{"sentinel":"' + 'x' * 21_000_000 + '"}')
        original_size = workspace.stat().st_size
        original_hash = install.digest(workspace)
        self.activate()
        backups = list((self.data / 'project_indexes').glob('*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(workspace.stat().st_size, original_size)
        self.assertEqual(install.digest(workspace), original_hash)
        self.assertEqual(backups[0].stat().st_size, original_size)
        self.assertEqual(install.digest(backups[0]), original_hash)
        self.assertEqual((self.data/'points.xyz64').read_bytes(), b'survey data unchanged')

    def test_component_failure_preserves_active_version(self):
        result=subprocess.CompletedProcess([],1,stdout=b'ImportError: Open3D native DLL could not load')
        with patch.object(install.subprocess,'run',return_value=result) as child:
            with self.assertRaisesRegex(RuntimeError,'Open3D native DLL could not load'):
                install.activate(self.stage,self.base,self.data)
        command=child.call_args.args[0]
        imported={alias.name for node in ast.walk(ast.parse(command[-1]))
                  if isinstance(node,ast.Import) for alias in node.names}
        self.assertIn('open3d',imported)
        self.assertTrue({'geometry_tools','feature_selection','measure_tools'} <= imported)
        self.assertEqual(Path(command[0]),self.stage/'runtime/python.exe')
        self.assertNotEqual(Path(child.call_args.kwargs['env']['TUNNEL_STUDIO_DATA']),self.data)
        self.assertTrue(self.stage.is_dir())
        self.assertFalse((self.base/install.VERSION).exists())
        self.assertEqual((self.data/'project.json').read_text(),'{"sentinel":"keep"}')
        self.assertEqual((self.data/'points.xyz64').read_bytes(),b'survey data unchanged')
        self.assertEqual(json.loads(self.marker.read_text())['version'],'0.14.7')

    def test_reinstall_retains_previous_same_version(self):
        target=self.base/install.VERSION;target.mkdir();(target/'previous.txt').write_text('original')
        result=self.activate()
        self.assertEqual((Path(result['previous_same_version'])/'previous.txt').read_text(),'original')

    def test_startup_metadata_self_check_failure_preserves_user_workspace_and_release(self):
        result = subprocess.CompletedProcess([], 1, stdout=b'RuntimeError: Startup metadata self-check failed')
        with patch.object(install.subprocess, 'run', return_value=result) as child:
            with self.assertRaisesRegex(RuntimeError, 'Startup metadata self-check failed'):
                install.activate(self.stage, self.base, self.data)
        command = child.call_args.args[0]
        self.assertIn('startup_smoke.verify_startup(server, projects, os.environ["TUNNEL_STUDIO_DATA"])', command[-1])
        self.assertNotEqual(Path(child.call_args.kwargs['env']['TUNNEL_STUDIO_DATA']), self.data)
        self.assertTrue(Path(child.call_args.kwargs['env']['TUNNEL_STUDIO_DATA']).name.startswith('TunnelStudio_setup_check_'))
        self.assertTrue(self.stage.is_dir())
        self.assertFalse((self.base / install.VERSION).exists())
        self.assertEqual((self.data / 'project.json').read_text(), '{"sentinel":"keep"}')
        self.assertEqual((self.data / 'points.xyz64').read_bytes(), b'survey data unchanged')
        self.assertTrue((self.old / 'old.txt').is_file())
        self.assertEqual(json.loads(self.marker.read_text())['version'], '0.14.7')

    def test_marker_failure_rolls_back_both_directories(self):
        target=self.base/install.VERSION;target.mkdir();(target/'previous.txt').write_text('original')
        with patch.object(install,'atomic_json',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError,'disk full'):self.activate()
        self.assertTrue(self.stage.is_dir())
        self.assertEqual((target/'previous.txt').read_text(),'original')
        self.assertEqual(json.loads(self.marker.read_text())['version'],'0.14.7')

    def test_active_application_blocks_installation(self):
        with install.exclusive(self.data):
            with self.assertRaisesRegex(RuntimeError,'Close Tunnel Studio'):self.activate()
        self.assertTrue(self.stage.is_dir())

    def test_path_traversal_rejected(self):
        p=self.stage/'FILES_SHA256.json';m=json.loads(p.read_text());m['files']['../outside']='wrong';p.write_text(json.dumps(m))
        with self.assertRaisesRegex(RuntimeError,'Invalid installer payload path'):self.activate()

    def test_installer_leaves_staging_directory_before_activation(self):
        script=Path(__file__).with_name('TunnelStudio.nsi').read_text()
        after_unpack=script.split('!include "payload_files.nsh"',1)[1]
        before_activate=after_unpack.split(' --stage ',1)[0]
        self.assertIn('SetOutPath "$PLUGINSDIR"',before_activate)

    def test_startup_self_check_refuses_another_workspace_before_writing(self):
        with tempfile.TemporaryDirectory(prefix='TunnelStudio_setup_check_') as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, 'isolated temporary workspace'):
                startup_smoke.verify_startup(SimpleNamespace(WORK=self.data), None, root)
            self.assertEqual(list(root.iterdir()), [])
        self.assertEqual((self.data/'project.json').read_text(), '{"sentinel":"keep"}')
        self.assertEqual((self.data/'points.xyz64').read_bytes(), b'survey data unchanged')

    def test_startup_self_check_refuses_existing_project_before_writing(self):
        with tempfile.TemporaryDirectory(prefix='TunnelStudio_setup_check_') as temporary:
            root = Path(temporary)
            project = root / 'project.json'
            project.write_bytes(b'{"sentinel":"never overwrite"}')
            with self.assertRaisesRegex(RuntimeError, 'refuses to replace existing project data'):
                startup_smoke.verify_startup(SimpleNamespace(WORK=root), None, root)
            self.assertEqual(project.read_bytes(), b'{"sentinel":"never overwrite"}')
            self.assertEqual(list(root.iterdir()), [project])


class StartupSmokeIntegrationTests(unittest.TestCase):
    """Run the production startup contract in a clean, real application process.

    Installer transaction tests intentionally inject component success/failure.
    These checks do not: they import the shipped server, project reader and
    startup probe with an isolated data directory before exercising startup.
    """
    CHILD = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import server, projects, startup_smoke

mode = sys.argv[3]
root = Path(os.environ['TUNNEL_STUDIO_DATA'])
if mode == 'old_metadata_limit':
    # A regression to the former 20 MB metadata guard must still fail setup.
    projects.MAX_MANIFEST_BYTES = 20_000_000
elif mode in ('corrupt_recovery', 'duplicate_named_recovery'):
    actual_start = server.start_empty
    def faulty_start():
        actual_start()
        recovery, = (root / 'startup_recovery').glob('*.json')
        if mode == 'corrupt_recovery':
            # Preserve length and valid JSON, but damage one byte of the probe.
            with recovery.open('r+b') as stream:
                stream.seek(256)
                assert stream.read(1) == b'x'
                stream.seek(256)
                stream.write(b'y')
        else:
            # Reproduce the historical duplicate-named-project behavior. The
            # check must catch this even though normal listing retires it.
            projects.save_named(root, projects.read_json(recovery),
                name='Installer startup self-check — previous session 30 Oct 2026 12:00')
    server.start_empty = faulty_start

report = startup_smoke.verify_startup(server, projects, root)
print(json.dumps(report, sort_keys=True))
'''

    def run_startup(self, mode='normal'):
        temporary = tempfile.TemporaryDirectory(prefix='TunnelStudio_setup_check_')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        support = Path(__file__).resolve().parent
        application = support.parent / 'app'
        self.assertTrue((application / 'server.py').is_file())
        env = os.environ.copy()
        env['TUNNEL_STUDIO_DATA'] = str(root)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run(
            [sys.executable, '-B', '-c', self.CHILD, str(application), str(support), mode],
            cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=60)
        return result, root

    def test_real_startup_preserves_complete_hidden_recovery_without_named_duplicate(self):
        result, root = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(report['passed'])
        self.assertGreater(report['fixture_metadata_bytes'], 20_000_000)
        self.assertEqual(report['named_previous_sessions_created'], 0)
        self.assertTrue(report['hidden_recovery_metadata_reopened'])
        self.assertEqual(list((root / 'projects').glob('*.json')), [])
        recovery, = (root / 'startup_recovery').glob('*.json')
        self.assertEqual(recovery.stat().st_size, report['fixture_metadata_bytes'])
        self.assertEqual(install.digest(recovery), report['fixture_sha256'])
        self.assertEqual(report['recovery_sha256'], report['fixture_sha256'])
        restored = json.loads(recovery.read_text(encoding='utf-8'))
        self.assertEqual(restored['project_title'], 'Installer startup self-check')
        probe = restored['report_settings']['installer_probe']
        self.assertEqual(len(probe), 21_000_000)
        self.assertEqual(hashlib.sha256(probe.encode()).hexdigest(),
                         report['recovery_metadata_content_sha256'])
        current = json.loads((root / 'project.json').read_text(encoding='utf-8'))
        self.assertEqual(current['project_title'], 'Untitled project')
        self.assertIsNone(current['axis'])
        self.assertIsNone(current['cloud'])
        self.assertFalse(current.get('job_items'))
        self.assertFalse(current.get('report_settings'))

    def test_real_startup_rejects_same_size_recovery_corruption(self):
        result, _ = self.run_startup('corrupt_recovery')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Startup self-check changed the prior autosave bytes.', result.stdout)

    def test_real_startup_rejects_legacy_named_recovery_duplicate(self):
        result, root = self.run_startup('duplicate_named_recovery')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Startup self-check created an unwanted named previous session.', result.stdout)
        # The gate observes the unwanted file before listing can silently retire it.
        self.assertEqual(len(list((root / 'projects').glob('*.json'))), 1)

    def test_real_startup_rejects_regressed_metadata_read_limit(self):
        result, root = self.run_startup('old_metadata_limit')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Startup metadata self-check failed:', result.stdout)
        recovery, = (root / 'startup_recovery').glob('*.json')
        self.assertGreater(recovery.stat().st_size, 20_000_000)


if __name__=='__main__':unittest.main(verbosity=2)
