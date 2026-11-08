# Copyright (c) 2026 Damian Gulczynski.
from pathlib import Path
import numpy as np


def generate(folder):
    folder=Path(folder); folder.mkdir(exist_ok=True); rng=np.random.default_rng(42)
    centres=np.array([[0.,0,0],[3,0,1],[0,4,0],[3,4,2],[1.2,2,1.3]])
    a=np.deg2rad(22); r=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1.]])
    t=np.array([200000.,375000.,100.]); chunks=[]; rows=[]
    for j,c in enumerate(centres):
        u,v=np.meshgrid(np.linspace(-.108625,.108625,80),np.linspace(-.108625,.108625,80)); u=u.ravel(); v=v.ravel()
        intensity=np.where(u*v>=0,220.,30.)+rng.normal(0,2,len(u)); p=c+np.column_stack((u,v,rng.normal(0,.00015,len(u))))
        chunks.append(np.column_stack((p,intensity)))
        control=r@c+t
        rows.append({'id':f'CP{j+1:02}','role':'check' if j==4 else 'fit','source':c.tolist(),'control':control.tolist(),'method':'Synthetic known centre'})
    path=folder/'synthetic_targets.csv'; np.savetxt(path,np.vstack(chunks),delimiter=',',header='x,y,z,intensity',comments='',fmt='%.9f')
    with (folder/'control.csv').open('w') as f:
        f.write('id,e,n,h\n')
        for row in rows: f.write(row['id']+','+','.join(f'{v:.9f}' for v in row['control'])+'\n')
    return path,rows
