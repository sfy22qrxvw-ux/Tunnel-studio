# Copyright (c) 2026 Damian Gulczynski.
"""Connected display simplification; never sample unrelated individual faces."""
from functools import lru_cache
from pathlib import Path
import numpy as np
from mesh_geometry import clean_mesh


def allocate(counts, budget, minimum=4):
    counts=np.asarray(counts,dtype=np.int64)
    if counts.ndim!=1 or np.any(counts<0) or int(budget)<0 or int(minimum)<1:
        raise ValueError('Invalid triangle counts or solid preview budget.')
    base=np.minimum(counts,minimum)
    if int(base.sum())>budget:
        raise ValueError('Too many separate mesh pieces for the solid preview budget. Hide some models or inspect a smaller selection. Full geometry is retained.')
    remaining=min(int(budget-base.sum()),int((counts-base).sum()))
    capacity=counts-base
    if remaining and capacity.sum():
        share=remaining*capacity/capacity.sum();extra=np.floor(share).astype(np.int64)
        left=remaining-int(extra.sum())
        if left:extra[np.argsort(-(share-extra),kind='stable')[:left]]+=1
        base+=extra
    return base


def _signature(path):
    p=Path(path);st=p.stat()
    return str(p.resolve()),st.st_size,st.st_mtime_ns


def geometry(meta,budget):
    return _cached(_signature(meta['vertices_file']),_signature(meta['faces_file']),
                   int(meta['vertices']),int(meta['triangles']),int(budget))


@lru_cache(maxsize=6)
def _cached(vkey,fkey,nv,nf,budget):
    if nv<0 or nf<0 or budget<1 or vkey[1]!=nv*24 or fkey[1]!=nf*12:
        raise ValueError('The mesh cache is incomplete or has an unexpected length. Reopen the source model or restore a saved project.')
    if not nf:return np.empty((0,3)),np.empty((0,3),dtype=np.uint32)
    if not nv:raise ValueError('The mesh has faces but no vertices.')
    v=np.memmap(vkey[0],dtype='<f8',mode='r',shape=(nv,3))
    f=np.memmap(fkey[0],dtype='<u4',mode='r',shape=(nf,3))
    if f.max()>=nv:raise ValueError('Mesh contains invalid face indices.')
    for start in range(0,nv,100000):
        if not np.isfinite(v[start:start+100000]).all():
            raise ValueError('Mesh contains non-finite vertex coordinates.')
    if nf<=budget:
        vertices,faces=np.array(v),np.array(f)
        vertices.setflags(write=False);faces.setflags(write=False)
        return vertices,faces
    try:import open3d as o3d
    except ImportError as e:
        raise ValueError('Connected solid preview needs Open3D for this large model. Reinstall the complete current Tunnel Studio installer, then retry. The full mesh is retained for export.') from e
    origin=np.asarray(v).mean(axis=0)
    mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(v-origin),o3d.utility.Vector3iVector(f))
    labels,counts,_=mesh.cluster_connected_triangles()
    labels=np.asarray(labels);counts=np.asarray(counts,dtype=np.int64)
    quotas=allocate(counts,budget)
    # Group once, avoiding an O(components * faces) selection loop.
    order=np.argsort(labels,kind='stable');ends=np.cumsum(counts);starts=ends-counts
    pieces_v=[];pieces_f=[];offset=0
    for start,end,quota in zip(starts,ends,quotas):
        faces=np.asarray(f[order[start:end]],dtype=np.int64)
        used,inv=np.unique(faces,return_inverse=True)
        pv=np.asarray(v[used])-origin;pf=inv.reshape(-1,3)
        part=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(pv),o3d.utility.Vector3iVector(pf))
        if len(pf)>quota:
            part=part.simplify_quadric_decimation(target_number_of_triangles=int(quota),boundary_weight=10.)
            pv,pf=clean_mesh(np.asarray(part.vertices),np.asarray(part.triangles))
            if not len(pf):
                raise ValueError('Solid preview simplification removed a complete surface piece. Inspect a smaller model or reduce a copy. Full geometry is retained.')
            if len(pf)>quota:
                raise ValueError('This mesh could not be simplified within the solid preview budget. Hide other models or reduce a copy. Full geometry is retained.')
            # Validate the cleaned result that will actually be displayed.
            part=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(pv),o3d.utility.Vector3iVector(pf))
            new_labels,new_counts,_=part.cluster_connected_triangles()
            if len(new_counts)!=1:
                raise ValueError('Solid preview simplification split a connected piece. Inspect a smaller model or reduce a copy. Full geometry is retained.')
        pieces_v.append(pv+origin);pieces_f.append(np.asarray(pf,dtype=np.uint32)+offset);offset+=len(pv)
    vertices=np.concatenate(pieces_v);faces=np.concatenate(pieces_f)
    vertices.setflags(write=False);faces.setflags(write=False)
    return vertices,faces
