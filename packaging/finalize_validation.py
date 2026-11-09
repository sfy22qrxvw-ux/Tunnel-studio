"""Collect completed release gates without counting repeated legacy tests twice.

Run from the recovered source checkout after all application owners freeze.
The standalone report may subsequently add the final installer extraction hash.
"""
from pathlib import Path
import hashlib,json,sys

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parent
APP=ROOT/'app'
CHECKS=ROOT/'work/checks'

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):value.update(block)
    return value.hexdigest()
def files(root):return {str(p.relative_to(root)):p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
def assert_passed(value,name):
    if value.get('failures',0) or value.get('errors',0) or value.get('passed') is False:raise RuntimeError('Failed release gate: '+name)
    if any(isinstance(c,dict) and c.get('passed') is False for c in value.get('checks',[])):raise RuntimeError('Failed release check: '+name)
def main():
    report=read(APP/'VALIDATION_0_15_0.json')
    backend={
      'final_combined_regressions':read(CHECKS/'final_backend_regression_report.json'),
      'gusac_synthetic':read(APP/'work/checks/gusac_synthetic_report.json'),
      'tmatch_synthetic':read(CHECKS/'tmatch_synthetic_report.json'),
      'paper_production_api':read(CHECKS/'paper_workflow_report.json'),
      'ray_box':read(CHECKS/'ray_box_report.json'),
      'mesh_to_cloud':read(CHECKS/'mesh_to_cloud_report.json'),
      'project_compatibility':read(CHECKS/'project_compatibility_report.json'),
      'designer':read(CHECKS/'designer_consolidated_report.json'),
      'mesh_repair':read(CHECKS/'mesh_repair_report.json'),
      'installer_transactions':read(CHECKS/'installer_transactions_report.json'),
    }
    ui={
      'paper_classification':read(CHECKS/'ui_paper_methods_results.json'),
      'legacy_surface':read(ROOT/'recovered/source/tests/ui/surface_ui_0_13_8_results.json'),
      'brush_and_file_drop':read(CHECKS/'ui_brush_drop_report.json'),
      'ray_box':read(CHECKS/'ui_ray_box_report.json'),
      'mesh_to_cloud':read(CHECKS/'ui_mesh_to_cloud_report.json'),
      'project_compatibility':read(CHECKS/'ui_project_compatibility_report.json'),
      'mesh_repair':backend['mesh_repair']['ui'],
    }
    for group in (backend,ui):
        for name,value in group.items():assert_passed(value,name)
    assert_passed(backend['designer']['backend'],'designer backend')
    assert_passed(backend['mesh_repair']['backend'],'mesh repair backend')
    if not all(x.get('passed') for x in backend['designer']['ui']):raise RuntimeError('Designer UI failed')
    # The six existing job tests in the mesh sampling report are already in the
    # final combined gate. Standalone repeated streaming tests are evidence only.
    bc={
      'final_combined_regressions':backend['final_combined_regressions']['tests_run'],
      'gusac_synthetic':backend['gusac_synthetic']['tests_run'],
      'tmatch_synthetic':backend['tmatch_synthetic']['test_count'],
      'paper_production_api':backend['paper_production_api']['tests_run'],
      'ray_box':len(backend['ray_box']['checks']),
      'mesh_to_cloud_new':backend['mesh_to_cloud']['new_sampling_checks'],
      'project_compatibility':backend['project_compatibility']['tests'],
      'designer':backend['designer']['backend']['tests'],
      'mesh_repair':backend['mesh_repair']['backend']['tests'],
      'installer_transactions':backend['installer_transactions']['tests_run'],
    }
    if not isinstance(bc['mesh_repair'],int):raise RuntimeError('Read the mesh repair report count before finalizing')
    uc={name:len(value['checks']) if 'checks' in value else value['scenarios'] for name,value in ui.items()};uc['designer']=len(backend['designer']['ui'])
    report.update(status='Consolidated application release gates passed. Installer extraction and payload hashes are recorded in the standalone delivered report.',
      automated_backend_test_count=sum(bc.values()),automated_ui_scenario_count=sum(uc.values()),
      automated_test_count=sum(bc.values())+sum(uc.values()),automated_test_counts={'backend':bc,'ui':uc},
      automated_tests={'backend':backend,'ui':ui},
      repeated_checks_not_double_counted={'mesh_sampling_existing_job_regressions':6,'source_streaming_output_pts_brush_native_bridge':'Included in the 77-test combined regression gate.'},
      consolidated_scope_document='RELEASE_SCOPE_0_15_0.txt')
    report['data_integrity']['legacy_tests_modified']=True
    report['data_integrity']['legacy_test_change_explanations']=backend['final_combined_regressions']['test_changes']
    current=files(APP)
    origins={
      '20_october_working_copy':WORKSPACE/'scope_updates/base_20oct/app',
      '24_october_0_14_11_update':WORKSPACE/'scope_updates/v01411/Tunnel_Studio_0_14_11_Consolidated_Update/payload/app',
    }
    preservation={}
    for name,folder in origins.items():
        prior=files(folder);common=prior.keys()&current.keys()
        changed=sorted(k for k in common if digest(prior[k])!=digest(current[k]))
        preservation[name]={'source_files':len(prior),'unchanged_files':len(common)-len(changed),'changed_files':changed,'not_active_in_current_app':sorted(prior.keys()-current.keys())}
    preservation['intentional_removals']={'pdf_drawing.py':'Later explicit decision removes PDF Drawing.','pdf_outlines.py':'PDF Drawing-specific implementation removed.','web/pdf-drawing-ui.js':'PDF Drawing UI removed.'}
    preservation['superseded_01411_files']={'gusac_tmatch.py':'Superseded by independently validated gusac.py/tmatch.py; historical source retained under recovered/later_updates.','test_gusac_tmatch.py':'Superseded by current GuSAC/TMatch/API test gates; retained as historical source.','historical_release_notes':'Copied under recovered/later_updates/0_14_11; current scope and validation have precedence.'}
    report['data_integrity']['old_work_preservation']=preservation
    report['data_integrity'].update(project_open_transaction_and_recovery_tested=True,missing_current_masks_are_not_reset=True,generated_mesh_samples_identified=True,inferred_mesh_faces_identified=True)
    ray=read(ROOT/'work/validation_0_15_0/ray_box_real_scan.json');ray['case']='ray_box_4m';ray['independent_ground_truth']=False;ray['accuracy_measured']=False
    report['real_scan_execution']=[x for x in report['real_scan_execution'] if x.get('case')!='ray_box_4m']+[ray]
    report['not_established']=[x for x in report['not_established'] if 'reported failing' not in x]+['The user reported failing project and mesh files were not supplied; generated compatibility and repair fixtures do not reproduce those exact files.','No 25 GB PTS import speed benchmark or physical File Explorer drag/drop run. Native renderer rewrite and TriMe/local-chart mesh engines remain proposals.']
    # The report cannot hash itself. The final installer manifest covers it.
    report['application_sha256']={name:digest(path) for name,path in sorted(current.items()) if name!='VALIDATION_0_15_0.json'}
    report['application_hash_scope']='All application files excluding this self-referential report and transient Python bytecode. Installer FILES_SHA256.json covers the packaged report as well.'
    report['packaging_source_sha256']={name:digest(path) for name,path in sorted(files(ROOT/'packaging').items())}
    encoded=json.dumps(report,indent=2,allow_nan=False)+'\n'
    (APP/'VALIDATION_0_15_0.json').write_text(encoded,encoding='utf-8')
    out=WORKSPACE/'output';out.mkdir(exist_ok=True);(out/'TunnelStudio_0.15.0_Validation.json').write_text(encoded,encoding='utf-8')
    (out/'TunnelStudio_0.15.0_Release_Scope.txt').write_bytes((APP/'RELEASE_SCOPE_0_15_0.txt').read_bytes())
    print(json.dumps({'backend_tests':sum(bc.values()),'ui_scenarios':sum(uc.values()),'application_files_hashed':len(report['application_sha256']),'failed_gates':0},indent=2))
if __name__=='__main__':main()
