# Copyright (c) 2026 Damian Gulczynski.
"""General plane/cylinder construction and full-retained-selection fitting.

Only initialization uses a bounded sample. Plane covariance, cylinder refinement,
extents and reported residuals use every selected retained finite source point.
Coordinates are float64 and shifted before numerical fitting. A temporary disk
spool avoids repeated source selection and bounds the in-memory point workspace.
Preview never changes project state; Apply checks its source/CAD context again.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import copy
import math
import tempfile
import uuid

import numpy as np

import cad_api
import cad_geometry as g
import workspace_tools
from bounded_rows import RowFile

BLOCK = 100000
INITIAL_POINTS = 16000
MAX_REFINEMENT = 25


def basis(normal):
    z=g.unit(normal,'Direction');hint=np.eye(3)[np.argmin(abs(z))]
    x=g.unit(hint-z*np.dot(hint,z));return np.column_stack((x,np.cross(z,x),z))


def stable_axis(axis):
    axis=np.asarray(axis,dtype=float)
    return axis if axis[np.argmax(np.abs(axis))]>=0 else -axis


@contextmanager
def selected_points(s,request,progress):
    """Spool exact full-density selection, retaining source revision for Apply."""
    before=workspace_tools.revision(s,request)
    with tempfile.TemporaryDirectory(prefix='geometry-fit-',dir=s.WORK) as folder:
        path=Path(folder)/'selected.xyz64';count=0;anchor=None;mean=np.zeros(3);M2=np.zeros((3,3))
        with path.open('wb') as out,closing(workspace_tools.batches(s,request,progress)) as batches:
            for points,_ in batches:
                if not len(points):continue
                if np.max(np.abs(points))>1e12:raise ValueError('Selected coordinates exceed the supported survey coordinate range.')
                if anchor is None:anchor=np.array(points[0],dtype=np.float64)
                np.asarray(points,dtype='<f8').tofile(out)
                q=points-anchor;bmean=q.mean(0);d=q-bmean;n=len(q);delta=bmean-mean;total=count+n
                M2+=d.T@d+np.outer(delta,delta)*(count*n/total);mean+=delta*(n/total);count=total
        if count<3:raise ValueError('Select at least three retained, finite measured points.')
        if workspace_tools.revision(s,request)!=before:raise ValueError('The source changed during fitting. Select the points again.')
        with RowFile(path,'<f8',count,3,BLOCK) as rows:
            yield rows,anchor+mean,M2/count,before


def chunks(points,progress,label):
    for start in range(0,len(points),BLOCK):
        progress(f'{label} · {min(len(points),start+BLOCK):,} / {len(points):,} selected points')
        yield np.asarray(points[start:start+BLOCK],dtype=np.float64)


def residual_summary(points,centre,axis,radius,progress):
    count=0;square=0.;absolute=0.;signed=0.;maximum=0.;lo=np.full(3,np.inf);hi=-lo
    B=basis(axis);angles=np.zeros(72,dtype=bool)
    for p in chunks(points,progress,'Measuring full-data fit residuals'):
        local=(p-centre)@B;lo=np.minimum(lo,local.min(0));hi=np.maximum(hi,local.max(0))
        if radius is None:res=local[:,2]
        else:
            res=np.hypot(local[:,0],local[:,1])-radius
            bins=np.floor((np.arctan2(local[:,1],local[:,0])+math.pi)/(2*math.pi)*len(angles)).astype(int)%len(angles);angles[bins]=True
        count+=len(res);square+=float(res@res);absolute+=float(np.abs(res).sum());signed+=float(res.sum());maximum=max(maximum,float(np.abs(res).max()))
    summary=dict(points=count,rms_mm=1000*math.sqrt(square/count),mean_absolute_mm=1000*absolute/count,
                 max_absolute_mm=1000*maximum,mean_signed_mm=1000*signed/count,all_selected_points=True)
    if radius is not None:
        # Circular occupancy records the observed angular span and rejects a
        # nearly planar strip masquerading as an enormous cylinder.
        occupied=np.flatnonzero(angles)
        gap=max(np.diff(np.r_[occupied,occupied[0]+len(angles)])) if len(occupied) else len(angles)
        summary['angular_coverage_degrees']=float((len(angles)-gap+1)*360/len(angles)) if len(occupied) else 0.
    return summary,lo,hi,B


def fit_plane(points,centre,covariance,progress=lambda message:None):
    eigenvalues,eigenvectors=np.linalg.eigh(covariance)
    if eigenvalues[1]<=max(1e-16,eigenvalues[2]*1e-10):
        raise ValueError('A plane needs points spread in two directions. This selection is a line or coincident points.')
    normal=stable_axis(eigenvectors[:,0]);stats,lo,hi,B=residual_summary(points,centre,normal,None,progress)
    width,height=hi[:2]-lo[:2]
    if min(width,height)<=1e-8:raise ValueError('The selected points do not define a usable plane area.')
    origin=centre+B[:,:2]@((lo[:2]+hi[:2])/2)
    spec=dict(kind='plane',origin=origin.tolist(),normal=normal.tolist(),x_direction=B[:,0].tolist(),width=float(width),height=float(height))
    stats.update(method='Orthogonal least-squares plane; all selected retained points',normal=normal.tolist(),centre=origin.tolist(),
                 eigenvalues=eigenvalues.tolist(),width_m=float(width),height_m=float(height))
    warnings=[]
    if math.sqrt(max(0.,eigenvalues[0]))>.02*min(width,height):warnings.append('The points have substantial depth relative to the plane size. Review the residuals; the selection may include more than one surface.')
    return spec,stats,warnings


def plane_from_three(points,raw):
    """Construct a finite plane from three accepted world-coordinate points."""
    points=g.points(points,3,3);first=points[1]-points[0];second=points[2]-points[0]
    lengths=np.linalg.norm([first,second],axis=1)
    cross=np.cross(first,second);area=float(np.linalg.norm(cross))
    if min(lengths)<=1e-8 or area<=max(1e-16,float(lengths.prod())*1e-8):
        raise ValueError('Three-point plane needs three distinct, non-collinear points. Pick a wider triangle.')
    x=first/lengths[0];normal=cross/area;y=np.cross(normal,x)
    local=(points-points[0])@np.column_stack((x,y));lo=local.min(0);hi=local.max(0)
    width=g.number(raw.get('width',float(hi[0]-lo[0])),'Plane width',True)
    height=g.number(raw.get('height',float(hi[1]-lo[1])),'Plane height',True)
    centre=points[0]+np.column_stack((x,y))@((lo+hi)/2)
    spec=dict(kind='plane',origin=centre.tolist(),normal=normal.tolist(),x_direction=x.tolist(),width=width,height=height)
    stats=dict(method='Plane through three accepted points',points=3,rms_mm=0.,mean_absolute_mm=0.,
               max_absolute_mm=0.,mean_signed_mm=0.,normal=normal.tolist(),centre=centre.tolist(),
               width_m=width,height_m=height,picked_points=points.tolist(),all_selected_points=False,
               original_coordinates_changed=False)
    warnings=[]
    if width<hi[0]-lo[0] or height<hi[1]-lo[1]:
        warnings.append('The chosen finite plane dimensions do not enclose all three defining points; its underlying plane still passes through them.')
    return spec,stats,warnings


def cylinder_values(q,parameters,B,derivative=False):
    u,v,alpha,beta,r=parameters
    n=B[:,2]+alpha*B[:,0]+beta*B[:,1];length=np.linalg.norm(n);axis=n/length
    centre=u*B[:,0]+v*B[:,1];w=q-centre;h=w@axis;perp=w-h[:,None]*axis
    distance=np.linalg.norm(perp,axis=1);safe=np.maximum(distance,1e-15);res=distance-r
    if not derivative:return res,centre,axis
    da=(B[:,0]-axis*np.dot(axis,B[:,0]))/length;db=(B[:,1]-axis*np.dot(axis,B[:,1]))/length
    J=np.column_stack((-(perp@B[:,0])/safe,-(perp@B[:,1])/safe,-h*(w@da)/safe,-h*(w@db)/safe,-np.ones(len(q))))
    return res,J


def cylinder_initial(sample,covariance,progress):
    from scipy.optimize import least_squares
    _,vectors=np.linalg.eigh(covariance);best=None;span=float(np.ptp(sample,axis=0).max())
    if span<=1e-8:raise ValueError('The selected points do not span a usable cylinder.')
    for k in range(3):
        progress('Finding cylinder direction · candidate '+str(k+1)+' / 3')
        B=basis(vectors[:,k]);uv=sample@B[:,:2]
        A=np.column_stack((2*uv,np.ones(len(uv))));b=np.einsum('ij,ij->i',uv,uv)
        candidate=np.linalg.lstsq(A,b,rcond=None)[0];r2=candidate[2]+candidate[:2]@candidate[:2]
        if r2<=1e-16:continue
        x=np.r_[candidate[:2],0.,0.,math.sqrt(r2)]
        if x[4]>span*20:continue
        result=least_squares(lambda p:cylinder_values(sample,p,B)[0],x,
            jac=lambda p:cylinder_values(sample,p,B,True)[1],
            bounds=([-span*100,-span*100,-20,-20,span*1e-7],[span*100,span*100,20,20,span*20]),
            max_nfev=90,xtol=1e-10,ftol=1e-10,gtol=1e-10)
        residual,c,a=cylinder_values(sample,result.x,B);cost=float(residual@residual)
        if best is None or cost<best[0]:best=(cost,c,a,float(result.x[4]))
    if best is None:raise ValueError('A cylinder could not be fitted to this selection. Select a curved surface with length along its axis.')
    return best[1:]


def cylinder_system(points,origin,parameters,B,progress,label,derivative=True):
    H=np.zeros((5,5));gradient=np.zeros(5);cost=0.
    for p in chunks(points,progress,label):
        value=cylinder_values(p-origin,parameters,B,derivative)
        if derivative:
            res,J=value;H+=J.T@J;gradient+=J.T@res
        else:res=value[0]
        cost+=float(res@res)
    return cost,H,gradient


def fit_cylinder(points,centre,covariance,progress=lambda message:None):
    if len(points)<20:raise ValueError('A cylinder fit needs at least 20 retained points spread around and along the curved surface.')
    eigenvalues=np.linalg.eigvalsh(covariance)
    if eigenvalues[1]<=max(1e-16,eigenvalues[2]*1e-8) or eigenvalues[0]<=max(1e-18,eigenvalues[2]*1e-10):
        raise ValueError('This selection is planar or linear. Select a cylinder surface with both curvature and axial length.')
    index=np.linspace(0,len(points)-1,min(INITIAL_POINTS,len(points)),dtype=np.int64)
    sample=np.asarray(points[index])-centre
    c,a,r=cylinder_initial(sample,covariance,progress)
    a=stable_axis(a);B=basis(a);origin=centre+c
    x=np.array([0.,0.,0.,0.,r]);damping=1e-5;converged=False;accepted=0
    cost,H,gradient=cylinder_system(points,origin,x,B,progress,'Refining cylinder against all selected points')
    for iteration in range(MAX_REFINEMENT):
        # A geometrically poor Hessian identifies unobservable cylinder axes
        # and prevents a nominally successful but meaningless result.
        scale=np.sqrt(np.maximum(np.diag(H),1e-30));scaled=H/np.outer(scale,scale)
        eigen=np.linalg.eigvalsh(scaled)
        if eigen[0]<=1e-10:raise ValueError('The cylinder fit is poorly constrained. Select a wider arc and a longer piece of the surface.')
        if np.linalg.norm(gradient/scale,ord=np.inf)<=max(1e-10,math.sqrt(max(cost,0.))*1e-9):converged=True;break
        success=False
        for trial in range(8):
            step=np.linalg.solve(H+np.diag(np.maximum(np.diag(H),1e-12))*damping,-gradient)
            candidate=x+step
            if candidate[4]<=1e-8 or np.linalg.norm(candidate[2:4])>10:damping*=10;continue
            candidate_cost,_,_=cylinder_system(points,origin,candidate,B,progress,f'Checking cylinder refinement {iteration+1}',False)
            if candidate_cost<=cost:
                improvement=cost-candidate_cost;old_cost=cost;x=candidate;cost=candidate_cost;damping=max(1e-12,damping/4);accepted+=1;success=True
                if np.linalg.norm(step[:2])<max(1e-10,x[4]*1e-9) and np.linalg.norm(step[2:4])<1e-9 and abs(step[4])<max(1e-10,x[4]*1e-9):converged=True
                if improvement<=max(1e-16,old_cost*1e-10):converged=True
                break
            damping*=10
        if not success:
            if np.linalg.norm(gradient/scale,ord=np.inf)<1e-7:converged=True
            break
        if converged:break
        cost,H,gradient=cylinder_system(points,origin,x,B,progress,f'Refining cylinder · pass {iteration+2}')
    if not converged:raise ValueError('The full-data cylinder fit did not converge. Select a shorter, clearer cylindrical surface and try again.')
    _,local_c,axis=cylinder_values(np.empty((0,3)),x,B);axis=stable_axis(axis);fitted_c=origin+local_c;r=float(x[4])
    stats,lo,hi,final_basis=residual_summary(points,fitted_c,axis,r,progress);height=float(hi[2]-lo[2])
    if not np.isfinite(np.r_[fitted_c,axis,r,lo,hi]).all() or not all(math.isfinite(stats[key]) for key in ('rms_mm','mean_absolute_mm','max_absolute_mm','mean_signed_mm','angular_coverage_degrees')):
        raise ValueError('The cylinder fit produced non-finite geometry or residuals. Check the selected coordinates and try a clearer surface.')
    if height<=max(1e-7,r*.02):raise ValueError('The selection has too little length along the cylinder axis. Select more of the cylinder, not a single ring.')
    if stats['angular_coverage_degrees']<45:raise ValueError('The selection covers less than 45° of the cylinder. Select a wider arc for a stable fit.')
    # A converged, supported cylinder remains useful as a reviewable best-fit
    # approximation even when the entire selected cloud is not cylindrical.
    # Keep the quality threshold explicit instead of silently relaxing it.
    rms_fraction=stats['rms_mm']/(1000*r)
    quality='poor' if rms_fraction>.05 else 'review' if rms_fraction>.01 else 'good'
    start=fitted_c+axis*lo[2];frame=dict(origin=start.tolist(),x=final_basis[:,0].tolist(),y=final_basis[:,1].tolist(),z=axis.tolist())
    spec=dict(kind='cylinder',origin=start.tolist(),radius=r,height=height,segments=96,frame=frame)
    stats.update(method='Geometric least-squares cylinder; initialization sample, full-data refinement and residuals',
                 axis=axis.tolist(),base=start.tolist(),radius_m=r,height_m=height,initialization_points=len(sample),
                 refinement_steps=accepted,converged=True,fit_quality=quality,
                 rms_to_radius_percent=100*rms_fraction,poor_fit_threshold_percent=5.)
    warnings=[]
    if quality=='poor':
        warnings.append(f'Poor cylinder fit: RMS deviation is {stats["rms_mm"]:.2f} mm ({100*rms_fraction:.2f}% of the {r:.4f} m radius), above the 5% poor-fit threshold. This best-fit approximation may not describe the tunnel lining. Inspect the residuals and dimensions before adding or using the model.')
    warnings.append('The fitted cylinder is a finite analytic model bounded by the observed axial extent. Its end caps and unobserved side are model geometry, not measured lining.')
    if stats['angular_coverage_degrees']<120:warnings.append('Only a partial arc was selected. Radius and axis are more sensitive to noise; review the residuals and dimensions.')
    if quality=='review':warnings.append('RMS deviation exceeds 1% of radius. The selection may contain roughness, nearby objects or a non-cylindrical surface.')
    return spec,stats,warnings


def preview(s,body):
    operation=body.get('operation');context=cad_api.context(s);revision=cad_api.current(s)['revision'];request=None;source_revision=None
    if operation=='plane':
        raw=body.get('plane') or {}
        if raw.get('points') is not None:
            points=g.points(raw['points'],3,3).tolist();refs=raw.get('point_sources') or []
            if refs and (not isinstance(refs,list) or len(refs)!=3):raise ValueError('Three snapped plane points need their three source records.')
            picked=[];versions={};ids=[]
            for index,ref in enumerate(refs):
                if not isinstance(ref,dict):raise ValueError('A snapped point source is invalid. Pick the point again.')
                value=cad_api.snap_cloud(s,dict(source_id=ref.get('source_id'),source_revision=ref.get('source_revision'),point=points[index]),s.progress)
                if 'index' in ref and (type(ref['index']) is not int or ref['index']!=value['index']):
                    raise ValueError('A snapped point identity changed. Pick the plane points again.')
                points[index]=value['point'];picked.append({k:value[k] for k in ('source_id','source_revision','index')})
                versions[value['source_id']]=value['source_revision']
                if value['source_id'] not in ids:ids.append(value['source_id'])
            if ids:
                request=dict(ids=ids,whole=True,versions=versions);source_revision=workspace_tools.revision(s,request)
            spec,stats,warnings=plane_from_three(points,raw)
            if picked:stats['point_sources']=picked
        else:
            spec=dict(kind='plane',**{k:raw[k] for k in ('origin','normal','width','height','x_direction') if k in raw})
            stats=dict(method='Plane defined by centre, normal and dimensions',points=0);warnings=[]
    elif operation in ('fit_plane','fit_cylinder'):
        request=copy.deepcopy(body.get('request') or {})
        with selected_points(s,request,s.progress) as (points,centre,covariance,source_revision):
            spec,stats,warnings=(fit_plane if operation=='fit_plane' else fit_cylinder)(points,centre,covariance,s.progress)
            stats.update(point_block_size=BLOCK,processing='Bounded original-point reads and disk-backed full-selection fit',
                         input_point_limit=None,original_coordinates_changed=False)
        if workspace_tools.revision(s,request)!=source_revision:raise ValueError('The selected scan changed during fitting. Preview again.')
    else:raise ValueError('Choose Create plane, Fit best plane or Fit best cylinder.')
    s.progress('Preparing editable geometry preview…');geometry=g.build(spec)
    if cad_api.context(s)!=context or cad_api.current(s)['revision']!=revision:raise ValueError('The modelling workspace changed. Preview again.')
    value=dict(token=uuid.uuid4().hex,operation=operation,geometry=geometry,statistics=stats,warnings=warnings,
               name=str(body.get('name') or {'plane':'Plane','fit_plane':'Best-fit plane','fit_cylinder':'Best-fit cylinder'}[operation]).strip()[:120],
               request=request,source_revision=source_revision,context=context,cad_revision=revision)
    s.GEOMETRY_PREVIEW=value
    return value


def apply(s,body):
    value=getattr(s,'GEOMETRY_PREVIEW',None)
    if not value or body.get('token')!=value['token']:raise ValueError('Preview these geometry settings before adding the model.')
    if cad_api.context(s)!=value['context'] or cad_api.current(s)['revision']!=value['cad_revision']:raise ValueError('The modelling workspace changed. Preview the geometry again.')
    if value['request'] and workspace_tools.revision(s,value['request'])!=value['source_revision']:raise ValueError('The selected source changed after fitting. Preview again.')
    data=copy.deepcopy(cad_api.current(s));geometry=copy.deepcopy(value['geometry'])
    # Fit evidence lives with its model. Editing a primitive is still supported;
    # the provenance explicitly describes the original fitted geometry only.
    geometry['fit_evidence']=dict(statistics=copy.deepcopy(value['statistics']),warnings=copy.deepcopy(value['warnings']),
                                  source_revision=value['source_revision'],operation=value['operation'],applies_to='Original fitted geometry before later parameter or transform edits')
    name=str(body.get('name') or value['name']).strip()[:120] or 'Geometry'
    record=cad_api.object_record(geometry,{**body,'name':name})
    if sum(len(o['vertices']) for o in data['objects'])+len(record['vertices'])>cad_api.MAX_PROJECT_VERTICES:raise ValueError('This model exceeds the editable vertex budget. Remove unneeded models or use another project.')
    data['objects'].append(record)
    if not any(x['name']==record['layer'] for x in data['layers']):data['layers'].append(dict(name=record['layer'],visible=True))
    data['last_created']=[record['id']];data['revision']=uuid.uuid4().hex
    s.progress('Saving editable geometry…');cad_api.commit(s,data)
    s.GEOMETRY_PREVIEW=None
    return dict(saved=True,id=record['id'],name=record['name'])


def public(s):
    value=getattr(s,'GEOMETRY_PREVIEW',None)
    if value and value['context']!=cad_api.context(s):value=None
    return dict(preview={k:copy.deepcopy(v) for k,v in value.items() if k not in ('request','source_revision','context','cad_revision')} if value else None,
                last_applied=getattr(s,'GEOMETRY_APPLIED',None))


def get(s,h,path,args):
    if path!='/api/geometry':return False
    h.send(public(s));return True


def post(s,h,path,body):
    if path not in ('/api/geometry-preview','/api/geometry-apply','/api/geometry-discard'):return False
    if s.JOB.get('busy'):raise ValueError('Wait for the current operation, or stop it before continuing.')
    if path=='/api/geometry-discard':
        value=getattr(s,'GEOMETRY_PREVIEW',None)
        discarded=not body.get('token') or value and body.get('token')==value['token']
        if discarded:s.GEOMETRY_PREVIEW=None
        h.send(dict(discarded=bool(discarded)));return True
    if path=='/api/geometry-preview':
        # Do not leave an older successful preview usable when a new request fails.
        s.GEOMETRY_PREVIEW=None;s.GEOMETRY_APPLIED=None
        s.start_job(lambda:preview(s,copy.deepcopy(body)),kind='modelling')
    else:
        def run():s.GEOMETRY_APPLIED=apply(s,body)
        s.start_job(run,kind='modelling')
    h.send(dict(started=True));return True
