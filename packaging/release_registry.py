# Copyright (c) 2026 Damian Gulczynski.
"""One owned, per-user Windows program entry for all Tunnel Studio upgrades.

No program or survey files are deleted here. The caller runs publication only
after the new payload passed its checks and its uninstaller exists. Publication
failure restores the previous stable entry and CurrentInstall pointer; legacy
entries are not touched until both new values have been read back successfully.

HKCU\\Software and its Uninstall descendant are shared between Windows 10/11
WOW64 views. Use the explicit 32-bit view once, matching the NSIS stub; sweeping
the other view could delete the same physical stable key a second time.
Microsoft: https://learn.microsoft.com/windows/win32/winprog64/shared-registry-keys
Python API: https://docs.python.org/3.12/library/winreg.html
"""
from dataclasses import dataclass
import ntpath
import os
import posixpath
import re


UNINSTALL_ROOT = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
STABLE_NAME = 'TunnelStudio'
STABLE_KEY = UNINSTALL_ROOT + '\\' + STABLE_NAME
VENDOR_KEY = r'Software\TunnelStudio'
PUBLISHER = 'Damian Gulczynski'
REG_SZ, REG_DWORD = 1, 4
VERSION_PATTERN = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
VERSION_RE = re.compile(VERSION_PATTERN)
LEGACY_RE = re.compile(r'TunnelStudio-(' + VERSION_PATTERN + r')', re.I)


@dataclass
class RegistryKey:
    values: dict
    subkeys: int = 0


class RegistrationError(RuntimeError):
    pass


class WindowsRegistry:
    """Small HKCU-only adapter; typed values make rollback lossless."""

    def __init__(self, winreg_module=None):
        if winreg_module is None:
            import winreg as winreg_module
        self.reg = winreg_module
        self.view = winreg_module.KEY_WOW64_32KEY

    def read(self, path):
        r = self.reg
        try:
            handle = r.OpenKey(r.HKEY_CURRENT_USER, path, 0, r.KEY_READ | self.view)
        except FileNotFoundError:
            return None
        with handle:
            children, count, _ = r.QueryInfoKey(handle)
            values = {}
            for index in range(count):
                name, value, kind = r.EnumValue(handle, index)
                values[name] = (value, kind)
            return RegistryKey(values, children)

    def names(self, path):
        r = self.reg
        try:
            handle = r.OpenKey(r.HKEY_CURRENT_USER, path, 0, r.KEY_READ | self.view)
        except FileNotFoundError:
            return []
        with handle:
            result, index = [], 0
            while True:
                try:
                    result.append(r.EnumKey(handle, index))
                except OSError as error:
                    if getattr(error, 'winerror', None) == 259:  # ERROR_NO_MORE_ITEMS
                        break
                    raise
                index += 1
            return result

    def set_values(self, path, values):
        r = self.reg
        with r.CreateKeyEx(r.HKEY_CURRENT_USER, path, 0, r.KEY_SET_VALUE | self.view) as handle:
            for name, (value, kind) in values.items():
                r.SetValueEx(handle, name, 0, kind, value)

    def delete_value(self, path, name):
        r = self.reg
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, path, 0, r.KEY_SET_VALUE | self.view) as handle:
                r.DeleteValue(handle, name)
        except FileNotFoundError:
            pass

    def delete_key(self, path):
        # Deliberately nonrecursive: an unexpected child key is preserved.
        self.reg.DeleteKeyEx(self.reg.HKEY_CURRENT_USER, path, access=self.view)


def _value(entry, name):
    if entry is None:
        return None
    found = [value for key, value in entry.values.items() if key.casefold() == name.casefold()]
    return found[0] if len(found) == 1 else None


def _string(entry, name):
    typed = _value(entry, name)
    return typed[0] if typed is not None and typed[1] == REG_SZ and isinstance(typed[0], str) else None


def _normal_path(value):
    value = os.fspath(value)
    if not isinstance(value, str) or not value or any(char in value for char in ('\0', '\r', '\n', '"')):
        raise ValueError('Invalid installation path.')
    windows = bool(ntpath.splitdrive(value)[0]) or '\\' in value
    module = ntpath if windows else posixpath
    if not module.isabs(value) or (windows and not ntpath.splitdrive(value)[0]):
        raise ValueError('Installation paths must be absolute.')
    _, tail = ntpath.splitdrive(value) if windows else ('', value)
    parts = tail.replace('\\', '/').split('/')
    if any(part in ('.', '..') or (part and part.rstrip(' .') != part) or ':' in part for part in parts):
        raise ValueError('Installation path contains an ambiguous component.')
    return module.normcase(module.normpath(value))


def _join(base, *parts):
    return (ntpath if ntpath.splitdrive(os.fspath(base))[0] or '\\' in os.fspath(base) else posixpath).join(os.fspath(base), *parts)


def _release_location(base, value):
    """Return (version, original path) only for a direct owned release child."""
    if not isinstance(value, str):
        return None
    try:
        normalized = _normal_path(value)
        module = ntpath if ntpath.splitdrive(normalized)[0] else posixpath
        folder = module.basename(normalized)
        if module.dirname(normalized) != _normal_path(base) or not VERSION_RE.fullmatch(folder):
            return None
        return folder, value
    except (TypeError, ValueError):
        return None


def _owned(name, entry, base):
    if entry is None or entry.subkeys or _string(entry, 'Publisher') != PUBLISHER:
        return None
    legacy = LEGACY_RE.fullmatch(name)
    if name.casefold() != STABLE_NAME.casefold() and legacy is None:
        return None
    location = _release_location(base, _string(entry, 'InstallLocation'))
    if location is None:
        return None
    version, target = location
    if legacy is not None and legacy.group(1) != version:
        return None
    if _string(entry, 'DisplayVersion') != version:
        return None
    names = {'Tunnel Studio ' + version}
    if legacy is None:
        names.add('Tunnel Studio')
    if _string(entry, 'DisplayName') not in names:
        return None
    command = _string(entry, 'UninstallString')
    if command is None:
        return None
    if command.startswith('"') and command.endswith('"'):
        command = command[1:-1]
    try:
        if _normal_path(command) != _normal_path(_join(target, 'UninstallTunnelStudio.exe')):
            return None
    except (TypeError, ValueError):
        return None
    return {'version': version, 'target': target}


def _context(base, target=None, version=None, registry=None, *, allow_backup=False):
    base = os.fspath(base)
    _normal_path(base)
    if registry is None:
        local = os.environ.get('LOCALAPPDATA')
        if not local or _normal_path(base) != _normal_path(_join(local, 'Programs', 'TunnelStudio', 'releases')):
            raise RegistrationError('Program registration requires the standard current-user Tunnel Studio releases folder.')
        registry = WindowsRegistry()
    if target is not None:
        target = os.fspath(target)
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise RegistrationError('Invalid application version for Windows program registration.')
        location = _release_location(base, target)
        valid = location is not None and location[0] == version
        if not valid and allow_backup:
            normalized = _normal_path(target)
            module = ntpath if ntpath.splitdrive(normalized)[0] else posixpath
            backup = re.fullmatch(re.escape(version) + r'_previous_[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', module.basename(normalized))
            valid = bool(backup and module.dirname(normalized) == _normal_path(base))
        if not valid:
            raise RegistrationError('Program registration target is not the expected release folder.')
    return base, target, registry


def read_current_install(base, *, registry=None):
    """Read the unambiguous current release from the vendor/stable records.

    Registry access errors intentionally propagate so file cleanup can be skipped
    when the previous active version is ambiguous. An owned stable entry can
    supply a missing vendor pointer, but two present records must agree. Only
    absence of both records returns None.
    """
    base, _, registry = _context(base, registry=registry)
    stable = registry.read(STABLE_KEY)
    owned = _owned(STABLE_NAME, stable, base)
    if stable is not None and owned is None:
        raise RegistrationError('The stable Windows program entry is not a verified Tunnel Studio release; older application folders must be preserved.')
    entry = registry.read(VENDOR_KEY)
    if _value(entry, 'CurrentInstall') is None:
        return owned['target'] if owned is not None else None
    value = _string(entry, 'CurrentInstall')
    if _release_location(base, value) is None:
        raise RegistrationError('The previous CurrentInstall registry pointer is not a verified direct release; older application folders must be preserved.')
    if owned is not None and _normal_path(value) != _normal_path(owned['target']):
        raise RegistrationError('The stable Windows program entry and CurrentInstall point to different releases; older application folders must be preserved.')
    return value


current_install = read_current_install


def _restore_entry(registry, path, before):
    if before is None:
        try:
            registry.delete_key(path)
        except FileNotFoundError:
            pass
        if registry.read(path) is not None:
            raise RegistrationError('Could not remove the incomplete Windows program entry.')
        return
    registry.set_values(path, before.values)
    actual = registry.read(path)
    old_names = {name.casefold() for name in before.values}
    for name in actual.values:
        if name.casefold() not in old_names:
            registry.delete_value(path, name)
    restored = registry.read(path)
    normalize = lambda values: {name.casefold(): typed for name, typed in values.items()}
    if restored is None or normalize(restored.values) != normalize(before.values) or restored.subkeys != before.subkeys:
        raise RegistrationError('Could not restore the prior Windows program entry.')


def _restore_stable(registry, before):
    _restore_entry(registry, STABLE_KEY, before)


def _restore_pointer(registry, before):
    if before is None:
        registry.delete_value(VENDOR_KEY, 'CurrentInstall')
    else:
        registry.set_values(VENDOR_KEY, {'CurrentInstall': before})
    if _value(registry.read(VENDOR_KEY), 'CurrentInstall') != before:
        raise RegistrationError('Could not restore the prior CurrentInstall pointer.')


def register_release(base, target, version, *, registry=None):
    """Publish the stable entry, then retire only validated legacy program rows.

    Publication errors raise after restoring prior registration so the caller can
    roll back its activation. Once publication is verified, obsolete-row cleanup
    is best effort and returns warnings without triggering activation rollback.
    """
    base, target, registry = _context(base, target, version, registry)
    before = registry.read(STABLE_KEY)
    if before is not None and _owned(STABLE_NAME, before, base) is None:
        raise RegistrationError('The existing TunnelStudio program entry is not a verified entry for this installation; it was left unchanged.')
    vendor = registry.read(VENDOR_KEY)
    pointer_before = _value(vendor, 'CurrentInstall')
    previous = _string(vendor, 'CurrentInstall')
    values = {
        'DisplayName': ('Tunnel Studio', REG_SZ),
        'DisplayVersion': (version, REG_SZ),
        'Publisher': (PUBLISHER, REG_SZ),
        'InstallLocation': (target, REG_SZ),
        'DisplayIcon': (_join(target, 'TunnelStudio.exe'), REG_SZ),
        'UninstallString': ('"' + _join(target, 'UninstallTunnelStudio.exe') + '"', REG_SZ),
        'NoModify': (1, REG_DWORD),
        'NoRepair': (1, REG_DWORD),
    }
    try:
        registry.set_values(STABLE_KEY, values)
        registry.set_values(VENDOR_KEY, {'CurrentInstall': (target, REG_SZ)})
        actual = registry.read(STABLE_KEY)
        if any(_value(actual, key) != value for key, value in values.items()):
            raise RegistrationError('Windows program entry did not match the installed release after publication.')
        if _value(registry.read(VENDOR_KEY), 'CurrentInstall') != (target, REG_SZ):
            raise RegistrationError('Windows CurrentInstall pointer did not match the installed release.')
    except Exception as error:
        failures = []
        for operation in (lambda: _restore_stable(registry, before),
                          lambda: _restore_pointer(registry, pointer_before)):
            try:
                operation()
            except Exception as restore_error:
                failures.append(str(restore_error))
        detail = 'Windows program registration failed. Legacy program entries were kept.'
        if failures:
            detail += ' Registration rollback needs review: ' + '; '.join(failures)
        raise RegistrationError(detail) from error
    report = {
        'stable_key': 'HKCU\\' + STABLE_KEY,
        'display_name': 'Tunnel Studio', 'display_version': version,
        'install_location': target, 'current_install_updated': True,
        'previous_current_install': previous,
        'previous_current_install_present': pointer_before is not None,
        'previous_current_install_validated': _release_location(base, previous) is not None,
        'legacy_entries_removed': [], 'legacy_entries_retained': [], 'warnings': [],
        'registry_scope': 'Current user, shared HKCU Software view; explicit 32-bit access.',
    }
    try:
        names = registry.names(UNINSTALL_ROOT)
    except Exception as error:
        report['warnings'].append('The new program entry is installed, but older program entries could not be listed: ' + str(error))
        return report
    # Names are case-insensitive; deduplicate instead of sweeping logical views.
    visited = set()
    for name in names:
        if not isinstance(name, str) or not LEGACY_RE.fullmatch(name) or name.casefold() in visited:
            continue
        visited.add(name.casefold())
        path = UNINSTALL_ROOT + '\\' + name
        try:
            entry = registry.read(path)
            if entry is None:
                continue
            if _owned(name, entry, base) is None:
                report['legacy_entries_retained'].append({'name': name, 'reason': 'Ownership, release path or uninstall command could not be verified.'})
                continue
            registry.delete_key(path)
            report['legacy_entries_removed'].append(name)
        except FileNotFoundError:
            pass
        except Exception as error:
            report['warnings'].append('Could not remove older program entry ' + name + ': ' + str(error))
    return report


def remove_release_registration(base, target, version, *, registry=None):
    """Remove only rows/pointer still referring to this exact release.

    An older release's uninstaller cannot remove the stable entry for a newer
    version. A matching legacy row participates in the same rollback transaction,
    so its removal failure cannot leave an orphan row after the caller removes
    the uninstaller. No unrelated vendor values, keys, projects or files are
    removed.
    """
    base, target, registry = _context(base, target, version, registry, allow_backup=True)
    report = {'removed_entries': [], 'current_install_removed': False, 'warnings': []}
    before = registry.read(STABLE_KEY)
    vendor = registry.read(VENDOR_KEY)
    legacy_name = 'TunnelStudio-' + version
    legacy_path = UNINSTALL_ROOT + '\\' + legacy_name
    legacy_before = registry.read(legacy_path)
    pointer_before = _value(vendor, 'CurrentInstall')
    owned = _owned(STABLE_NAME, before, base)
    remove_stable = owned is not None and _normal_path(owned['target']) == _normal_path(target)
    legacy_owned = _owned(legacy_name, legacy_before, base)
    remove_legacy = legacy_owned is not None and _normal_path(legacy_owned['target']) == _normal_path(target)
    pointer = _string(vendor, 'CurrentInstall')
    try:
        remove_pointer = pointer is not None and _normal_path(pointer) == _normal_path(target)
    except ValueError:
        remove_pointer = False
    stable_attempted = pointer_attempted = legacy_attempted = False
    try:
        if remove_stable:
            stable_attempted = True
            registry.delete_key(STABLE_KEY)
        if remove_pointer:
            pointer_attempted = True
            registry.delete_value(VENDOR_KEY, 'CurrentInstall')
        if remove_legacy:
            legacy_attempted = True
            registry.delete_key(legacy_path)
        if remove_stable and registry.read(STABLE_KEY) is not None:
            raise RegistrationError('Windows still has this release\'s stable program entry.')
        if remove_pointer and _value(registry.read(VENDOR_KEY), 'CurrentInstall') is not None:
            raise RegistrationError('Windows still has this release\'s CurrentInstall pointer.')
        if remove_legacy and registry.read(legacy_path) is not None:
            raise RegistrationError('Windows still has this release\'s legacy program entry.')
    except Exception as error:
        failures = []
        operations = []
        if legacy_attempted:
            operations.append(lambda: _restore_entry(registry, legacy_path, legacy_before))
        if pointer_attempted:
            operations.append(lambda: _restore_pointer(registry, pointer_before))
        if stable_attempted:
            operations.append(lambda: _restore_stable(registry, before))
        for operation in operations:
            try:
                operation()
            except Exception as restore_error:
                failures.append(str(restore_error))
        detail = 'Windows program registration removal failed; the prior registration was restored for retry.'
        if failures:
            detail = 'Windows program registration removal failed. Registration rollback needs review: ' + '; '.join(failures)
        raise RegistrationError(detail) from error
    if remove_stable:
        report['removed_entries'].append(STABLE_NAME)
    report['current_install_removed'] = remove_pointer
    if remove_legacy:
        report['removed_entries'].append(legacy_name)
    return report
