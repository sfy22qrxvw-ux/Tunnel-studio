"""Ownership and rollback tests for the current-user Windows program entry."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import release_registry as rr


BASE = r'C:\Users\Damian\AppData\Local\Programs\TunnelStudio\releases'


def target(version):
    return BASE + '\\' + version


def program(version, *, stable=False):
    folder = target(version)
    return rr.RegistryKey({
        'DisplayName': ('Tunnel Studio' if stable else 'Tunnel Studio ' + version, rr.REG_SZ),
        'DisplayVersion': (version, rr.REG_SZ),
        'Publisher': (rr.PUBLISHER, rr.REG_SZ),
        'InstallLocation': (folder, rr.REG_SZ),
        'DisplayIcon': (folder + r'\TunnelStudio.exe', rr.REG_SZ),
        'UninstallString': ('"' + folder + r'\UninstallTunnelStudio.exe' + '"', rr.REG_SZ),
        'NoModify': (1, rr.REG_DWORD), 'NoRepair': (1, rr.REG_DWORD),
    })


def legacy(version):
    return rr.UNINSTALL_ROOT + '\\TunnelStudio-' + version


class FakeRegistry:
    """Typed, case-insensitive shared HKCU model with per-operation fault injection."""

    def __init__(self):
        self.data = {}
        self.spelling = {}
        self.events = []
        self.fail_once = None
        self.after_read = None

    def seed(self, path, entry):
        self.data[path.casefold()] = deepcopy(entry)
        self.spelling[path.casefold()] = path

    def _event(self, action, path, name=None):
        event = (action, path.casefold(), name)
        self.events.append(event)
        if self.fail_once == event:
            self.fail_once = None
            raise PermissionError('Injected registry access failure')

    def fail(self, action, path, name=None):
        self.fail_once = (action, path.casefold(), name)

    def read(self, path):
        self._event('read', path)
        result = deepcopy(self.data.get(path.casefold()))
        return self.after_read(path, result) if self.after_read else result

    def names(self, path):
        self._event('names', path)
        prefix = path.casefold() + '\\'
        return [self.spelling[key][len(prefix):] for key in self.data
                if key.startswith(prefix) and '\\' not in key[len(prefix):]]

    def set_values(self, path, values):
        for name, typed in values.items():
            self._event('set', path, name)
            key = self.data.setdefault(path.casefold(), rr.RegistryKey({}))
            self.spelling.setdefault(path.casefold(), path)
            existing = next((old for old in key.values if old.casefold() == name.casefold()), name)
            key.values[existing] = deepcopy(typed)

    def delete_value(self, path, name):
        self._event('delete_value', path, name)
        key = self.data.get(path.casefold())
        if key:
            existing = next((old for old in key.values if old.casefold() == name.casefold()), None)
            if existing is not None:
                del key.values[existing]

    def delete_key(self, path):
        self._event('delete_key', path)
        if path.casefold() not in self.data:
            raise FileNotFoundError(path)
        if self.data[path.casefold()].subkeys:
            raise PermissionError('Key has unexpected children')
        del self.data[path.casefold()]
        self.spelling.pop(path.casefold(), None)


class RegistryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.reg = FakeRegistry()
        self.reg.seed(rr.VENDOR_KEY, rr.RegistryKey({
            'CurrentInstall': (target('0.18.4'), rr.REG_SZ),
            'Theme': ('dark', rr.REG_SZ),
            'UnrelatedBinary': (b'preserve\0bytes', 3),
        }, subkeys=2))
        self.reg.seed(legacy('0.18.3'), program('0.18.3'))
        self.reg.seed(legacy('0.18.4'), program('0.18.4'))

    def publish(self, version='0.18.5'):
        return rr.register_release(BASE, target(version), version, registry=self.reg)

    def test_upgrade_replaces_multiple_version_rows_with_one_stable_entry(self):
        self.reg.seed(legacy('0.14.8'), program('0.14.8'))
        report = self.publish()
        self.assertEqual(self.reg.names(rr.UNINSTALL_ROOT), ['TunnelStudio'])
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['DisplayName'], ('Tunnel Studio', rr.REG_SZ))
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['DisplayVersion'], ('0.18.5', rr.REG_SZ))
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['UninstallString'],
                         ('"' + target('0.18.5') + r'\UninstallTunnelStudio.exe' + '"', rr.REG_SZ))
        self.assertEqual(set(report['legacy_entries_removed']), {'TunnelStudio-0.14.8', 'TunnelStudio-0.18.3', 'TunnelStudio-0.18.4'})
        self.assertEqual(report['previous_current_install'], target('0.18.4'))
        self.assertTrue(report['previous_current_install_validated'])
        self.assertFalse(report['warnings'])
        vendor = self.reg.read(rr.VENDOR_KEY)
        self.assertEqual(vendor.values['Theme'], ('dark', rr.REG_SZ))
        self.assertEqual(vendor.values['UnrelatedBinary'], (b'preserve\0bytes', 3))
        self.assertEqual(vendor.subkeys, 2)

    def test_next_upgrade_updates_same_key_and_reinstallation_is_idempotent(self):
        self.publish()
        self.publish('0.18.6')
        report = self.publish('0.18.6')
        self.assertEqual(self.reg.names(rr.UNINSTALL_ROOT), ['TunnelStudio'])
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['DisplayVersion'][0], '0.18.6')
        self.assertEqual(report['legacy_entries_removed'], [])

    def test_existing_stable_custom_values_survive_successful_upgrade(self):
        prior = program('0.18.4', stable=True)
        prior.values['CustomBinary'] = (b'preserve unchanged', 3)
        self.reg.seed(rr.STABLE_KEY, prior)
        self.publish()
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['CustomBinary'], prior.values['CustomBinary'])

    def test_unverified_legacy_entries_are_never_removed(self):
        mutations = [
            ('Publisher', ('Another author', rr.REG_SZ)),
            ('InstallLocation', (r'C:\Other\0.1.2', rr.REG_SZ)),
            ('UninstallString', ('"' + target('0.1.3') + r'\UninstallTunnelStudio.exe" /S', rr.REG_SZ)),
            ('DisplayVersion', ('9.9.9', rr.REG_SZ)),
            ('DisplayName', ('Different product', rr.REG_SZ)),
            ('InstallLocation', (target('0.1.6') + r'\..\0.1.6', rr.REG_SZ)),
            ('Publisher', (rr.PUBLISHER, 2)),
        ]
        protected = {}
        for index, (field, typed) in enumerate(mutations, 1):
            version = '0.1.' + str(index)
            entry = program(version)
            entry.values[field] = typed
            path = legacy(version)
            self.reg.seed(path, entry)
            protected[path.casefold()] = deepcopy(entry)
        child_entry = program('0.1.8'); child_entry.subkeys = 1
        self.reg.seed(legacy('0.1.8'), child_entry)
        protected[legacy('0.1.8').casefold()] = deepcopy(child_entry)
        odd = rr.UNINSTALL_ROOT + r'\TunnelStudio-0.18.4-other'
        self.reg.seed(odd, program('0.18.4'))
        protected[odd.casefold()] = self.reg.read(odd)
        self.publish()
        for path, expected in protected.items():
            self.assertEqual(self.reg.data[path], expected, path)

    def test_unverified_stable_collision_blocks_without_mutation(self):
        foreign = program('0.18.4', stable=True)
        foreign.values['Publisher'] = ('Someone else', rr.REG_SZ)
        self.reg.seed(rr.STABLE_KEY, foreign)
        before = deepcopy(self.reg.data)
        with self.assertRaisesRegex(rr.RegistrationError, 'not a verified entry'):
            self.publish()
        self.assertEqual(self.reg.data, before)

    def test_partial_stable_write_restores_full_prior_entry_and_all_legacy_rows(self):
        prior = program('0.18.4', stable=True)
        prior.values['CustomBinary'] = (b'original bytes', 3)
        self.reg.seed(rr.STABLE_KEY, prior)
        before = deepcopy(self.reg.data)
        self.reg.fail('set', rr.STABLE_KEY, 'DisplayVersion')
        with self.assertRaisesRegex(rr.RegistrationError, 'Legacy program entries were kept'):
            self.publish()
        self.assertEqual(self.reg.data, before)

    def test_failed_first_publication_removes_incomplete_stable_key(self):
        before = deepcopy(self.reg.data)
        self.reg.fail('set', rr.STABLE_KEY, 'Publisher')
        with self.assertRaises(rr.RegistrationError):
            self.publish()
        self.assertEqual(self.reg.data, before)

    def test_vendor_pointer_failure_rolls_back_successfully_written_stable_entry(self):
        self.reg.seed(rr.STABLE_KEY, program('0.18.4', stable=True))
        before = deepcopy(self.reg.data)
        self.reg.fail('set', rr.VENDOR_KEY, 'CurrentInstall')
        with self.assertRaises(rr.RegistrationError):
            self.publish()
        self.assertEqual(self.reg.data, before)

    def test_readback_mismatch_rolls_back_before_any_legacy_entry_is_removed(self):
        before = deepcopy(self.reg.data)
        def corrupt_once(path, result):
            if path == rr.STABLE_KEY and ('set', rr.VENDOR_KEY.casefold(), 'CurrentInstall') in self.reg.events:
                self.reg.after_read = None
                result.values['DisplayVersion'] = ('0.0.0', rr.REG_SZ)
            return result
        self.reg.after_read = corrupt_once
        with self.assertRaises(rr.RegistrationError):
            self.publish()
        self.assertEqual(self.reg.data, before)
        self.assertFalse(any(event[0] == 'delete_key' and event[1] == legacy('0.18.3').casefold() for event in self.reg.events))

    def test_legacy_removal_failure_is_warning_after_stable_publication(self):
        self.reg.fail('delete_key', legacy('0.18.3'))
        report = self.publish()
        self.assertTrue(report['warnings'])
        self.assertEqual(self.reg.read(rr.STABLE_KEY).values['DisplayVersion'][0], '0.18.5')
        self.assertIsNotNone(self.reg.read(legacy('0.18.3')))
        self.assertIsNone(self.reg.read(legacy('0.18.4')))

    def test_legacy_listing_failure_does_not_roll_back_new_registration(self):
        self.reg.fail('names', rr.UNINSTALL_ROOT)
        report = self.publish()
        self.assertTrue(report['warnings'])
        self.assertEqual(rr.read_current_install(BASE, registry=self.reg), target('0.18.5'))
        self.assertIsNotNone(self.reg.read(legacy('0.18.3')))

    def test_previous_pointer_validation_preserves_ambiguity_for_file_cleanup(self):
        self.assertEqual(rr.read_current_install(BASE, registry=self.reg), target('0.18.4'))
        for value in (r'C:\Other\0.18.4', target('0.18.4') + r'\..\0.18.4', 'not a path'):
            self.reg.set_values(rr.VENDOR_KEY, {'CurrentInstall': (value, rr.REG_SZ)})
            with self.assertRaisesRegex(rr.RegistrationError, 'older application folders must be preserved'):
                rr.read_current_install(BASE, registry=self.reg)
        report = self.publish()
        self.assertEqual(report['previous_current_install'], 'not a path')
        self.assertTrue(report['previous_current_install_present'])
        self.assertFalse(report['previous_current_install_validated'])

    def test_only_absent_previous_pointer_returns_none(self):
        self.reg.delete_value(rr.VENDOR_KEY, 'CurrentInstall')
        self.assertIsNone(rr.read_current_install(BASE, registry=self.reg))
        self.reg.set_values(rr.VENDOR_KEY, {'CurrentInstall': (target('0.18.4'), 2)})
        with self.assertRaises(rr.RegistrationError):
            rr.read_current_install(BASE, registry=self.reg)

    def test_owned_stable_entry_must_agree_with_present_vendor_pointer(self):
        self.reg.seed(rr.STABLE_KEY, program('0.18.4', stable=True))
        self.assertEqual(rr.read_current_install(BASE, registry=self.reg), target('0.18.4'))
        self.reg.seed(rr.STABLE_KEY, program('0.18.5', stable=True))
        before = deepcopy(self.reg.data)
        with self.assertRaisesRegex(rr.RegistrationError, 'point to different releases'):
            rr.read_current_install(BASE, registry=self.reg)
        self.assertEqual(self.reg.data, before)

    def test_owned_stable_entry_supplies_missing_pointer_without_rewriting_it(self):
        self.reg.seed(rr.STABLE_KEY, program('0.18.4', stable=True))
        self.reg.delete_value(rr.VENDOR_KEY, 'CurrentInstall')
        before = deepcopy(self.reg.data)
        self.assertEqual(rr.read_current_install(BASE, registry=self.reg), target('0.18.4'))
        self.assertEqual(self.reg.data, before)
        report = self.publish()
        self.assertFalse(report['previous_current_install_present'])
        self.assertIsNone(report['previous_current_install'])

    def test_unverified_stable_entry_blocks_active_release_resolution(self):
        foreign = program('0.18.4', stable=True)
        foreign.values['Publisher'] = ('Another author', rr.REG_SZ)
        self.reg.seed(rr.STABLE_KEY, foreign)
        before = deepcopy(self.reg.data)
        with self.assertRaisesRegex(rr.RegistrationError, 'stable Windows program entry is not a verified'):
            rr.read_current_install(BASE, registry=self.reg)
        self.assertEqual(self.reg.data, before)

    def test_valid_stable_entry_does_not_override_an_invalid_present_pointer(self):
        self.reg.seed(rr.STABLE_KEY, program('0.18.4', stable=True))
        self.reg.set_values(rr.VENDOR_KEY, {'CurrentInstall': (r'C:\Other\0.18.4', rr.REG_SZ)})
        with self.assertRaisesRegex(rr.RegistrationError, 'not a verified direct release'):
            rr.read_current_install(BASE, registry=self.reg)

    def test_previous_pointer_read_error_propagates(self):
        self.reg.fail('read', rr.VENDOR_KEY)
        with self.assertRaises(PermissionError):
            rr.read_current_install(BASE, registry=self.reg)

    def test_path_case_slashes_and_spaces_match_windows_semantics(self):
        entry = self.reg.read(legacy('0.18.3'))
        entry.values['InstallLocation'] = (target('0.18.3').upper().replace('\\', '/'), rr.REG_SZ)
        entry.values['UninstallString'] = ('"' + target('0.18.3').upper() + r'\UNINSTALLTUNNELSTUDIO.EXE"', rr.REG_SZ)
        self.reg.seed(legacy('0.18.3'), entry)
        self.publish()
        self.assertIsNone(self.reg.read(legacy('0.18.3')))

    def test_unexpected_target_is_rejected_before_registry_mutation(self):
        before = deepcopy(self.reg.data)
        for folder, version in ((r'C:\Elsewhere\0.18.5', '0.18.5'),
                                (target('0.18.5') + r'\..\0.18.5', '0.18.5'),
                                (target('0.18.5'), '0.18.6'),
                                (target('0.18.5_previous_20261030_230001_deadbeef'), '0.18.5')):
            with self.assertRaises((rr.RegistrationError, ValueError)):
                rr.register_release(BASE, folder, version, registry=self.reg)
        self.assertEqual(self.reg.data, before)


class RegistryRemovalTests(unittest.TestCase):
    def setUp(self):
        self.reg = FakeRegistry()
        self.reg.seed(rr.STABLE_KEY, program('0.18.5', stable=True))
        self.reg.seed(rr.VENDOR_KEY, rr.RegistryKey({
            'CurrentInstall': (target('0.18.5'), rr.REG_SZ),
            'Theme': ('dark', rr.REG_SZ),
        }, subkeys=1))
        self.reg.seed(legacy('0.18.4'), program('0.18.4'))

    def remove(self, version='0.18.5', folder=None):
        return rr.remove_release_registration(BASE, folder or target(version), version, registry=self.reg)

    def test_current_uninstall_removes_only_current_rows_and_pointer(self):
        self.reg.seed(legacy('0.18.5'), program('0.18.5'))
        report = self.remove()
        self.assertEqual(set(report['removed_entries']), {'TunnelStudio', 'TunnelStudio-0.18.5'})
        self.assertTrue(report['current_install_removed'])
        self.assertIsNone(self.reg.read(rr.STABLE_KEY))
        self.assertIsNotNone(self.reg.read(legacy('0.18.4')))
        self.assertEqual(self.reg.read(rr.VENDOR_KEY), rr.RegistryKey({'Theme': ('dark', rr.REG_SZ)}, subkeys=1))

    def test_old_uninstaller_never_removes_new_current_entry_or_pointer(self):
        current = self.reg.read(rr.STABLE_KEY)
        vendor = self.reg.read(rr.VENDOR_KEY)
        report = self.remove('0.18.4')
        self.assertEqual(report['removed_entries'], ['TunnelStudio-0.18.4'])
        self.assertFalse(report['current_install_removed'])
        self.assertEqual(self.reg.read(rr.STABLE_KEY), current)
        self.assertEqual(self.reg.read(rr.VENDOR_KEY), vendor)

    def test_same_version_backup_uninstaller_keeps_current_normal_release_entry(self):
        before = deepcopy(self.reg.data)
        report = self.remove(folder=target('0.18.5_previous_20261030_230001_deadbeef'))
        self.assertEqual(report['removed_entries'], [])
        self.assertFalse(report['current_install_removed'])
        self.assertEqual(self.reg.data, before)

    def test_pointer_removal_failure_restores_deleted_stable_entry_and_legacy_rows(self):
        before = deepcopy(self.reg.data)
        self.reg.fail('delete_value', rr.VENDOR_KEY, 'CurrentInstall')
        with self.assertRaisesRegex(rr.RegistrationError, 'prior registration was restored'):
            self.remove()
        self.assertEqual(self.reg.data, before)

    def test_stable_removal_failure_leaves_pointer_and_previous_entries(self):
        before = deepcopy(self.reg.data)
        self.reg.fail('delete_key', rr.STABLE_KEY)
        with self.assertRaises(rr.RegistrationError):
            self.remove()
        self.assertEqual(self.reg.data, before)

    def test_matching_legacy_removal_failure_restores_all_typed_registration_records(self):
        self.reg.seed(legacy('0.18.5'), program('0.18.5'))
        self.reg.set_values(rr.STABLE_KEY, {'OriginalBinary': (b'preserve\0stable', 3)})
        self.reg.set_values(rr.VENDOR_KEY, {'UnrelatedDword': (73, rr.REG_DWORD)})
        before = deepcopy(self.reg.data)
        self.reg.fail('delete_key', legacy('0.18.5'))
        with self.assertRaisesRegex(rr.RegistrationError, 'prior registration was restored'):
            self.remove()
        self.assertEqual(self.reg.data, before)

    def test_uninstall_readback_failure_restores_legacy_row_after_successful_deletion(self):
        self.reg.seed(legacy('0.18.5'), program('0.18.5'))
        before = deepcopy(self.reg.data)
        def bad_readback(path, result):
            if path == rr.STABLE_KEY and ('delete_key', legacy('0.18.5').casefold(), None) in self.reg.events:
                self.reg.after_read = None
                return program('0.18.5', stable=True)
            return result
        self.reg.after_read = bad_readback
        with self.assertRaises(rr.RegistrationError):
            self.remove()
        self.assertEqual(self.reg.data, before)

    def test_old_uninstaller_matching_legacy_failure_preserves_new_current_registration(self):
        before = deepcopy(self.reg.data)
        self.reg.fail('delete_key', legacy('0.18.4'))
        with self.assertRaises(rr.RegistrationError):
            self.remove('0.18.4')
        self.assertEqual(self.reg.data, before)


class WindowsAdapterTests(unittest.TestCase):
    def test_delete_is_hkcu_nonrecursive_and_uses_shared_32_bit_view_explicitly(self):
        calls = []
        module = SimpleNamespace(HKEY_CURRENT_USER='HKCU', KEY_WOW64_32KEY=0x200,
                                 DeleteKeyEx=lambda *args, **kwargs: calls.append((args, kwargs)))
        adapter = rr.WindowsRegistry(module)
        adapter.delete_key(rr.STABLE_KEY)
        self.assertEqual(calls, [(('HKCU', rr.STABLE_KEY), {'access': 0x200})])


if __name__ == '__main__':
    unittest.main(verbosity=2)
