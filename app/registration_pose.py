"""Virtual rigid poses for disk-backed scans.

Point caches remain in their original stored coordinate frame. Registration only
updates a small rigid transform in metadata; consumers that need world coordinates
apply it while streaming. This avoids rewriting multi-gigabyte XYZ caches per link.
"""
import json
import numpy as np

_R='registration_rotation'
_T='registration_translation'

def get(meta):
    R=np.asarray(meta.get(_R,np.eye(3)),dtype=float)
    t=np.asarray(meta.get(_T,[0.,0.,0.]),dtype=float)
    if R.shape!=(3,3) or t.shape!=(3,) or not np.isfinite(R).all() or not np.isfinite(t).all():
        raise ValueError('Invalid virtual registration pose.')
    return R,t

def is_identity(meta,atol=1e-14):
    R,t=get(meta)
    return np.allclose(R,np.eye(3),atol=atol,rtol=0) and np.allclose(t,0,atol=atol,rtol=0)

def set_pose(meta,R,t):
    R=np.asarray(R,dtype=float);t=np.asarray(t,dtype=float)
    if R.shape!=(3,3) or t.shape!=(3,) or not np.isfinite(R).all() or not np.isfinite(t).all():raise ValueError('Invalid registration transform.')
    meta[_R]=R.tolist();meta[_T]=t.tolist();meta['virtual_registration']=True
    return meta

def compose(meta,R,t):
    """Left-compose a world-space rigid motion onto the scan's current pose."""
    oldR,oldt=get(meta);R=np.asarray(R,float);t=np.asarray(t,float)
    return set_pose(meta,R@oldR,R@oldt+t)

def revision(meta):
    """Geometry identity used by browser caches and saved point selections.

    Keep the legacy identity for an unpositioned scan, but a virtual movement
    must invalidate consumers just as replacing its XYZ cache would.
    """
    base=str(meta.get('file',''))+'|'+str(meta.get('mask_file') or '')
    if is_identity(meta):return base
    R,t=get(meta)
    return base+'|pose:'+json.dumps([R.tolist(),t.tolist()],separators=(',',':'))

def points(meta,q):
    R,t=get(meta)
    if np.allclose(R,np.eye(3),atol=1e-14,rtol=0) and np.allclose(t,0,atol=1e-14,rtol=0):return np.asarray(q)
    return np.asarray(q)@R.T+t

def point(meta,p):
    return points(meta,np.asarray(p,float).reshape(1,3))[0]

def vector(meta,v):
    R,_=get(meta);return R@np.asarray(v,float)

def inverse_point(meta,p):
    R,t=get(meta);return (np.asarray(p,float)-t)@R

def world_bounds(meta):
    lo=np.asarray(meta['minimum'],float);hi=np.asarray(meta['maximum'],float)
    corners=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])],float)
    q=points(meta,corners);return q.min(0),q.max(0)

def local_view(meta,view):
    R,t=get(meta);out=dict(view)
    out['target']=((np.asarray(view['target'],float)-t)@R).tolist()
    for k in ('right','up','forward'):
        out[k]=(np.asarray(view[k],float)@R).tolist()
    return out
