"""Owned, bounded binary row reads for repeated exact point-cloud operations.

This deliberately has no whole-file array conversion or memory mapping. Array
indices preserve their order and duplicates; large gaps use separate short
reads instead of allocating the span between the first and last index.
"""
from pathlib import Path
import numpy as np


class RowFile:
    def __init__(self,path,dtype,count,width=1,block=100000):
        self.path=Path(path);self.dtype=np.dtype(dtype);self.count=int(count)
        self.width=int(width);self.block=int(block);self.stream=None
        if self.count<0 or self.width<1 or self.block<1:raise ValueError('Invalid binary row dimensions.')
        if self.path.stat().st_size!=self.count*self.width*self.dtype.itemsize:
            raise ValueError('A point or result cache is incomplete or changed: '+self.path.name)

    def __enter__(self):
        self.stream=self.path.open('rb');return self

    def __exit__(self,*args):
        if self.stream is not None:self.stream.close();self.stream=None

    def __len__(self):return self.count

    def read(self,start,count):
        start=int(start);count=int(count)
        if start<0 or count<0 or start+count>self.count or count>self.block:
            raise ValueError('A bounded point read exceeds its row range.')
        if self.stream is None:raise ValueError('The point reader is closed.')
        self.stream.seek(start*self.width*self.dtype.itemsize)
        result=np.fromfile(self.stream,self.dtype,count=count*self.width)
        if result.size!=count*self.width:raise ValueError('A point or result cache changed during the read.')
        return result if self.width==1 else result.reshape(count,self.width)

    def take(self,indices):
        indices=np.asarray(indices)
        if indices.ndim!=1 or indices.dtype.kind not in ('i','u') or len(indices)>self.block:
            raise ValueError('Select a bounded array of integer original point IDs.')
        if len(indices) and (indices.min()<0 or indices.max()>=self.count):
            raise ValueError('The review point identities are invalid.')
        result=np.empty((len(indices),) if self.width==1 else (len(indices),self.width),dtype=self.dtype)
        if not len(indices):return result
        indices=indices.astype(np.int64,copy=False)
        order=np.argsort(indices,kind='stable');ordered=indices[order]
        cells=ordered//self.block
        boundaries=np.r_[0,np.flatnonzero(cells[1:]!=cells[:-1])+1,len(ordered)]
        for left,right in zip(boundaries[:-1],boundaries[1:]):
            chosen=ordered[left:right]
            # Dense/small spans use one read. Sparse calibration/review IDs
            # must not force a full block read for each distant measurement.
            if chosen[-1]-chosen[0]+1<=max(4096,16*len(chosen)):
                spans=[(int(left),int(right))]
            else:
                cuts=np.r_[left,left+np.flatnonzero(np.diff(chosen)>64)+1,right]
                spans=zip(cuts[:-1],cuts[1:])
            for lo,hi in spans:
                first=int(ordered[lo]);last=int(ordered[hi-1])
                values=self.read(first,last-first+1)
                result[order[lo:hi]]=values[ordered[lo:hi]-first]
        return result

    def __getitem__(self,key):
        if isinstance(key,slice):
            start,stop,step=key.indices(self.count)
            if step!=1:raise ValueError('Use explicit original IDs for a strided bounded read.')
            return self.read(start,max(0,stop-start))
        if isinstance(key,(int,np.integer)):
            index=int(key)
            if index<0:index+=self.count
            return self.read(index,1)[0]
        return self.take(key)
