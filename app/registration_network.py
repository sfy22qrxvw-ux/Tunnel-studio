"""Scan motion constraints and compatibility entry point for bundle adjustment."""
import hashlib,json
import numpy as np
from scipy.spatial.transform import Rotation
import registration,registration_workspace as rw

def edge_mode(edge, fallback='rigid'):
    return edge.get('mode') or (edge.get('solution') or {}).get('mode') or fallback


def _basis(axis):
    axis=np.asarray(axis,float)
    if axis.shape!=(3,) or not np.isfinite(axis).all() or np.linalg.norm(axis)<1e-12:
        raise ValueError('A stored keep-vertical direction is invalid. Re-register that link.')
    axis=axis/np.linalg.norm(axis)
    seed=np.eye(3)[int(np.argmin(np.abs(axis)))]
    u=np.cross(axis,seed);u/=np.linalg.norm(u)
    return np.column_stack((u,np.cross(axis,u),axis))


def _edge_solution(edge, rows, tolerance):
    mode=edge_mode(edge)
    if mode!='vertical':return registration.solve(rows,tolerance,mode)
    basis=_basis(edge.get('vertical_axis',[0.,0.,1.]))
    origin=np.mean([r['control'] for r in rows if r['role']=='fit'],axis=0)
    local=[{**r,'source':((np.asarray(r['source'])-origin)@basis).tolist(),
            'control':((np.asarray(r['control'])-origin)@basis).tolist()} for r in rows]
    solution=registration.solve(local,tolerance,'vertical')
    R=basis@np.asarray(solution['rotation'])@basis.T
    t=origin+basis@np.asarray(solution['translation'])-R@origin
    solution.update(rotation=R.tolist(),translation=t.tolist())
    return solution


class _MixedMotions:
    """Exact old 2D-link constraints with independently solved 3D setups.

    A vertical-linked group shares two tilt parameters and each setup has its
    own heading/translation. Its shared tilt is fixed when it contains the
    selected anchor. Thus an old two-target link never gains unobservable 3D
    freedoms merely because a new link is solved in full 3D.
    """
    def __init__(self,ids,edges,anchor,centres):
        self.centres=centres;self.anchor=anchor;self.size=0;self.groups=[];self.nodes={}
        remaining=set(ids)
        while remaining:
            group={min(remaining)}
            while True:
                before=len(group)
                for edge in edges:
                    if edge_mode(edge)=='vertical' and (edge['a'] in group or edge['b'] in group):
                        group.update((edge['a'],edge['b']))
                if len(group)==before:break
            remaining-=group
            vertical=[e for e in edges if edge_mode(e)=='vertical' and e['a'] in group and e['b'] in group]
            if not vertical:
                ident=next(iter(group));indices=[]
                if ident!=anchor:indices=list(range(self.size,self.size+6));self.size+=6
                self.nodes[ident]=dict(kind='rigid',indices=indices)
                continue
            basis=_basis(vertical[0].get('vertical_axis',[0.,0.,1.]))
            for edge in vertical[1:]:
                axis=_basis(edge.get('vertical_axis',[0.,0.,1.]))[:,2]
                if abs(float(axis@basis[:,2]))<1-1e-7:
                    raise ValueError('Connected keep-vertical links have different stored up directions. Re-register those links with one consistent vertical direction before a loop adjustment.')
            shared=[]
            if anchor not in group:shared=list(range(self.size,self.size+2));self.size+=2
            entry=dict(ids=sorted(group),basis=basis,indices=shared)
            self.groups.append(entry)
            for ident in sorted(group):
                indices=[]
                if ident!=anchor:indices=list(range(self.size,self.size+4));self.size+=4
                self.nodes[ident]=dict(kind='vertical',indices=indices,group=entry)
        self.fixed=[anchor];self.moving=sorted(set(ids)-{anchor});self.seed_ids={anchor}

    def dependencies(self,ident):
        node=self.nodes[ident]
        return node['indices']+(node.get('group',{}).get('indices',[]))

    def transforms(self,values):
        out={}
        for ident,node in self.nodes.items():
            ix=node['indices']
            if not ix:out[ident]=(np.eye(3),np.zeros(3));continue
            v=values[ix]
            if node['kind']=='rigid':R=Rotation.from_rotvec(v[:3]).as_matrix()
            else:
                group=node['group'];basis=group['basis'];tilt=values[group['indices']] if group['indices'] else np.zeros(2)
                R=Rotation.from_rotvec(basis[:,:2]@tilt).as_matrix()@Rotation.from_rotvec(basis[:,2]*v[0]).as_matrix()
            out[ident]=(R,v[-3:])
        return out

    def seed(self,poses):
        start=np.zeros(self.size)
        for group in self.groups:
            if not group['indices']:continue
            root=next((ident for ident in group['ids'] if ident in poses),None)
            if root is None:continue
            basis=group['basis'];axis=basis[:,2];target=poses[root][0]@axis
            cross=np.cross(axis,target);length=np.linalg.norm(cross)
            tilt=(cross/length*np.arctan2(length,np.clip(axis@target,-1,1)) if length>1e-12 else
                  basis[:,0]*np.pi if axis@target<0 else np.zeros(3))
            start[group['indices']]=basis[:,:2].T@tilt
        for ident,node in self.nodes.items():
            ix=node['indices']
            if not ix or ident not in poses:continue
            R,t=poses[ident]
            if node['kind']=='rigid':angles=Rotation.from_matrix(R).as_rotvec()
            else:
                group=node['group'];basis=group['basis'];tilt=start[group['indices']] if group['indices'] else np.zeros(2)
                Q=Rotation.from_rotvec(basis[:,:2]@tilt).as_matrix();direction=Q.T@R@basis[:,0]
                angles=[np.arctan2(basis[:,1]@direction,basis[:,0]@direction)]
            start[ix]=np.r_[angles,R@self.centres[ident]+t-self.centres[ident]]
        return start


class _ReferenceMotions:
    """Compatibility for explicitly held measured-coordinate workflows."""
    def __init__(self,ids,centres,kept,mode):
        from registration_references import Motion
        self.centres=centres;self.motions={ident:Motion([np.asarray(r['coordinates'])-centres[ident] for r in kept if r['scan_id']==ident],mode) for ident in sorted(ids)}
        self.indices={};self.size=0
        for ident,motion in self.motions.items():
            self.indices[ident]=list(range(self.size,self.size+motion.dof));self.size+=motion.dof
        self.moving=[ident for ident,motion in self.motions.items() if motion.dof]
        self.fixed=sorted(set(ids)-set(self.moving))
        self.seed_ids={ident for ident,motion in self.motions.items() if not motion.dof or motion.pivot is not None}

    def dependencies(self,ident):return self.indices[ident]
    def transforms(self,values):return {ident:motion.transform(values[self.indices[ident]]) for ident,motion in self.motions.items()}
    def seed(self,poses):
        start=np.zeros(self.size)
        for ident,motion in self.motions.items():
            if motion.dof and motion.pivot is None and ident in poses:
                R,t=poses[ident];angles=[np.arctan2(R[1,0],R[0,0])] if motion.mode=='vertical' else Rotation.from_matrix(R).as_rotvec()
                start[self.indices[ident]]=np.r_[angles,R@self.centres[ident]+t-self.centres[ident]]
        return start


def topology(s,ids):
    links=[{**{k:l.get(k) for k in ('id','a','b','matches','vertical_axis')},'mode':edge_mode(l)} for l in s.STATE.get('scan_links',[]) if l['a'] in ids and l['b'] in ids]
    return hashlib.sha256(json.dumps(links,sort_keys=True).encode()).hexdigest()

def edge_rows(s,edge):
    aa,bb=rw.scan(s,edge['a']),rw.scan(s,edge['b']);ta,tb=rw.targets(aa),rw.targets(bb);rows=[];sa=set();sb=set()
    if not edge.get('matches'):raise ValueError('The link '+aa['name']+' ↔ '+bb['name']+' has no recorded target pairs. Open that link and match its targets before adjusting this network.')
    for m in rw.matched_targets(aa,bb,edge['matches']):
        role=m.get('role','fit')
        if role=='off':continue
        if role=='leica':role='fit'
        a,b=m['a'],m['b']
        if a not in ta or b not in tb:raise ValueError('A target recorded on '+aa['name']+' ↔ '+bb['name']+' is missing. Review this link first.')
        if a in sa or b in sb:raise ValueError('Each target may appear only once in each link.')
        sa.add(a);sb.add(b);rows.append(dict(id=a+' ↔ '+b,role=role,source=tb[b]['source'],control=ta[a]['source']))
    return rows

def stats(rows,role='fit'):
    errors=[r['error_mm'] for r in rows if r['role']==role]
    return dict(count=len(errors),rms_mm=float(np.sqrt(np.mean(np.square(errors)))) if errors else None,max_mm=max(errors) if errors else None)

def solve(s,a,b,matches,tolerance=5,mode='rigid',anchor=None,progress=lambda m:None,references=None):
    """Adjust the full connected bundle, including available surface links."""
    from bundle_workflow import solve_link
    return solve_link(s,a,b,matches,tolerance,mode,anchor,progress,references)
