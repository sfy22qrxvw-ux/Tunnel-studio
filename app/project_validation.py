# Copyright (c) 2026 Damian Gulczynski.
"""Bounded validation of saved full-density cleaning results."""
from pathlib import Path
import tempfile
import numpy as np

BLOCK=100000

def validate_surface(result, work, progress=lambda _:None):
    total=result['statistics']['input_points'];source=result['source_meta']['count']
    valid=np.asarray(sorted({-1,*[r['id'] for r in result['clusters']]}),dtype=np.int64)
    if not set(result['kept'])<=set(valid.tolist()):raise ValueError('Invalid surface cluster labels.')
    ordered=True;last=-1;read=0
    with open(result['point_ids_file'],'rb') as ids,open(result['labels_file'],'rb') as labels:
        while read<total:
            count=min(BLOCK,total-read);i=np.fromfile(ids,'<i8',count=count);lab=np.fromfile(labels,'<i4',count=count)
            if len(i)!=count or len(lab)!=count:raise ValueError('Incomplete surface extraction cache.')
            if i.min()<0 or i.max()>=source:raise ValueError('Invalid surface point identities.')
            if not np.isin(lab,valid).all():raise ValueError('Invalid surface cluster labels.')
            ordered&=bool(i[0]>last and (np.diff(i)>0).all());last=int(i[-1]);read+=count
            progress(f'Checking saved analysis · {read:,}/{total:,} point identities')
    if ordered:return
    # Legacy methods can output unsorted IDs. Use an on-disk byte set rather
    # than np.unique over a multi-billion-entry cloud. Map only 8 MiB at once.
    with tempfile.TemporaryDirectory(prefix='project_id_check_',dir=work) as folder:
        path=Path(folder)/'seen.bin'
        with path.open('wb') as f:f.truncate(source)
        step=8*1024*1024;read=0
        with open(result['point_ids_file'],'rb') as ids:
            while read<total:
                values=np.fromfile(ids,'<i8',count=min(BLOCK,total-read));unique=np.unique(values)
                if len(unique)!=len(values):raise ValueError('Invalid surface point identities: duplicate IDs.')
                blocks=unique//step
                for block in np.unique(blocks):
                    start=int(block)*step;local=unique[blocks==block]-start
                    seen=np.memmap(path,'u1','r+',offset=start,shape=(min(step,source-start),))
                    try:
                        if seen[local].any():raise ValueError('Invalid surface point identities: duplicate IDs.')
                        seen[local]=1
                    finally:seen._mmap.close()
                read+=len(values);progress(f'Checking unordered saved identities · {read:,}/{total:,}')
