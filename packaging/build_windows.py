#!/usr/bin/env python3
"""Build the full Windows installer from a frozen application and staged runtime.

This build host uses a relocatable MinGW/NSIS toolchain. The runtime is recovered
unchanged from the prior installer. No scan or project files are part of the
installer. PDF Drawing and its separate PDFium runtime are deliberately absent.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

VERSION = '0.23.4'
BASELINE_VERSION = '0.23.3'
SOURCE = Path(__file__).resolve().parents[1]
# Historical screenshots are retained in the working tree, but are not product
# assets and must not enter the application installer or release source archive.
DOCUMENTATION_EXCLUSIONS = {'preview_3d.png', 'preview_heatmap.png'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, cwd, env):
    print('Running:', command[0], flush=True)
    subprocess.run([str(x) for x in command], cwd=cwd, env=env, check=True)


def transfer_copy(name):
    return bool(re.search(r'/\.[^/]+\.(?:dll|pyd|exe|zip)\.[A-Za-z0-9]{6}(?:\.[A-Za-z0-9]{6})*$|/\.rsync-tmp/[^/]+$', name, re.I))


def source_files(root, prefix):
    return {prefix + p.relative_to(root).as_posix() for p in root.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.log'}
            and p.relative_to(root).as_posix() not in DOCUMENTATION_EXCLUSIONS}


def nsis_escape(text):
    return str(text).replace('$', '$$').replace('"', '$\\"')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True,
                        help='Root of relocated Ubuntu compiler/NSIS packages')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-manifest', type=Path, required=True)
    args = parser.parse_args()
    stage, toolroot, output = args.stage.resolve(), args.tools.resolve(), args.output.resolve()
    payload = stage / 'payload'
    runtime = payload / 'runtime'
    baseline_manifest = json.loads(args.baseline_manifest.read_text())
    if baseline_manifest.get('version') != BASELINE_VERSION:
        raise SystemExit('The runtime baseline must be the verified ' + BASELINE_VERSION + ' release.')
    baseline = baseline_manifest['files']
    omitted = sorted(name for name in baseline if name.startswith('runtime/') and transfer_copy(name))
    runtime_hashes = {name: sha for name, sha in baseline.items()
                      if name.startswith('runtime/') and name not in omitted}
    for name, expected in runtime_hashes.items():
        if not (payload / name).is_file() or digest(payload / name) != expected:
            raise SystemExit('Baseline runtime verification failed: ' + name)
    runtime_names = set(runtime_hashes)
    # Delete staging-only transfer remnants. The closed NSIS file list below also
    # prevents any later filesystem transfer remnants from entering the installer.
    for path in runtime.rglob('*'):
        if path.is_file() and path.relative_to(payload).as_posix() not in runtime_names:
            path.unlink()
    for relative in ['python.exe', 'pythonw.exe', 'python312.dll', 'python312.zip',
                     'Lib/site-packages/numpy/__init__.py',
                     'Lib/site-packages/scipy/__init__.py']:
        if not (runtime / relative).is_file():
            raise SystemExit('Missing runtime component: ' + relative)
    release = {}
    exec((SOURCE / 'app/release.py').read_text(), release)
    if release['VERSION'] != VERSION:
        raise SystemExit('The application release version has not been frozen.')
    for name in ['gusac.py', 'tmatch.py', 'ray_box_extraction.py', 'mesh_to_cloud.py', 'mesh_repair.py',
                 'analysis_sources.py', 'large_sections.py', 'preview_store.py', 'preview_index.py',
                 'bounded_rows.py', 'explorer_deletion.py', 'joint_detection.py',
                 'web/explorer-delete-ui.js', 'web/explorer-delete-ui.css',
                 'test_explorer_deletion_0183.py', 'test_geometry_full_input_0183.py',
                 'test_point_delete_batch_0183.py', 'test_whole_cloud_workflows_0183.py',
                 'test_joint_range_0183.py',
                 'test_alignment_range_0183.py', 'scan_geometry.py', 'tunnel_axis_fit.py',
                 'RELEASE_SCOPE_0_18_0.txt', 'VALIDATION_0_18_0.json', 'CHANGELOG_0_18_0.txt',
                 'RELEASE_SCOPE_0_18_1.txt', 'VALIDATION_0_18_1.json', 'CHANGELOG_0_18_1.txt',
                 'RELEASE_SCOPE_0_18_2.txt', 'VALIDATION_0_18_2.json', 'CHANGELOG_0_18_2.txt',
                 'RELEASE_SCOPE_0_18_3.txt', 'VALIDATION_0_18_3.json', 'CHANGELOG_0_18_3.txt',
                 'RELEASE_SCOPE_0_18_4.txt', 'VALIDATION_0_18_4.json', 'CHANGELOG_0_18_4.txt',
                 'RELEASE_SCOPE_0_18_5.txt', 'VALIDATION_0_18_5.json', 'CHANGELOG_0_18_5.txt',
                 'RELEASE_SCOPE_0_18_6.txt', 'VALIDATION_0_18_6.json', 'CHANGELOG_0_18_6.txt',
                 'RELEASE_SCOPE_0_18_7.txt', 'VALIDATION_0_18_7.json', 'CHANGELOG_0_18_7.txt',
                 'RELEASE_SCOPE_0_18_8.txt', 'VALIDATION_0_18_8.json', 'CHANGELOG_0_18_8.txt',
                 'cloud_registration.py', 'cloud_registration_workflow.py', 'rtc360_vis_probe.py', 'rtc360_vis_pose.py', 'rtc360_vis_workflow.py',
                 'RELEASE_SCOPE_0_18_9.txt', 'VALIDATION_0_18_9.json', 'CHANGELOG_0_18_9.txt',
                 'RELEASE_SCOPE_0_19_0.txt', 'VALIDATION_0_19_0.json', 'CHANGELOG_0_19_0.txt',
                 'RELEASE_SCOPE_0_20_0.txt', 'VALIDATION_0_20_0.json', 'CHANGELOG_0_20_0.txt',
                 'RELEASE_SCOPE_0_21_0.txt', 'VALIDATION_0_21_0.json', 'CHANGELOG_0_21_0.txt',
                 'RELEASE_SCOPE_0_21_3.txt', 'VALIDATION_0_21_3.json', 'CHANGELOG_0_21_3.txt',
                 'RELEASE_SCOPE_0_21_4.txt', 'VALIDATION_0_21_4.json', 'CHANGELOG_0_21_4.txt',
                 'RELEASE_SCOPE_0_21_5.txt', 'VALIDATION_0_21_5.json', 'CHANGELOG_0_21_5.txt',
                 'RELEASE_SCOPE_0_21_6.txt', 'VALIDATION_0_21_6.json', 'CHANGELOG_0_21_6.txt',
                 'RELEASE_SCOPE_0_21_7.txt', 'VALIDATION_0_21_7.json', 'CHANGELOG_0_21_7.txt',
                 'RELEASE_SCOPE_0_22_0.txt', 'VALIDATION_0_22_0.json', 'CHANGELOG_0_22_0.txt',
                 'shape_lining.py', 'test_shape_reference_0220.py', 'web/workspace-modern.css',
                 'window_chrome.py','test_window_chrome_0223.py','web/window-chrome.js','web/window-chrome.css',
                 'appearance_palettes.py','native_progress.py','test_shape_region_review_0226.py','test_progress_appearance_0225.py','web/tunnel.ico',
                 'RELEASE_SCOPE_0_22_3.txt','VALIDATION_0_22_3.json','CHANGELOG_0_22_3.txt',
                 'leica_catalog.py','test_leica_catalog_0222.py','leica_scan_preview.py','test_leica_scan_preview_0225.py',
                 'RELEASE_SCOPE_0_22_2.txt','VALIDATION_0_22_2.json','CHANGELOG_0_22_2.txt',
                 'project_browser.py','test_project_browser_0221.py','test_selected_merge_0221.py',
                 'web/project-browser-ui.js','web/project-browser.css','web/selected-merge-ui.js',
                 'RELEASE_SCOPE_0_22_1.txt','VALIDATION_0_22_1.json','CHANGELOG_0_22_1.txt',
                 'shape_inspection.py', 'scan_appearance.py', 'test_shape_inspection_0217.py', 'test_scan_appearance_0217.py', 'web/scan-appearance-ui.js',
                 'compact_shapes.py', 'shape_examples.py', 'shape_library.py', 'shape_library_api.py', 'shape_separated.py', 'test_shape_library_0216.py',
                 'bolt_gaps.py', 'segment_panels.py', 'section_fitting.py', 'section_epochs.py', 'tunnel_volume.py',
                 'web/segment-review.js', 'web/project-save-location.js',
                 'section_circle.py', 'test_section_circle_0215.py', 'web/circle-clean-ui.js', 'shape_matching.py', 'shape_selection.py', 'test_shape_matching_0214.py', 'web/shape-selection-ui.js', 'test_bundle_independence_0212.py', 'deleted_points.py', 'test_deleted_points_0213.py', 'lining_departures.py', 'test_lining_departures_0213.py', 'storage_catalog.py', 'storage_inventory.py', 'storage_cleanup.py', 'workspace_folders.py', 'project_folder_copy.py', 'project_shapefiles.py', 'desktop_exit.py',
                 'web/storage-inventory-ui.js', 'web/workspace-folders.js', 'web/storage-cleanup-ui.js', 'web/desktop-close.js',
                 'web/project-folder-copy.js', 'web/project-shapefiles.js',
                 'parallel_meshing.py',
                 'bundle_adjustment.py', 'bundle_workflow.py', 'compute_runtime.py', 'tunnel_monitoring.py', 'monitoring_graphics.py', 'rtc360_names.py',
                 'web/bundle-adjustment-ui.js', 'web/tunnel-monitoring-ui.js', 'web/analysis-workspaces.css',
                 'visual_registration.py', 'web/visual-registration-ui.js', 'web/visual-registration-ui.css',
                 'test_delete_alias_0187.py', 'test_point_delete_fast_0187.py',
                 'test_mask_storage_0187.py', 'test_project_retained_metadata_0187.py',
                 'test_registered_snapshot_0187.py', 'test_point_delete_ui_0187.js',
                 'test_analysis_counts_0187.py',
                 'display_buffers.py', 'web/display-buffers.js',
                 'test_display_buffers_0186.py', 'test_render_loading_0186.py',
                 'test_inventory_loading_0186.py', 'test_render_ui_0186.js',
                 'test_registration_workflow_0186.py', 'test_registration_transaction_0186.py',
                 'test_registration_ui_0186.js',
                 'ALGORITHM_REVIEW_2026_10_30.txt',
                 'robust_registration.py', 'registration_review.py', 'registration_workspace.py',
                 'web/scan-registration-ui.js', 'ROBUST_REGISTRATION_METHOD_0_17_3.txt',
                 'RELEASE_SCOPE_0_17_4.txt', 'VALIDATION_0_17_4.json',
                 'inspection.py', 'inspection_api.py', 'inspection_points.py', 'inspection_geometry.py',
                 'geometry_tools.py', 'feature_selection.py', 'measure_tools.py',
                 'web/ribbon-layout.js', 'web/parameter-help.js',
        'mesh_workflows.py', 'mesh_sampling.py', 'web/mesh-results-ui.js', 'web/mesh-results-ui.css', 'MESH_METHOD_0_17_2.txt', 'RELEASE_SCOPE_0_17_2.txt', 'VALIDATION_0_17_2.json',
                 'ui_preferences.py', 'scan_compaction.py', 'web/explorer-polish.js', 'web/explorer-polish.css', 'web/theme-ui.js', 'web/theme-ui.css', 'web/permanent-points-ui.js', 'web/permanent-points-ui.css',
                 'alignment_model.py', 'tunnel_designer.py', 'web/alignment-hub.js', 'web/alignment-editor-ui.js', 'web/tunnel-designer-ui.js', 'web/ribbon-modern.css',
                 'web/geometry-tools-ui.js', 'web/geometry-tools-ui.css',
                 'web/feature-selection-ui.js', 'web/feature-selection-ui.css',
                 'web/measure-tools-ui.js', 'web/measure-tools-ui.css',
                 'web/inspection-ui.js', 'web/inspection-ui.css',
                 'web/mesh-to-cloud-ui.js', 'web/mesh-repair-ui.js', 'web/file-drop-ui.js',
                 'GUSAC_TMATCH_METHOD_0_15_0.txt', 'METHOD_RAY_BOX_0_14_10.txt',
                 'MESH_TO_CLOUD_0_15_0.txt', 'GEOMETRY_METHOD_0_17_0.txt',
                 'METHODS_LOCAL_FEATURES_0_17_0.txt', 'RELEASE_SCOPE_0_17_1.txt', 'VALIDATION_0_17_1.json']:
        if not (SOURCE / 'app' / name).is_file():
            raise SystemExit('Missing frozen release artifact: ' + name)
    notes = SOURCE / 'packaging/RELEASE_NOTES.txt'
    helper_names = ['windows_installer.py', 'release_registry.py', 'release_cleanup.py']
    for name in helper_names + ['test_release_registry.py', 'test_release_cleanup.py', 'test_upgrade_integration.py']:
        if not (SOURCE / 'packaging' / name).is_file():
            raise SystemExit('Missing installer lifecycle module: ' + name)
    if not notes.is_file() or not notes.read_text().startswith('Tunnel Studio ' + VERSION + '\n'):
        raise SystemExit('Release notes are missing or have the wrong version.')
    for name in ['pdf_drawing.py', 'pdf_outlines.py', 'web/pdf-drawing-ui.js']:
        if (SOURCE / 'app' / name).exists():
            raise SystemExit('The removed PDF Drawing feature is still present: ' + name)
    if (payload / 'app').exists():
        shutil.rmtree(payload / 'app')
    shutil.copytree(SOURCE / 'app', payload / 'app',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log', *DOCUMENTATION_EXCLUSIONS))
    support = payload / 'build_support'
    if support.exists():
        shutil.rmtree(support)
    shutil.copytree(SOURCE / 'packaging', support,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log'))
    for name in ['TunnelStudio.nsi', 'windows_installer.py', 'launcher.c', 'launcher.rc']:
        shutil.copy2(support / name, stage / name)
    bootstrap = stage / 'bootstrap'
    if bootstrap.exists():
        shutil.rmtree(bootstrap)
    bootstrap.mkdir()
    bootstrap_names = sorted(Path(name).name for name in runtime_hashes if len(Path(name).parts) == 2)
    for name in bootstrap_names:
        shutil.copy2(runtime / name, bootstrap / name)
    # Activation, registry migration and program cleanup use only the standard
    # library. Bundle helpers beside the isolated bootstrap interpreter.
    for name in helper_names:
        shutil.copy2(support / name, bootstrap / name)
        bootstrap_names.append(name)
    bootstrap_include = '\n'.join('File "${BOOTSTRAP}/' + nsis_escape(name) + '"' for name in bootstrap_names) + '\n'
    (stage / 'bootstrap_files.nsh').write_text(bootstrap_include)
    (support / 'bootstrap_files.nsh').write_text(bootstrap_include)
    binpath = toolroot / 'usr/bin'
    compiler = binpath / 'x86_64-w64-mingw32-gcc-posix'
    alias = binpath / 'x86_64-w64-mingw32-gcc'
    if not alias.exists():
        alias.symlink_to('x86_64-w64-mingw32-gcc-posix')
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PATH'] = str(binpath) + os.pathsep + env.get('PATH', '')
    env['NSISDIR'] = str(toolroot / 'usr/share/nsis')
    run([binpath / 'x86_64-w64-mingw32-windres', 'launcher.rc', '-O', 'coff',
         '-o', 'launcher_res.o'], stage, env)
    run([compiler, '-municode', '-mwindows', '-Os', '-s', '-static-libgcc',
         'launcher.c', 'launcher_res.o', '-o', 'payload/TunnelStudio.exe', '-luser32'],
        stage, env)
    # A fresh stage only needs the runtime. Copy required licence documents
    # from the verified baseline instead of relying on leftover staging files.
    notices = {'COPYRIGHT.txt', 'THIRD_PARTY_NOTICES.txt'} | {
        name for name in baseline if name.startswith('compiler_licenses/')}
    for name in notices:
        original = args.baseline_manifest.parent / name
        if name not in baseline or not original.is_file() or digest(original) != baseline[name]:
            raise SystemExit('Baseline licence verification failed: ' + name)
        (payload / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, payload / name)
    # Regenerate metadata from the verified preceding runtime; the separate
    # prerelease PDF Drawing engine is no longer a release dependency.
    shutil.copy2(args.baseline_manifest.parent / 'DEPENDENCIES.json', payload / 'DEPENDENCIES.json')
    # Record every bundled package.
    packages = []
    for name in sorted(runtime_names):
        if not name.endswith('.dist-info/METADATA'):
            continue
        path = payload / name
        meta = email.message_from_string(path.read_text(errors='replace'))
        packages.append({'name': meta.get('Name'), 'version': meta.get('Version'),
                         'location': path.parent.relative_to(payload).as_posix()})
    (payload / 'DEPENDENCIES_INSTALLED.json').write_text(json.dumps({
        'python': '3.12.10, recovered Windows embedded runtime',
        'packages': packages,
        'native_windows_execution_on_build_host': False,
        'installation_component_check': 'Executed on Windows by the installer before activation',
    }, indent=2) + '\n')
    shutil.copy2(notes, payload / 'RELEASE_NOTES.txt')
    names = runtime_names | source_files(SOURCE / 'app', 'app/') | source_files(SOURCE / 'packaging', 'build_support/')
    names |= {name for name in baseline if name.startswith('compiler_licenses/')}
    names |= {'RELEASE_NOTES.txt', 'DEPENDENCIES_INSTALLED.json', 'DEPENDENCIES.json',
              'THIRD_PARTY_NOTICES.txt', 'COPYRIGHT.txt', 'TunnelStudio.exe',
              'build_support/bootstrap_files.nsh', 'build_support/payload_files.nsh'}
    data_extensions = {'.e57', '.las', '.laz', '.pts', '.ply', '.xyz', '.xyz64',
                       '.hspca', '.tstudio', '.db', '.sqlite', '.npy', '.npz'}
    private_data = sorted(name for name in names if not name.startswith('runtime/')
                          and Path(name).suffix.lower() in data_extensions)
    if private_data:
        raise SystemExit('Survey data must not enter the release payload: ' + repr(private_data))
    lines, previous_parent = [], None
    for name in sorted(names | {'FILES_SHA256.json'}):
        parent = str(Path(name).parent).replace('/', '\\')
        if parent != previous_parent:
            folder = '$Stage' + ('\\' + nsis_escape(parent) if parent != '.' else '')
            lines.append('SetOutPath "' + folder + '"')
            previous_parent = parent
        lines.append('File "${PAYLOAD}/' + nsis_escape(name) + '"')
    include = '\n'.join(lines) + '\n'
    (stage / 'payload_files.nsh').write_text(include)
    (support / 'payload_files.nsh').write_text(include)
    files = {name: digest(payload / name) for name in sorted(names)}
    manifest = {'version': VERSION, 'files': files}
    (payload / 'FILES_SHA256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    run([sys.executable, '-m', 'unittest', '-v', 'test_installer', 'test_component_diagnostics',
         'test_release_registry', 'test_release_cleanup', 'test_upgrade_integration'], support, env)
    bootstrap_probe = ('import runpy,sys; from pathlib import Path; '
                       'ns=runpy.run_path(sys.argv[1]); mods=ns["_lifecycle_modules"](); '
                       'assert all(Path(m.__file__).resolve().parent == Path(sys.argv[1]).resolve().parent for m in mods); '
                       'print("Isolated bootstrap lifecycle imports verified", flush=True)')
    run([sys.executable, '-I', '-S', '-B', '-c', bootstrap_probe,
         bootstrap / 'windows_installer.py'], stage, env)
    # Execute the production command against the actual staged app and support
    # files. This catches application/self-check drift that mocked activation
    # tests cannot detect. The Windows installer still repeats the check using
    # its own bundled Windows Python before activation on the user's computer.
    from windows_installer import smoke_check, verify
    startup_output = smoke_check(payload, interpreter=sys.executable)
    startup_gate = {'passed': True, 'platform': sys.platform,
                    'interpreter': sys.executable,
                    'native_windows_bundled_runtime_execution': False,
                    'production_component_command_executed': True,
                    'staged_application_and_support_used': True,
                    'output': startup_output}
    print(startup_output, flush=True)
    # Guard generated build-support files too: the independent archive verifier
    # must never be the first place that changed staging bytes are detected.
    verify(payload)
    run([binpath / 'makensis', '-V2', 'TunnelStudio.nsi'], stage, env)
    verify(payload)
    result = stage / ('TunnelStudio_Setup_' + VERSION + '_x64.exe')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / result.name
    shutil.copy2(result, destination)
    report = {'version': VERSION, 'installer': str(destination),
              'installer_bytes': destination.stat().st_size,
              'installer_sha256': digest(destination),
              'manifest_files': len(files), 'app_files': len([x for x in files if x.startswith('app/')]),
              'native_windows_execution': False,
              'payload_source_match_checked': True,
              'build_host_startup_gate': startup_gate,
              'isolated_bootstrap_helper_imports_passed': True,
              'documentation_exclusions': sorted('app/' + name for name in DOCUMENTATION_EXCLUSIONS),
              'baseline_runtime_files_preserved': len(runtime_hashes),
              'unused_transfer_copies_omitted_from_old_manifest': len(omitted)}
    for relative, expected in files.items():
        if relative.startswith('app/') and digest(SOURCE / relative) != expected:
            raise RuntimeError('Application source changed during packaging: ' + relative)
    (output / ('INSTALLER_VALIDATION_' + VERSION.replace('.', '_') + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
