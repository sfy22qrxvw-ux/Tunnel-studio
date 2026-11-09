"""Combine current, named release gates without counting repeated tests twice.

Run after every application owner freezes. Packaging independently checks the
finished archive after this application report has been embedded.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import platform

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
CHECKS = ROOT / 'work/checks'
VALIDATION = ROOT / 'work/validation_0_16_0'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_files(directory):
    return {path.relative_to(directory).as_posix(): path
            for path in directory.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts
            and path.suffix not in ('.pyc', '.pyo', '.log')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--performance', type=Path, required=True)
    args = parser.parse_args()
    gates = {}
    rows = {}

    baseline = read(VALIDATION / 'backend_regressions.json')
    gates['retained_and_initial_comparison_regressions'] = baseline
    # Superseded comparison results are replaced by a current-source refresh.
    for row in baseline['test_results']:
        if not row['id'].startswith(('test_inspection.', 'test_inspection_points.')):
            rows[row['id']] = {**row, 'gate': 'retained_regressions'}
    http = read(VALIDATION / 'backend_http_rerun.json')
    gates['local_http_retest'] = http
    if http['failures'] or http['errors'] or http['skips'] or http['tests_run'] != 1:
        raise RuntimeError('The actual-localhost workflow gate did not pass.')
    for row in http['test_results']:
        if row['id'] not in rows:
            raise RuntimeError('HTTP retest does not match an original case.')
        rows[row['id']] = {**row, 'gate': 'retained_regressions',
                           'retested_reason': http['reason']}
    final = read(VALIDATION / 'comparison_final.json')
    gates['final_comparison_and_full_workflow'] = final
    if final['failures'] or final['errors'] or final['skips']:
        raise RuntimeError('Final comparison/workflow gate did not pass.')
    for row in final['test_results']:
        if row['id'] in rows:
            raise RuntimeError('Unexpected duplicate final comparison test.')
        rows[row['id']] = {**row, 'gate': 'comparison_and_full_workflow'}

    for key, filename, module in (
        ('reference_geometry', 'inspection_geometry_report.json', 'test_inspection_geometry'),
        ('export_destination', 'export_destination_backend_report.json', 'test_export_destination'),
    ):
        report = read(VALIDATION / filename)
        gates[key] = report
        if report['failures'] or report['errors'] or report.get('skipped', 0):
            raise RuntimeError(key + ' did not pass.')
        if len(report['test_ids']) != report['tests']:
            raise RuntimeError(key + ' has an incomplete test inventory.')
        for raw in report['test_ids']:
            ident = raw.replace('__main__.', module + '.', 1)
            if ident in rows:
                raise RuntimeError('Duplicate backend test: ' + ident)
            rows[ident] = dict(id=ident, outcome='passed', gate=key)

    installer = read(ROOT / 'work/build_0_16_0/installer_transactions.json')
    gates['installer_transactions'] = installer
    installer_ids = []
    for node in ast.parse((ROOT / 'packaging/test_installer.py').read_text()).body:
        if isinstance(node, ast.ClassDef):
            installer_ids.extend('test_installer.' + node.name + '.' + method.name
                                 for method in node.body
                                 if isinstance(method, ast.FunctionDef) and method.name.startswith('test_'))
    if installer['failures'] or installer['errors'] or len(installer_ids) != installer['tests_passed']:
        raise RuntimeError('Installer transaction inventory did not pass.')
    for ident in installer_ids:
        if ident in rows:
            raise RuntimeError('Duplicate installer test.')
        rows[ident] = dict(id=ident, outcome='passed', gate='installer_transactions')
    if any(row['outcome'] != 'passed' for row in rows.values()):
        raise RuntimeError('At least one final backend test is not passing.')

    ui = {}
    for key, path in (
        ('cleaning_and_selection', CHECKS / 'surface_ui_regression_report.json'),
        ('cad_workspace', CHECKS / 'ui_workspace_regression_report.json'),
        ('inspection_controls', CHECKS / 'ui_inspection_report.json'),
        ('export_destination', VALIDATION / 'export_destination_ui_report.json'),
        ('actual_browser_workflow', CHECKS / 'ui_inspection_browser_report.json'),
    ):
        report = read(path)
        if report.get('passed') is not True or not report.get('checks'):
            raise RuntimeError(key + ' UI gate is incomplete.')
        if any(item.get('passed') is not True for item in report['checks']):
            raise RuntimeError(key + ' includes a failed UI case.')
        ui[key] = report

    real = read(VALIDATION / 'real_scan_inspection.json')
    if real.get('passed') is not True or not real.get('source_unchanged') or not real.get('original_xyz_unchanged'):
        raise RuntimeError('The full real-scan workflow is incomplete.')
    if len(real['steps']) != 5 or any(row.get('passed') is not True for row in real['steps']):
        raise RuntimeError('The real-scan workflow does not include all five completed stages.')
    for name, digest in real['comparison_source_sha256'].items():
        if sha(APP / name) != digest:
            raise RuntimeError('Real scan used a different comparison source: ' + name)
    performance = read(args.performance)
    if performance.get('passed') is not True or performance['source_sha256'] != sha(APP / 'inspection_points.py'):
        raise RuntimeError('Performance evidence does not match the frozen exact cloud engine.')
    if not all(row['exact_agreement'] for row in performance['final_measurement']['variants']):
        raise RuntimeError('A measured cloud reference variant changed the exact answer.')
    syntax = read(VALIDATION / 'python_syntax.json')
    if syntax['failures']:
        raise RuntimeError('Application Python syntax check failed.')
    javascript = read(VALIDATION / 'javascript_syntax.json')
    if javascript['failures']:
        raise RuntimeError('Application JavaScript syntax check failed.')
    for name, digest in javascript['source_sha256'].items():
        if sha(APP / 'web' / name) != digest:
            raise RuntimeError('JavaScript changed after its syntax gate: ' + name)
    current = source_files(APP)
    original_root = ROOT.parent / 'export_review_1025/source/TunnelStudio_0.15.0_Source/app'
    original = source_files(original_root)
    added = sorted(set(current) - set(original))
    removed = sorted(set(original) - set(current))
    changed = sorted(name for name in current.keys() & original.keys()
                     if name != 'VALIDATION_0_16_0.json' and sha(current[name]) != sha(original[name]))
    report = dict(
        release='0.16.0', date_utc='2026-10-26',
        status='Current application gates passed; finished installer extraction/hash verification is recorded separately.',
        build_host=platform.platform(),
        automated_backend_test_count=len(rows),
        automated_ui_scenario_count=sum(len(value['checks']) for value in ui.values()),
        backend_tests=list(rows.values()), backend_gates=gates, ui_gates=ui,
        real_scan=real, cloud_reference_performance=performance, python_syntax=syntax,
        javascript_syntax=javascript,
        current_source_changes=dict(added=added, changed=changed, removed=removed),
        consolidated_scope_document='RELEASE_SCOPE_0_16_0.txt',
        prior_release_scope='RELEASE_SCOPE_0_15_0.txt',
        primary_paper_method='GuSAC remains primary. GUSAC_TMATCH_METHOD_0_15_0.txt records implementation adaptations and limits.',
        test_counting='The failed sandbox socket attempt is preserved as evidence and superseded by its exact passing localhost retest. Initial comparison test results are replaced by the current-engine comparison/workflow gate. Repeated tests are counted once by canonical test ID. Browser scenarios are counted separately from backend unit/integration tests.',
        application_sha256={name: sha(path) for name, path in sorted(current.items())
                            if name != 'VALIDATION_0_16_0.json'},
        packaging_source_sha256={name: sha(path) for name, path in sorted(source_files(ROOT / 'packaging').items())},
        not_established=[
            'Native Windows installer execution, WebView2 save dialogs, physical Windows DPI scaling and GPU rendering were not run on this Linux host.',
            'The user-reported native Export cancelled issue has no Windows reproducer here. An explicit manual path route and directory-validation fix were implemented and tested.',
            'No full 550-million-point project or whole-process-tree 7 GB benchmark was available. Fixed cache/component estimates and parent RSS are not a whole-app guarantee.',
            'Known-reference comparisons of the real E57 verify execution and data identity, not independently labelled tunnel segmentation accuracy.',
            'Whole-area SMRG pipe/lining separation, a dedicated protruding-bolt detector and the complete new best-fit geometry/region-growth toolset remain outstanding.',
            'Mesh-source comparisons use area-weighted samples; reported maxima are not continuous-surface or symmetric Hausdorff guarantees.',
        ],
    )
    target = APP / 'VALIDATION_0_16_0.json'
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'report': str(target), 'backend_tests': len(rows),
                      'ui_scenarios': report['automated_ui_scenario_count'],
                      'real_points': real['steps'][0]['points'],
                      'retained_points': real['retained_points'],
                      'application_files_hashed': len(report['application_sha256'])}, indent=2))


if __name__ == '__main__':
    main()
