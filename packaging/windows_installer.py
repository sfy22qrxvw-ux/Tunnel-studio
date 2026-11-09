# Copyright (c) 2026 Damian Gulczynski.
"""Transactional activation for the single-file Windows installer (stdlib only)."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
import argparse, hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile, time, traceback, uuid

VERSION = '0.23.4'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


@contextmanager
def exclusive(data):
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'session.lock').open('a+b') as lock:
        try:
            lock.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Close Tunnel Studio and its browser recovery window, then run this installer again.') from error
        yield


def verify(stage, progress=lambda _: None):
    manifest = json.loads((stage / 'FILES_SHA256.json').read_text(encoding='utf-8'))
    if manifest.get('version') != VERSION:
        raise RuntimeError('The installer payload has the wrong version.')
    root=stage.resolve()
    total=len(manifest['files'])
    progress(f'Verifying {total:,} extracted program files')
    last_update=time.monotonic()
    for index,(name,expected) in enumerate(manifest['files'].items(),1):
        relative = Path(name)
        path = stage / relative
        if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(root) or path.is_symlink():
            raise RuntimeError('Invalid installer payload path: ' + name)
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError('Installer file verification failed: ' + name)
        if index % 100 == 0 and time.monotonic()-last_update >= 1:
            progress(f'Verified {index:,} / {total:,} program files')
            last_update=time.monotonic()
    for name in ('TunnelStudio.exe', 'runtime/python.exe', 'runtime/pythonw.exe', 'app/desktop.py', 'app/release.py'):
        if name not in manifest['files']:
            raise RuntimeError('Required installer file is missing: ' + name)
    progress(f'Verified {total:,} / {total:,} program files')
    return total


def _component_output(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value or '')


def smoke_check(stage, *, interpreter=None):
    # Importing the actual application must never open the user's real workspace.
    with tempfile.TemporaryDirectory(prefix='TunnelStudio_setup_check_') as tmp:
        env = os.environ.copy()
        env['TUNNEL_STUDIO_DATA'] = tmp
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        code = ('import sys, os, json; sys.path.insert(0, "app"); sys.path.insert(0, "build_support"); '
                'print("Checking bundled application imports", flush=True); '
                'import numpy, scipy, pye57, laspy; '
                'import server, desktop, pointio, projects, project_browser, leica_catalog, window_chrome, surface_api, lining_tiles, '
                'scan_compaction, project_validation, gusac, tmatch, ray_box_extraction, mesh_to_cloud, mesh_repair, '
                'tunnel_designer, inspection, inspection_api, inspection_points, inspection_geometry, '
                'geometry_tools, feature_selection, measure_tools, ui_preferences, '
                'bounded_rows, explorer_deletion, joint_detection, scan_geometry, tunnel_axis_fit, '
                'robust_registration, registration_review, registration_workspace, visual_registration, cloud_registration, cloud_registration_workflow, rtc360_vis_probe, rtc360_vis_pose, rtc360_vis_workflow, '
                'compute_runtime, parallel_meshing, clean_tunnel, deleted_points, lining_departures, shape_matching, shape_selection, shape_inspection, shape_lining, scan_appearance, compact_shapes, shape_examples, shape_library, shape_library_api, shape_separated, section_circle, bundle_adjustment, bundle_workflow, bolt_gaps, segment_panels, section_fitting, section_epochs, tunnel_monitoring, monitoring_graphics, tunnel_volume, storage_catalog, storage_inventory, storage_cleanup, workspace_folders, project_folder_copy, project_shapefiles, desktop_exit; '
                'print("Checking Open3D", flush=True); import open3d; '
                'print("Checking CAD engines", flush=True); '
                'import cad_geometry; cad_geometry.engines(); '
                'print("Checking isolated startup and hidden recovery", flush=True); '
                'import startup_smoke; '
                'print("Isolated startup self-check: " + json.dumps(startup_smoke.verify_startup(server, projects, os.environ["TUNNEL_STUDIO_DATA"]), sort_keys=True)); '
                f'from release import VERSION; assert VERSION == {VERSION!r}; '
                'print("Tunnel Studio " + VERSION + " components verified")')
        # The installer always uses the bundled interpreter. The explicit override
        # lets the build run this same command on its host as an additional gate;
        # that check is recorded separately and is not native Windows validation.
        executable = str(interpreter) if interpreter is not None else str(stage / 'runtime/python.exe')
        try:
            result = subprocess.run([executable, '-B', '-c', code],
                                    cwd=str(stage), env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=180,
                                    creationflags=0x08000000 if sys.platform == 'win32' else 0)
        except subprocess.TimeoutExpired as error:
            output = _component_output(error.output).strip()[-6000:]
            raise RuntimeError('The bundled application startup check timed out after 180 seconds.\n' + output) from error
        output = _component_output(result.stdout).strip()
        if result.returncode:
            # NSIS displays the first line. Include the actual final exception (or
            # last flushed phase for a native crash), while retaining the log tail.
            last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), 'No diagnostic output was returned.')
            status = str(result.returncode)
            if result.returncode < 0 or result.returncode > 255:
                status += f' / 0x{result.returncode & 0xffffffff:08X}'
            raise RuntimeError('The bundled application could not start its components (exit ' + status + '): ' + last[:500] + '\n' + output[-6000:])
        return output


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_marker(path, original):
    if original is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(original)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _release_reference(base, value):
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if (not path.is_absolute() or path.parent != base
            or not re.fullmatch(r'\d+\.\d+\.\d+(?:_previous_\d{8}_\d{6}_[0-9a-f]{8})?', path.name)
            or path.is_symlink() or not path.is_dir() or path.resolve() != path):
        return None
    info = path.stat()
    if getattr(info, 'st_file_attributes', 0) & 0x400:
        return None
    return path


def _previous_release(base, original_marker, previous_reader):
    previous = pointer = None
    warnings = []
    if original_marker is not None:
        try:
            record = json.loads(original_marker)
            previous = _release_reference(base, record.get('program_folder'))
            if previous is None:
                warnings.append('The prior active-release record was not a verified release path.')
        except (ValueError, AttributeError, TypeError):
            warnings.append('The prior active-release record could not be read.')
    if previous_reader is not None:
        try:
            value = previous_reader(base)
            pointer = _release_reference(base, value) if value is not None else None
            if value is not None and pointer is None:
                warnings.append('The prior Windows installation pointer was not a verified release path.')
        except Exception as error:
            warnings.append('The prior Windows installation pointer could not be read: ' + str(error))
    if previous is not None and pointer is not None and previous != pointer:
        warnings.append('The prior Windows installation pointer and active-release record disagree.')
    if warnings:
        return None, warnings
    return previous or pointer, []


def _prepare_uninstaller_receipt(stage):
    uninstaller = stage / 'UninstallTunnelStudio.exe'
    if uninstaller.is_symlink() or not uninstaller.is_file():
        raise RuntimeError('The staged Windows uninstaller is missing.')
    with uninstaller.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise RuntimeError('The staged Windows uninstaller is not an executable.')
    atomic_json(stage / 'INSTALL_RECEIPT.json', {
        'product': 'Tunnel Studio', 'publisher': 'Damian Gulczynski', 'version': VERSION,
        'manifest_sha256': digest(stage / 'FILES_SHA256.json'),
        'uninstaller_sha256': digest(uninstaller),
    })


def _plain_program_file(target, name):
    path = target
    for part in ('', *Path(name).parts):
        if part:
            path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('An installer metadata path became a link or reparse point.')
    if not stat.S_ISREG(info.st_mode):
        raise OSError('Installer metadata is not a regular file.')
    return path


def activate(stage, base, data, smoke=smoke_check, *, registrar=None,
             previous_reader=None, cleanup=None, progress=lambda _: None):
    stage, base, data = Path(stage).resolve(), Path(base).resolve(), Path(data).resolve()
    if stage.parent != base or not stage.name.startswith('.installing-') or stage.is_symlink():
        raise RuntimeError('Unexpected installer staging folder.')
    target = base / VERSION
    if target.is_symlink():
        raise RuntimeError('The installation folder must not be a symbolic link.')
    if base.is_relative_to(data) or data.is_relative_to(target):
        raise RuntimeError('The application installation folder overlaps the configured survey workspace. The workspace was kept.')
    with exclusive(data):
        started=time.monotonic()
        verified = verify(stage,progress)
        verified_at=time.monotonic()
        progress('Testing application startup and bundled processing engines (up to 3 minutes)')
        component_check = smoke(stage)
        tested_at=time.monotonic()
        progress('Saving recovery information and activating the new release')
        if registrar is not None:
            _prepare_uninstaller_receipt(stage)
        marker = base / 'current_release.json'
        if marker.is_symlink():
            raise RuntimeError('The active-release record must not be a symbolic link.')
        original_marker = marker.read_bytes() if marker.exists() else None
        prior_active, cleanup_warnings = _previous_release(base, original_marker, previous_reader)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
        if (data / 'project.json').is_file():
            indexes = data / 'project_indexes'
            indexes.mkdir(exist_ok=True)
            shutil.copy2(data / 'project.json', indexes / ('before_' + VERSION + '_' + stamp + '.json'))
        previous = base / (VERSION + '_previous_' + stamp) if target.exists() else None
        moved_previous = installed = False
        registration = None
        try:
            if previous:
                target.rename(previous)
                moved_previous = True
            stage.rename(target)
            installed = True
            retained_previous = previous if previous is not None and prior_active == target else prior_active
            atomic_json(marker, {'version': VERSION, 'program_folder': str(target),
                                 'previous_program_folder': str(retained_previous) if retained_previous else None})
            # The CLI supplies the real Windows publisher. It restores its own
            # stable key/pointer on publication failure before this rollback runs.
            if registrar is not None:
                registration = registrar(base, target, VERSION)
                if (registration.get('previous_current_install_present')
                        and not registration.get('previous_current_install_validated')):
                    cleanup_warnings.append('The prior Windows installation pointer was ambiguous; older program files were kept.')
        except Exception:
            rollback_errors = []
            try:
                _restore_marker(marker, original_marker)
            except OSError as error:
                rollback_errors.append('active-release record: ' + str(error))
            if installed:
                try:
                    target.rename(stage)
                except OSError as error:
                    rollback_errors.append('new release folder: ' + str(error))
            if moved_previous:
                try:
                    previous.rename(target)
                except OSError as error:
                    rollback_errors.append('prior release folder: ' + str(error))
            if rollback_errors:
                raise RuntimeError('The update failed and could not finish restoring the installation records. '
                                   'The prior release folder and survey data were retained. ' + '; '.join(rollback_errors))
            raise
        # Core activation and stable Windows registration have committed. Optional
        # old-program cleanup must never turn this success into a false rollback.
        cleanup_report = {'status': 'skipped', 'warnings': cleanup_warnings}
        activated_at=time.monotonic()
        if cleanup is not None:
            progress('Checking older program versions for safe cleanup')
            try:
                if retained_previous is None or cleanup_warnings:
                    older_folders = any(path != target and _release_reference(base, str(path)) is not None
                                        for path in base.iterdir())
                    if older_folders:
                        cleanup_report['warnings'] = cleanup_warnings or [
                            'The prior active version is not known; older program files were kept.']
                    else:
                        cleanup_report = {'status': 'complete', 'removed_files': 0,
                                          'removed_bytes': 0, 'warnings': []}
                else:
                    cleanup_report = cleanup(base, target, retained_previous, data_root=data)
            except Exception as error:
                cleanup_report = {'status': 'partial', 'warnings': [
                    'The update is installed. Older program files could not all be cleaned: ' + str(error)]}
        progress('Application checks complete')
        return {'version': VERSION, 'program_folder': str(target), 'verified_files': verified,
                'previous_same_version': str(previous) if previous else None,
                'previous_program_folder': str(retained_previous) if retained_previous else None,
                'windows_registration': registration, 'obsolete_program_cleanup': cleanup_report,
                'component_check': component_check, 'survey_data_changed': False,
                'timings_seconds': {'file_verification': round(verified_at-started,3),
                                    'startup_and_engines': round(tested_at-verified_at,3),
                                    'activation': round(activated_at-tested_at,3),
                                    'older_release_cleanup': round(time.monotonic()-activated_at,3)}}


def _uninstall_metadata(target):
    """Capture only our exact generated receipt and its verified ownership files."""
    try:
        receipt_path = _plain_program_file(target, 'INSTALL_RECEIPT.json')
        if receipt_path.stat().st_size > 64 * 1024:
            return {}
        encoded = receipt_path.read_bytes()
        receipt = json.loads(encoded)
        if (set(receipt) != {'product', 'publisher', 'version', 'manifest_sha256', 'uninstaller_sha256'}
                or receipt['product'] != 'Tunnel Studio'
                or receipt['publisher'] != 'Damian Gulczynski' or receipt['version'] != VERSION
                or digest(_plain_program_file(target, 'FILES_SHA256.json')) != receipt['manifest_sha256']):
            return {}
        manifest_path = _plain_program_file(target, 'FILES_SHA256.json')
        if manifest_path.stat().st_size > 32 * 1024 * 1024:
            return {}
        manifest = json.loads(manifest_path.read_bytes())
        return {'UninstallTunnelStudio.exe': receipt['uninstaller_sha256'],
                'FILES_SHA256.json': receipt['manifest_sha256'],
                'INSTALL_RECEIPT.json': hashlib.sha256(encoded).hexdigest(),
                'COPYRIGHT.txt': manifest['files']['COPYRIGHT.txt'],
                'app/release.py': manifest['files']['app/release.py']}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def uninstall_release(target, base, data, *, current_reader, unregistrar, remover):
    """Remove confirmed program files under the workspace lock; preserve user data.

    Owned files blocked by Windows retain their registration, anchors and
    bootstrap-backed uninstaller for retry. An older retained release cannot
    remove the current release marker or shared shortcuts.
    """
    target, base, data = Path(target).absolute(), Path(base).resolve(), Path(data).resolve()
    if base.is_relative_to(data) or data.is_relative_to(target):
        raise RuntimeError('The application uninstall folder overlaps the configured survey workspace. The workspace was kept.')
    if (_release_reference(base, str(target)) is None
            or not re.fullmatch(re.escape(VERSION) + r'(?:_previous_\d{8}_\d{6}_[0-9a-f]{8})?', target.name)):
        raise RuntimeError('The requested uninstall folder is not this Tunnel Studio release.')
    with exclusive(data):
        marker = base / 'current_release.json'
        original = marker.read_bytes() if marker.exists() and not marker.is_symlink() else None
        if marker.is_symlink():
            raise RuntimeError('The active-release record must not be a symbolic link.')
        marked, warnings = _previous_release(base, original, current_reader)
        if warnings:
            raise RuntimeError('The installation records need repair before uninstalling. ' + '; '.join(warnings))
        was_current = marked == target
        generated = _uninstall_metadata(target)
        cleanup_report = remover(base, target, data_root=data)
        if cleanup_report.get('status') == 'refused' or cleanup_report.get('pending_files', 0):
            try:
                atomic_json(data / ('uninstall_' + VERSION + '.json'), cleanup_report)
            except OSError:
                pass
            raise RuntimeError('Some installed program files could not be removed. The uninstall entry and recovery metadata were kept for retry. '
                               + '; '.join(cleanup_report.get('warnings', [])[:3]))
        # Publication/removal owns its own typed-registry rollback. The usable
        # generated uninstaller is retained until that operation has succeeded.
        registration = unregistrar(base, target, VERSION)
        if marked is None and 'TunnelStudio' in registration.get('removed_entries', []):
            was_current = True
        report = {'version': VERSION, 'program_folder': str(target),
                  'removed_current_release': was_current, 'program_cleanup': cleanup_report,
                  'windows_registration': registration, 'survey_data_changed': False,
                  'generated_metadata_removed': [], 'warnings': []}
        if was_current and original is not None:
            try:
                if marker.read_bytes() == original:
                    marker.unlink()
            except OSError as error:
                report['warnings'].append('The obsolete release marker could not be removed: ' + str(error))
        for name, expected in generated.items():
            try:
                path = _plain_program_file(target, name)
                if digest(path) != expected:
                    continue
                _plain_program_file(target, name)
                path.unlink()
                report['generated_metadata_removed'].append(name)
            except OSError as error:
                report['warnings'].append('A small installer metadata file was retained: ' + str(error))
        for folder in (target / 'app', target):
            try:
                folder.rmdir()  # Only empty directories.
            except OSError:
                pass
        return report


def _lifecycle_modules():
    # The NSIS bootstrap interpreter uses an isolated ._pth. Helpers are bundled
    # beside that interpreter, outside any release that may be renamed/removed.
    here = Path(__file__).resolve().parent
    support = here / 'bootstrap' if (here / 'bootstrap/release_registry.py').is_file() else here
    sys.path.insert(0, str(support))
    import release_registry, release_cleanup
    return release_registry, release_cleanup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage')
    parser.add_argument('--base')
    parser.add_argument('--check-closed', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--target')
    parser.add_argument('--error-file', required=True)
    args = parser.parse_args()
    data = Path(os.environ.get('TUNNEL_STUDIO_DATA', str(Path(os.environ['LOCALAPPDATA']) / 'TunnelStudio')))
    try:
        if args.check_closed:
            with exclusive(data):
                pass
        else:
            registry, releases = _lifecycle_modules()
            local = Path(os.environ['LOCALAPPDATA'])
            if args.uninstall:
                report = uninstall_release(args.target, args.base, data,
                    current_reader=registry.read_current_install,
                    unregistrar=registry.remove_release_registration,
                    remover=lambda base, target, **kw: releases.remove_release(base, target, local_app_data=local, **kw))
                try:
                    atomic_json(data / ('uninstall_' + VERSION + '.json'), report)
                except OSError:
                    pass
                return 0 if report['removed_current_release'] else 10
            def progress(message):
                try:
                    print(message,flush=True)
                except (OSError, ValueError):
                    pass  # Losing the display pipe cannot interrupt activation.

            report = activate(args.stage, args.base, data, progress=progress,
                registrar=registry.register_release,
                previous_reader=registry.read_current_install,
                cleanup=lambda base, current, previous, **kw: releases.cleanup(base, current, previous, local_app_data=local, **kw))
            # Installation is committed; a report write must not turn success into failure.
            try:
                atomic_json(data / ('installation_' + VERSION + '.json'), report)
            except OSError:
                pass
            registration = report.get('windows_registration') or {}
            cleanup_report = report.get('obsolete_program_cleanup') or {}
            if (registration.get('warnings') or registration.get('legacy_entries_retained')
                    or cleanup_report.get('status') in ('partial', 'refused')
                    or cleanup_report.get('warnings')):
                return 20  # Successfully installed; nonfatal legacy cleanup needs review.
        return 0
    except Exception as error:
        detail = traceback.format_exc()
        try:
            data.mkdir(parents=True, exist_ok=True)
            prefix = 'uninstall_' if args.uninstall else 'installation_'
            (data / (prefix + VERSION + '_error.txt')).write_text(detail, encoding='utf-8')
        except OSError:
            pass
        Path(args.error_file).write_text(str(error) + '\n', encoding='utf-8')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
