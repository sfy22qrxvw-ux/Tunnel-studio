# Copyright (c) 2026 Damian Gulczynski.
"""Conservative cleanup of obsolete, manifest-owned Windows program files.

The caller invokes ``cleanup`` only after successful activation and publication
of the stable uninstall entry. ``remove_release`` is the same primitive for an
explicit uninstall whose registry/active-release identity the caller verified.

No recursive tree deletion is used. Unknown files, changed files, survey/cache
formats and the configured data workspace survive. Tiny ownership anchors remain
so a locked-file failure can be retried safely on a later update. Byte counts are
logical file sizes removed, not a promise of physical disk space reclaimed.
"""
from pathlib import Path, PurePosixPath
import ast
import hashlib
import json
import os
import re
import stat


_VERSION = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
_RELEASE_NAME = re.compile(r'(?P<version>' + _VERSION + r')(?:_previous_[0-9]{8}_[0-9]{6}_[0-9a-f]{8})?\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_PROGRAM_ROOTS = {'app', 'runtime', 'build_support', 'compiler_licenses'}
_ROOT_FILES = {'TunnelStudio.exe', 'COPYRIGHT.txt', 'DEPENDENCIES.json',
               'DEPENDENCIES_INSTALLED.json', 'RELEASE_NOTES.txt',
               'THIRD_PARTY_NOTICES.txt', 'UninstallTunnelStudio.exe'}
_ANCHORS = {'FILES_SHA256.json', 'COPYRIGHT.txt', 'app/release.py'}
_REQUIRED = {'TunnelStudio.exe', 'runtime/python.exe', 'runtime/pythonw.exe',
             'app/desktop.py', 'app/release.py', 'COPYRIGHT.txt'}
_DATA_DIRECTORIES = {'projects', 'startup_recovery', 'project_indexes', 'display_cache'}
_DATA_NAMES = {'project.json', 'workspace.json', 'current_release.json', 'session.json'}
_DATA_ENDINGS = (
    '.e57', '.las', '.laz', '.pts', '.ptx', '.ptsx', '.xyz', '.xyz64',
    '.ply', '.obj', '.stl', '.dxf', '.dwg', '.iges', '.igs', '.step', '.stp',
    '.hspca', '.tstudio', '.tunnelstudio', '.db', '.sqlite', '.sqlite3', '.csv',
    '.attr32', '.mask', '.preview64', '.analysis64', '.vertices64', '.faces32',
    '.vcolour32', '.faceflags', '.face_flags', '.truth', '.surface_ids',
    '.surface_labels', '.surface.ids64', '.surface.labels32', '.patch_ids',
    '.selection', '.design-results', '.inspection', '.inspection64', '.results64',
    '.inspection-results', '.feature_xyz64', '.feature_ids', '.feature_labels',
    '.feature_selected', '.setup-photo.dat', '.section.preview64', '.mesh.json',
    '.owner.json', '.ids', '.labels', '.i64', '.i32', '.vertices', '.faces',
)
_MANIFEST_LIMIT = 32 * 1024 * 1024
_DETAIL_LIMIT = 40


class UnsafeRelease(ValueError):
    pass


def _absolute(value):
    return Path(os.path.abspath(os.fspath(value)))


def _same(left, right):
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _under(path, parent):
    try:
        return os.path.commonpath([os.path.normcase(str(path)),
                                   os.path.normcase(str(parent))]) == os.path.normcase(str(parent))
    except ValueError:
        return False


def _reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) &
                                            getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _safe_stat(path):
    """Inspect every component without following a link/junction/reparse point."""
    path = _absolute(path)
    cursor = Path(path.anchor)
    result = cursor.lstat()
    if _reparse(result):
        raise UnsafeRelease('A path component is a symbolic link or reparse point.')
    for part in path.parts[1:]:
        if not stat.S_ISDIR(result.st_mode):
            raise UnsafeRelease('A parent path component is not a directory.')
        cursor = cursor / part
        result = cursor.lstat()
        if _reparse(result):
            raise UnsafeRelease('A path component is a symbolic link or reparse point.')
    return result


def _signature(info):
    # File identity and type matter here. Permission/executable bits can differ
    # between pathname stat and descriptor fstat on Windows and are not content.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IFMT(info.st_mode))


def _read(path, maximum=None):
    """Read/hash a stable regular file, never following a final POSIX symlink."""
    before = _safe_stat(path)
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeRelease('A program file is not a regular file.')
    if maximum is not None and before.st_size > maximum:
        raise UnsafeRelease('Ownership metadata exceeds the supported check size.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    pieces = [] if maximum is not None else None
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _signature(opened) != _signature(before) or _reparse(opened):
            raise UnsafeRelease('A program file changed while being opened.')
        total = 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(block)
            if maximum is not None and total > maximum:
                raise UnsafeRelease('Ownership metadata changed size while being read.')
            digest.update(block)
            if pieces is not None:
                pieces.append(block)
        if _signature(os.fstat(stream.fileno())) != _signature(before):
            raise UnsafeRelease('A program file changed while being read.')
    if _signature(_safe_stat(path)) != _signature(before):
        raise UnsafeRelease('A program file changed after being read.')
    return digest.hexdigest(), before, b''.join(pieces) if pieces is not None else None


def _environment(base, local_app_data, data_root):
    local = local_app_data if local_app_data is not None else os.environ.get('LOCALAPPDATA')
    if not local:
        raise UnsafeRelease('The LocalAppData installation root is unavailable.')
    local = _absolute(local)
    base = _absolute(base)
    expected = local / 'Programs' / 'TunnelStudio' / 'releases'
    if not _same(base, expected):
        raise UnsafeRelease('Cleanup is restricted to the canonical Tunnel Studio releases folder.')
    if not stat.S_ISDIR(_safe_stat(base).st_mode):
        raise UnsafeRelease('The releases folder is not a regular directory.')
    data = _absolute(data_root if data_root is not None else
                     os.environ.get('TUNNEL_STUDIO_DATA', str(local / 'TunnelStudio')))
    if _under(base, data):
        raise UnsafeRelease('The releases folder is inside the protected survey-data workspace.')
    return base, data


def _release_path(base, value):
    path = _absolute(value)
    if not _same(path.parent, base) or not _RELEASE_NAME.fullmatch(path.name):
        raise UnsafeRelease('The release must be a recognized direct child of the releases folder.')
    return path


def _manifest(release):
    match = _RELEASE_NAME.fullmatch(release.name)
    if not match or not stat.S_ISDIR(_safe_stat(release).st_mode):
        raise UnsafeRelease('The release folder is not recognized.')
    _, _, encoded = _read(release / 'FILES_SHA256.json', _MANIFEST_LIMIT)
    data = json.loads(encoded.decode('utf-8'))
    if not isinstance(data, dict) or data.get('version') != match.group('version'):
        raise UnsafeRelease('The release folder and manifest versions do not match.')
    files = data.get('files')
    if not isinstance(files, dict) or not _REQUIRED <= files.keys():
        raise UnsafeRelease('The release ownership manifest is incomplete.')
    folded = set()
    for name, expected in files.items():
        if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name or
                any(char in name for char in '<>:"|?*') or
                not isinstance(expected, str) or not _SHA256.fullmatch(expected)):
            raise UnsafeRelease('The release manifest contains an invalid file entry.')
        relative = PurePosixPath(name)
        if (relative.is_absolute() or name != relative.as_posix() or
                any(part in ('.', '..') or part.endswith((' ', '.')) for part in relative.parts)):
            raise UnsafeRelease('The release manifest contains an unsafe file path.')
        key = name.casefold()
        if key in folded:
            raise UnsafeRelease('The release manifest has conflicting Windows file paths.')
        folded.add(key)
    values = {}
    release_hash, _, release_code = _read(release / 'app/release.py', 1024 * 1024)
    if release_hash != files['app/release.py']:
        raise UnsafeRelease('The release ownership code was modified.')
    for node in ast.parse(release_code.decode('utf-8-sig')).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('VERSION', 'APP_NAME', 'COPYRIGHT'):
                    values[target.id] = ast.literal_eval(node.value)
    if (values.get('VERSION') != data['version'] or values.get('APP_NAME') != 'Tunnel Studio' or
            not isinstance(values.get('COPYRIGHT'), str) or
            not re.fullmatch(r'(?:©|Copyright \(c\)) 20[0-9]{2} Damian Gulczynski', values['COPYRIGHT'])):
        raise UnsafeRelease('The release does not identify the Tunnel Studio publisher.')
    copyright_hash, _, notice = _read(release / 'COPYRIGHT.txt', 1024 * 1024)
    if (copyright_hash != files['COPYRIGHT.txt'] or
            not notice.splitlines() or notice.splitlines()[0] != b'Tunnel Studio' or
            not re.search(rb'Copyright \(c\) 20[0-9]{2} Damian Gulczynski\.', notice)):
        raise UnsafeRelease('The release copyright ownership record is missing or modified.')
    return data


def _protected(name, path, data_root):
    relative = PurePosixPath(name)
    lower = name.lower()
    if _under(path, data_root):
        return 'survey workspace'
    if any(part.lower() in _DATA_DIRECTORIES for part in relative.parts):
        return 'project or cache directory'
    if relative.name.lower() in _DATA_NAMES or lower.endswith(_DATA_ENDINGS):
        return 'survey, project or cache format'
    if relative.suffix.lower() in ('.npy', '.npz') and relative.parts[0] != 'runtime':
        return 'point array outside the bundled runtime'
    if len(relative.parts) == 1 and name not in _ROOT_FILES:
        return 'unrecognized program-layout file'
    if len(relative.parts) > 1 and relative.parts[0] not in _PROGRAM_ROOTS:
        return 'unrecognized program-layout folder'
    return None


def _report(path=None):
    return {'status': 'complete', 'release_path': str(path) if path is not None else None,
            'removed_files': 0, 'removed_bytes': 0, 'pending_files': 0,
            'protected_files': 0, 'unlisted_paths': [], 'unlisted_paths_truncated': False,
            'retained_anchors': [], 'warnings': []}


def _warning(report, message):
    if len(report['warnings']) < _DETAIL_LIMIT:
        report['warnings'].append(str(message))


def _inventory_residue(release, files, data_root, report):
    """Look only in known program folders; never recurse into unknown user trees."""
    known_directories = {'.'}
    for name in files:
        known_directories.update(parent.as_posix() for parent in PurePosixPath(name).parents)
    pending = [release]
    seen = 0
    while pending:
        directory = pending.pop()
        try:
            _safe_stat(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen += 1
                    if seen > 50_000:
                        report['unlisted_paths_truncated'] = True
                        return
                    path = Path(entry.path)
                    name = path.relative_to(release).as_posix()
                    info = entry.stat(follow_symlinks=False)
                    is_directory = stat.S_ISDIR(info.st_mode) and not _reparse(info)
                    if (is_directory and name in known_directories and
                            not _under(path, data_root) and
                            not any(part.lower() in _DATA_DIRECTORIES for part in PurePosixPath(name).parts)):
                        pending.append(path)
                        continue
                    if name not in files and name not in _ANCHORS:
                        if len(report['unlisted_paths']) < _DETAIL_LIMIT:
                            report['unlisted_paths'].append(name + ('/' if is_directory else ''))
                        else:
                            report['unlisted_paths_truncated'] = True
        except (OSError, UnsafeRelease) as error:
            _warning(report, 'A retained folder could not be inspected: ' + str(error))


def _remove_owned(release, data_root):
    report = _report(release)
    try:
        manifest = _manifest(release)
    except (OSError, ValueError, SyntaxError, TypeError, RecursionError) as error:
        report['status'] = 'refused'
        _warning(report, error)
        return report
    report['version'] = manifest['version']
    files = manifest['files']
    directories = set()
    for name, expected in files.items():
        path = release / name
        if name in _ANCHORS:
            report['retained_anchors'].append(name)
            continue
        protected = _protected(name, path, data_root)
        if protected:
            try:
                _safe_stat(path)
            except FileNotFoundError:
                continue
            except (OSError, UnsafeRelease):
                pass
            report['protected_files'] += 1
            continue
        try:
            actual, info, _ = _read(path)
            if actual != expected:
                report['protected_files'] += 1
                _warning(report, 'Modified file kept: ' + name)
                continue
            if _signature(_safe_stat(path)) != _signature(info):
                raise UnsafeRelease('File changed immediately before removal.')
            path.unlink()
            report['removed_files'] += 1
            report['removed_bytes'] += info.st_size
            directories.update(parent for parent in path.parents if parent != release and _under(parent, release))
        except FileNotFoundError:
            continue
        except UnsafeRelease as error:
            report['protected_files'] += 1
            _warning(report, 'Unsafe or changed file kept: ' + name + ': ' + str(error))
        except OSError as error:
            report['pending_files'] += 1
            _warning(report, 'File could not yet be removed: ' + name + ': ' + str(error))
    report['retained_anchors'].append('FILES_SHA256.json')
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            if stat.S_ISDIR(_safe_stat(directory).st_mode):
                directory.rmdir()  # Only empty directories; never recursive.
        except (OSError, UnsafeRelease):
            pass
    _inventory_residue(release, files, data_root, report)
    if report['pending_files']:
        report['status'] = 'partial'
    return report


def remove_release(base, target, *, data_root=None, local_app_data=None):
    """Remove only verified owned program files for an authorized uninstall.

The caller must first confirm this is the installed application's requested
uninstall target, and close the application/acquire its process lock.
"""
    report = _report(target)
    try:
        base, data = _environment(base, local_app_data, data_root)
        release = _release_path(base, target)
        if _under(release, data):
            raise UnsafeRelease('The release is inside the protected survey-data workspace.')
    except (OSError, ValueError, TypeError) as error:
        report['status'] = 'refused'
        _warning(report, error)
        return report
    return _remove_owned(release, data)


def cleanup(base, current, previous=None, *, data_root=None, local_app_data=None):
    """After successful upgrade, retain current and the exact prior working release.

The committed release marker is verified before examining obsolete candidates.
An invalid environment/marker refuses all cleanup. A malformed legacy release is
left alone and reported; it never prevents other eligible program-only cleanup.
"""
    report = _report()
    report.update(releases=[], preserved_current=str(current),
                  preserved_previous=str(previous) if previous is not None else None)
    try:
        base, data = _environment(base, local_app_data, data_root)
        current = _release_path(base, current)
        if previous is not None:
            previous = _release_path(base, previous)
            if _same(previous, current):
                raise UnsafeRelease('The prior release must be distinct from the active release.')
        current_manifest = _manifest(current)
        _, _, marker_bytes = _read(base / 'current_release.json', 64 * 1024)
        marker = json.loads(marker_bytes.decode('utf-8'))
        if (not isinstance(marker, dict) or marker.get('version') != current_manifest['version'] or
                not isinstance(marker.get('program_folder'), str) or
                not _same(_absolute(marker['program_folder']), current)):
            raise UnsafeRelease('The current-release marker has not committed the requested release.')
        candidates = list(base.iterdir())
    except (OSError, ValueError, TypeError, SyntaxError, RecursionError) as error:
        report['status'] = 'refused'
        _warning(report, error)
        return report
    for candidate in sorted(candidates, key=lambda path: path.name):
        if (_same(candidate, current) or previous is not None and _same(candidate, previous) or
                not _RELEASE_NAME.fullmatch(candidate.name)):
            continue
        if _under(candidate, data):
            item = _report(candidate)
            item['status'] = 'refused'
            _warning(item, 'This release is inside the protected survey-data workspace.')
        else:
            item = _remove_owned(candidate, data)
        report['releases'].append(item)
        for key in ('removed_files', 'removed_bytes', 'pending_files', 'protected_files'):
            report[key] += item[key]
        if item['status'] != 'complete':
            report['status'] = 'partial'
    return report
