#!/usr/bin/env python3
"""Inspect the finished Windows installer without executing Windows code.

The caller must first extract the completed installer with 7-Zip and require a
successful exit code. This check compares the real archive inventory, extracted
bytes, frozen application and the previous release's runtime independently.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

VERSION = '0.22.6'
BASELINE_VERSION = '0.22.5'
DOCUMENTATION_EXCLUSIONS = {'preview_3d.png', 'preview_heatmap.png'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def is_transfer_copy(name):
    return bool(re.search(r'/\.[^/]+\.(?:dll|pyd|exe|zip)\.[A-Za-z0-9]{6}(?:\.[A-Za-z0-9]{6})*$|/\.rsync-tmp/[^/]+$', name, re.I))


def source_paths(root):
    return {path.relative_to(root).as_posix(): path for path in root.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts
            and path.suffix not in {'.pyc', '.log'}
            and path.relative_to(root).as_posix() not in DOCUMENTATION_EXCLUSIONS}


def pe_info(path):
    with path.open('rb') as stream:
        header = stream.read(1024 * 1024)
    if header[:2] != b'MZ':
        raise ValueError('Not a Windows executable: ' + str(path))
    pe = struct.unpack_from('<I', header, 60)[0]
    if header[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid Windows PE signature: ' + str(path))
    machine = struct.unpack_from('<H', header, pe + 4)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', header, opt)[0]
    directories = opt + (112 if magic == 0x20b else 96)
    cert_offset, cert_size = struct.unpack_from('<II', header, directories + 4 * 8)
    return {'machine': hex(machine), 'format': 'PE32+' if magic == 0x20b else 'PE32',
            'subsystem': struct.unpack_from('<H', header, opt + 68)[0],
            'has_authenticode_certificate': bool(cert_offset and cert_size),
            'expected_version_resource_found': VERSION.encode('utf-16le') in header}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--extracted', type=Path, required=True)
    parser.add_argument('--baseline-manifest', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--seven-zip', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    manifests = list(args.extracted.rglob('FILES_SHA256.json'))
    if len(manifests) != 1:
        raise RuntimeError('Expected exactly one extracted payload manifest')
    manifest_path = manifests[0]
    payload = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != VERSION:
        raise RuntimeError('Wrong manifest version')
    expected_names = set(manifest['files']) | {'FILES_SHA256.json'}
    listing = subprocess.check_output([str(args.seven_zip), 'l', '-slt', str(args.installer)], text=True)
    prefix = 'Path = ' + payload.relative_to(args.extracted).as_posix() + '/'
    archive_names = {line[len(prefix):] for line in listing.splitlines() if line.startswith(prefix)}
    # NSIS may expose its generated uninstaller at the staged destination during
    # extraction. It is created by WriteUninstaller, not a manifest File payload.
    generated_uninstaller_in_payload = 'UninstallTunnelStudio.exe' in archive_names and 'UninstallTunnelStudio.exe' not in manifest['files']
    if generated_uninstaller_in_payload:
        expected_names.add('UninstallTunnelStudio.exe')
    if archive_names != expected_names:
        raise RuntimeError('Archive inventory mismatch: missing=' + str(sorted(expected_names - archive_names))
                           + ' extra=' + str(sorted(archive_names - expected_names)))
    paths = {p.relative_to(payload).as_posix(): p for p in payload.rglob('*') if p.is_file()}
    transfer_copies = sorted(set(paths) - archive_names)
    for name in transfer_copies:
        if not is_transfer_copy(name):
            raise RuntimeError('Unexpected extracted file: ' + name)
        # Remove only confirmed nonarchive transfer artifacts, never an archive member.
        paths.pop(name).unlink(missing_ok=True)
    if set(paths) != expected_names:
        raise RuntimeError('Extracted inventory differs from manifest')
    with ThreadPoolExecutor(4) as executor:
        computed = dict(zip(paths, executor.map(digest, paths.values())))
    mismatches = [name for name, expected in manifest['files'].items() if computed[name] != expected]
    if mismatches:
        raise RuntimeError('Extracted file hash mismatch: ' + str(mismatches))

    app = args.source / 'app'
    source = source_paths(app)
    app_names = {name.removeprefix('app/') for name in paths if name.startswith('app/')}
    if app_names != set(source):
        raise RuntimeError('Frozen application inventory differs')
    for name, path in source.items():
        if computed['app/' + name] != digest(path):
            raise RuntimeError('Frozen application source differs: ' + name)
    packaging_source = source_paths(args.source / 'packaging')
    support_names = {name.removeprefix('build_support/') for name in paths if name.startswith('build_support/')}
    generated_support = {'bootstrap_files.nsh', 'payload_files.nsh'}
    if support_names - generated_support != set(packaging_source) - generated_support:
        raise RuntimeError('Packaged build support inventory differs from source')
    for name, path in packaging_source.items():
        if computed['build_support/' + name] != digest(path):
            raise RuntimeError('Packaged build support source differs: ' + name)

    baseline = json.loads(args.baseline_manifest.read_text())
    if baseline.get('version') != BASELINE_VERSION:
        raise RuntimeError('Wrong baseline release')
    old_runtime = {name: sha for name, sha in baseline['files'].items()
                   if name.startswith('runtime/') and not is_transfer_copy(name)}
    current_runtime = {name for name in paths if name.startswith('runtime/')}
    if current_runtime != set(old_runtime):
        raise RuntimeError('Runtime inventory differs from the verified baseline')
    for name, expected in old_runtime.items():
        if computed[name] != expected:
            raise RuntimeError('Inherited runtime component changed: ' + name)

    essential = [
        'app/analysis_sources.py', 'app/large_sections.py', 'app/preview_store.py', 'app/preview_index.py',
        'app/bounded_rows.py', 'app/explorer_deletion.py', 'app/joint_detection.py',
        'app/web/explorer-delete-ui.js', 'app/web/explorer-delete-ui.css',
        'app/test_explorer_deletion_0183.py', 'app/test_geometry_full_input_0183.py',
        'app/test_point_delete_batch_0183.py', 'app/test_whole_cloud_workflows_0183.py',
        'app/test_joint_range_0183.py',
        'app/test_alignment_range_0183.py', 'app/scan_geometry.py', 'app/tunnel_axis_fit.py',
        'app/RELEASE_SCOPE_0_18_0.txt', 'app/VALIDATION_0_18_0.json', 'app/CHANGELOG_0_18_0.txt',
        'app/RELEASE_SCOPE_0_18_1.txt', 'app/VALIDATION_0_18_1.json', 'app/CHANGELOG_0_18_1.txt',
        'app/RELEASE_SCOPE_0_18_2.txt', 'app/VALIDATION_0_18_2.json', 'app/CHANGELOG_0_18_2.txt',
        'app/RELEASE_SCOPE_0_18_3.txt', 'app/VALIDATION_0_18_3.json', 'app/CHANGELOG_0_18_3.txt',
        'app/RELEASE_SCOPE_0_18_4.txt', 'app/VALIDATION_0_18_4.json', 'app/CHANGELOG_0_18_4.txt',
        'app/RELEASE_SCOPE_0_18_5.txt', 'app/VALIDATION_0_18_5.json', 'app/CHANGELOG_0_18_5.txt',
        'app/RELEASE_SCOPE_0_18_6.txt', 'app/VALIDATION_0_18_6.json', 'app/CHANGELOG_0_18_6.txt',
        'app/RELEASE_SCOPE_0_18_7.txt', 'app/VALIDATION_0_18_7.json', 'app/CHANGELOG_0_18_7.txt',
        'app/test_delete_alias_0187.py', 'app/test_point_delete_fast_0187.py',
        'app/test_mask_storage_0187.py', 'app/test_project_retained_metadata_0187.py',
        'app/test_registered_snapshot_0187.py', 'app/test_point_delete_ui_0187.js',
        'app/test_analysis_counts_0187.py',
        'app/display_buffers.py', 'app/web/display-buffers.js',
        'app/test_display_buffers_0186.py', 'app/test_render_loading_0186.py',
        'app/test_inventory_loading_0186.py', 'app/test_render_ui_0186.js',
        'app/test_registration_workflow_0186.py', 'app/test_registration_transaction_0186.py',
        'app/test_registration_ui_0186.js',
        'app/ALGORITHM_REVIEW_2026_10_30.txt',
        'app/robust_registration.py', 'app/registration_review.py', 'app/registration_workspace.py',
        'app/web/scan-registration-ui.js', 'app/ROBUST_REGISTRATION_METHOD_0_17_3.txt',
        'app/RELEASE_SCOPE_0_17_4.txt', 'app/VALIDATION_0_17_4.json',
        'app/projects.py', 'app/session_start.py', 'build_support/startup_smoke.py',
        'build_support/release_registry.py', 'build_support/release_cleanup.py',
        'build_support/test_release_registry.py', 'build_support/test_release_cleanup.py',
        'build_support/test_upgrade_integration.py',
        'app/geometry_tools.py', 'app/feature_selection.py', 'app/measure_tools.py',
        'app/web/ribbon-layout.js', 'app/web/parameter-help.js',
        'app/mesh_workflows.py', 'app/mesh_sampling.py', 'app/web/mesh-results-ui.js', 'app/web/mesh-results-ui.css', 'app/MESH_METHOD_0_17_2.txt', 'app/RELEASE_SCOPE_0_17_2.txt', 'app/VALIDATION_0_17_2.json',
        'app/ui_preferences.py', 'app/scan_compaction.py', 'app/web/explorer-polish.js', 'app/web/explorer-polish.css', 'app/web/theme-ui.js', 'app/web/theme-ui.css', 'app/web/permanent-points-ui.js', 'app/web/permanent-points-ui.css',
        'app/alignment_model.py', 'app/tunnel_designer.py', 'app/web/alignment-hub.js', 'app/web/alignment-editor-ui.js', 'app/web/tunnel-designer-ui.js', 'app/web/ribbon-modern.css',
        'app/web/geometry-tools-ui.js', 'app/web/geometry-tools-ui.css',
        'app/web/feature-selection-ui.js', 'app/web/feature-selection-ui.css',
        'app/web/measure-tools-ui.js', 'app/web/measure-tools-ui.css',
        'app/inspection.py', 'app/inspection_api.py', 'app/inspection_points.py', 'app/inspection_geometry.py',
        'app/web/inspection-ui.js', 'app/web/inspection-ui.css',
        'app/gusac.py', 'app/tmatch.py', 'app/ray_box_extraction.py',
        'app/mesh_to_cloud.py', 'app/mesh_repair.py',
        'app/web/mesh-to-cloud-ui.js', 'app/web/mesh-repair-ui.js', 'app/web/file-drop-ui.js',
        'app/surface_api.py', 'app/surface_extraction.py', 'app/web/surface-ui.js',
        'app/web/job-ui.js', 'app/web/index.html', 'app/tunnel_designer.py', 'app/web/tunnel-designer-ui.js',
        'app/GUSAC_TMATCH_METHOD_0_15_0.txt', 'app/GEOMETRY_METHOD_0_17_0.txt',
        'app/METHODS_LOCAL_FEATURES_0_17_0.txt', 'app/RELEASE_SCOPE_0_17_1.txt', 'app/VALIDATION_0_17_1.json',
        'RELEASE_NOTES.txt', 'build_support/RELEASE_NOTES.txt',
        'runtime/python.exe', 'runtime/pythonw.exe', 'runtime/TunnelStudioBackend.exe', 'TunnelStudio.exe',
    ]
    missing = sorted(set(essential) - set(paths))
    if missing:
        raise RuntimeError('Missing required components: ' + str(missing))
    removed = sorted(name for name in paths if name.startswith('runtime/pdf_engine/') or name in {
        'app/pdf_drawing.py', 'app/pdf_outlines.py', 'app/web/pdf-drawing-ui.js'})
    if removed:
        raise RuntimeError('Removed PDF Drawing components are present: ' + str(removed))
    data_extensions = {'.e57', '.las', '.laz', '.pts', '.ply', '.xyz', '.xyz64',
                       '.hspca', '.tstudio', '.db', '.sqlite', '.npy', '.npz'}
    survey_payload = sorted(name for name in paths if not name.startswith('runtime/')
                            and Path(name).suffix.lower() in data_extensions)
    allowed_dirs = {'app', 'runtime', 'build_support', 'compiler_licenses'}
    unexpected_roots = {name.split('/')[0] for name in paths if '/' in name} - allowed_dirs
    if survey_payload or unexpected_roots:
        raise RuntimeError('Unexpected private data or payload directory')
    screenshots = sorted('app/' + name for name in DOCUMENTATION_EXCLUSIONS if 'app/' + name in paths)
    if screenshots:
        raise RuntimeError('Historical screenshots must not enter the release payload: ' + repr(screenshots))
    if (payload / 'RELEASE_NOTES.txt').read_bytes() != (args.source / 'packaging/RELEASE_NOTES.txt').read_bytes():
        raise RuntimeError('The packaged release notes differ from their source.')
    release_versions = [ast.literal_eval(node.value) for node in ast.parse((payload / 'app/release.py').read_text()).body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets)]
    if release_versions != [VERSION]:
        raise RuntimeError('Wrong application release version')
    installer_pe = pe_info(args.installer)
    launcher_pe = pe_info(payload / 'TunnelStudio.exe')
    if installer_pe['machine'] != '0x14c' or launcher_pe['machine'] != '0x8664':
        raise RuntimeError('Unexpected executable architecture')
    if not installer_pe['expected_version_resource_found'] or not launcher_pe['expected_version_resource_found']:
        raise RuntimeError('Wrong executable version resource')
    if launcher_pe['subsystem'] != 2:
        raise RuntimeError('Launcher is not a Windows GUI executable')
    bootstrap = args.extracted / '$PLUGINSDIR' / 'bootstrap'
    for name in ('windows_installer.py', 'release_registry.py', 'release_cleanup.py'):
        if digest(bootstrap / name) != digest(args.source / 'packaging' / name):
            raise RuntimeError('Bootstrap lifecycle helper differs from source: ' + name)
    if digest(args.extracted / '$PLUGINSDIR/windows_installer.py') != digest(args.source / 'packaging/windows_installer.py'):
        raise RuntimeError('The executed bootstrap activation helper differs from source')
    bootstrap_runtime = {name: expected for name, expected in old_runtime.items() if len(Path(name).parts) == 2}
    for name, expected in bootstrap_runtime.items():
        if digest(bootstrap / Path(name).name) != expected:
            raise RuntimeError('Bootstrap runtime differs from verified runtime: ' + name)
    uninstallers = list(args.extracted.rglob('UninstallTunnelStudio.exe'))
    if len(uninstallers) != 1:
        raise RuntimeError('Expected exactly one generated Windows uninstaller')
    uninstaller_pe = pe_info(uninstallers[0])
    if uninstaller_pe['machine'] != '0x14c' or uninstaller_pe['subsystem'] != 2:
        raise RuntimeError('Invalid generated Windows uninstaller')
    report = {
        'version': VERSION, 'installer': str(args.installer.resolve()),
        'installer_bytes': args.installer.stat().st_size, 'installer_sha256': digest(args.installer),
        'extracted_payload': str(payload.resolve()), 'verified_manifest_files': len(manifest['files']),
        'archive_missing_file_count': 0, 'archive_extra_file_count': 0,
        'extracted_missing_file_count': 0, 'extracted_extra_file_count': 0,
        'hash_mismatch_count': 0, 'hash_mismatches': [],
        'frozen_application_files_matched': len(app_names), 'frozen_application_source_checked': True,
        'frozen_application_inventory_mismatch_count': 0, 'frozen_application_hash_mismatch_count': 0,
        'packaging_source_files_matched': len(packaging_source),
        'packaging_source_inventory_mismatch_count': 0, 'packaging_source_hash_mismatch_count': 0,
        'inherited_runtime_files_unchanged': len(old_runtime), 'runtime_added_file_count': 0,
        'runtime_missing_file_count': 0, 'runtime_hash_mismatch_count': 0,
        'local_nonarchive_transfer_copies_removed': len(transfer_copies),
        'removed_pdf_drawing_components_present': removed, 'private_scan_or_project_files': survey_payload,
        'documentation_exclusions': sorted('app/' + name for name in DOCUMENTATION_EXCLUSIONS),
        'excluded_screenshots_present': screenshots,
        'required_components_present': essential, 'installer_pe': installer_pe, 'launcher_pe': launcher_pe,
        'generated_uninstaller_pe': uninstaller_pe,
        'generated_uninstaller_in_payload': generated_uninstaller_in_payload,
        'bootstrap_lifecycle_helpers_matched': 3,
        'bootstrap_runtime_files_matched': len(bootstrap_runtime),
        'native_windows_execution': False,
        'windows_component_check': 'Before activation, the installer imports the application, geometry, measurement, local feature, Open3D and CAD engines, then runs the isolated 21 MB metadata startup/reopen self-check. Any check failure retains the previous release and user workspace.',
        'installer_startup_metadata_self_check': {
            'synthetic_metadata_payload_bytes': 21_000_000,
            'phase': 'Before release activation',
            'workspace': 'A newly created temporary directory; never the user survey workspace',
            'checks': ['server.start_empty opens blank', 'previous autosave preserved byte for byte',
                       'hidden recovery reopens with complete metadata',
                       'no duplicate named previous-session project is created'],
            'native_windows_execution_on_build_host': False,
        },
        'notes': [
            'The standard 32-bit Unicode NSIS stub installs the x64 application and requires Windows x64.',
            'Native Windows installation, WebView2 and GPU execution were not run on the Linux build host.',
            'All functional runtime files match the verified previous release byte for byte.',
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
