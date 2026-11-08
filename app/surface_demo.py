# Copyright (c) 2026 Damian Gulczynski.
"""Synthetic intersecting tunnels with known wall/pipe/plant/noise labels."""
from pathlib import Path
import numpy as np


def generate(folder,spacing=.065,seed=47):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);rng=np.random.default_rng(seed);sets=[];truth=[]
    def axis(a,b):return np.arange(a+spacing/2,b,spacing)
    def plane(fixed,value,a,b,limit=None):
        u,v=np.meshgrid(axis(*a),axis(*b));q=np.zeros((u.size,3));free=[j for j in range(3) if j!=fixed];q[:,fixed]=value;q[:,free[0]]=u.ravel();q[:,free[1]]=v.ravel()
        if limit is not None:q=q[limit(q)]
        q+=rng.normal(0,.0015,q.shape);sets.append(q);truth.append(np.ones(len(q),dtype='u1'))
    # Boundary of the union of perpendicular square-section tunnels.
    cross=lambda q:(np.abs(q[:,0])<=1.5)|(np.abs(q[:,1])<=1.5)
    for z in [-1.5,1.5]:plane(2,z,(-4,4),(-4,4),cross)
    for y in [-1.5,1.5]:plane(1,y,(-4,4),(-1.5,1.5),lambda q:np.abs(q[:,0])>=1.5)
    for x in [-1.5,1.5]:plane(0,x,(-4,4),(-1.5,1.5),lambda q:np.abs(q[:,1])>=1.5)
    theta=np.arange(0,2*np.pi,.22);length=axis(-3.8,3.8);xx,tt=np.meshgrid(length,theta);pipe=np.c_[xx.ravel(),1.13+.09*np.cos(tt.ravel()),-.8+.09*np.sin(tt.ravel())];pipe+=rng.normal(0,.001,pipe.shape);sets.append(pipe);truth.append(np.zeros(len(pipe),dtype='u1'))
    # A free-standing equipment box, not touching the lining.
    for fixed in range(3):
        for value in [-.3,.3]:
            u,v=np.meshgrid(axis(-.3,.3),axis(-.3,.3));q=np.zeros((u.size,3));free=[j for j in range(3) if j!=fixed];q[:,fixed]=value;q[:,free[0]]=u.ravel();q[:,free[1]]=v.ravel();q[:,2]-=.6;sets.append(q);truth.append(np.zeros(len(q),dtype='u1'))
    noise=rng.uniform([-3.9,-1.4,-1.4],[3.9,1.4,1.4],(150,3));sets.append(noise);truth.append(np.zeros(len(noise),dtype='u1'))
    points=np.vstack(sets)+[200000.,375000.,100.];labels=np.concatenate(truth);order=rng.permutation(len(points));points=points[order];labels=labels[order]
    scan=folder/'synthetic_intersection.csv';np.savetxt(scan,points,delimiter=',',fmt='%.6f',header='e,n,h',comments='');labelpath=folder/'synthetic_surface.truth8';labels.tofile(labelpath)
    return scan,labelpath,points,labels
