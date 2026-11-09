"""Prepare the selected workspace scan for ring suggestions in one operation."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import copy,hashlib,json,uuid
import job_api,jobdata,scene_objects,design_compare,ring_suggestions
import scan_edits


def source(s,ident=None):
    if s.STATE.get('axis') is None:raise ValueError('Ring joints need chainage. Import an alignment, or review your scan-derived alignment and click Use for chainage.')
    if s.STATE.get('demo'):raise ValueError('Use your project alignment before suggesting joints in a real scan.')
    scans=[x for x in s.STATE.get('job_items',[]) if x['kind']=='scan']
    if ident:
        x=job_api.item(s,ident)
        if x['kind']!='scan':raise ValueError('Select a point cloud for ring joint suggestions.')
        return x
    if s.STATE.get('cloud'):
        return next((x for x in scans if x['id']==s.STATE.get('active_job_scan')),dict(id=None,name=s.STATE['cloud'].get('name','Analysis scan'),meta=s.STATE['cloud']))
    if len(scans)==1:return scans[0]
    raise ValueError('Choose one of the imported scans in the Ring joints Scan list.' if scans else 'Import a scan in Scans & models first.')


def run(s,body):
    from core import project_cloud
    job_api.sync_active(s);x=source(s,body.get('scan_id'));old=copy.deepcopy(s.STATE);selection=getattr(s,'SELECTION',None);created=[]
    try:
        m=x['meta'];axis=s.STATE['axis'];stamp=hashlib.sha256(json.dumps([axis.a.tolist(),design_compare.fingerprint(m)]).encode()).hexdigest()
        derived=s.STATE.get('derived');count=s.STATE.get('valid_points',0)
        cached=scan_edits.reusable_projection(m,axis,s.WORK)
        if cached:
            derived,count=cached
            s.progress('Reusing the verified alignment projection')
        elif not derived or not Path(derived).is_file() or Path(derived).stat().st_size!=int(m['count'])*32 or s.STATE.get('joint_projection_stamp')!=stamp:
            jobdata.room(s.WORK,m['count']*32);dest=asset_folder(s.WORK,'analysis')/(uuid.uuid4().hex+'.analysis64');created.append(dest)
            s.progress('Preparing '+x['name']+' for ring joint suggestions…')
            derived,count=project_cloud(m,axis,dest,s.progress)
            if not count:raise ValueError('No points fall within the alignment. Check that the scan and alignment share coordinates and metres.')
            scan_edits.record_projection(m,derived,count)
        if count==0:raise ValueError('No points fall within the alignment. Check that the scan and alignment share coordinates and metres.')
        result=ring_suggestions.detect(m,derived,axis,body,s.progress,clip=x.get('limit_box') if x.get('limit_hide_outside',True) else None)
        result.update(source_id=x['id'],source_name=x['name'],source_revision=scene_objects.source_revision(x))
        s.STATE.update(cloud=copy.deepcopy(m),derived=derived,valid_points=count,active_job_scan=x['id'],edits=copy.deepcopy(x.get('edit_history',[])),redo=copy.deepcopy(x.get('redo_history',[])),joints=result,joint_projection_stamp=stamp,report_ready=False,clean_ready=False,export_ready=None)
        s.SELECTION=None;s.save();s.progress(result.get('diagnostic','Ring suggestions complete.'))
    except BaseException:
        s.STATE.clear();s.STATE.update(old);s.SELECTION=selection
        for p in created:p.unlink(missing_ok=True)
        raise
