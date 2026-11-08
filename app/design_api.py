# Copyright (c) 2026 Damian Gulczynski.
"""Scene-integrated design comparison, stale-input checks and downloadable results."""
from project_data_layout import folder as asset_folder
from pathlib import Path
import design_compare as compare
import job_api, jobdata, analysis_sources


def current(s):
    result=s.STATE.get('design_comparison')
    if not result:return None
    try:
        source=analysis_sources.resolve(s,result['source_id'])
        if compare.fingerprint(source['meta'])!=result['source_fingerprint']:raise ValueError('Source scan changed.')
        if result['mode']=='alignment':
            axis=s.STATE.get('axis')
            if axis is None or compare.axis_fingerprint(axis.a)!=result['axis_fingerprint']:raise ValueError('Reference alignment changed.')
            kind='scan_estimate' if s.STATE.get('axis_provenance',{}).get('kind')=='scan_estimate' else 'design_alignment'
            if result.get('reference_kind',kind)!=kind:raise ValueError('Alignment reference type changed.')
        else:
            reference=job_api.item(s,result['reference_id'])
            if compare.fingerprint(reference['meta'])!=result['reference_fingerprint']:raise ValueError('Design mesh changed.')
        p=Path(result['results_file'])
        if not p.is_file() or p.stat().st_size!=result['record_count']*compare.RECORD.itemsize:raise ValueError('Comparison data unavailable.')
    except (ValueError,OSError,KeyError) as e:
        return {**result,'stale':True,'stale_reason':str(e)+' Run Cloud vs Design again.'}
    return {**result,'stale':False}


def public(result):
    return None if not result else {k:v for k,v in result.items() if not k.endswith('_file') and not k.endswith('_fingerprint')}


def get(s,h,path,args):
    if path not in ('/api/design-comparison','/api/design-comparison-preview','/api/design-comparison.csv','/api/design-sources'):return False
    if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
    if path=='/api/design-sources':
        sources=analysis_sources.collections(s)+[x for x in s.STATE.get('job_items',[]) if x.get('kind')=='scan' and not x.get('merge_archived') and not x.get('merge_stale')]
        return h.send({'sources':[dict(id=x['id'],name=x['name'],count=x['meta']['count']) for x in sources]}) or True
    r=current(s)
    if path=='/api/design-comparison':return h.send({'result':public(r)}) or True
    if not r or r.get('stale'):raise ValueError(r.get('stale_reason') if r else 'Run Cloud vs Design first.')
    if path.endswith('-preview'):
        if args.get('format',[''])[0]=='binary':
            import display_buffers
            payload=display_buffers.cached_result(r,lambda:{**compare.preview(r,packed=True),'source_id':r['source_id'],'result_id':r['id']})
            return h.send(payload,'application/octet-stream') or True
        return h.send({**compare.preview(r),'source_id':r['source_id'],'result_id':r['id']}) or True
    out=asset_folder(s.WORK,'export')/(r['id']+'.design.csv')
    if not out.is_file():
        jobdata.room(s.WORK,r['record_count']*190);temp=out.with_suffix('.csv.tmp')
        try:compare.export_csv(r,temp);temp.replace(out)
        finally:temp.unlink(missing_ok=True)
    h.send_file(out,'text/csv; charset=utf-8','Cloud_vs_Design.csv');return True


def post(s,h,path,body):
    if path!='/api/design-compare':return False
    if s.JOB['busy']:raise ValueError('Wait for the current operation to finish.')
    def run():
        source=analysis_sources.resolve(s,body.get('source_id'))
        if source['kind']!='scan':raise ValueError('Choose a source scan.')
        mode=body.get('mode','alignment');axis=s.STATE.get('axis');reference=None
        if mode=='alignment':
            if axis is None:raise ValueError('Import the circular design alignment, or choose an imported design mesh.')
            if s.STATE.get('demo'):raise ValueError('Load your own design before comparing a scene scan; the demonstration alignment cannot be used here.')
        else:
            reference=job_api.item(s,body.get('reference_id'))
            if reference['kind']!='mesh':raise ValueError('Choose an imported design mesh.')
        previous=s.STATE.get('design_comparison')
        result=compare.run(source['meta'],s.WORK,mode,axis.a if axis is not None else None,
            reference['meta'] if reference else None,body.get('tolerance_mm',25),body.get('band_m',.5),s.progress,
            lambda:bool(getattr(s,'JOB_CANCEL_FILE',None) and s.JOB_CANCEL_FILE.exists()))
        result.update(source_id=source['id'],source_name=source['name'],reference_id=reference['id'] if reference else None,
                      reference_name=reference['name'] if reference else axis.name)
        estimated=mode=='alignment' and s.STATE.get('axis_provenance',{}).get('kind')=='scan_estimate'
        result.update(reference_kind='scan_estimate' if estimated else 'design_alignment' if mode=='alignment' else 'design_mesh',
                      reference_label='Estimated alignment' if estimated else 'Design alignment' if mode=='alignment' else 'Design mesh')
        if estimated:
            result['mode_label']='Radial deviation from estimated alignment'
            result['note']='Positive outside / negative inside the circular reference defined by the estimated alignment and its node radii. This describes variation from the scan-derived reference; it does not independently verify construction against a design. Nearest 3D centreline-segment radial deviation.'
        s.STATE['design_comparison']=result
        try:s.save()
        except Exception:
            s.STATE['design_comparison']=previous;Path(result['results_file']).unlink(missing_ok=True);raise
        s.progress(f"Cloud vs Design complete: {result['used']:,} points, {result['within_percent']:.1f}% within tolerance.")
    s.start_job(run,'analysis',native_progress=True)
    return h.send({'started':True}) or True
