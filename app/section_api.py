# Copyright (c) 2026 Damian Gulczynski.
"""Section comparison routes. Saved references retain the exact section frame."""
import copy,csv,io,time
import section_research

def calculate(s,args,reference=True):
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    report,points,_=s.section(args)
    threshold=float(args.get('fit_threshold',['20'])[0])
    return section_research.evaluate(report,points,threshold,s.STATE.get('section_reference') if reference else None)

def get(s,h,path,args):
    if path not in ('/api/section-research','/api/section-research-csv'):return False
    r=calculate(s,args)
    if path.endswith('-csv'):
        stream=io.StringIO();w=csv.writer(stream)
        w.writerow(['Tunnel Studio robust section comparison']);w.writerow(['reference',r['reference_kind']])
        for key,value in r['plane'].items():w.writerow([key,*value] if isinstance(value,(list,tuple)) else [key,value])
        w.writerow(['fit_threshold_mm',r['fit']['fit_threshold_mm']]);w.writerow(['fit_rms_mm',r['fit']['fit_rms_mm']])
        for key,value in r['comparison'].items():
            if key!='radial_curve':w.writerow([key,value])
        w.writerow(['crown_angle_deg','common_origin_radial_change_mm','own_centres_shape_change_mm'])
        w.writerows(r['comparison']['radial_curve'])
        h.send(stream.getvalue(),'text/csv; charset=utf-8',filename='section_comparison.csv')
    else:h.send(r)
    return True

def post(s,h,path,body):
    if path!='/api/section-reference':return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation to finish.')
    action=body.get('action');old=s.STATE.get('section_reference')
    if action=='clear':s.STATE['section_reference']=None
    elif action=='save':
        name=str(body.get('name','Baseline section')).strip()[:100]
        if not name:raise ValueError('Enter a reference name.')
        args={k:[body[k]] for k in ('station','width','band','tolerance','fit_threshold') if k in body}
        r=calculate(s,args,False)
        s.STATE['section_reference']={'name':name,'fit':copy.deepcopy(r['fit']),'plane':copy.deepcopy(r['plane']),
            'source_name':s.STATE['cloud']['name'],'saved_at':time.time(),'units':'metres',
            'wall_band_m':r['wall_band_m'],'design_radius':r['design_radius']}
    else:raise ValueError('Choose Save reference or Clear reference.')
    try:s.save()
    except Exception:s.STATE['section_reference']=old;raise
    h.send({'saved':True});return True
